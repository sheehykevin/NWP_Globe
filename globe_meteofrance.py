"""Météo-France AROME (0.025 and 0.01 deg) and AROME-IFS (0.025 deg).

Open data on data.gouv.fr ("Paquets AROME"), served anonymously from

https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/<run>/<model>/<res>/<pkg>/
    <model>__<res>__<pkg>__<hours>__<run>.grib2
    (<run> like 2026-11-18T09:00:00Z; <hours> 00H06H, 07H12H ... 49H51H at
    0.025 deg, one file per hour 00H ... 51H at 0.01 deg)

AROME runs every 3 hours, AROME-IFS every 6 hours; both to T+51.
Grid: regular lat/lon, 12W-16E, 37.5-55.4N.

There is no byte index, so whole package files are fetched, only those a
switched-on layer needs. Values are the file's own; rain over a period is
the step in the run accumulation, wind speed and direction come from
10 m u/v, the gust is the maximum over the past hour. CAPE is
``CAPE_INS``, Météo-France's instantaneous CAPE (the parcel used is not
stated in the files).

The packages differ by resolution (checked 18 Nov 2026):
- 0.025 deg SP1: MSLP, 2 m T, 10 m u/v, gust ``10fg``, precipitation
  ``tp``; SP2: CAPE.
- 0.01 deg SP1: 2 m T, 10 m u/v, gust as components ``10efg``/``10nfg``;
  SP2: CAPE, and rain, snow and graupel accumulations (``tirf``,
  ``tsnowp``, ``tgrp``), summed for total precipitation. **No MSLP** at
  0.01 deg (only surface pressure), so no isobars or centres.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

BASE = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"
LAST_STEP = 51

# Our parameter -> (package, GRIB shortName(s), kind); several names
# are summed (precipitation) or combined as components (gust).
SOURCES_0025 = {
    "pressure_at_mean_sea_level": ("SP1", ("prmsl",), "instant"),
    "temperature_at_screen_level": ("SP1", ("2t",), "instant"),
    "CAPE_most_unstable_below_500hPa": ("SP2", ("CAPE_INS",), "instant"),
    "wind_gust_at_10m": ("SP1", ("10fg",), "hourmax"),
    "u": ("SP1", ("10u",), "instant"),
    "v": ("SP1", ("10v",), "instant"),
    "tp": ("SP1", ("tp",), "fromstart"),
}
SOURCES_001 = {
    "temperature_at_screen_level": ("SP1", ("2t",), "instant"),
    "CAPE_most_unstable_below_500hPa": ("SP2", ("CAPE_INS",), "instant"),
    "wind_gust_at_10m": ("SP1", ("10efg", "10nfg"), "hourmax"),
    "u": ("SP1", ("10u",), "instant"),
    "v": ("SP1", ("10v",), "instant"),
    "tp": ("SP2", ("tirf", "tsnowp", "tgrp"), "fromstart"),
}
SOURCES = SOURCES_0025
WIND_PARAMS = {"wind_speed_at_10m", "wind_direction_at_10m"}

# Raw packages kept in memory (few: they are tens of MB each).
_PACKAGES = OrderedDict()
_LOCKS = {}
_LOCK = threading.Lock()


def _run_iso(run):
    return f"{datetime.strptime(run, '%Y%m%dT%H%MZ'):%Y-%m-%dT%H:00:00Z}"


class Product:
    """One Météo-France model (the interface globe_data expects)."""

    def __init__(self, model, res, every):
        self.model, self.res, self.every = model, res, every
        self.sources = SOURCES_001 if res == "001" else SOURCES_0025
        self.FIELDS = ({p for p in self.sources if p not in ("u", "v", "tp")}
                       | WIND_PARAMS | {"precipitation_accumulation"})

    def hours(self, step):
        """The file's hours part for a forecast step."""
        if self.res == "001":
            return f"{step:02d}H"
        if step <= 6:
            return "00H06H"
        start = ((step - 1) // 6) * 6 + 1
        return f"{start:02d}H{min(start + 5, LAST_STEP):02d}H"

    def url(self, run, package, step):
        iso = _run_iso(run)
        return (f"{BASE}/{iso}/{self.model}/{self.res}/{package}/"
                f"{self.model}__{self.res}__{package}__{self.hours(step)}"
                f"__{iso}.grib2")

    @staticmethod
    def leads(run_hour, accumulated):
        if accumulated:
            return [(h, 1) for h in range(1, LAST_STEP + 1)]
        return [(h, None) for h in range(0, LAST_STEP + 1)]

    def latest_run(self, now=None, back_hours=48):
        """Newest run whose last package (SP1) is published."""
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        t = now.replace(minute=0, second=0, microsecond=0)
        for _ in range(back_hours):
            if t.hour % self.every == 0:
                run = f"{t:%Y%m%dT%H%MZ}"
                req = urllib.request.Request(
                    self.url(run, "SP1", LAST_STEP), method="HEAD")
                try:
                    with urllib.request.urlopen(req, timeout=20):
                        return run
                except (urllib.error.URLError, OSError):
                    pass
            t -= timedelta(hours=1)
        return None

    def _package(self, url):
        """Raw package bytes and its messages {(shortName, stepRange):
        (offset, length)}; downloaded once, a few kept in memory."""
        with _LOCK:
            if url in _PACKAGES:
                _PACKAGES.move_to_end(url)
                return _PACKAGES[url]
            lock = _LOCKS.setdefault(url, threading.Lock())
        with lock:                      # one download per package
            with _LOCK:
                if url in _PACKAGES:
                    return _PACKAGES[url]
            try:
                with urllib.request.urlopen(url, timeout=600) as r:
                    raw = r.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                raise
            pkg = (raw, _scan(raw))
            with _LOCK:
                _PACKAGES[url] = pkg
                while len(_PACKAGES) > 3:
                    _PACKAGES.popitem(last=False)
            return pkg

    def _get(self, run, source, step, cache):
        """One source's field(s) at ``step``: a list of decoded fields,
        one per GRIB name, or None if any is missing."""
        package, shorts, kind = source
        rng = {"instant": str(step), "hourmax": f"{step - 1}-{step}",
               "fromstart": f"0-{step}"}[kind]
        out = []
        for short in shorts:
            key = (short, rng)
            if key not in cache:
                pkg = self._package(self.url(run, package, step))
                if pkg is None or key not in pkg[1]:
                    return None
                import globe_gfs
                off, n = pkg[1][key]
                cache[key] = globe_gfs._decode(pkg[0][off:off + n])
            out.append(cache[key])
        return out

    def make_field(self, run, parameter, valid, period, cache):
        """Values of one of our parameters at ``valid`` in Met Office
        units (Pa, K, J/kg, m/s, degrees, metres of rain) with lon/lat."""
        import numpy as np
        step = int((valid - datetime.strptime(run, "%Y%m%dT%H%MZ"))
                   / timedelta(hours=1))
        src = self.sources

        def total(fields):              # rain + snow + graupel, or one
            return sum(f[0] for f in fields), fields[0][1], fields[0][2]
        if parameter == "precipitation_accumulation":
            now = self._get(run, src["tp"], step, cache)
            if now is None:
                return None
            amount, lon, lat = total(now)
            if step - period > 0:
                before = self._get(run, src["tp"], step - period, cache)
                if before is None:
                    return None
                amount = amount - total(before)[0]
            return amount / 1000.0, lon, lat                 # mm -> m
        if parameter in WIND_PARAMS:
            u = self._get(run, src["u"], step, cache)
            v = self._get(run, src["v"], step, cache)
            if u is None or v is None:
                return None
            u, v = u[0], v[0]
            if parameter == "wind_speed_at_10m":
                return np.hypot(u[0], v[0]), u[1], u[2]
            return (np.degrees(np.arctan2(-u[0], -v[0])) % 360.0,
                    u[1], u[2])
        if parameter not in src or (parameter == "wind_gust_at_10m"
                                    and step == 0):
            return None
        got = self._get(run, src[parameter], step, cache)
        if got is None:
            return None
        if len(got) == 2:               # gust components -> speed
            return np.hypot(got[0][0], got[1][0]), got[0][1], got[0][2]
        return got[0]


def _scan(raw):
    """Where each GRIB message is: {(shortName, stepRange): (off, len)}."""
    import eccodes as ec
    out, i = {}, 0
    while True:
        i = raw.find(b"GRIB", i)
        if i < 0:
            return out
        n = int.from_bytes(raw[i + 8:i + 16], "big")       # GRIB2 length
        gid = ec.codes_new_from_message(raw[i:i + n])
        try:
            out[(ec.codes_get(gid, "shortName"),
                 str(ec.codes_get(gid, "stepRange")))] = (i, n)
        finally:
            ec.codes_release(gid)
        i += n


AROME_0025 = Product("arome", "0025", 3)
AROME_001 = Product("arome", "001", 3)
AROMEIFS_0025 = Product("aromeifs", "0025", 6)
