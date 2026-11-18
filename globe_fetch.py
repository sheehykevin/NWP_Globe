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
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr

import globe_data as data

KNMI_API = "https://api.dataplatform.knmi.nl/open-data/v1"
# P3: deterministic, 0.05 deg rotated grid (DINI domain), 3.4 GB a run
# (the P4a ensemble on the same grid is 5.85 GB).
ANONYMOUS_KNMI_KEY = (
    "eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6IjUzYTg1ZDBhMmQ5Yz"
    "RkYzJiYWNlNzQ4NTQ2Zjk4ODExIiwiaCI6Im11cm11cjEyOCJ9")
ANONYMOUS_KNMI_UNTIL = datetime(2027, 8, 1)
KNMI_DATASET = ("harmonie_arome_cy43_p3", "1.0")
INNER = re.compile(r"_(\d{12})_(\d{3})00_GB", re.I)   # run, lead
# Copernicus Marine wave models: dataset and box (west, east, south,
# north) fetched. Both give significant wave height and mean direction.
WAVE_MODELS = {
    "ibi": dict(label="IBI waves", box=(-19.5, 5.5, 25.5, 56.5),
                dataset="cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i"),
    "nws": dict(label="NWS waves", box=(-16.5, 13.5, 45.5, 63.0),
                dataset="cmems_mod_nws_wav_anfc_1.5km_PT1H-i"),
}
IBI_PARAMS = {"VHM0": "sea_surface_wave_significant_height",
              "VMDR": "sea_surface_wave_from_direction"}

