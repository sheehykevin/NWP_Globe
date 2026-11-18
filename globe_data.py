"""Where the model files are, finding the latest runs, and downloading.

Everything is downloaded on request into a session folder,
``data/session``, which is deleted when the globe closes (and at the
next start, if it was not). UKV and Global files come anonymously over
HTTPS from the Met Office's public AWS bucket, one file per field per
hour, when a layer needs them. HARMONIE and IBI are fetched whole by
``globe_fetch.py`` into the same folder and layout:

    data/session/<model dir>/<run>/<parameter>/<valid>-PT<lead>H00M-<parameter>[-PT01H|-PT03H].nc

An optional read-only archive (the UKMO NWP tools' download archive)
can be searched too.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from globe_paths import APP_DIR as HERE  # next to the exe when built
# The store: every model's newest fetched run, kept until a newer run of
# that model is fetched or the store is cleared (globe_jobs.py).
DATA = os.path.join(HERE, "data", "store")
# Downloads are made by fetch jobs; the window only reads the store.
ALLOW_DOWNLOAD = True
BUCKET_URL = ("https://met-office-atmospheric-model-data.s3.eu-west-2"
              ".amazonaws.com")

# run_hours / last lead: how the latest complete run is found.
MODELS = {
    "ukv": dict(label="UKV", dir="ukv", prefix="uk-deterministic-2km",
                run_hours=range(0, 24, 3), last_lead=54),
    "harmonie": dict(label="HARMONIE (KNMI)", dir="harmonie", prefix=None),
    # DWD open data, file by file (globe_icon.py).
    # Météo-France open data, package by package (globe_meteofrance.py).
    "arome_001": dict(label="AROME 1.3 km (Météo-France)", dir="arome_001",
                      prefix="mf"),
    "arome_0025": dict(label="AROME 2.5 km (Météo-France)",
                       dir="arome_0025", prefix="mf"),
    "aromeifs_0025": dict(label="AROME-IFS 2.5 km (Météo-France)",
                          dir="aromeifs_0025", prefix="mf"),
    "icon": dict(label="ICON-EU (DWD)", dir="icon", prefix="dwd"),
    # ECMWF open data 0.1 deg, field by field (globe_ecmwf.py).
    "ecmwf": dict(label="ECMWF IFS 0.1", dir="ecmwf", prefix="ecmwf"),
    # NOAA GFS on AWS, field by field (globe_gfs.py): its two products
    # are two models, each on its own grid.
    "gfs_sflux": dict(label="GFS sflux 13 km (NOAA)", dir="gfs_sflux",
                      prefix="noaa"),
    "gfs_0p25": dict(label="GFS 0.25 (NOAA)", dir="gfs_0p25",
                     prefix="noaa"),
    "global": dict(label="UKMO Global", dir="global10km",
                   prefix="global-deterministic-10km",
                   run_hours=range(0, 24, 6), last_lead=54),
    "nws": dict(label="NWS waves (1.5 km)", dir="nws", prefix=None),
    "ibi": dict(label="IBI waves", dir="ibi", prefix=None),
}
# Parameters that are accumulations carry their period in the name.
ACCUMULATED = {"precipitation_accumulation"}
NAME = re.compile(r"^(\d{8}T\d{4}Z)-PT(\d{4})H00M-([A-Za-z0-9_]+?)"
                  r"(?:-PT(\d\d)H)?\.nc$")
READY_PARAM = "pressure_at_mean_sea_level"


def stamp(text):
    return datetime.strptime(text, "%Y%m%dT%H%MZ")


# Models fetched field by field through a module of their own.
FIELD_SOURCES = {"icon": "globe_icon", "ecmwf": "globe_ecmwf",
                 "gfs_sflux": "globe_gfs:SFLUX", "gfs_0p25": "globe_gfs:P025",
                 "arome_001": "globe_meteofrance:AROME_001",
                 "arome_0025": "globe_meteofrance:AROME_0025",
                 "aromeifs_0025": "globe_meteofrance:AROMEIFS_0025"}


def field_source(model):
    import importlib
    module, _, name = FIELD_SOURCES[model].partition(":")
    mod = importlib.import_module(module)
    return getattr(mod, name) if name else mod


def published_leads(model, parameter, run=None):
    """(lead, period) pairs a model publishes: Met Office hourly to
    T+54, Global then 3-hourly to T+69; ICON-EU per globe_icon.leads.
    Accumulations start at T+1 and (Global) switch from 1-hour to 3-hour
    periods after T+54."""
    acc = parameter in ACCUMULATED
    if model in FIELD_SOURCES:
        src = field_source(model)
        if parameter not in src.FIELDS:
            return []
        return src.leads(stamp(run).hour, acc)
    out = [(h, 1 if acc else None) for h in range(1 if acc else 0, 55)]
    if model == "global":
        out += [(h, 3 if acc else None) for h in range(57, 70, 3)]
    return out


def file_name(run, parameter, valid, period=None):
    lead = int((valid - stamp(run)) / timedelta(hours=1))
    name = f"{valid:%Y%m%dT%H%MZ}-PT{lead:04d}H00M-{parameter}"
    if period is not None:
        name += f"-PT{period:02d}H"
    return name + ".nc"


def latest_run(model, now=None, back_hours=48):
    """Newest run of a Met Office model whose last file is published
    (checked with an HTTPS HEAD request), or None."""
    if model in FIELD_SOURCES:
        return field_source(model).latest_run(now)
    m = MODELS[model]
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(back_hours):
        if t.hour in m["run_hours"]:
            run = f"{t:%Y%m%dT%H%MZ}"
            name = file_name(run, READY_PARAM,
                             t + timedelta(hours=m["last_lead"]))
            req = urllib.request.Request(
                f"{BUCKET_URL}/{m['prefix']}/{run}/{name}", method="HEAD")
            try:
                with urllib.request.urlopen(req, timeout=20):
                    return run
            except (urllib.error.URLError, OSError):
                pass
        t -= timedelta(hours=1)
    return None


def held_runs(model):
    """Runs of a model in the store, oldest first."""
    try:
        runs = os.listdir(os.path.join(DATA, MODELS[model]["dir"]))
    except OSError:
        return []
    return sorted(r for r in runs if re.fullmatch(r"\d{8}T\d{4}Z", r))


CLOSING = threading.Event()        # set when the globe closes
ACTIVE = [0]                       # downloads in progress
ACTIVE_LOCK = threading.Lock()


def log_path():
    return os.path.join(DATA, "log.txt")


def log_event(action, model="", run="", detail=""):
    """One line in the store's log (data/store/log.txt): every deletion
    and anything else worth explaining later. Several processes write;
    a failed write is retried briefly, then given up."""
    import time
    line = (f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}  {action:<10}"
            f"  {model:<14} {run:<15} {detail}\n")
    path = log_path()
    for _ in range(5):
        try:
            os.makedirs(DATA, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
                os.replace(path, path[:-4] + ".old.txt")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except OSError:
            time.sleep(0.1)


def last_event(model):
    """The newest log line about ``model``, or ''."""
    try:
        with open(log_path(), encoding="utf-8") as f:
            lines = [s.rstrip() for s in f if f"  {model} " in s]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def drop_old_runs(model, keep):
    """Delete a model's runs in the store other than ``keep``."""
    for run in held_runs(model):
        if run != keep:
            log_event("drop-run", model, run, f"newer run {keep} kept")
            shutil.rmtree(os.path.join(DATA, MODELS[model]["dir"], run),
                          ignore_errors=True)


