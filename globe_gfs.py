"""NOAA GFS from the public AWS bucket ``noaa-gfs-bdp-pds``, field by field.

https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.<YYYYMMDD>/<HH>/atmos/
    gfs.t<HH>z.sfluxgrbf<FFF>.grib2     (+ .idx)  T1534 Gaussian, ~13 km
    gfs.t<HH>z.pgrb2.0p25.f<FFF>        (+ .idx)  regular 0.25 deg

Runs at 00, 06, 12 and 18 UTC, hourly to T+120 (used here; later steps
are 3-hourly). As in reference/NOAA_GFS_S3_surface_fields_guide.md, each
field comes from the finer ``sflux`` product where it exists there and
from the 0.25 deg product otherwise; only the needed GRIB messages are
fetched, by HTTP range requests from the ``.idx`` byte offsets:

    sflux: 2 m temperature (TMP), 10 m wind (UGRD/VGRD), precipitation
           (PRATE, see below)
    0.25:  MSLP (PRMSL), most-unstable CAPE (CAPE 255-0 mb above
           ground), gust (GUST: the
           instantaneous surface gust; GFS has no hourly maximum gust)

The globe lists the two products as two models, "GFS sflux" and
"GFS 0.25", each on its own grid (never regridded). The North Atlantic
and Europe block is kept.

Precipitation: ``sflux`` PRATE is a mean rate since the last 6-hourly
reset (0-1, 0-2, ... 0-6 h, then 6-7 h ...). Mean rate x duration is the
amount since the reset, so each hour's amount is exact: the step between
consecutive amounts (or the 1-hour mean x 3600 s right after a reset).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
LAST_STEP = 120
AREA = (-80.0, 50.0, 15.0, 80.0)          # west, east, south, north

# Our parameter -> (product, GRIB variable, level, statistic)
SOURCES = {
    "pressure_at_mean_sea_level": ("pgrb2", "PRMSL", "mean sea level", None),
    "temperature_at_screen_level": ("sflux", "TMP", "2 m above ground", None),
    # Most-unstable CAPE: best parcel in the lowest 255 hPa (as UKV's
    # and ECMWF's most-unstable CAPE; not surface-based CAPE).
    "CAPE_most_unstable_below_500hPa": ("pgrb2", "CAPE",
                                        "255-0 mb above ground", None),
    "wind_gust_at_10m": ("pgrb2", "GUST", "surface", None),
    "u": ("sflux", "UGRD", "10 m above ground", None),
    "v": ("sflux", "VGRD", "10 m above ground", None),
    "rate": ("sflux", "PRATE", "surface", "ave"),
}
# Which of our parameters each product supplies.
PRODUCT_FIELDS = {
    "sflux": {"temperature_at_screen_level", "wind_speed_at_10m",
              "wind_direction_at_10m", "precipitation_accumulation"},
    "pgrb2": {"pressure_at_mean_sea_level",
              "CAPE_most_unstable_below_500hPa", "wind_gust_at_10m"},
}


def _url(run, product, step):
    t = datetime.strptime(run, "%Y%m%dT%H%MZ")
    folder = f"{BASE}/gfs.{t:%Y%m%d}/{t:%H}/atmos/"
    if product == "sflux":
        return folder + f"gfs.t{t:%H}z.sfluxgrbf{step:03d}.grib2"
    return folder + f"gfs.t{t:%H}z.pgrb2.0p25.f{step:03d}"


def leads(run_hour, accumulated):
    """(lead, period) pairs used: hourly to T+120."""
    if accumulated:
        return [(h, 1) for h in range(1, LAST_STEP + 1)]
    return [(h, None) for h in range(0, LAST_STEP + 1)]


class Product:
    """One GFS product as a model source (the interface globe_data
    expects: FIELDS, leads, latest_run, make_field)."""

    def __init__(self, product):
        self.FIELDS = PRODUCT_FIELDS[product]

    leads = staticmethod(leads)

    @staticmethod
    def latest_run(now=None):
        return latest_run(now)

    @staticmethod
    def make_field(run, parameter, valid, period, cache):
        return make_field(run, parameter, valid, period, cache)


def latest_run(now=None, back_hours=48):
    """Newest run with both products' T+120 index published."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(back_hours):
        if t.hour % 6 == 0:
            run = f"{t:%Y%m%dT%H%MZ}"
            try:
                for product in ("sflux", "pgrb2"):
                    req = urllib.request.Request(
                        _url(run, product, LAST_STEP) + ".idx",
                        method="HEAD")
                    with urllib.request.urlopen(req, timeout=20):
                        pass
                return run
            except (urllib.error.URLError, OSError):
                pass
        t -= timedelta(hours=1)
    return None


