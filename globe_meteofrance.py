"""Météo-France AROME (0.025 and 0.01 deg) and AROME-IFS (0.025 deg).

Open data on data.gouv.fr ("Paquets AROME"), served anonymously from

https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/<run>/<model>/<res>/<pkg>/
    <model>__<res>__<pkg>__<hours>__<run>.grib2
    (<run> like 2026-11-18T09:00:00Z; <hours> 00H06H, 07H12H ... 49H51H at
    0.025 deg, one file per hour 00H ... 51H at 0.01 deg)

AROME runs every 3 hours, AROME-IFS every 6 hours; both to T+51.
Grid: regular lat/lon, 12W-16E, 37.5-55.4N.

There is no byte index, so the globe makes one: it reads the first
kilobyte of each GRIB2 message in a package (HTTP range requests) for
its code, level and time range, then fetches only the messages wanted
(not whole packages). Values are the file's own; rain over a period is
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

# Package indexes (message positions), made once per file.
_INDEXES = {}
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

    def _index(self, url):
        """Messages of one package file: {(code, level type, start, end):
        (offset, length)}, from range requests on their headers; None if
        the file is not published. Kept for the session."""
        with _LOCK:
            if url in _INDEXES:
                return _INDEXES[url]
            lock = _LOCKS.setdefault(url, threading.Lock())
        with lock:                      # one index per package
            with _LOCK:
                if url in _INDEXES:
                    return _INDEXES[url]
            import requests
            from globe_fetch import _range
            s = requests.Session()
            first = s.get(url, headers={"Range": "bytes=0-0"}, timeout=60)
            if first.status_code == 404:
                return None
            first.raise_for_status()
            total = int(first.headers["Content-Range"].rsplit("/", 1)[1])
            out, pos = {}, 0
            while pos + 16 <= total:
                head = _range(s, url, pos, min(pos + 1023, total - 1))
                if head[:4] != b"GRIB":
                    break
                length = int.from_bytes(head[8:16], "big")
                key = _grib2_key(head)
                if key:
                    out[key] = (pos, length)
                pos += length
            with _LOCK:
                _INDEXES[url] = out
            return out

    def _get(self, run, source, step, cache):
        """One source's field(s) at ``step``: a list of decoded fields,
        one per GRIB name, or None if any is missing. Only those messages
        are downloaded."""
        package, shorts, kind = source
        when = {"instant": (step, step), "hourmax": (step - 1, step),
                "fromstart": (0, step)}[kind]
        out = []
        url = self.url(run, package, step)
        for short in shorts:
            disc, cat, num, level = CODES[short]
            ckey = (short, when)
            if ckey not in cache:
                index = self._index(url)
                if index is None:
                    return None
                hit = [v for k, v in index.items()
                       if k[:3] == (disc, cat, num)
                       and (level is None or k[3] == level)
                       and k[4:] == when]
                if not hit:
                    return None
                import requests
                import globe_gfs
                from globe_fetch import DECODE_LOCK, _range
                off, n = hit[0]
                raw = _range(requests.Session(), url, off, off + n - 1)
                with DECODE_LOCK:
                    cache[ckey] = globe_gfs._decode(raw)
            out.append(cache[ckey])
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


# GRIB2 code (discipline, category, number) and first-surface type of
# each field used (from the packages' inventory, docs/models/arome_*.md;
# None: any level type).
CODES = {
    "prmsl": (0, 3, 1, None), "2t": (0, 0, 0, 103), "10u": (0, 2, 2, 103),
    "10v": (0, 2, 3, 103), "10fg": (0, 2, 22, 103), "10efg": (0, 2, 23, 103),
    "10nfg": (0, 2, 24, 103), "tp": (0, 1, 52, None),
    "CAPE_INS": (0, 7, 6, None), "tirf": (0, 1, 65, None),
    "tsnowp": (0, 1, 66, None), "tgrp": (0, 1, 75, None),
}


def _grib2_key(head):
    """(discipline, category, number, first surface type, start hour,
    end hour) of a GRIB2 message from its first bytes (section 4,
    templates 4.0 and 4.8; hours as the forecast time unit)."""
    disc = head[6]
    p = 16
    while p + 5 <= len(head):
        size = int.from_bytes(head[p:p + 4], "big")
        num = head[p + 4]
        if num == 4:
            s = head[p:p + size]
            if len(s) < 28:
                return None
            template = int.from_bytes(s[7:9], "big")
            cat, number = s[9], s[10]
            ft = int.from_bytes(s[18:22], "big")
            surface = s[22]
            start = end = ft
            if template == 8 and len(s) >= 53:
                end = ft + int.from_bytes(s[49:53], "big")
            return disc, cat, number, surface, start, end
        if size <= 0:
            return None
        p += size
    return None


AROME_0025 = Product("arome", "0025", 3)
AROME_001 = Product("arome", "001", 3)
AROMEIFS_0025 = Product("aromeifs", "0025", 6)