def migrate():
    """Move data from the older layout (data/prefetched, data/session)
    into the store, once."""
    old = os.path.join(HERE, "data", "prefetched")
    if os.path.isdir(old):
        left = merge_tree(old, DATA)
        if left:
            log_event("migrate", detail=f"{left} file(s) could not be moved "
                      f"from {old}; tried again at the next start")
    shutil.rmtree(os.path.join(HERE, "data", "session"), ignore_errors=True)


def merge_tree(src, dst):
    """Move every file under ``src`` to the same place under ``dst``
    (files already there are kept, the duplicate in ``src`` removed),
    then remove the folders of ``src`` that became empty. Nothing that
    failed to move is deleted. Returns how many files were left."""
    left = 0
    for root, dirs, files in os.walk(src, topdown=False):
        rel = os.path.relpath(root, src)
        target = os.path.normpath(os.path.join(dst, rel))
        for f in files:
            a, b = os.path.join(root, f), os.path.join(target, f)
            try:
                if os.path.exists(b):
                    os.remove(a)
                else:
                    os.makedirs(target, exist_ok=True)
                    os.replace(a, b)
            except OSError:
                left += 1
        try:
            os.rmdir(root)                      # only if empty
        except OSError:
            pass
    return left


def clear_session(wait=0.0):
    """Delete the whole store (the "Clear downloaded data" action).
    With ``wait``, first stop downloads (those in flight abort) and wait
    up to that long for them."""
    import time
    if wait:
        CLOSING.set()
        end = time.time() + wait
        while ACTIVE[0] and time.time() < end:
            time.sleep(0.1)
    for _ in range(5):
        shutil.rmtree(DATA, ignore_errors=True)
        if not os.path.exists(DATA):
            break
        time.sleep(0.3)
    log_event("cleared", detail="the whole store was deleted")


