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

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "session")
BUCKET_URL = ("https://met-office-atmospheric-model-data.s3.eu-west-2"
              ".amazonaws.com")

# run_hours / last lead: how the latest complete run is found.
MODELS = {
    "ukv": dict(label="UKV", dir="ukv", prefix="uk-deterministic-2km",
                run_hours=range(0, 24, 3), last_lead=54),
    "harmonie": dict(label="HARMONIE (KNMI)", dir="harmonie", prefix=None),
    # DWD open data, file by file (globe_icon.py).
    "icon": dict(label="ICON-EU (DWD)", dir="icon", prefix="dwd"),
    # ECMWF open data 0.1 deg, field by field (globe_ecmwf.py).
    "ecmwf": dict(label="ECMWF IFS 0.1", dir="ecmwf", prefix="ecmwf"),
    "global": dict(label="Global", dir="global10km",
                   prefix="global-deterministic-10km",
                   run_hours=range(0, 24, 6), last_lead=54),
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
FIELD_SOURCES = {"icon": "globe_icon", "ecmwf": "globe_ecmwf"}


def field_source(model):
    import importlib
    return importlib.import_module(FIELD_SOURCES[model])


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
    """Runs of a model already in the session folder, oldest first."""
    try:
        runs = os.listdir(os.path.join(DATA, MODELS[model]["dir"]))
    except OSError:
        return []
    return sorted(r for r in runs if re.fullmatch(r"\d{8}T\d{4}Z", r))


CLOSING = threading.Event()        # set when the globe closes
ACTIVE = [0]                       # downloads in progress
ACTIVE_LOCK = threading.Lock()


def clear_session(wait=0.0):
    """Delete everything downloaded in this (or a crashed) session.
    With ``wait``, first stop downloads (those in flight abort) and wait
    up to that long for them, so nothing is written after the folder is
    gone."""
    import time
    if wait:
        CLOSING.set()
        end = time.time() + wait
        while ACTIVE[0] and time.time() < end:
            time.sleep(0.1)
    for _ in range(5):
        shutil.rmtree(DATA, ignore_errors=True)
        if not os.path.exists(DATA):
            return
        time.sleep(0.3)


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
        self.downloads = 0

    def _scan(self, parameter):
        out = {}
        for root in reversed(self.dirs):          # session folder wins
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

    def path(self, parameter, valid, period=None, fetch=True):
        """Path of one file, downloading it if needed; None if it is not
        available."""
        key = (valid, period)
        held = self.held(parameter)
        if key in held:
            return held[key]
        if not (fetch and self.can_download) or CLOSING.is_set():
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
