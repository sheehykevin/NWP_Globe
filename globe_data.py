"""Where the model files are, and fetching the Global ones that are not.

Files are looked for first in this project's own ``data`` folder, then
in the UKMO NWP tools' archive (read only; never written to). Global
files that are in neither are downloaded, anonymously over HTTPS, from
the Met Office's public AWS bucket into ``data``. UKV is never
downloaded here: its files come from the archive.

Layout everywhere: ``<root>/<model dir>/<run>/<parameter>/<file>.nc``
with file names ``<valid>-PT<lead>H00M-<parameter>[-PT01H|-PT03H].nc``.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
KEEP_DOWNLOADED_RUNS = 2
BUCKET_URL = ("https://met-office-atmospheric-model-data.s3.eu-west-2"
              ".amazonaws.com")

MODELS = {
    "ukv": dict(label="UKV", dir="ukv", prefix=None),
    "global": dict(label="Global", dir="global10km",
                   prefix="global-deterministic-10km"),
    # Fetched by globe_fetch.py into data/ in the same layout.
    "harmonie": dict(label="HARMONIE (KNMI)", dir="harmonie", prefix=None),
    "ibi": dict(label="IBI waves", dir="ibi", prefix=None),
}
# Parameters that are accumulations carry their period in the name.
ACCUMULATED = {"precipitation_accumulation"}
NAME = re.compile(r"^(\d{8}T\d{4}Z)-PT(\d{4})H00M-([A-Za-z0-9_]+?)"
                  r"(?:-PT(\d\d)H)?\.nc$")


def stamp(text):
    return datetime.strptime(text, "%Y%m%dT%H%MZ")


def global_leads(parameter):
    """(lead, period) pairs the Global model publishes (from the
    inventory): hourly to T+54, then 3-hourly to T+69. Accumulations
    start at T+1 and switch from 1-hour to 3-hour periods after T+54."""
    hourly = range(1 if parameter in ACCUMULATED else 0, 55)
    tail = range(57, 70, 3)
    if parameter in ACCUMULATED:
        return [(h, 1) for h in hourly] + [(h, 3) for h in tail]
    return [(h, None) for h in hourly] + [(h, None) for h in tail]


class Files:
    """Every file one run has for one model, and how to get more."""

    def __init__(self, model, run, archive):
        self.model, self.run = model, run
        self.dirs = [os.path.join(DATA, MODELS[model]["dir"], run),
                     os.path.join(archive, MODELS[model]["dir"], run)]
        self.can_download = MODELS[model]["prefix"] is not None
        self.lock = threading.Lock()
        self.found = {}          # parameter -> {(valid, period): path}
        self.missing = set()     # (parameter, valid, period) not published
        self.downloads = 0

    def _scan(self, parameter):
        out = {}
        for root in reversed(self.dirs):          # own data folder wins
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
        (Global) what the model publishes."""
        keys = set(self.held(parameter))
        if self.can_download:
            start = stamp(self.run)
            keys |= {(start + timedelta(hours=lead), period)
                     for lead, period in global_leads(parameter)}
        return keys

    def path(self, parameter, valid, period=None, fetch=True):
        """Path of one file, downloading it (Global) if needed; None if
        it is not available."""
        key = (valid, period)
        held = self.held(parameter)
        if key in held:
            return held[key]
        if not (fetch and self.can_download):
            return None
        with self.lock:
            if (parameter, valid, period) in self.missing:
                return None
        return self._download(parameter, valid, period)

    def _download(self, parameter, valid, period):
        lead = int((valid - stamp(self.run)) / timedelta(hours=1))
        name = f"{valid:%Y%m%dT%H%MZ}-PT{lead:04d}H00M-{parameter}"
        if period is not None:
            name += f"-PT{period:02d}H"
        name += ".nc"
        url = f"{BUCKET_URL}/{MODELS[self.model]['prefix']}/{self.run}/{name}"
        folder = os.path.join(self.dirs[0], parameter)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        part = dest + ".part"
        with self.lock:
            self.downloads += 1
        try:
            with urllib.request.urlopen(url, timeout=60) as r, \
                    open(part, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
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
        with self.lock:
            self.found.setdefault(parameter, {})[(valid, period)] = dest
        return dest


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def latest_run(model, archive):
    runs = set()
    for root in (DATA, archive):
        try:
            runs |= set(os.listdir(os.path.join(root, MODELS[model]["dir"])))
        except OSError:
            pass
    runs = sorted(r for r in runs if re.fullmatch(r"\d{8}T\d{4}Z", r))
    if not runs:
        raise SystemExit(f"No {MODELS[model]['label']} runs in {archive} "
                         f"or {DATA}")
    return runs[-1]


def prune_downloads(keep_run=None):
    """Keep the newest few downloaded Global runs (and the one in use)."""
    root = os.path.join(DATA, MODELS["global"]["dir"])
    try:
        runs = sorted(os.listdir(root))
    except OSError:
        return
    for run in runs[:-KEEP_DOWNLOADED_RUNS]:
        if run != keep_run:
            shutil.rmtree(os.path.join(root, run), ignore_errors=True)
