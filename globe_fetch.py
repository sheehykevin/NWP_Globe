"""Fetch HARMONIE (KNMI Open Data) and IBI waves (Copernicus Marine).

Both are stored in this project's ``data`` folder in the same layout as
the Met Office files, one NetCDF per field per hour, so the globe reads
every model the same way:

    data/<model>/<run>/<parameter>/<valid>-PT<lead>H00M-<parameter>.nc

Values are copied from the source files on their native grids. The only
calculations are unit-free rearrangements the Met Office files already
have: hourly rain as the difference of HARMONIE's run accumulations,
and wind speed/direction from HARMONIE's u/v components (turned from
grid to true north).

    conda run -n ukmo_globe python globe_fetch.py harmonie [--run YYYYMMDDHH]
    conda run -n ukmo_globe python globe_fetch.py ibi [--hours 72]

KNMI needs an API key in the KNMI_API_KEY environment variable or in a
``.env`` file next to this script (``KNMI_API_KEY=...``). Copernicus
Marine needs ``copernicusmarine login`` run once.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tarfile
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr

import globe_data as data

KNMI_API = "https://api.dataplatform.knmi.nl/open-data/v1"
KNMI_DATASET = ("harmonie_arome_cy43_p4a", "1.0")
CONTROL = re.compile(r"_meteo_000_(\d{12})_(\d{3})00_", re.I)
IBI_DATASET = "cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i"
IBI_PARAMS = {"VHM0": "sea_surface_wave_significant_height",
              "VMDR": "sea_surface_wave_from_direction"}

# HARMONIE GRIB1 fields kept (KNMI local table 253), by
# (indicatorOfParameter, indicatorOfTypeOfLevel, level). Only fields
# found in the downloaded run are written; the inventory lists all.
HARMONIE_FIELDS = {
    (1, 103, 0): "mslp",
    (11, 105, 2): "t2m",
    (33, 105, 10): "u10",
    (34, 105, 10): "v10",
    (162, 105, 10): "ugust",
    (163, 105, 10): "vgust",
    (61, 105, 0): "tp_acc",
    (160, 105, 0): "cape",
}


def _say(*args):
    print(*args, flush=True)


# ---- writing files in the shared layout ---------------------------------

def _write(model, run, param, valid, lead, values, coords, attrs,
           period=None):
    folder = os.path.join(data.DATA, data.MODELS[model]["dir"],
                          f"{run:%Y%m%dT%H%MZ}", param)
    os.makedirs(folder, exist_ok=True)
    name = f"{valid:%Y%m%dT%H%MZ}-PT{lead:04d}H00M-{param}"
    if period:
        name += f"-PT{period:02d}H"
    path = os.path.join(folder, name + ".nc")
    ds = xr.Dataset({param: (("y", "x"), values.astype(np.float32),
                             attrs)}, coords=coords)
    ds[param].encoding.update(zlib=True, complevel=4)
    tmp = path + ".part"
    ds.to_netcdf(tmp)
    os.replace(tmp, path)


# ---- IBI waves ----------------------------------------------------------

def fetch_ibi(hours=72, start=None):
    """Today's 00Z onwards, ``hours`` long, whole IBI domain."""
    import copernicusmarine
    start = start or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = start + timedelta(hours=hours)
    tmp = os.path.join(data.DATA, "ibi", "_download")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    _say(f"IBI waves {start:%Y-%m-%d %H}Z to {end:%Y-%m-%d %H}Z ...")
    copernicusmarine.subset(
        dataset_id=IBI_DATASET, variables=list(IBI_PARAMS),
        minimum_longitude=-19.5, maximum_longitude=5.5,
        minimum_latitude=25.5, maximum_latitude=56.5,
        start_datetime=f"{start:%Y-%m-%dT%H:%M:%S}",
        end_datetime=f"{end:%Y-%m-%dT%H:%M:%S}",
        output_directory=tmp, output_filename="ibi.nc")
    with xr.open_dataset(os.path.join(tmp, "ibi.nc")) as ds:
        lat = ds["latitude"].values.astype(float)
        lon = ds["longitude"].values.astype(float)
        coords = {"latitude": ("y", lat), "longitude": ("x", lon),
                  "latitude_bnds": (("y", "nv"), _edges_bounds(lat)),
                  "longitude_bnds": (("x", "nv"), _edges_bounds(lon))}
        for k, t in enumerate(ds["time"].values):
            valid = datetime.fromtimestamp(
                int(t.astype("datetime64[s]").astype(int)),
                timezone.utc).replace(tzinfo=None)
            lead = int((valid - start) / timedelta(hours=1))
            for var, param in IBI_PARAMS.items():
                _write("ibi", start, param, valid, lead,
                       ds[var].isel(time=k).values,
                       coords, {"units": ds[var].attrs.get("units", "")})
    shutil.rmtree(tmp, ignore_errors=True)
    _prune("ibi")
    _say("IBI done:", f"{start:%Y%m%dT%H%MZ}")


