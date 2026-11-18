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
