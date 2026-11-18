"""DWD ICON-EU from the DWD open data server, file by file.

https://opendata.dwd.de/weather/nwp/icon-eu/grib/<HH>/<param>/
    icon-eu_europe_regular-lat-lon_single-level_<YYYYMMDDHH>_<LLL>_<PARAM>.grib2.bz2

Each run hour has its own folder holding only its newest run. Only
every 3 hours; 00, 06, 12 and 18 UTC go to T+120 (hourly to T+78, then
3-hourly), the others to T+30. Grid: regular lat/lon 0.0625 deg (about
7 km), 23.5W-62.5E, 29.5-70.5N.

Files are fetched when a layer needs them and written as NetCDF in the
same layout as the Met Office files. Values are the file's own; the only
calculations are those the Met Office files already carry: rain over a
period as the step in ICON's run accumulation, and wind speed/direction
from the 10 m u/v components (true-north components on this grid).
CAPE is ICON's mixed-layer CAPE (CAPE_ML): ICON-EU publishes no
most-unstable CAPE.
"""

from __future__ import annotations

import bz2
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
NAME = "icon-eu_europe_regular-lat-lon_single-level_{run}_{lead:03d}_{p}.grib2.bz2"
LONG_RUNS = (0, 6, 12, 18)

# Our parameter -> ICON parameter(s).
FIELDS = {
    "pressure_at_mean_sea_level": ["pmsl"],
    "temperature_at_screen_level": ["t_2m"],
    "CAPE_most_unstable_below_500hPa": ["cape_ml"],
    "wind_gust_at_10m": ["vmax_10m"],
    "wind_speed_at_10m": ["u_10m", "v_10m"],
    "wind_direction_at_10m": ["u_10m", "v_10m"],
    "precipitation_accumulation": ["tot_prec"],
}


def last_lead(run_hour):
    return 120 if run_hour in LONG_RUNS else 30


def leads(run_hour, accumulated):
    """(lead, period) pairs a run publishes."""
    top = last_lead(run_hour)
    out = [(h, 1 if accumulated else None)
           for h in range(1 if accumulated else 0, min(top, 78) + 1)]
    out += [(h, 3 if accumulated else None) for h in range(81, top + 1, 3)]
    return out


def latest_run(now=None):
    """Newest run whose last MSLP file is on the server, from the folder
    listings of each run hour (each holds that hour's newest run),
    fetched at the same time."""
    from concurrent.futures import ThreadPoolExecutor
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    def newest(hour):
        try:
            with urllib.request.urlopen(f"{BASE}/{hour:02d}/pmsl/",
                                        timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            return None
        found = re.findall(r"single-level_(\d{10})_(\d{3})_PMSL", html)
        if not found:
            return None
        run = max(r for r, _ in found)
        if not any(r == run and int(lead) == last_lead(hour)
                   for r, lead in found):
            return None                      # still being published
        t = datetime.strptime(run, "%Y%m%d%H")
        return t if t <= now else None

    with ThreadPoolExecutor(8) as pool:
        runs = [t for t in pool.map(newest, LONG_RUNS) if t]
    return f"{max(runs):%Y%m%dT%H%MZ}" if runs else None


def _grib(run, lead, param, cache):
    """One ICON field (values south to north) and its grid, or None."""
    key = (param, lead)
    if key in cache:
        return cache[key]
    import eccodes as ec
    t = datetime.strptime(run, "%Y%m%dT%H%MZ")
    url = (f"{BASE}/{t:%H}/{param}/" + NAME.format(
        run=f"{t:%Y%m%d%H}", lead=lead, p=param.upper()))
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = bz2.decompress(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            cache[key] = None
            return None
        raise
    from globe_paths import GRIB_LOCK
    GRIB_LOCK.acquire()                 # eccodes: one thread at a time
    try:
        gid = ec.codes_new_from_message(raw)
    except Exception:
        GRIB_LOCK.release()
        raise
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
        GRIB_LOCK.release()
    vals = np.where(vals == miss, np.nan, vals).astype(np.float32)
    lon = g["longitudeOfFirstGridPointInDegrees"] + \
        g["iDirectionIncrementInDegrees"] * np.arange(g["Ni"])
    lon = np.where(lon > 180, lon - 360, lon)
    step = g["jDirectionIncrementInDegrees"]
    lat = g["latitudeOfFirstGridPointInDegrees"] + \
        (step if g["jScansPositively"] else -step) * np.arange(g["Nj"])
    if lat[0] > lat[-1]:
        lat, vals = lat[::-1], vals[::-1]
    out = (vals, lon, lat)
    cache[key] = out
    return out


def make_field(run, parameter, valid, period, cache):
    """Values of one of our parameters at ``valid`` (as the Met Office
    files hold them: Pa, K, J/kg, m/s, degrees, metres of rain), with
    the grid's lon/lat, or None if ICON has no such file."""
    lead = int((valid - datetime.strptime(run, "%Y%m%dT%H%MZ"))
               / timedelta(hours=1))
    if parameter == "precipitation_accumulation":
        now = _grib(run, lead, "tot_prec", cache)
        before = _grib(run, lead - period, "tot_prec", cache)
        if now is None or before is None:
            return None
        return (now[0] - before[0]) / 1000.0, now[1], now[2]   # mm -> m
    if parameter in ("wind_speed_at_10m", "wind_direction_at_10m"):
        u = _grib(run, lead, "u_10m", cache)
        v = _grib(run, lead, "v_10m", cache)
        if u is None or v is None:
            return None
        if parameter == "wind_speed_at_10m":
            return np.hypot(u[0], v[0]), u[1], u[2]
        return (np.degrees(np.arctan2(-u[0], -v[0])) % 360.0,
                u[1], u[2])
    f = _grib(run, lead, FIELDS[parameter][0], cache)
    return f


def write_netcdf(path, parameter, values, lon, lat):
    """One field in the Met Office-like layout the globe reads."""
    import xarray as xr

    def bounds(c):
        mid = 0.5 * (c[1:] + c[:-1])
        e = np.concatenate([[2 * c[0] - mid[0]], mid, [2 * c[-1] - mid[-1]]])
        return np.column_stack([e[:-1], e[1:]])
    ds = xr.Dataset({parameter: (("y", "x"), values.astype(np.float32))},
                    coords={"latitude": ("y", lat), "longitude": ("x", lon),
                            "latitude_bnds": (("y", "nv"), bounds(lat)),
                            "longitude_bnds": (("x", "nv"), bounds(lon))})
    ds[parameter].encoding.update(zlib=True, complevel=4)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    ds.to_netcdf(tmp)
    os.replace(tmp, path)
