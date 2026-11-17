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
Global MSLP is drawn as labelled isobars.

Keys: Left/Right one hour, Down/Up six hours, h home (Ireland, north
up), n north up from the current view. Dragging keeps north up.
"""

from __future__ import annotations

import argparse
import glob
import os
import threading
import warnings
from datetime import datetime, timedelta

import contourpy
import geovista as gv
import numpy as np
import pyvista as pv
import xarray as xr
from pyproj import CRS, Transformer

HERE = os.path.dirname(os.path.abspath(__file__))
# Default: the UKMO NWP tools' archive next to this project.
ARCHIVE = os.path.normpath(os.path.join(HERE, "..", "ukmo_nwp", "data",
                                        "archive"))
RAIN = "precipitation_accumulation"
MSLP = "pressure_at_mean_sea_level"
# Rainfall colour steps (mm in the hour), roughly as on the pages.
LEVELS = [0.1, 0.5, 1, 2, 4, 8, 16]
# Radar-style colours; they stand out on the grey base.
COLOURS = ["#9be564", "#2fb344", "#fde725", "#fd9a2a", "#e8322a",
           "#c51b8a", "#7a0177"]
ISOBARS = np.arange(940, 1061, 4)
HOME = (-8.0, 53.4, 7.0)            # lon, lat, zoom
ATLANTIC = (-15.0, 52.0, 2.4)
KEY_HELP = ("Left/Right: hour   Down/Up: 6 hours   "
            "h: home   n: north up   q: quit")


def _stamp(text):
    return datetime.strptime(text, "%Y%m%dT%H%MZ")


def _index(model, run, param):
    """{valid time: file} for one run and parameter. The valid time is
    the first part of each file name."""
    out = {}
    for path in glob.glob(os.path.join(ARCHIVE, model, run, param, "*.nc")):
        out[_stamp(os.path.basename(path).split("-")[0])] = path
    return out


def _latest_run(model):
    runs = sorted(os.listdir(os.path.join(ARCHIVE, model)))
    if not runs:
        raise SystemExit(f"No {model} runs in {ARCHIVE}")
    return runs[-1]


# Isobar points are made on the globe already, so GeoVista's "no CRS"
# warning for them means nothing.
warnings.filterwarnings("ignore", message="geovista found no coordinate")
# NetCDF/HDF5 reading is not safe from two threads at once.
READ_LOCK = threading.Lock()
DRY = 255                           # class code: dry or missing


def _values(path, name, scale):
    """Raw values from one file (scaled to mm or hPa only)."""
    with READ_LOCK, xr.open_dataset(path) as ds:
        return ds[name].values * scale


def _classes(mm):
    """mm -> colour class 0..6 as bytes, DRY for dry and missing cells
    (stored small so every hour can be kept in memory)."""
    out = (np.digitize(mm, LEVELS) - 1).astype(np.uint8)
    out[~np.isfinite(mm) | (mm < LEVELS[0])] = DRY
    return out.ravel()


def _shown(classes):
    """Class bytes -> what the mesh draws; DRY becomes see-through."""
    out = classes.astype(np.float32)
    out[classes == DRY] = np.nan
    return out


def _xyz(lon, lat):
    """Longitude/latitude -> points on GeoVista's unit globe."""
    lam, phi = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(phi) * np.cos(lam),
                            np.cos(phi) * np.sin(lam),
                            np.sin(phi)]).astype(np.float32)


def isobar_mesh(lon, lat, hpa):
    """Isobars from the Global MSLP field as lines on the globe.

    contourpy on the 2D grid is about six times faster than VTK
    contouring the globe mesh. The first column is repeated at the end
    so lines close across the 0/360 seam."""
    lon = np.append(lon, lon[0] + 360.0)
    hpa = np.column_stack([hpa, hpa[:, 0]])
    gen = contourpy.contour_generator(lon, lat, hpa, line_type="Separate")
    pts, cells, vals, n = [], [], [], 0
    for level in ISOBARS:
        for line in gen.lines(level):
            if len(line) < 2:
                continue
            pts.append(line)
            cells.append(np.concatenate([[len(line)],
                                         np.arange(n, n + len(line))]))
            vals.append(np.full(len(line), level, dtype=np.float32))
            n += len(line)
    if not pts:
        return pv.PolyData()
    xy = np.concatenate(pts)
    mesh = pv.PolyData(_xyz(xy[:, 0], xy[:, 1]),
                       lines=np.concatenate(cells))
    mesh.point_data["hPa"] = np.concatenate(vals)
    return mesh


def ukv_mesh(path):
    with xr.open_dataset(path) as ds:
        var = ds["lwe_thickness_of_precipitation_amount"]
        crs = CRS.from_cf(ds[var.attrs["grid_mapping"]].attrs)
        xb = ds["projection_x_coordinate_bnds"].values
        yb = ds["projection_y_coordinate_bnds"].values
        shape = var.shape
    xe = np.append(xb[:, 0], xb[-1, 1])
    ye = np.append(yb[:, 0], yb[-1, 1])
    xx, yy = np.meshgrid(xe, ye)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326",
                                    always_xy=True).transform(xx, yy)
    return gv.Transform.from_2d(lon, lat, data=np.full(shape, np.nan),
                                name="rain")