class Files:
    """Every file one run has for one model, and how to get more."""

    def __init__(self, model, run, archive=None):
        self.model, self.run = model, run
        self.dirs = [os.path.join(DATA, MODELS[model]["dir"], run)]
        if archive:
            self.dirs.append(os.path.join(archive, MODELS[model]["dir"], run))
        self.can_download = MODELS[model]["prefix"] is not None
        self.lock = threading.Lock()
        self.found = {}          # parameter -> {(valid, period): path}
        self.missing = set()     # (parameter, valid, period) not published
        self.inflight = {}       # (parameter, valid, period) -> Event
        self.icon_cache = {}     # ICON raw fields, a few at a time
        self.expected_cache = {}
        self.downloads = 0

    def _scan(self, parameter):
        out = {}
        for root in reversed(self.dirs):    # session folder wins
            for path in glob.glob(os.path.join(root, parameter, "*.nc")):
                m = NAME.match(os.path.basename(path))
                if m and m.group(3) == parameter:
                    period = int(m.group(4)) if m.group(4) else None
                    out[(stamp(m.group(1)), period)] = path
        return out

    def held(self, parameter):
        """{(valid, period): path} for files already on disk."""
        with self.lock:
            if parameter not in self.found:
                self.found[parameter] = self._scan(parameter)
            return dict(self.found[parameter])

    def expected(self, parameter):
        """{(valid, period)} this run should have: what is held, plus
        (Met Office models) what the model publishes."""
        keys = set(self.held(parameter))
        if self.can_download:
            start = stamp(self.run)
            keys |= {(start + timedelta(hours=lead), period)
                     for lead, period in published_leads(
                         self.model, parameter, self.run)}
        return keys

    def _expected_cached(self, parameter):
        with self.lock:
            hit = self.expected_cache.get(parameter)
        if hit is None:
            hit = self.expected(parameter)
            with self.lock:
                self.expected_cache[parameter] = hit
        return hit

    def path(self, parameter, valid, period=None, fetch=True):
        """Path of one file, downloading it if needed; None if it is not
        available."""
        key = (valid, period)
        held = self.held(parameter)
        if key in held:
            return held[key]
        if not (fetch and self.can_download and ALLOW_DOWNLOAD) or \
                CLOSING.is_set():
            return None
        # Only steps the model is used for: no requests at hours a model
        # does not publish, or beyond its last lead in the globe.
        if key not in self._expected_cached(parameter):
            return None
        # One download per file: two layers often need the same file at
        # once (MSLP for isobars and for centres); the second waits for
        # the first instead of downloading into the same temporary file.
        job = (parameter, valid, period)
        with self.lock:
            if job in self.missing:
                return None
            wait = self.inflight.get(job)
            if wait is None:
                self.inflight[job] = threading.Event()
        if wait is not None:
            wait.wait(120)
            return self.held(parameter).get(key)
        try:
            return self._download(parameter, valid, period)
        finally:
            with self.lock:
                self.inflight.pop(job).set()

    def _download(self, parameter, valid, period):
        if self.model in FIELD_SOURCES:
            return self._download_icon(parameter, valid, period)
        name = file_name(self.run, parameter, valid, period)
        url = f"{BUCKET_URL}/{MODELS[self.model]['prefix']}/{self.run}/{name}"
        folder = os.path.join(self.dirs[0], parameter)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        part = dest + ".part"
        with self.lock:
            self.downloads += 1
        with ACTIVE_LOCK:
            ACTIVE[0] += 1
        try:
            with urllib.request.urlopen(url, timeout=60) as r, \
                    open(part, "wb") as f:
                while not CLOSING.is_set():      # stop at once on close
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if CLOSING.is_set():
                _remove(part)
                return None
            os.replace(part, dest)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):            # not published
                with self.lock:
                    self.missing.add((parameter, valid, period))
            _remove(part)
            return None
        except (OSError, urllib.error.URLError):
            _remove(part)
            return None
        finally:
            with self.lock:
                self.downloads -= 1
            with ACTIVE_LOCK:
                ACTIVE[0] -= 1
        with self.lock:
            self.found.setdefault(parameter, {})[(valid, period)] = dest
        return dest


def _download_icon(self, parameter, valid, period):
    """Fetch the ICON-EU / ECMWF field(s) behind one of our files and
    write it in our layout."""
    import globe_icon
    src = field_source(self.model)
    with ACTIVE_LOCK:
        ACTIVE[0] += 1
    with self.lock:
        self.downloads += 1
        if len(self.icon_cache) > 24:           # keep memory bounded
            self.icon_cache.clear()
    try:
        if CLOSING.is_set():
            return None
        got = src.make_field(self.run, parameter, valid, period,
                             self.icon_cache)
        if got is None or CLOSING.is_set():
            if got is None:
                with self.lock:
                    self.missing.add((parameter, valid, period))
            return None
        dest = os.path.join(self.dirs[0], parameter,
                            file_name(self.run, parameter, valid, period))
        globe_icon.write_netcdf(dest, parameter, *got)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(MODELS[self.model]["label"] + ":", parameter, valid, exc)
        return None
    finally:
        with self.lock:
            self.downloads -= 1
        with ACTIVE_LOCK:
            ACTIVE[0] -= 1
    with self.lock:
        self.found.setdefault(parameter, {})[(valid, period)] = dest
    return dest


Files._download_icon = _download_icon


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