# HARMONIE GRIB1 fields kept (KNMI local table 253), by
# (indicatorOfParameter, indicatorOfTypeOfLevel, level,
# timeRangeIndicator), as found in a P3 file on 18 Nov 2026
# (docs/harmonie_inventory.md lists all). Total precipitation (61) is
# accumulated from the run start (time range 4); gusts are the maximum
# over the past hour (time range 2). P3 has no CAPE.
HARMONIE_FIELDS = {
    (1, 103, 0, 0): "mslp",
    (11, 105, 2, 0): "t2m",
    (33, 105, 10, 0): "u10",
    (34, 105, 10, 0): "v10",
    (162, 105, 10, 2): "ugust",
    (163, 105, 10, 2): "vgust",
    (61, 105, 0, 4): "tp_acc",
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

def fetch_waves(model="ibi", hours=72, start=None):
    """Today's 00Z onwards, ``hours`` long, the model's whole domain."""
    import copernicusmarine
    wm = WAVE_MODELS[model]
    start = start or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = start + timedelta(hours=hours)
    tmp = os.path.join(data.DATA, model, "_download")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    _say(f"{wm['label']} {start:%Y-%m-%d %H}Z to {end:%Y-%m-%d %H}Z ...")
    west, east, south, north = wm["box"]
    copernicusmarine.subset(
        dataset_id=wm["dataset"], variables=list(IBI_PARAMS),
        minimum_longitude=west, maximum_longitude=east,
        minimum_latitude=south, maximum_latitude=north,
        start_datetime=f"{start:%Y-%m-%dT%H:%M:%S}",
        end_datetime=f"{end:%Y-%m-%dT%H:%M:%S}",
        output_directory=tmp, output_filename="waves.nc")
    with xr.open_dataset(os.path.join(tmp, "waves.nc")) as ds:
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
                _write(model, start, param, valid, lead,
                       ds[var].isel(time=k).values,
                       coords, {"units": ds[var].attrs.get("units", "")})
    shutil.rmtree(tmp, ignore_errors=True)
    _prune(model)
    _say(f"{wm['label']} done:", f"{start:%Y%m%dT%H%MZ}")


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
    # No personal key: KNMI's shared anonymous key, published for
    # unregistered use (developer.dataplatform.knmi.nl/open-data-api).
    # Shared by all anonymous users (50 requests a minute, 3000 an hour);
    # a fetch makes two API requests. Valid until 1 August 2027; KNMI
    # publishes its replacement on the same page.
    if datetime.now() >= ANONYMOUS_KNMI_UNTIL:
        raise SystemExit("No KNMI API key, and the built-in anonymous "
                         "key has expired: see docs/CREDENTIALS.md")
    _say("No personal KNMI API key: using KNMI's shared anonymous key "
         "(see docs/CREDENTIALS.md)")
    return ANONYMOUS_KNMI_KEY


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


# Our parameter -> the HARMONIE fields (HARMONIE_FIELDS names) it needs.
HARMONIE_NEEDS = {
    "pressure_at_mean_sea_level": {"mslp"},
    "temperature_at_screen_level": {"t2m"},
    "wind_speed_at_10m": {"u10", "v10"},
    "wind_direction_at_10m": {"u10", "v10"},
    "wind_gust_at_10m": {"ugust", "vgust"},
    "precipitation_accumulation": {"tp_acc"},
}


DECODE_LOCK = __import__("threading").Lock()


def _range(session, url, start, end):
    """Bytes start..end (inclusive) of a file, by an HTTP range request."""
    for attempt in range(4):
        try:
            r = session.get(url, headers={"Range": f"bytes={start}-{end}"},
                            timeout=120)
            if r.status_code == 206:
                return r.content
            r.raise_for_status()
            raise OSError(f"range not honoured (HTTP {r.status_code})")
        except Exception:
            if attempt == 3:
                raise
            import time
            time.sleep(2 * (attempt + 1))


def _tar_members(session, url, total):
    """(name, data offset, size) of each file in a TAR, read from its
    512-byte headers only (PAX / GNU long names handled)."""
    out, off, pending = [], 0, None
    while off + 512 <= total:
        hdr = _range(session, url, off, off + 511)
        if not hdr.strip(b"\0"):
            break
        name = hdr[0:100].rstrip(b"\0").decode("utf-8", "replace")
        size = int(hdr[124:136].strip(b"\0 ") or b"0", 8)
        kind = hdr[156:157]
        data = off + 512
        if kind in (b"x", b"g", b"L"):
            body = _range(session, url, data, data + size - 1)
            if kind == b"L":
                pending = body.rstrip(b"\0").decode("utf-8", "replace")
            elif kind == b"x":
                for rec in body.decode("utf-8", "replace").splitlines():
                    if " path=" in rec:
                        pending = rec.split(" path=", 1)[1]
        elif kind in (b"0", b"\0"):
            out.append((pending or name, data, size))
            pending = None
        off = data + ((size + 511) // 512) * 512
    return out


def _grib1_index(session, url, start, size):
    """(key, offset, length) of each GRIB1 message in a TAR member, from
    the first 32 bytes of each: key = (parameter, level type, level,
    time range indicator)."""
    out, pos, end = [], start, start + size
    while pos + 32 <= end:
        head = _range(session, url, pos, pos + 31)
        if head[:4] != b"GRIB":
            break
        length = int.from_bytes(head[4:7], "big")
        key = (head[16], head[17], int.from_bytes(head[18:20], "big"),
               head[28])
        out.append((key, pos, length))
        pos += length
    return out


def fetch_harmonie(run=None, params=None):
    """Fetch a HARMONIE P3 run (default: newest), only the fields the
    given parameters need (default: all the globe uses). The TAR is
    never downloaded whole: its member headers and each GRIB message's
    first bytes are read with HTTP range requests, then only the wanted
    messages are fetched (a few hundred MB instead of 3.4 GB)."""
    import eccodes as ec
    import requests
    from concurrent.futures import ThreadPoolExecutor
    session = requests.Session()
    session.headers["Authorization"] = knmi_key()
    if run:
        name = f"HARM43_V1_P3_{run}.tar"
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
    info = _knmi(session, f"/files/{name}/url")
    url = info["temporaryDownloadUrl"]
    plain = requests.Session()               # the temporary link: no key
    first = plain.get(url, headers={"Range": "bytes=0-0"}, timeout=60)
    total = int(first.headers["Content-Range"].rsplit("/", 1)[1])
    wanted_names = set().union(*(HARMONIE_NEEDS[p] for p in (
        params or HARMONIE_NEEDS) if p in HARMONIE_NEEDS))
    wanted = {k: v for k, v in HARMONIE_FIELDS.items() if v in wanted_names}
    _say(f"HARMONIE {name}: reading the archive's index "
         f"({total / 1e9:.1f} GB, fetching only "
         f"{', '.join(sorted(wanted_names))}) ...")
    members = [(INNER.search(n), off, size)
               for n, off, size in _tar_members(plain, url, total)]
    members = [(int(m.group(2)), off, size) for m, off, size in members if m]
    if not members:
        raise SystemExit("No forecast files found in the archive")
    with ThreadPoolExecutor(16) as pool:
        indexes = list(pool.map(
            lambda m: (m[0], _grib1_index(plain, url, m[1], m[2])), members))
    inventory = {}
    for _, msgs in indexes:
        for key, _, _ in msgs:
            inventory.setdefault(key[:3], set()).add(key[3])
    got = [0]

    def fields_of(lead_msgs):
        lead, msgs = lead_msgs
        fields, grid = {}, None
        for key, off, length in msgs:
            if key not in wanted:
                continue
            raw = _range(plain, url, off, off + length - 1)
            got[0] += length
            with DECODE_LOCK:              # eccodes: one thread at a time
                gid = ec.codes_new_from_message(raw)
                try:
                    grid = grid or _grid_of(gid, ec)
                    ni, nj = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
                    vals = ec.codes_get_values(gid).reshape(nj, ni)
                    miss = ec.codes_get(gid, "missingValue")
                    fields[wanted[key]] = np.where(vals == miss, np.nan,
                                                   vals)
                finally:
                    ec.codes_release(gid)
        return lead, fields, grid

    acc_prev, grid = {}, None
    with ThreadPoolExecutor(8) as pool:
        for lead, fields, g in pool.map(fields_of, sorted(indexes)):
            grid = grid or g
            _store_harmonie(run_time, lead, fields, grid, acc_prev)
            _say(f"  T+{lead}: {', '.join(sorted(fields)) or 'none'}")
    _store_precip(run_time, acc_prev, grid)
    _write_inventory(name, inventory)
    _prune("harmonie")
    _say(f"HARMONIE done: {run_time:%Y%m%dT%H%MZ} "
         f"({got[0] / 1e6:.0f} MB fetched of {total / 1e9:.1f} GB)")


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
    if "u10" in f and "v10" in f:
        u, v = _to_true_north(f["u10"], f["v10"], x, y, attrs)
        put("wind_speed_at_10m", np.hypot(u, v), "m s-1")
        put("wind_direction_at_10m",
            np.degrees(np.arctan2(-u, -v)) % 360.0, "degrees")
    if "ugust" in f and "vgust" in f:
        put("wind_gust_at_10m", np.hypot(f["ugust"], f["vgust"]), "m s-1")
    if "tp_acc" in f:
        # Total precipitation (kg m-2 = mm) since the run start; hours
        # arrive out of order, so the hourly steps are taken once the
        # whole run is in (_store_precip).
        acc_prev[lead] = f["tp_acc"].astype(np.float32)


def _store_precip(run, acc, grid):
    """Hourly precipitation: the step between consecutive leads."""
    if grid is None:
        return
    x, y, attrs = grid
    order = np.argsort(y)
    coords = {"rlon": ("x", x), "rlat": ("y", y[order])}
    for lead in sorted(acc):
        if lead - 1 in acc:
            hour = (acc[lead] - acc[lead - 1]) / 1000.0     # mm -> m
            _write("harmonie", run, "precipitation_accumulation",
                   run + timedelta(hours=lead), lead, hour[order], coords,
                   {**attrs, "units": "m"}, period=1)


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
    lines = [f"# HARMONIE: GRIB1 fields in {name}", "",
             "| indicatorOfParameter | indicatorOfTypeOfLevel | level | "
             "timeRangeIndicator | used as |", "|---|---|---|---|---|"]
    for key in sorted(inventory):
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | "
                     f"{', '.join(map(str, sorted(inventory[key])))} | "
                     f"{', '.join(v for k, v in HARMONIE_FIELDS.items() if k[:3] == key)} |")
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
    for name in WAVE_MODELS:
        i = sub.add_parser(name)
        i.add_argument("--hours", type=int, default=72)
        i.add_argument("--start", help="YYYYMMDDHH (default: today 00Z)")
    args = ap.parse_args(argv)
    if args.what == "harmonie":
        fetch_harmonie(args.run)
    else:
        start = (datetime.strptime(args.start, "%Y%m%d%H")
                 if args.start else None)
        fetch_waves(args.what, args.hours, start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