def global_mesh(path):
    with xr.open_dataset(path) as ds:
        lonb = ds["longitude_bnds"].values
        latb = ds["latitude_bnds"].values
    lons = np.append(lonb[:, 0], lonb[-1, 1])
    lats = np.append(latb[:, 0], latb[-1, 1])
    return gv.Transform.from_1d(lons, lats,
                                data=np.full((lats.size - 1, lons.size - 1),
                                             np.nan), name="rain")


def grey_texture():
    """Natural Earth I shaded relief, in light grey so coloured
    forecast layers stand out."""
    rgb = gv.natural_earth_1().to_array()[..., :3].astype(float)
    grey = rgb @ [0.299, 0.587, 0.114]
    grey = 95.0 + grey * 0.6                     # lighter, less contrast
    return pv.Texture(np.dstack([grey] * 3).astype(np.uint8))


def isobar_labels(isobars, spacing=0.12):
    """Isobar values at points along the lines, about one per
    ``spacing``-sized box (globe radii; 0.12 is about 750 km), lifted
    just off the globe so the layers under them do not hide them."""
    if isobars.n_points == 0:
        return None, []
    xyz = isobars.points
    hpa = isobars.point_data["hPa"]
    # One label per box and isobar value: the point nearest the box
    # centre, so labels sit where the line crosses the box middle.
    box = np.floor(xyz / spacing).astype(np.int64)
    off = np.sum((xyz / spacing - box - 0.5) ** 2, axis=1)
    key = np.column_stack([box, np.rint(hpa).astype(np.int64)])
    order = np.lexsort((off, *key.T[::-1]))
    _, first = np.unique(key[order], axis=0, return_index=True)
    kept = order[first]
    # Boxes in a chequerboard only, so neighbours' labels do not touch.
    kept = kept[np.sum(box[kept], axis=1) % 2 == 0]
    return xyz[kept] * 1.004, [f"{hpa[i]:.0f}" for i in kept]