def _index(run, product, step, cache):
    key = ("idx", product, step)
    if key not in cache:
        with urllib.request.urlopen(_url(run, product, step) + ".idx",
                                    timeout=60) as r:
            lines = r.read().decode("utf-8", "replace").splitlines()
        recs = []
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 6 and parts[1].isdigit():
                recs.append((int(parts[1]), parts[3], parts[4], parts[5]))
        cache[key] = recs
    return cache[key]


def _timing_ok(timing, step, stat, start=None):
    timing = timing.strip()
    if stat is None:
        return timing in (f"{step} hour fcst", "anl" if step == 0 else "")
    m = re.fullmatch(r"(\d+)-(\d+) hour (ave|acc) fcst", timing)
    return bool(m and m.group(3) == stat and int(m.group(2)) == step
                and (start is None or int(m.group(1)) == start))


def _message(run, source, step, cache, start=None):
    """One GRIB message's decoded field (cut to AREA, south to north)."""
    product, var, level, stat = source
    key = (product, var, level, stat, step, start)
    if key in cache:
        return cache[key]
    try:
        recs = _index(run, product, step, cache)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    hits = [k for k, r in enumerate(recs)
            if r[1] == var and r[2] == level
            and _timing_ok(r[3], step, stat, start)]
    if not hits:
        cache[key] = None
        return None
    k = hits[0]
    begin = recs[k][0]
    end = recs[k + 1][0] - 1 if k + 1 < len(recs) else ""
    req = urllib.request.Request(_url(run, product, step),
                                 headers={"Range": f"bytes={begin}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    cache[key] = _decode(raw)
    return cache[key]


def _decode(raw):
    import eccodes as ec
    gid = ec.codes_new_from_message(raw)
    try:
        ni, nj = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
        vals = ec.codes_get_values(gid).reshape(nj, ni)
        miss = ec.codes_get(gid, "missingValue")
        lon0 = ec.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        dlon = ec.codes_get(gid, "iDirectionIncrementInDegrees")
        if ec.codes_get(gid, "gridType") == "regular_gg":
            lat = np.asarray(ec.codes_get_array(gid, "distinctLatitudes"))
            if ec.codes_get(gid, "jScansPositively") == 0:
                lat = np.sort(lat)[::-1]          # north to south rows
        else:
            dlat = ec.codes_get(gid, "jDirectionIncrementInDegrees")
            lat0 = ec.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
            sign = 1 if ec.codes_get(gid, "jScansPositively") else -1
            lat = lat0 + sign * dlat * np.arange(nj)
    finally:
        ec.codes_release(gid)
    vals = np.where(vals == miss, np.nan, vals).astype(np.float32)
    lon = (lon0 + dlon * np.arange(ni) + 180.0) % 360.0 - 180.0
    order = np.argsort(lon)
    lon, vals = lon[order], vals[:, order]
    if lat[0] > lat[-1]:
        lat, vals = lat[::-1], vals[::-1]
    west, east, south, north = AREA
    cols = np.flatnonzero((lon >= west) & (lon <= east))
    rows = np.flatnonzero((lat >= south) & (lat <= north))
    return (vals[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1],
            lon[cols[0]:cols[-1] + 1], lat[rows[0]:rows[-1] + 1])


def _since_reset(run, step, cache):
    """Precipitation (kg m-2 = mm) since the last 6-hourly reset at
    ``step``, from the mean rate over that time; 0 at a reset."""
    start = ((step - 1) // 6) * 6
    f = _message(run, SOURCES["rate"], step, cache, start=start)
    if f is None:
        return None
    return f[0] * (step - start) * 3600.0, f[1], f[2]


def make_field(run, parameter, valid, period, cache):
    """Values of one of our parameters at ``valid`` in Met Office units
    (Pa, K, J/kg, m/s, degrees, metres of rain) with lon/lat, or None."""
    step = int((valid - datetime.strptime(run, "%Y%m%dT%H%MZ"))
               / timedelta(hours=1))
    if parameter == "precipitation_accumulation":
        if period != 1 or step < 1:
            return None
        now = _since_reset(run, step, cache)
        if now is None:
            return None
        if (step - 1) % 6 == 0:                  # first hour after reset
            amount = now[0]
        else:
            before = _since_reset(run, step - 1, cache)
            if before is None:
                return None
            amount = now[0] - before[0]
        return np.maximum(amount, 0.0) / 1000.0, now[1], now[2]   # m
    if parameter in ("wind_speed_at_10m", "wind_direction_at_10m"):
        u = _message(run, SOURCES["u"], step, cache)
        v = _message(run, SOURCES["v"], step, cache)
        if u is None or v is None:
            return None
        if parameter == "wind_speed_at_10m":
            return np.hypot(u[0], v[0]), u[1], u[2]
        return (np.degrees(np.arctan2(-u[0], -v[0])) % 360.0, u[1], u[2])
    return _message(run, SOURCES[parameter], step, cache)


SFLUX = Product("sflux")
P025 = Product("pgrb2")