def _edges_bounds(centres):
    """Cell bounds half way between centres (regular grid)."""
    mid = 0.5 * (centres[1:] + centres[:-1])
    edges = np.concatenate([[2 * centres[0] - mid[0]], mid,
                            [2 * centres[-1] - mid[-1]]])
    return np.column_stack([edges[:-1], edges[1:]])


# ---- HARMONIE from KNMI ---------------------------------------------------

def knmi_key():
    key = os.environ.get("KNMI_API_KEY")
    if key:
        return key.strip()
    env = os.path.join(data.HERE, ".env")
    try:
        with open(env, encoding="utf-8") as f:
            for line in f:
                name, _, value = line.strip().partition("=")
                if name.strip() == "KNMI_API_KEY" and value.strip():
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    raise SystemExit("No KNMI API key: set KNMI_API_KEY or put "
                     "KNMI_API_KEY=... in NWP_Globe/.env")


def _knmi(session, path, **params):
    r = session.get(f"{KNMI_API}/datasets/{KNMI_DATASET[0]}/versions/"
                    f"{KNMI_DATASET[1]}{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _grib_messages(buf):
    """Split a byte string into GRIB messages (edition 1 or 2)."""
    i = 0
    while True:
        i = buf.find(b"GRIB", i)
        if i < 0:
            return
        edition = buf[i + 7]
        if edition == 1:
            n = int.from_bytes(buf[i + 4:i + 7], "big")
        else:
            n = int.from_bytes(buf[i + 8:i + 16], "big")
        yield buf[i:i + n]
        i += max(n, 4)


def _grid_of(gid, ec):
    """Rotated-pole grid of one message, as coordinates and attributes."""
    g = {k: ec.codes_get(gid, k) for k in (
        "Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
        "longitudeOfFirstGridPointInDegrees", "iDirectionIncrementInDegrees",
        "jDirectionIncrementInDegrees", "latitudeOfSouthernPoleInDegrees",
        "longitudeOfSouthernPoleInDegrees", "jScansPositively")}
    di, dj = g["iDirectionIncrementInDegrees"], g["jDirectionIncrementInDegrees"]
    x = g["longitudeOfFirstGridPointInDegrees"] + di * np.arange(g["Ni"])
    y0 = g["latitudeOfFirstGridPointInDegrees"]
    y = y0 + (dj if g["jScansPositively"] else -dj) * np.arange(g["Nj"])
    x = np.where(x > 180, x - 360, x)
    attrs = {"grid_mapping_name": "rotated_latitude_longitude",
             "grid_north_pole_latitude": -g["latitudeOfSouthernPoleInDegrees"],
             "grid_north_pole_longitude":
                 (g["longitudeOfSouthernPoleInDegrees"] + 180.0 + 180) % 360
                 - 180}
    return x, y, attrs


def fetch_harmonie(run=None):
    import eccodes as ec
    import requests
    session = requests.Session()
    session.headers["Authorization"] = knmi_key()
    if run:
        name = f"harm43_v1_P4a_{run}.tar"
    else:
        listing = _knmi(session, "/files", maxKeys=5, orderBy="created",
                        sorting="desc")
        names = [f["filename"] for f in listing.get("files", [])
                 if f["filename"].endswith(".tar")]
        if not names:
            raise SystemExit("No HARMONIE runs listed by KNMI")
        name = names[0]
    run_time = datetime.strptime(re.search(r"(\d{10})", name).group(1),
                                 "%Y%m%d%H")
    url = _knmi(session, f"/files/{name}/url")["temporaryDownloadUrl"]
    _say(f"HARMONIE {name}: streaming (about 6 GB); keeping the control "
         "member only ...")
    inventory = {}
    acc_prev = {}
    grid = None
    seen = 0
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        with tarfile.open(fileobj=r.raw, mode="r|*") as tar:
            for member in tar:
                m = CONTROL.search(member.name)
                if not (member.isfile() and m):
                    continue
                lead = int(m.group(2))
                buf = tar.extractfile(member).read()
                seen += 1
                fields = {}
                for msg in _grib_messages(buf):
                    gid = ec.codes_new_from_message(msg)
                    try:
                        key = tuple(ec.codes_get(gid, k) for k in (
                            "indicatorOfParameter", "indicatorOfTypeOfLevel",
                            "level"))
                        tri = ec.codes_get(gid, "timeRangeIndicator")
                        inventory.setdefault(key, set()).add(tri)
                        if key in HARMONIE_FIELDS:
                            if grid is None:
                                grid = _grid_of(gid, ec)
                            ni = ec.codes_get(gid, "Ni")
                            nj = ec.codes_get(gid, "Nj")
                            vals = ec.codes_get_values(gid).reshape(nj, ni)
                            miss = ec.codes_get(gid, "missingValue")
                            vals = np.where(vals == miss, np.nan, vals)
                            fields[HARMONIE_FIELDS[key]] = vals
                    finally:
                        ec.codes_release(gid)
                _store_harmonie(run_time, lead, fields, grid, acc_prev)
                _say(f"  T+{lead}: {', '.join(sorted(fields)) or 'none'}")
    _write_inventory(name, inventory)
    if not seen:
        raise SystemExit("No control-member files found in the archive")
    _prune("harmonie")
    _say("HARMONIE done:", f"{run_time:%Y%m%dT%H%MZ}")


def _store_harmonie(run, lead, f, grid, acc_prev):
    if grid is None:
        return
    x, y, attrs = grid
    order = np.argsort(y)                    # store south to north
    coords = {"rlon": ("x", x), "rlat": ("y", y[order])}
    valid = run + timedelta(hours=lead)
    attrs = dict(attrs)

    def put(param, values, units, period=None):
        _write("harmonie", run, param, valid, lead, values[order], coords,
               {**attrs, "units": units}, period)

    if "mslp" in f:
        put("pressure_at_mean_sea_level", f["mslp"], "Pa")
    if "t2m" in f:
        put("temperature_at_screen_level", f["t2m"], "K")
    if "cape" in f:
        put("CAPE_most_unstable_below_500hPa", f["cape"], "J kg-1")
    if "u10" in f and "v10" in f:
        u, v = _to_true_north(f["u10"], f["v10"], x, y, attrs)
        put("wind_speed_at_10m", np.hypot(u, v), "m s-1")
        put("wind_direction_at_10m",
            np.degrees(np.arctan2(-u, -v)) % 360.0, "degrees")
    if "ugust" in f and "vgust" in f:
        put("wind_gust_at_10m", np.hypot(f["ugust"], f["vgust"]), "m s-1")
    if "tp_acc" in f:
        # Accumulated from the run start: the hour's rain is the step.
        if lead - 1 in acc_prev:
            put("precipitation_accumulation",
                (f["tp_acc"] - acc_prev[lead - 1]) / 1000.0, "m", period=1)
        acc_prev[lead] = f["tp_acc"]


def _to_true_north(u, v, x, y, attrs):
    """Grid-relative u/v on a rotated grid -> east/north components."""
    from pyproj import CRS, Transformer
    t = Transformer.from_crs(CRS.from_cf(attrs), "EPSG:4326",
                             always_xy=True).transform
    xx, yy = np.meshgrid(x, y)
    lon0, lat0 = t(xx, yy)
    lon1, lat1 = t(xx, yy + 0.01)            # a step to grid north
    dx = (lon1 - lon0) * np.cos(np.radians(lat0))
    dy = lat1 - lat0
    angle = np.arctan2(dx, dy)               # grid north from true north
    c, s = np.cos(angle), np.sin(angle)
    return u * c + v * s, -u * s + v * c


def _write_inventory(name, inventory):
    path = os.path.join(data.HERE, "docs", "harmonie_inventory.md")
    lines = [f"# HARMONIE P4a control member: GRIB1 fields in {name}", "",
             "| indicatorOfParameter | indicatorOfTypeOfLevel | level | "
             "timeRangeIndicator | used as |", "|---|---|---|---|---|"]
    for key in sorted(inventory):
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | "
                     f"{', '.join(map(str, sorted(inventory[key])))} | "
                     f"{HARMONIE_FIELDS.get(key, '')} |")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _say("Inventory written:", path)


def _prune(model, keep=2):
    root = os.path.join(data.DATA, data.MODELS[model]["dir"])
    runs = sorted(r for r in os.listdir(root)
                  if re.fullmatch(r"\d{8}T\d{4}Z", r))
    for r in runs[:-keep]:
        shutil.rmtree(os.path.join(root, r), ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="what", required=True)
    h = sub.add_parser("harmonie")
    h.add_argument("--run", help="YYYYMMDDHH (default: newest listed)")
    i = sub.add_parser("ibi")
    i.add_argument("--hours", type=int, default=72)
    i.add_argument("--start", help="YYYYMMDDHH (default: today 00Z)")
    args = ap.parse_args(argv)
    if args.what == "harmonie":
        fetch_harmonie(args.run)
    else:
        start = (datetime.strptime(args.start, "%Y%m%d%H")
                 if args.start else None)
        fetch_ibi(args.hours, start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
