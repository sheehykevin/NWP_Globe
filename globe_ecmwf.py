"""ECMWF IFS 0.1 deg open data (HRES, 3-hourly steps), field by field.

https://data.ecmwf.int/forecasts/<YYYYMMDD>/<HH>z/ifs/0p1/oper/
    <YYYYMMDDHH>0000-<step>h-oper-fc-sfc.grib2   (+ .index: one JSON line
    per field with its byte offset and length)

All four runs are in the "oper" stream at 0.1 deg: 00 and 12 UTC to
T+120 (used to T+120 here), 06 and 18 UTC to T+90, every 3 hours. Only
the fields a layer needs are fetched, by HTTP range requests (as the
ecmwf-opendata package does). The grid is global 0.1 deg lat/lon; the
North Atlantic and Europe block (as the Global model's) is kept.

Values are the file's own; as for the other models, rain over a period
is the step in the run accumulation (so 3-hour amounts at best: no
1-hour rain), and wind speed/direction come from the 10 m u/v
components. Gust: ``10fg`` (maximum over the hour before the step) to
T+90, then ``10fg3`` (maximum over the 3 hours before the step).
CAPE is ECMWF's most-unstable CAPE (``mucape``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

BASE = "https://data.ecmwf.int/forecasts"
LAST_STEP = {0: 120, 6: 90, 12: 120, 18: 90}
AREA = (-80.0, 50.0, 15.0, 80.0)          # west, east, south, north

FIELDS = {
    "pressure_at_mean_sea_level": ["msl"],
    "temperature_at_screen_level": ["2t"],
    "CAPE_most_unstable_below_500hPa": ["mucape"],
    "wind_gust_at_10m": ["10fg", "10fg3"],
    "wind_speed_at_10m": ["10u", "10v"],
    "wind_direction_at_10m": ["10u", "10v"],
    "precipitation_accumulation": ["tp"],
}


def _url(run, step, ext):
    t = datetime.strptime(run, "%Y%m%dT%H%MZ")
    return (f"{BASE}/{t:%Y%m%d}/{t:%H}z/ifs/0p1/oper/"
            f"{t:%Y%m%d%H}0000-{step}h-oper-fc-sfc.{ext}")


def leads(run_hour, accumulated):
    """(lead, period) pairs a run publishes: every 3 hours."""
    top = LAST_STEP[run_hour]
    if accumulated:
        return [(h, 3) for h in range(3, top + 1, 3)]
    return [(h, None) for h in range(0, top + 1, 3)]


def latest_run(now=None, back_hours=48):
    """Newest run whose last step's index is published."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(back_hours):
        if t.hour in LAST_STEP:
            run = f"{t:%Y%m%dT%H%MZ}"
            req = urllib.request.Request(
                _url(run, LAST_STEP[t.hour], "index"), method="HEAD")
            try:
                with urllib.request.urlopen(req, timeout=20):
                    return run
            except (urllib.error.URLError, OSError):
                pass
        t -= timedelta(hours=1)
    return None


def _index(run, step, cache):
    key = ("index", step)
    if key not in cache:
        with urllib.request.urlopen(_url(run, step, "index"),
                                    timeout=60) as r:
            cache[key] = {(d["param"], d.get("levtype")): d
                          for d in map(json.loads, r)}
    return cache[key]


def _grib(run, step, params, cache):
    """The first of ``params`` present at ``step``, cut to AREA (south
    to north), with its lon/lat; None if none is published."""
    for param in params:
        key = (param, step)
        if key in cache:
            return cache[key]
        try:
            idx = _index(run, step, cache)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        d = idx.get((param, "sfc"))
        if d is None:
            continue
        req = urllib.request.Request(_url(run, step, "grib2"), headers={
            "Range": f"bytes={d['_offset']}-{d['_offset'] + d['_length'] - 1}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
        cache[key] = _decode(raw)
        return cache[key]
    return None


def _decode(raw):
    """One GRIB message decoded (one thread at a time: eccodes)."""
    from globe_paths import GRIB_LOCK
    with GRIB_LOCK:
        return _decode_unlocked(raw)


def _decode_unlocked(raw):
    import eccodes as ec
    gid = ec.codes_new_from_message(raw)
    try:
        g = {k: ec.codes_get(gid, k) for k in (
            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
            "longitudeOfFirstGridPointInDegrees",
            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
            "jScansPositively")}
        vals = ec.codes_get_values(gid).reshape(g["Nj"], g["Ni"])
        miss = ec.codes_get(gid, "missingValue")
    finally:
        ec.codes_release(gid)
    vals = np.where(vals == miss, np.nan, vals).astype(np.float32)
    lon = g["longitudeOfFirstGridPointInDegrees"] + \
        g["iDirectionIncrementInDegrees"] * np.arange(g["Ni"])
    lon = (lon + 180.0) % 360.0 - 180.0
    step = g["jDirectionIncrementInDegrees"]
    lat = g["latitudeOfFirstGridPointInDegrees"] + \
        (step if g["jScansPositively"] else -step) * np.arange(g["Nj"])
    order = np.argsort(lon)                   # west to east from -180
    lon, vals = lon[order], vals[:, order]
    if lat[0] > lat[-1]:
        lat, vals = lat[::-1], vals[::-1]
    west, east, south, north = AREA
    cols = np.flatnonzero((lon >= west) & (lon <= east))
    rows = np.flatnonzero((lat >= south) & (lat <= north))
    return (vals[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1],
            lon[cols[0]:cols[-1] + 1], lat[rows[0]:rows[-1] + 1])


def make_field(run, parameter, valid, period, cache):
    """Values of one of our parameters at ``valid`` in Met Office units
    (Pa, K, J/kg, m/s, degrees, metres of rain) with lon/lat, or None."""
    step = int((valid - datetime.strptime(run, "%Y%m%dT%H%MZ"))
               / timedelta(hours=1))
    if parameter == "precipitation_accumulation":
        now = _grib(run, step, ["tp"], cache)
        before = (_grib(run, step - period, ["tp"], cache)
                  if step - period > 0 else None)
        if now is None or (step - period > 0 and before is None):
            return None
        amount = now[0] if before is None else now[0] - before[0]
        return amount, now[1], now[2]          # already metres
    if parameter in ("wind_speed_at_10m", "wind_direction_at_10m"):
        u = _grib(run, step, ["10u"], cache)
        v = _grib(run, step, ["10v"], cache)
        if u is None or v is None:
            return None
        if parameter == "wind_speed_at_10m":
            return np.hypot(u[0], v[0]), u[1], u[2]
        return (np.degrees(np.arctan2(-u[0], -v[0])) % 360.0, u[1], u[2])
    return _grib(run, step, FIELDS[parameter], cache)
