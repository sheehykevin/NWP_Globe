"""Fetch jobs: download and prepare chosen layers of one model.

A job fetches every hour of the given layers of one model into the
store (``data/store``, prepared layers in ``cache/store``), skipping
fields and layers already there. The window runs jobs as separate
processes (its download buttons and fetch menus); ``prefetch.bat`` runs
them from the command line:

    globe.py --job MODEL [LAYER ...] [--latest]
    globe.py --prefetch [MODEL ...] [--layers LAYER ...]

The store keeps each model's newest fetched run: fetching a newer run of
a model deletes that model's older run (data and prepared layers).

Progress is printed as lines the window reads:
    PROGRESS <model> <done> <wanted>
    DONE <model> <run>
    FAILED <model> <message>
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import time

import globe_data as data

WORKERS = 6


def _say(*args):
    print(*args, flush=True)


def stored_run(model):
    runs = data.held_runs(model)
    return runs[-1] if runs else None


def migrate_cache():
    """Move prepared layers from the older layout into the store cache."""
    import globe_core as core
    old = os.path.join(core.CACHE, "prefetched")
    if os.path.isdir(old):
        left = data.merge_tree(old, core.STORE_CACHE)
        if left:
            data.log_event("migrate", detail=f"{left} prepared file(s) could "
                           f"not be moved from {old}")
    shutil.rmtree(os.path.join(core.CACHE, "session"), ignore_errors=True)


def drop_old_cache(model, keep):
    """Delete a model's prepared layers of runs other than ``keep``."""
    import globe_core as core
    gone = set()
    for path in glob.glob(os.path.join(core.STORE_CACHE, f"{model}_*.npz")):
        name = os.path.basename(path)
        if f"_{keep}_" not in name:
            try:
                os.remove(path)
                gone |= set(re.findall(r"_(\d{8}T\d{4}Z)_", name)[:1])
            except OSError:
                pass
    for run in sorted(gone):
        data.log_event("drop-cache", model, run,
                       f"prepared layers; newer run {keep} kept")


def model_size(model):
    """Bytes a model uses in the store (its runs and prepared layers)."""
    import globe_core as core
    total = 0
    for root, _, files in os.walk(os.path.join(data.DATA,
                                               data.MODELS[model]["dir"])):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    for path in glob.glob(os.path.join(core.STORE_CACHE, f"{model}_*.npz")):
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


def clear_model(model):
    """Delete one model's runs and prepared layers from the store."""
    import globe_core as core
    shutil.rmtree(os.path.join(data.DATA, data.MODELS[model]["dir"]),
                  ignore_errors=True)
    for path in glob.glob(os.path.join(core.STORE_CACHE, f"{model}_*.npz")):
        try:
            os.remove(path)
        except OSError:
            pass
    data.log_event("clear", model, detail="Clear this model's data")


def prepared_counts(model, run, folder=None):
    """{layer: hours prepared} of one model run in the store cache."""
    import globe_core as core
    folder = folder or core.STORE_CACHE
    counts, tail = {}, f"_{run}_"
    try:
        names = os.listdir(folder)
    except OSError:
        return counts
    for f in names:
        if f.startswith(model + "_") and tail in f and                 f.endswith(".npz") and not f.endswith(".part.npz"):
            style = f[len(model) + 1:f.index(tail)]
            counts[style] = counts.get(style, 0) + 1
    return counts


def run_job(model, layers=None, latest=False):
    """Fetch and prepare ``layers`` (default: all the model can have) of
    ``model``. The run: the latest published one when ``latest`` or
    when none is stored, else the stored one."""
    import geovista as gv
    import globe_core as core
    import globe_fetch

    styles = core.capable_styles(model)
    layers = [s for s in (layers or styles) if s in styles]
    if not layers:
        _say(f"DONE {model} -")
        return 0
    # Left over from a fetch that was stopped.
    for path in glob.glob(os.path.join(core.STORE_CACHE,
                                       f"{model}_*.part.npz")):
        try:
            os.remove(path)
        except OSError:
            pass
    old = stored_run(model)
    # Layers already fetched for the stored run: carried over to a newer
    # run, so fetching one layer of a new run does not lose the others.
    carried = [s for s in prepared_counts(model, old) if s in styles]         if old else []
    run = None if latest else old
    fetch_layers = layers + [s for s in carried if s not in layers]         if latest else layers
    params = {p for s in fetch_layers for p in core.needs(s)}
    try:
        if model == "harmonie":
            hrun = run[:8] + run[9:11] if run else None
            globe_fetch.fetch_harmonie(hrun, params)
            run = stored_run(model)
        elif model in core.WAVE_MODELS:
            if run is None:
                globe_fetch.fetch_waves(model)
                run = stored_run(model)
        lm = core.load_model(model, run=run)
    except (Exception, SystemExit) as exc:
        _say(f"FAILED {model} {exc}")
        return 1
    if old and lm.run != old:
        extra = [s for s in carried if s not in layers]
        layers = layers + extra
        if extra:
            _say(f"NOTE {model} new run {lm.run[9:11]}Z: refreshing "
                 f"{len(extra)} layer(s) fetched for {old[9:11]}Z")

    pl = gv.GeoPlotter(off_screen=True, window_size=(100, 100))
    scene = core.Scene(pl, keep_in_memory=False)
    scene.drawing = False                    # prepare only, draw nothing
    scene.add_model(lm)
    for lid in scene.layers:
        scene.visible[lid] = core.split_id(lid)[1] in layers
    mine = [lid for lid in scene.layers if scene.visible[lid]]
    wanted = len(mine) * len(scene.times)
    scene.start_workers(WORKERS)
    last = -1
    while True:
        with scene.lock:
            done = sum((lid, t) in scene.cache for lid in mine
                       for t in scene.times)
            busy = bool(scene.claimed)
        if done != last:
            _say(f"PROGRESS {model} {done} {wanted}")
            last = done
        if done >= wanted and not busy:
            break
        time.sleep(1)
    data.CLOSING.set()                       # stop this job's workers
    # Older run removed only now: the window shows it until this is done.
    data.drop_old_runs(model, lm.run)
    drop_old_cache(model, lm.run)
    _say(f"DONE {model} {lm.run}")
    return 0