class Globe:
    def __init__(self, args):
        self.ukv_run = args.ukv_run or _latest_run("ukv")
        self.glo_run = args.global_run or _latest_run("global10km")
        self.ukv_rain = _index("ukv", self.ukv_run, RAIN)
        self.glo_rain = _index("global10km", self.glo_run, RAIN)
        self.glo_mslp = _index("global10km", self.glo_run, MSLP)
        self.times = sorted(set(self.glo_rain) & set(self.glo_mslp))
        if not self.times:
            raise SystemExit(f"No Global rain/MSLP in {self.glo_run}")
        if args.valid:
            start = _stamp(args.valid)
        else:
            both = [t for t in self.times if t in self.ukv_rain]
            start = both[0] if both else self.times[0]
        if start not in self.times:
            raise SystemExit(f"No Global data valid {args.valid}")
        self.i = self.times.index(start)

        self.ukv = ukv_mesh(next(iter(self.ukv_rain.values()))) \
            if self.ukv_rain else None
        self.glo = global_mesh(self.glo_rain[self.times[0]])
        with xr.open_dataset(self.glo_mslp[self.times[0]]) as ds:
            self.lon = ds["longitude"].values
            self.lat = ds["latitude"].values
        self.dynamic = []
        # Every hour is prepared in the background, nearest to the
        # shown hour first, so stepping only swaps in ready data.
        self.cache = {}
        self.cache_lock = threading.Lock()

        pl = self.pl = gv.GeoPlotter(off_screen=bool(args.screenshot),
                                     window_size=(1400, 1000))
        pl.set_background("#0b1020")
        texture = (gv.natural_earth_1() if args.base == "colour"
                   else grey_texture())
        pl.add_base_layer(texture=texture, zlevel=0)
        common = dict(scalars="rain", cmap=COLOURS, clim=(0, len(LEVELS)),
                      nan_opacity=0.0, show_scalar_bar=False,
                      n_colors=len(LEVELS))
        # GeoVista draws a copy of each mesh: keep the drawn one, so a
        # new time step changes what is on screen.
        self.glo = pl.add_mesh(self.glo, zlevel=2, opacity=0.85,
                               **common).mapper.dataset
        if self.ukv is not None:
            self.ukv = pl.add_mesh(self.ukv, zlevel=4,
                                   **common).mapper.dataset
        pl.add_coastlines(color="black", line_width=1, zlevel=8)
        pl.add_legend([(f">={v:g} mm", c) for v, c in zip(LEVELS, COLOURS)],
                      bcolor="#0b1020", face="rectangle",
                      size=(0.11, 0.2), loc="lower right")
        pl.add_text(KEY_HELP, position="lower_left", font_size=9,
                    color="#c8c8c8", name="keys")

        lon, lat, zoom = ATLANTIC if args.view == "atlantic" else HOME
        self.go_to(lon, lat, zoom)
        self.home = pl.camera_position
        # Terrain style turns the globe about its axis and tilts it,
        # never rolls it: north stays up while dragging.
        pl.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)
        pl.add_key_event("Right", lambda: self.step(1))
        pl.add_key_event("Left", lambda: self.step(-1))
        pl.add_key_event("Up", lambda: self.step(6))
        pl.add_key_event("Down", lambda: self.step(-6))
        pl.add_key_event("h", self.go_home)
        pl.add_key_event("n", self.north_up)
        self.show_time()
        if not args.screenshot:
            threading.Thread(target=self._prepare_all, daemon=True).start()

    # ---- view ----
    def go_to(self, lon, lat, zoom):
        self.pl.view_poi(x=lon, y=lat)
        self.pl.camera.zoom(zoom)
        self.north_up(render=False)

    def go_home(self):
        self.pl.camera_position = self.home
        self.pl.render()

    def north_up(self, render=True):
        cam = self.pl.camera
        cam.up = (0.0, 0.0, 1.0)
        cam.OrthogonalizeViewUp()
        if render:
            self.pl.render()

    # ---- time ----
    def step(self, n, render=True):
        self.i = int(np.clip(self.i + n, 0, len(self.times) - 1))
        self.show_time()
        if render:
            self.pl.render()

    def _prepare(self, t):
        """Everything one hour needs on screen, from the raw values."""
        glo = _classes(_values(self.glo_rain[t],
                               "lwe_thickness_of_precipitation_amount",
                               1000.0))
        ukv = None
        if self.ukv is not None and t in self.ukv_rain:
            ukv = _classes(_values(self.ukv_rain[t],
                                   "lwe_thickness_of_precipitation_amount",
                                   1000.0))
        hpa = _values(self.glo_mslp[t], "air_pressure_at_sea_level", 0.01)
        isobars = isobar_mesh(self.lon, self.lat, hpa)
        ready = dict(glo=glo, ukv=ukv, isobars=isobars,
                     labels=isobar_labels(isobars))
        with self.cache_lock:
            self.cache[t] = ready
        return ready

    def _prepare_all(self):
        while True:
            with self.cache_lock:
                todo = [k for k in range(len(self.times))
                        if self.times[k] not in self.cache]
            if not todo:
                return
            k = min(todo, key=lambda k: (abs(k - self.i), k < self.i))
            try:
                self._prepare(self.times[k])
            except Exception as exc:            # keep the window usable
                print("Could not prepare", self.times[k], exc)
                return

    def show_time(self):
        pl, t = self.pl, self.times[self.i]
        with self.cache_lock:
            ready = self.cache.get(t)
        if ready is None:
            ready = self._prepare(t)
        self.glo.cell_data["rain"] = _shown(ready["glo"])
        ukv_text = "UKV: no data"
        if self.ukv is not None:
            if ready["ukv"] is not None:
                self.ukv.cell_data["rain"] = _shown(ready["ukv"])
                ukv_text = f"UKV {self.ukv_run} T+{self._lead(t, self.ukv_run)}"
            else:
                self.ukv.cell_data["rain"][:] = np.nan
                self.ukv.Modified()

        isobars = ready["isobars"]
        for actor in self.dynamic:
            pl.remove_actor(actor, render=False)
        self.dynamic = []
        if isobars.n_points:
            self.dynamic.append(pl.add_mesh(isobars, color="#1a1a1a",
                                            line_width=1.5, zlevel=6))
        pts, labels = ready["labels"]
        if pts is not None:
            self.dynamic.append(pl.add_point_labels(
                pts, labels, font_size=11, text_color="#1a1a1a",
                shape=None, show_points=False, always_visible=False,
                render=False))

        pl.add_text(f"Hourly rainfall, valid {t:%a %d %b %H:%M} UTC\n"
                    f"{ukv_text} (on top), Global {self.glo_run} "
                    f"T+{self._lead(t, self.glo_run)}; "
                    "Global MSLP isobars every 4 hPa\n"
                    "Native model cells, no resampling. Prototype.",
                    position="upper_left", font_size=10, color="white",
                    name="title")

    @staticmethod
    def _lead(t, run):
        return int((t - _stamp(run)) / timedelta(hours=1))


def main():
    global ARCHIVE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=ARCHIVE,
                    help="download archive folder (default: %(default)s)")
    ap.add_argument("--ukv-run", help="default: newest in the archive")
    ap.add_argument("--global-run", help="default: newest in the archive")
    ap.add_argument("--valid", help="start time, e.g. 20261116T0300Z "
                    "(default: first hour both models cover)")
    ap.add_argument("--base", choices=["grey", "colour"], default="grey")
    ap.add_argument("--view", choices=["ireland", "atlantic"],
                    default="ireland")
    ap.add_argument("--steps", type=int, default=0,
                    help="hours to step before a screenshot (testing)")
    ap.add_argument("--screenshot", help="write a PNG and exit")
    args = ap.parse_args()
    ARCHIVE = args.archive

    g = Globe(args)
    if args.steps:
        g.step(args.steps, render=False)
    if args.screenshot:
        g.pl.show(screenshot=args.screenshot)
        print("wrote", args.screenshot, "valid", g.times[g.i])
    else:
        g.pl.show()


if __name__ == "__main__":
    main()
