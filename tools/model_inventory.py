"""Write docs/models/*.md: what each model source publishes.

Reads the live listings and index files (nothing is guessed): the Met
Office bucket listing, DWD's ICON-EU parameter folders, an ECMWF step
index, the GFS .idx files, a GRIB scan of the Météo-France surface
packages, HARMONIE's GRIB scan (docs/harmonie_inventory.md, written by
each HARMONIE fetch) and the Copernicus Marine dataset variables. Then
writes one file per source and docs/models/SUMMARY.md, a cross-model
table of common fields.

    conda run -n ukmo_globe python tools/model_inventory.py

AROME's scan downloads the SP1-SP3 packages of one 6-hour block
(0.025 deg, about 170 MB per model) and one hour at 0.01 deg (SP1-SP3
and HP1, about 120 MB). Everything else is listings and indexes.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "models")
TODAY = datetime.now(timezone.utc).strftime("%d %B %Y")


def get(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


# ---- collectors: each returns (run, rows); a row is a dict with an
# "id" (the source's own name) and descriptive columns -------------------

def metoffice(prefix):
    import globe_data
    model = "ukv" if prefix.startswith("uk-") else "global"
    run = globe_data.latest_run(model)
    base = "https://met-office-atmospheric-model-data.s3.eu-west-2.amazonaws.com"
    keys, token = [], None
    while True:
        url = f"{base}/?list-type=2&prefix={prefix}/{run}/"
        if token:
            url += "&continuation-token=" + urllib.request.quote(token)
        xml = get(url).decode()
        keys += re.findall(r"<Key>([^<]+)</Key>", xml)
        m = re.search(r"<NextContinuationToken>([^<]+)<", xml)
        if not m:
            break
        token = m.group(1)
    found = defaultdict(lambda: {"leads": set(), "periods": set()})
    for k in keys:
        m = re.search(r"-PT(\d{4})H00M-([A-Za-z0-9_]+?)(?:-PT(\d\d)H)?\.nc$", k)
        if m:
            f = found[m.group(2)]
            f["leads"].add(int(m.group(1)))
            if m.group(3):
                f["periods"].add(f"PT{m.group(3)}H")
    rows = []
    for name in sorted(found):
        f = found[name]
        leads = sorted(f["leads"])
        rows.append({"id": name, "leads": f"T+{leads[0]} to T+{leads[-1]} "
                     f"({len(leads)} files)",
                     "period": ", ".join(sorted(f["periods"])) or "-"})
    return run, rows


def icon():
    import globe_icon
    run = globe_icon.latest_run()
    base = f"{globe_icon.BASE}/{run[9:11]}/"
    html = get(base).decode("utf-8", "replace")
    rows = []
    for p in sorted(set(re.findall(r'href="([a-z0-9_]+)/"', html))):
        names = re.findall(r'href="(icon-eu[^"]+\.grib2\.bz2)"',
                           get(base + p + "/").decode("utf-8", "replace"))
        kinds = sorted({re.search(r"regular-lat-lon_([a-z-]+)_", n).group(1)
                        for n in names
                        if re.search(r"regular-lat-lon_([a-z-]+)_", n)})
        levels = sorted({int(m.group(1)) for n in names for m in
                         [re.search(r"_\d{10}_\d{3}_(\d+)_", n)] if m})
        lv = (f"{len(levels)} levels ({levels[0]}-{levels[-1]})"
              if levels else "-")
        rows.append({"id": p, "level type": ", ".join(kinds) or "-",
                     "levels": lv, "files": len(names)})
    return run, rows


def ecmwf():
    import globe_ecmwf
    run = globe_ecmwf.latest_run()
    lines = get(globe_ecmwf._url(run, 6, "index")).decode().splitlines()
    found = OrderedDict()
    for line in lines:
        d = json.loads(line)
        key = (d["param"], d.get("levtype", ""))
        found.setdefault(key, set()).add(d.get("levelist", ""))
    rows = []
    for (param, lt), levs in sorted(found.items()):
        levs = sorted(x for x in levs if x)
        rows.append({"id": f"{param}:{lt}", "param": param,
                     "level type": lt,
                     "levels": ", ".join(levs) if levs else "-"})
    return run, rows


def gfs(product):
    import globe_gfs
    run = globe_gfs.latest_run()
    lines = get(globe_gfs._url(run, product, 6) + ".idx").decode().splitlines()
    found = OrderedDict()
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 6:
            found.setdefault(f"{parts[3]}:{parts[4]}", set()).add(parts[5])
    rows = [{"id": k, "variable": k.split(":")[0], "level": k.split(":", 1)[1],
             "timing at T+6": "; ".join(sorted(v))} for k, v in found.items()]
    return run, rows


def arome(product, packages, hours):
    import eccodes as ec
    run = product.latest_run()
    rows = []
    for pkg in packages:
        url = product.url(run, pkg, hours)
        try:
            raw = get(url, timeout=600)
        except Exception as exc:
            rows.append({"id": f"{pkg}: not scanned ({exc})"})
            continue
        i, seen = 0, OrderedDict()
        while True:
            i = raw.find(b"GRIB", i)
            if i < 0:
                break
            n = int.from_bytes(raw[i + 8:i + 16], "big")
            g = ec.codes_new_from_message(raw[i:i + n])

            def k(name):
                try:
                    return str(ec.codes_get(g, name))
                except Exception:
                    return "?"
            key = (k("shortName"), k("typeOfLevel"), k("level"))
            if key not in seen:
                seen[key] = (k("name"), k("units"), k("stepType"),
                             f"{k('discipline')}.{k('parameterCategory')}."
                             f"{k('parameterNumber')}")
            ec.codes_release(g)
            i += n
        for (short, lt, lev), (name, units, st, code) in seen.items():
            rows.append({"id": f"{short}:{lt}:{lev}", "package": pkg,
                         "shortName": short, "level": f"{lt} {lev}",
                         "name": name, "units": units, "stat": st,
                         "GRIB2 code": code})
    return run, rows


def harmonie():
    path = os.path.join(ROOT, "docs", "harmonie_inventory.md")
    rows = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return None, rows
    title = lines[0] if lines else ""
    run = re.search(r"(\d{10})", title)
    names = {1: "PMSL / PSRF", 6: "Geopotential", 11: "Temperature",
             17: "Dew point", 20: "Visibility", 33: "u wind", 34: "v wind",
             52: "Relative humidity", 61: "Total precipitation",
             65: "Snow water equivalent", 66: "Snow depth",
             67: "Mixed layer depth", 71: "Total cloud", 73: "Low cloud",
             74: "Medium cloud", 75: "High cloud", 81: "Land cover",
             111: "Net short-wave flux", 112: "Net long-wave flux",
             117: "Global radiation", 122: "Sensible heat flux",
             132: "Latent heat flux", 162: "u gust", 163: "v gust",
             181: "Rain", 184: "Snow", 186: "Cloud base", 201: "Graupel"}
    lt = {"100": "isobaric hPa", "103": "above MSL", "105": "above ground",
          "109": "hybrid", "200": "entire atmosphere"}
    for line in lines:
        m = re.match(r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d, ]+)\|",
                     line)
        if m:
            code = int(m.group(1))
            rows.append({"id": f"{code}:{m.group(2)}:{m.group(3)}",
                         "code": code, "name": names.get(code, "?"),
                         "level": f"{lt.get(m.group(2), m.group(2))} "
                                  f"{m.group(3)}",
                         "time range": m.group(4).strip()})
    return (run.group(1) if run else "?"), rows


def copernicus(dataset):
    import copernicusmarine
    ds = copernicusmarine.open_dataset(dataset_id=dataset)
    rows = [{"id": v, "standard name": ds[v].attrs.get("standard_name", ""),
             "long name": ds[v].attrs.get("long_name", ""),
             "units": ds[v].attrs.get("units", "")} for v in ds.data_vars]
    run = f"{str(ds.time.values[0])[:13]} to {str(ds.time.values[-1])[:13]}"
    return run, rows


# ---- per-source facts the listings do not say --------------------------

MODELS = OrderedDict()


def model(key, title, **facts):
    MODELS[key] = dict(title=title, **facts)


model("ukv", "UKV (Met Office, 2 km)",
      collect=lambda: metoffice("uk-deterministic-2km"),
      access="Met Office open data on AWS, anonymous HTTPS: "
             "`met-office-atmospheric-model-data/uk-deterministic-2km/`.",
      latest="Newest run (every 3 h) whose MSLP at T+54 is published "
             "(HEAD request): `globe_data.latest_run`.",
      naming="`<run>/<valid>-PT<lead>H00M-<parameter>[-PT01H|-PT03H].nc`, "
             "one NetCDF per field per hour.",
      grid="Lambert azimuthal equal-area, 2 km (cell bounds in the files).",
      leads="Hourly to T+54; 03 and 15Z runs go further at 3-hourly steps.",
      used="precipitation_accumulation, pressure_at_mean_sea_level, "
           "temperature_at_screen_level, wind_speed_at_10m, "
           "wind_direction_at_10m, wind_gust_at_10m, "
           "CAPE_most_unstable_below_500hPa",
      adding="Add a style in `globe_core.STYLES` with `param=` the file's "
             "parameter name; files download on demand. Full details: "
             "`reference/parameter_inventory.md`.")
model("global", "UKMO Global (Met Office, 10 km)",
      collect=lambda: metoffice("global-deterministic-10km"),
      access="Met Office open data on AWS, anonymous HTTPS: "
             "`met-office-atmospheric-model-data/global-deterministic-10km/`.",
      latest="Newest run (every 6 h) whose MSLP at T+54 is published.",
      naming="As UKV.", grid="Regular lat/lon, 2560 x 1920 (about 10 km); "
             "the globe keeps 80W-50E, 15-80N.",
      leads="Hourly to T+54, then 3-hourly to T+69 (accumulations become "
            "PT03H).",
      used="As UKV.",
      adding="As UKV. Full details: `reference/parameter_inventory_global.md`.")
model("harmonie", "HARMONIE P3 (KNMI)", collect=harmonie,
      access="KNMI Open Data API, dataset `harmonie_arome_cy43_p3` v1.0; "
             "API key or KNMI's anonymous key (docs/CREDENTIALS.md).",
      latest="Newest TAR in the dataset listing (`globe_fetch.fetch_harmonie`).",
      naming="`HARM43_V1_P3_<YYYYMMDDHH>.tar` (about 3.4 GB) holding "
             "`HA43_N55_<run>_<lead>00_GB` per hour, GRIB1.",
      grid="Rotated lat/lon 0.05 deg (about 5.5 km), DINI domain (Europe).",
      leads="Hourly to T+60; runs every hour.",
      used="1:103:0 (MSLP), 11:105:2, 33/34:105:10, 162/163:105:10 (gust), "
           "61:105:0 time range 4 (total precipitation)",
      adding="Add the GRIB1 key to `globe_fetch.HARMONIE_FIELDS` and write "
             "it in `_store_harmonie`; the whole run is re-streamed. Rows "
             "come from the last fetch's scan (`docs/harmonie_inventory.md`).")
model("icon", "ICON-EU (DWD)", collect=icon,
      access="DWD open data, anonymous HTTPS: "
             "`opendata.dwd.de/weather/nwp/icon-eu/grib/<HH>/<param>/`.",
      latest="Newest 00/06/12/18Z run whose MSLP T+120 is in its folder "
             "(`globe_icon.latest_run`).",
      naming="`icon-eu_europe_regular-lat-lon_<level type>_<YYYYMMDDHH>_"
             "<lead>[_<level>]_<PARAM>.grib2.bz2`, one per field per step.",
      grid="Regular lat/lon 0.0625 deg (about 7 km), 23.5W-62.5E, 29.5-70.5N.",
      leads="Hourly to T+78, then 3-hourly to T+120 (00/06/12/18Z).",
      used="pmsl, t_2m, u_10m, v_10m, vmax_10m, tot_prec, cape_ml",
      adding="Add our parameter to `globe_icon.FIELDS` with the folder "
             "name; `make_field` fetches it.")
model("ecmwf", "ECMWF IFS 0.1 deg", collect=ecmwf,
      access="ECMWF open data (CC BY 4.0), anonymous HTTPS: "
             "`data.ecmwf.int/forecasts/<date>/<HH>z/ifs/0p1/oper/`.",
      latest="Newest run whose last step's index is published "
             "(`globe_ecmwf.latest_run`).",
      naming="`<YYYYMMDDHH>0000-<step>h-oper-fc-sfc.grib2` + `.index` "
             "(JSON, byte ranges); pressure-level files `-pl`.",
      grid="Global regular lat/lon 0.1 deg; the globe keeps 80W-50E, 15-80N.",
      leads="3-hourly: 00/12Z to T+144 (used to T+120), 06/18Z to T+90.",
      used="msl, 2t, 10u, 10v, 10fg / 10fg3, tp, mucape",
      adding="Add to `globe_ecmwf.FIELDS`; fields are range-requested from "
             "the step's index. Rows: surface file index at T+6.")
model("gfs_sflux", "GFS sflux (NOAA, about 13 km)",
      collect=lambda: gfs("sflux"),
      access="NOAA GFS on AWS, anonymous HTTPS: `noaa-gfs-bdp-pds`.",
      latest="Newest run with T+120 index for both products "
             "(`globe_gfs.latest_run`).",
      naming="`gfs.<date>/<HH>/atmos/gfs.t<HH>z.sfluxgrbf<FFF>.grib2` + `.idx`.",
      grid="T1534 Gaussian (about 13 km); the globe keeps 80W-50E, 15-80N.",
      leads="Hourly to T+120 (used), 3-hourly to T+384.",
      used="TMP:2 m, UGRD/VGRD:10 m, PRATE:surface",
      adding="Add to `globe_gfs.SOURCES` and `PRODUCT_FIELDS`. Rows: T+6 .idx; "
             "statistics (ave/acc) reset every 6 hours: check the timing.")
model("gfs_0p25", "GFS 0.25 deg (NOAA)", collect=lambda: gfs("pgrb2"),
      access="As GFS sflux.", latest="As GFS sflux.",
      naming="`gfs.t<HH>z.pgrb2.0p25.f<FFF>` + `.idx`.",
      grid="Regular lat/lon 0.25 deg; the globe keeps 80W-50E, 15-80N.",
      leads="As GFS sflux.",
      used="PRMSL:mean sea level, CAPE:255-0 mb above ground, GUST:surface",
      adding="As GFS sflux. Rows: T+6 .idx.")


def _mf(res, model_name, every, scan_pkgs, hours):
    import globe_meteofrance as mf
    prod = {("arome", "0025"): mf.AROME_0025, ("arome", "001"): mf.AROME_001,
            ("aromeifs", "0025"): mf.AROMEIFS_0025}[(model_name, res)]
    return lambda: arome(prod, scan_pkgs, hours)


model("arome_001", "AROME 1.3 km (Météo-France, 0.01 deg)",
      collect=_mf("001", "arome", 3, ["SP1", "SP2", "SP3", "HP1"], 3),
      access="Météo-France open data (data.gouv.fr \"Paquets AROME\"), "
             "anonymous HTTPS: `meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/`.",
      latest="Newest run (every 3 h) whose SP1 `51H` file is published.",
      naming="`<run ISO>/arome/001/<pkg>/arome__001__<pkg>__<HH>H__<run ISO>"
             ".grib2`, one file per package per hour; no byte index.",
      grid="Regular lat/lon 0.01 deg, 12W-16E, 37.5-55.4N.",
      leads="Hourly to T+51.",
      used="SP1 2t, 10u, 10v, 10efg/10nfg; SP2 CAPE_INS, tirf+tsnowp+tgrp",
      adding="Add to `globe_meteofrance.SOURCES_001`; whole package files "
             "are fetched. Rows: scan of the T+3 files (HP2/HP3 not "
             "scanned). No MSLP at 0.01 deg.")
model("arome_0025", "AROME 2.5 km (Météo-France, 0.025 deg)",
      collect=_mf("0025", "arome", 3, ["SP1", "SP2", "SP3"], 3),
      access="As AROME 1.3 km.", latest="Newest run (every 3 h) whose SP1 "
             "`49H51H` package is published.",
      naming="`<run ISO>/arome/0025/<pkg>/arome__0025__<pkg>__<hours>__"
             "<run ISO>.grib2`, 6-hour packages (`00H06H` ... `49H51H`).",
      grid="Regular lat/lon 0.025 deg, 12W-16E, 37.5-55.4N.",
      leads="Hourly to T+51.",
      used="SP1 prmsl, 2t, 10u, 10v, 10fg, tp; SP2 CAPE_INS",
      adding="Add to `globe_meteofrance.SOURCES_0025`. Rows: scan of the "
             "`00H06H` SP1-SP3 packages; HP1-HP3 and IP1-IP5 (height, "
             "pressure-level fields; 0.7-6 GB per run each) not scanned.")
model("aromeifs_0025", "AROME-IFS 2.5 km (Météo-France)",
      collect=_mf("0025", "aromeifs", 6, ["SP1", "SP2", "SP3"], 3),
      access="As AROME 1.3 km.", latest="Newest run (every 6 h) whose SP1 "
             "`49H51H` package is published.",
      naming="As AROME 2.5 km with `aromeifs` for `arome`.",
      grid="As AROME 2.5 km.", leads="Hourly to T+51.",
      used="As AROME 2.5 km.", adding="As AROME 2.5 km.")
model("nws", "NWS waves (Copernicus Marine, 1.5 km)",
      collect=lambda: copernicus("cmems_mod_nws_wav_anfc_1.5km_PT1H-i"),
      access="Copernicus Marine (free login: docs/CREDENTIALS.md), product "
             "`NWSHELF_ANALYSISFORECAST_WAV_004_014`.",
      latest="Today 00Z to +72 h (`globe_fetch.fetch_waves`).",
      naming="Subset requests through `copernicusmarine.subset`.",
      grid="About 1.5 km lat/lon, 16W-13E, 46-62.7N.",
      leads="Hourly; analysis and forecast in one time series.",
      used="VHM0, VMDR",
      adding="Add the variable to `globe_fetch.IBI_PARAMS` (used for both "
             "wave models) and a style in `globe_core.STYLES`.")
model("ibi", "IBI waves (Copernicus Marine, 0.027 deg)",
      collect=lambda: copernicus("cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i"),
      access="Copernicus Marine, product `IBI_ANALYSISFORECAST_WAV_005_005`.",
      latest="As NWS waves.", naming="As NWS waves.",
      grid="About 3 km lat/lon, 19W-5E, 26-56N.", leads="As NWS waves.",
      used="VHM0, VMDR", adding="As NWS waves.")

# ---- cross-model summary -------------------------------------------------
# One pattern per common field, matched against every model's ids.
COMMON = OrderedDict([
    ("Mean sea-level pressure", r"^(pressure_at_mean_sea_level|msl:sfc|PRMSL:mean sea|pmsl$|prmsl:|1:103:0)"),
    ("2 m temperature", r"^(temperature_at_screen_level$|2t:|TMP:2 m above|t_2m$|11:105:2)"),
    ("10 m wind", r"^(wind_speed_at_10m$|10u:|UGRD:10 m above|u_10m$|33:105:10)"),
    ("10 m gust", r"^(wind_gust_at_10m|10fg|GUST:|vmax_10m$|10efg|162:105:10)"),
    ("Total precipitation", r"^(precipitation_accumulation$|tp:|PRATE:surface|APCP:surface|tot_prec$|tirf:|61:105:0|181:105:0)"),
    ("CAPE (any)", r"^(CAPE_|mucape|cape|CAPE:|cape_ml|cape_con|160:)"),
    ("CIN", r"^(CIN_|mucin|cin|CIN:|cin_ml)"),
    ("Total cloud", r"^(cloud_amount_of_total_cloud$|tcc:|TCDC:entire|clct$|71:105:0)"),
    ("Low / medium / high cloud", r"^(cloud_amount_of_low_cloud$|lcc:|LCDC:|clcl$|73:105:0)"),
    ("2 m relative humidity", r"^(relative_humidity_at_screen_level$|2r:|r2:|RH:2 m above|relhum_2m$|52:105:2)"),
    ("2 m dew point", r"^(temperature_of_dew_point_at_screen_level$|2d:|DPT:2 m above|td_2m$|17:105:2)"),
    ("Visibility", r"^(visibility_at_screen_level$|vis:|VIS:surface|vis$|20:105:0)"),
    ("Snow depth", r"^(snow_depth|sd:|SNOD:|WEASD:|h_snow$|65:105:0|66:105:0)"),
    ("Max / min 2 m temperature", r"^(temperature_at_screen_level_max$|mx2t|TMAX:|tmax_2m$)"),
    ("Freezing level", r"^(.*freezing_level|deg0l|HGT:0C isotherm|hzerocl$)"),
    ("100 m or upper-level wind", r"^(wind_speed_on_pressure_levels$|100u:|u:pl|UGRD:(100 m|\d+ mb)|u$|100u:heightAboveGround|33:105:100|33:100:)"),
    ("Significant wave height", r"^VHM0$"),
])


def write():
    os.makedirs(OUT, exist_ok=True)
    have = OrderedDict()
    for key, m in MODELS.items():
        print("reading", key, flush=True)
        try:
            run, rows = m["collect"]()
            note = ""
        except Exception as exc:
            run, rows, note = "?", [], f"Could not read the source: {exc}"
        have[key] = [r["id"] for r in rows]
        cols = [c for c in (rows[0] if rows else {}) if c != "id"]
        lines = [f"# {m['title']}", "",
                 f"_Generated by `tools/model_inventory.py` on {TODAY}; "
                 f"run read: {run}._", ""]
        for label, k in (("Access", "access"), ("Latest run", "latest"),
                         ("File naming", "naming"), ("Grid", "grid"),
                         ("Run hours and leads", "leads"),
                         ("Used by the globe", "used"),
                         ("Adding a field", "adding")):
            lines.append(f"- **{label}:** {m[k]}")
        lines += ["", f"## Fields ({len(rows)})", ""]
        if note:
            lines.append(note)
        elif rows:
            lines.append("| id | " + " | ".join(cols) + " |")
            lines.append("|---" * (len(cols) + 1) + "|")
            for r in rows:
                lines.append(f"| `{r['id']}` | " + " | ".join(
                    str(r.get(c, "")) for c in cols) + " |")
        open(os.path.join(OUT, f"{key}.md"), "w",
             encoding="utf-8").write("\n".join(lines) + "\n")

    keys = list(MODELS)
    head = ["Field"] + [MODELS[k]["title"].split(" (")[0] for k in keys] + \
        ["Models"]
    lines = ["# Fields across models", "",
             f"_Generated by `tools/model_inventory.py` on {TODAY} from the "
             "live listings (see each model's file). A tick means a field "
             "matching the row was found; it does not mean the definitions "
             "are identical (e.g. CAPE parcel type, gust period). 'Models' "
             "counts weather models only (the wave models are excluded)._",
             "", "| " + " | ".join(head) + " |", "|---" * len(head) + "|"]
    weather = [k for k in keys if k not in ("nws", "ibi")]
    for field, pat in COMMON.items():
        rx = re.compile(pat)
        cells, n = [], 0
        for k in keys:
            hit = [i for i in have[k] if rx.search(i)]
            cells.append("yes" if hit else ("?" if not have[k] else ""))
            n += bool(hit) and k in weather
        lines.append(f"| {field} | " + " | ".join(cells) +
                     f" | {n}/{len(weather)} |")
    lines += ["", "Blank: not in the source's listing. '?': the source "
              "could not be read this time."]
    open(os.path.join(OUT, "SUMMARY.md"), "w",
         encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)


if __name__ == "__main__":
    write()
