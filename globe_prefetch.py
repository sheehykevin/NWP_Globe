"""Pre-download and prepare every model, layer and hour in one go.

    conda run -n ukmo_globe python globe.py --prefetch [MODEL ...]
    prefetch.bat [MODEL ...]                  (same, from this folder)
    nwp-globe-fetch.exe --prefetch [MODEL ...]  (built program)

For each model (default: all), the latest run is fetched as the download
buttons do, then every layer the model has is prepared for every hour:
files downloaded, fields computed and saved. Everything goes into
``data/prefetched`` and ``cache/prefetched``, kept until the next
pre-download replaces them (they are not deleted when the globe closes).
The globe then opens with these models loaded and needs no downloads to
show any of their layers.

The new run is written to ``*_new`` folders and swapped in at the end,
so a failed or interrupted pre-download leaves the previous one in place.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone

import globe_data as data
from globe_paths import APP_DIR

PREFETCHED = os.path.join(APP_DIR, "data", "prefetched")
PREFETCHED_CACHE = os.path.join(APP_DIR, "cache", "prefetched")
MANIFEST = os.path.join(PREFETCHED, "manifest.json")
WORKERS = 6


def manifest():
    """{model: run} of the pre-downloaded runs, or {}."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f).get("runs", {})
    except (OSError, ValueError):
        return {}


def _size(folder):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(folder) for f in fs)


def swap(new, old):
    """Put ``new`` in place of ``old``; True when done."""
    aside = old + "_old"
    shutil.rmtree(aside, ignore_errors=True)
    try:
        if os.path.exists(old):
            os.replace(old, aside)
        os.replace(new, old)
    except OSError:
        if not os.path.exists(old) and os.path.exists(aside):
            os.replace(aside, old)               # put the old one back
        return False
    shutil.rmtree(aside, ignore_errors=True)
    return True


def finish_swap():
    """Swap in a complete pre-download left by a refused swap."""
    ok = True
    for new, old in ((PREFETCHED + "_new", PREFETCHED),
                     (PREFETCHED_CACHE + "_new", PREFETCHED_CACHE)):
        if os.path.exists(new):
            ok &= swap(new, old)
    print("Swapped in." if ok else "Still locked: close the program "
          "holding data/prefetched or cache/prefetched.")
    return 0 if ok else 1


def run(models=None):

    import geovista as gv
    import globe_core as core
    import globe_fetch

    models = models or list(core.MODEL_ORDER)
    new_data, new_cache = PREFETCHED + "_new", PREFETCHED_CACHE + "_new"
    for folder in (new_data, new_cache):
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
    data.DATA = new_data                     # downloads go here
    data.READ_PREFETCHED = False             # the old ones are replaced
    core.PREFETCHED_CACHE = new_cache
    pl = gv.GeoPlotter(off_screen=True, window_size=(200, 200))
    scene = core.Scene(pl, disk=new_cache, keep_in_memory=False)
    scene.drawing = False                    # prepare only, draw nothing
    runs, report = {}, []
    t_all = time.time()
    for m in models:
        label = data.MODELS[m]["label"]
        print(f"== {label}", flush=True)
        t0 = time.time()
        try:
            if m == "harmonie":
                globe_fetch.fetch_harmonie()
            elif m in ("ibi", "nws"):
                globe_fetch.fetch_waves(m)
            lm = core.load_model(m)
        except (Exception, SystemExit) as exc:
            print(f"   skipped: {exc}", flush=True)
            report.append((label, "skipped", str(exc)[:80]))
            continue
        scene.add_model(lm)
        for lid in scene.layers:
            scene.visible[lid] = lid.startswith(m + ":")
        mine = [lid for lid in scene.layers if lid.startswith(m + ":")]
        wanted = len(mine) * len(scene.times)
        if not getattr(scene, "_started", False):
            scene.start_workers(WORKERS)
            scene._started = True
        scene.wake.set()
        last = -1
        while True:
            with scene.lock:
                done = sum((lid, t) in scene.cache for lid in mine
                           for t in scene.times)
                busy = any(k[0] in mine for k in scene.claimed)
            if done != last:
                print(f"   {done}/{wanted} layer-hours ready", flush=True)
                last = done
            if done >= wanted and not busy:
                break
            time.sleep(2)
        scene.rain.clear()                   # free this model's rain store
        scene.centres_xyz.clear()
        runs[m] = lm.run
        took = time.time() - t0
        print(f"   done: run {lm.run}, {took / 60:.1f} min", flush=True)
        report.append((label, lm.run, f"{took / 60:.1f} min"))
    data.CLOSING.set()                       # stop the workers
    time.sleep(1)
    if not runs:
        print("Nothing pre-downloaded; the previous pre-download is kept.")
        return 1
    with open(os.path.join(new_data, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"made": datetime.now(timezone.utc).isoformat(),
                   "runs": runs}, f, indent=1)
    # Swap the new pre-download in, as a whole: the old one is renamed
    # aside first, so a locked folder leaves both complete.
    for new, old in ((new_data, PREFETCHED), (new_cache, PREFETCHED_CACHE)):
        if not swap(new, old):
            print(f"Could not replace {old}: a program (the globe, File "
                  "Explorer or a command window) has it open. The new "
                  f"pre-download is complete in {new}; close that program "
                  "and run: globe.py --prefetch-swap (or prefetch.bat "
                  "--prefetch-swap).")
            return 1
    print("\nPre-download complete "
          f"({(time.time() - t_all) / 60:.0f} min; data "
          f"{_size(PREFETCHED) / 1e9:.1f} GB, prepared layers "
          f"{_size(PREFETCHED_CACHE) / 1e9:.1f} GB):")
    for row in report:
        print("  " + " | ".join(row))
    return 0
