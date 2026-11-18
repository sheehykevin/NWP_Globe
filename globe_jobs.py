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
        os.makedirs(core.STORE_CACHE, exist_ok=True)
        for name in os.listdir(old):
            try:
                os.replace(os.path.join(old, name),
                           os.path.join(core.STORE_CACHE, name))
            except OSError:
                pass
        shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(os.path.join(core.CACHE, "session"), ignore_errors=True)


def drop_old_cache(model, keep):
    """Delete a model's prepared layers of runs other than ``keep``."""
    import globe_core as core
    for path in glob.glob(os.path.join(core.STORE_CACHE, f"{model}_*.npz")):
        if f"_{keep}_" not in os.path.basename(path):
            try:
                os.remove(path)
            except OSError:
                pass


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
    params = {p for s in layers for p in core.needs(s)}
    run = None if latest else stored_run(model)
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
    data.drop_old_runs(model, lm.run)
    drop_old_cache(model, lm.run)

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