def prefetch(models=None, layers=None):
    """Latest run of each model (default: all), the given layers
    (default: all), one job after another."""
    import globe_core as core
    t0 = time.time()
    failed = 0
    for m in models or core.MODEL_ORDER:
        _say(f"== {data.MODELS[m]['label']}")
        data.CLOSING.clear()
        failed += run_job(m, layers, latest=True)
    _say(f"Pre-download complete in {(time.time() - t0) / 60:.0f} min"
         + (f" ({failed} model(s) failed)" if failed else ""))
    return 1 if failed else 0


# ---- jobs as background processes (the window starts and follows them) ----
# Each job writes its output to data/store/jobs/<model>.log and its process
# id to <model>.pid while it runs, so it can outlive the window and a new
# window can pick it up.

def jobs_dir():
    return os.path.join(data.DATA, "jobs")


def job_paths(model):
    d = jobs_dir()
    return os.path.join(d, f"{model}.log"), os.path.join(d, f"{model}.pid")


def pid_alive(pid):
    """Is process ``pid`` still running (Windows and others)?"""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # query limited info
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259         # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def kill(pid):
    """End process ``pid`` (TerminateProcess on Windows)."""
    import signal
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass


def job_pid(model):
    """Process id of the model's running job, or None."""
    try:
        with open(job_paths(model)[1]) as f:
            pid = int(f.read().strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def _command(argv):
    """The program and arguments that run ``globe.py argv`` (built: the
    console program next to the exe)."""
    import sys
    if getattr(sys, "frozen", False):
        return [os.path.join(os.path.dirname(sys.executable),
                             "nwp-globe-fetch.exe"), *argv]
    return [sys.executable, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "globe.py"), *argv]


def _spawn(argv):
    """Start a process that survives the window, with no console."""
    import subprocess
    flags = 0
    if os.name == "nt":
        flags = (subprocess.DETACHED_PROCESS
                 | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    kw = dict(cwd=os.path.dirname(os.path.dirname(data.DATA)),
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        # Leave the launcher's Windows job object (conda run, terminals)
        # so closing it does not end the fetch; refused where not allowed.
        try:
            return subprocess.Popen(_command(argv), creationflags=flags
                                    | 0x01000000, **kw).pid
        except OSError:
            pass
    return subprocess.Popen(_command(argv), creationflags=flags, **kw).pid


def start_job(model, layers, latest=False):
    """Start a job in the background; returns its process id."""
    log, _ = job_paths(model)
    os.makedirs(jobs_dir(), exist_ok=True)
    try:
        os.remove(log)                  # the window reads it from the start
    except OSError:
        pass
    return _spawn(["--job", model, *layers,
                   *(["--latest"] if latest else []), "--log"])


def run_logged(model, layers=None, latest=False):
    """run_job with its output in the model's log, and a process-id file
    while it runs."""
    import sys
    import traceback
    log, pidfile = job_paths(model)
    os.makedirs(jobs_dir(), exist_ok=True)
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    out = open(log, "a", encoding="utf-8", buffering=1)
    saved = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = out
    try:
        return run_job(model, layers, latest)
    except BaseException as exc:
        traceback.print_exc()
        _say(f"FAILED {model} {exc}")
        return 1
    finally:
        sys.stdout, sys.stderr = saved
        out.close()
        try:
            os.remove(pidfile)
        except OSError:
            pass


def queue_path():
    return os.path.join(jobs_dir(), "queue.json")


def start_queue(items):
    """Run queued jobs [(model, layers, latest), ...] one after another
    in one background process (used when the window closes)."""
    import json
    os.makedirs(jobs_dir(), exist_ok=True)
    with open(queue_path(), "w", encoding="utf-8") as f:
        json.dump([list(i) for i in items], f)
    return _spawn(["--run-queue"])


def run_queue():
    import json
    try:
        with open(queue_path(), encoding="utf-8") as f:
            items = json.load(f)
        os.remove(queue_path())
    except (OSError, ValueError):
        return 0
    failed = 0
    for model, layers, latest in items:
        if job_pid(model):              # already being fetched
            continue
        data.CLOSING.clear()
        failed += run_logged(model, layers, latest) or 0
    return 1 if failed else 0


def latest_published(model):
    """The newest run of ``model`` its source has published, as
    YYYYMMDDTHHMMZ (None if it cannot be found). Network calls."""
    from datetime import datetime, timezone
    import globe_core as core
    if model in core.WAVE_MODELS:              # today 00Z, as fetched
        return f"{datetime.now(timezone.utc):%Y%m%d}T0000Z"
    if model == "harmonie":
        import requests
        import globe_fetch
        s = requests.Session()
        s.headers["Authorization"] = globe_fetch.knmi_key()
        listing = globe_fetch._knmi(s, "/files", maxKeys=5, orderBy="created",
                                    sorting="desc")
        for f in listing.get("files", []):
            m = re.search(r"(\d{8})(\d{2})\.tar$", f["filename"])
            if m:
                return f"{m.group(1)}T{m.group(2)}00Z"
        return None
    return data.latest_run(model)
