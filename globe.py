"""3D globe: UKV and Global rainfall on one globe (prototype).

Reads the download archive written by the UKMO NWP tools
(<archive>/ukv/<run>/<parameter>/*.nc, <archive>/global10km/...).

    conda env create -f environment.yml
    conda run -n ukmo_globe python globe.py --archive PATH

Each model is drawn on its own native cells: every cell is a quad built
from the file's cell bounds (UKV in its Lambert equal-area metres
turned into longitude/latitude by pyproj, Global straight from its
latitude/longitude bounds). Nothing is resampled; the value on a cell is
the value in the file. UKV sits on top of Global where the two overlap.
Global MSLP is drawn as isobars.
"""

from __future__ import annotations

import argparse
import glob
import os

import geovista as gv
import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

HERE = os.path.dirname(os.path.abspath(__file__))
# Default: the UKMO NWP tools' archive next to this project.
ARCHIVE = os.path.normpath(os.path.join(HERE, "..", "ukmo_nwp", "data",
                                        "archive"))
# Rainfall colour steps (mm in the hour), roughly as on the pages.
LEVELS = [0.1, 0.5, 1, 2, 4, 8, 16]
# Radar-style, so light rain stands out from the blue sea.
COLOURS = ["#9be564", "#2fb344", "#fde725", "#fd9a2a", "#e8322a",
           "#c51b8a", "#7a0177"]


def _file(model, run, param, lead):
    pat = os.path.join(ARCHIVE, model, run, param, f"*-PT{lead:04d}H00M-*")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise SystemExit(f"No file for {model} {run} {param} T+{lead}")
    return hits[0]


def _classes(mm):
    """mm -> colour class index; dry and missing cells become NaN so
    they are see-through (the value itself is not changed)."""
    out = np.digitize(mm, LEVELS).astype(float) - 1
    out[~np.isfinite(mm) | (mm < LEVELS[0])] = np.nan
    return out


def ukv_mesh(run, lead):
    ds = xr.open_dataset(_file("ukv", run, "precipitation_accumulation",
                               lead))
    var = ds["lwe_thickness_of_precipitation_amount"]
    gm = ds[var.attrs["grid_mapping"]].attrs
    crs = CRS.from_cf(gm)
    xb = ds["projection_x_coordinate_bnds"].values
    yb = ds["projection_y_coordinate_bnds"].values
    xe = np.append(xb[:, 0], xb[-1, 1])
    ye = np.append(yb[:, 0], yb[-1, 1])
    xx, yy = np.meshgrid(xe, ye)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326",
                                    always_xy=True).transform(xx, yy)
    mm = var.values * 1000.0
    mesh = gv.Transform.from_2d(lon, lat, data=_classes(mm), name="rain")
    mesh.cell_data["mm"] = mm.ravel()
    return mesh, str(ds["time"].values)[:16]


def global_meshes(run, lead):
    ds = xr.open_dataset(_file("global10km", run,
                               "precipitation_accumulation", lead))
    lonb = ds["longitude_bnds"].values
    latb = ds["latitude_bnds"].values
    lons = np.append(lonb[:, 0], lonb[-1, 1])
    lats = np.append(latb[:, 0], latb[-1, 1])
    mm = ds["lwe_thickness_of_precipitation_amount"].values * 1000.0
    rain = gv.Transform.from_1d(lons, lats, data=_classes(mm), name="rain")
    rain.cell_data["mm"] = mm.ravel()

    ps = xr.open_dataset(_file("global10km", run,
                               "pressure_at_mean_sea_level", lead))
    hpa = ps["air_pressure_at_sea_level"].values / 100.0
    p = gv.Transform.from_1d(lons, lats, data=hpa, name="hPa")
    isobars = p.cell_data_to_point_data().contour(
        isosurfaces=np.arange(940, 1061, 4), scalars="hPa")
    return rain, isobars, str(ds["time"].values)[:16]


def main():
    global ARCHIVE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=ARCHIVE,
                    help="download archive folder (default: %(default)s)")
    ap.add_argument("--ukv-run", default="20261115T2100Z")
    ap.add_argument("--ukv-lead", type=int, default=6)
    ap.add_argument("--global-run", default="20261115T1200Z")
    ap.add_argument("--global-lead", type=int, default=15)
    ap.add_argument("--screenshot", help="write a PNG and exit")
    ap.add_argument("--view", choices=["ireland", "atlantic"],
                    default="atlantic")
    args = ap.parse_args()
    ARCHIVE = args.archive

    ukv, ukv_t = ukv_mesh(args.ukv_run, args.ukv_lead)
    glo, isobars, glo_t = global_meshes(args.global_run, args.global_lead)
    if ukv_t != glo_t:
        print(f"Warning: valid times differ (UKV {ukv_t}, Global {glo_t})")

    pl = gv.GeoPlotter(off_screen=bool(args.screenshot),
                       window_size=(1400, 1000))
    pl.set_background("#0b1020")
    pl.add_base_layer(texture=gv.natural_earth_1(), zlevel=0)
    common = dict(scalars="rain", cmap=COLOURS, clim=(0, len(LEVELS)),
                  nan_opacity=0.0, show_scalar_bar=False,
                  n_colors=len(LEVELS))
    pl.add_mesh(glo, zlevel=2, opacity=0.85, **common)
    pl.add_mesh(ukv, zlevel=4, **common)
    pl.add_mesh(isobars, color="white", line_width=1.5, zlevel=6)
    pl.add_coastlines(color="black", line_width=1, zlevel=8)

    pl.add_text(f"Hourly rainfall, valid {glo_t} UTC\n"
                f"UKV {args.ukv_run} T+{args.ukv_lead} (on top), "
                f"Global {args.global_run} T+{args.global_lead}; "
                "Global MSLP isobars every 4 hPa\n"
                "Native model cells, no resampling. Prototype.",
                position="upper_left", font_size=10, color="white")
    labels = [f">={v:g}" for v in LEVELS]
    pl.add_legend(list(zip([f"{t} mm" for t in labels], COLOURS)),
                  bcolor="#0b1020", face="rectangle", size=(0.11, 0.2),
                  loc="lower right")

    if args.view == "ireland":
        pl.view_poi(x=-8.0, y=53.4)
        pl.camera.zoom(7.0)
    else:
        pl.view_poi(x=-15.0, y=52.0)
        pl.camera.zoom(2.4)

    if args.screenshot:
        pl.show(screenshot=args.screenshot)
        print("wrote", args.screenshot)
    else:
        pl.show()


if __name__ == "__main__":
    main()
