"""The globe itself: data, layers and drawing, with no window code.

Each model is drawn on its own native cells: every cell is a quad built
from the file's cell bounds (UKV in its Lambert equal-area metres
turned into longitude/latitude by pyproj, Global straight from its
latitude/longitude bounds). Nothing is resampled; the colour of a cell
comes from the value in the file. Isobars are contoured on each model's
own grid; wind barbs are drawn at a subset of UKV cell centres with
those cells' own speed and direction.

``Scene`` works with any GeoVista plotter: the Qt window in ``globe.py``
or an off-screen plotter for screenshots.
"""

from __future__ import annotations

import glob
import os
import shutil
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
CACHE = os.path.join(HERE, "cache")
CACHE_VERSION = 3                   # bump when prepared data changes
KEEP_CACHED_RUNS = 6

RAIN = ("precipitation_accumulation", "lwe_thickness_of_precipitation_amount")
MSLP = ("pressure_at_mean_sea_level", "air_pressure_at_sea_level")
WIND = ("wind_speed_at_10m", "wind_speed")
WDIR = ("wind_direction_at_10m", "wind_from_direction")
GUST = ("wind_gust_at_10m", "wind_speed_of_gust")

RAIN_BOUNDS = [0.1, 0.5, 1, 2, 4, 8, 16, np.inf]
RAIN_COLOURS = ["#9be564", "#2fb344", "#fde725", "#fd9a2a", "#e8322a",
                "#c51b8a", "#7a0177"]
# Wind bands and colours as the UKMO NWP viewer's wind and gust layers
# (km/h; the gust bands are the products' gust warning bands).
WIND_COLOURS = ["#FFFFFF", "#BFEFFF", "#73D0FF", "#FFF200", "#FFA500",
                "#EE1111", "#8B0015"]
WIND_BOUNDS = [0, 20, 40, 55, 65, 80, 100, 130]
GUST_BOUNDS = [0, 30, 60, 90, 110, 130, 160, 200]
ISOBARS = np.arange(940, 1061, 4)

# Every layer, bottom to top. "z" lifts a layer off the globe so the
# order holds; "model" says which run it comes from.
LAYERS = {
    "Global rain": dict(kind="raster", model="global", source=RAIN,
                        scale=1000.0, bounds=RAIN_BOUNDS,
                        colours=RAIN_COLOURS, clear_below=0.1,
                        units="mm in the hour", z=2, visible=True,
                        opacity=1.0),
    "UKV wind speed": dict(kind="raster", model="ukv", source=WIND,
                           scale=3.6, bounds=WIND_BOUNDS,
                           colours=WIND_COLOURS, clear_below=None,
                           units="km/h, 10 m mean", z=3, visible=False,
                           opacity=0.76),
    "UKV gust": dict(kind="raster", model="ukv", source=GUST, scale=3.6,
                     bounds=GUST_BOUNDS, colours=WIND_COLOURS,
                     clear_below=None, units="km/h, 10 m gust", z=3.5,
                     visible=False, opacity=0.76),
    "UKV rain": dict(kind="raster", model="ukv", source=RAIN, scale=1000.0,
                     bounds=RAIN_BOUNDS, colours=RAIN_COLOURS,
                     clear_below=0.1, units="mm in the hour", z=4,
                     visible=True, opacity=1.0),
    "Global MSLP": dict(kind="isobars", model="global", colour="#1a1a1a",
                        z=6, visible=True, opacity=1.0),
    "UKV MSLP": dict(kind="isobars", model="ukv", colour="#1f4fbf", z=7,
                     visible=True, opacity=1.0),
    "UKV wind barbs": dict(kind="barbs", model="ukv", colour="#202020",
                           z=9, visible=False, opacity=1.0),
}
BARB_STRIDE = 24                    # every 24th UKV cell, about 48 km
BARB_LENGTH = 0.0055                # globe radii, about 35 km

HOME = (-8.0, 53.4, 7.0)            # lon, lat, zoom
ATLANTIC = (-15.0, 52.0, 2.4)
# Global area drawn: (west, east, south, north) in degrees, or None for
# the whole world. A block of the native cells, never resampled.
AREAS = {"atlantic": (-80.0, 50.0, 15.0, 80.0), "world": None}

# Line points are made on the globe already, so GeoVista's "no CRS"
# warning for them means nothing.
warnings.filterwarnings("ignore", message="geovista found no coordinate")
# NetCDF/HDF5 reading is not safe from two threads at once.
READ_LOCK = threading.Lock()
CLEAR = 255                         # class code: see-through


def stamp(text):
    return datetime.strptime(text, "%Y%m%dT%H%MZ")


def lead_hours(t, run):
    return int((t - stamp(run)) / timedelta(hours=1))


def _index(model, run, param):
    """{valid time: file} for one run and parameter. The valid time is
    the first part of each file name."""
    out = {}
    for path in glob.glob(os.path.join(ARCHIVE, model, run, param, "*.nc")):
        out[stamp(os.path.basename(path).split("-")[0])] = path
    return out


def latest_run(model):
    try:
        runs = sorted(os.listdir(os.path.join(ARCHIVE, model)))
    except OSError:
        runs = []
    if not runs:
        raise SystemExit(f"No {model} runs in {ARCHIVE}")
    return runs[-1]


def _values(path, name, scale):
    """Raw values from one file (scaled to mm, km/h or hPa only)."""
    with READ_LOCK, xr.open_dataset(path) as ds:
        return ds[name].values * scale


def classes(values, bounds, clear_below=None):
    """Values -> colour class as bytes; missing values, and values below
    ``clear_below``, become CLEAR (see-through). Stored small so every
    hour can be kept in memory."""
    n = len(bounds) - 1
    out = np.clip(np.digitize(values, bounds[1:-1]), 0, n - 1)
    out = out.astype(np.uint8)
    clear = ~np.isfinite(values)
    if clear_below is not None:
        clear |= values < clear_below
    out[clear] = CLEAR
    return out.ravel()


def shown(cls):
    """Class bytes -> what the mesh draws; CLEAR becomes NaN."""
    out = cls.astype(np.float32)
    out[cls == CLEAR] = np.nan
    return out


def xyz(lon, lat):
    """Longitude/latitude -> points on GeoVista's unit globe."""
    lam, phi = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(phi) * np.cos(lam),
                            np.cos(phi) * np.sin(lam),
                            np.sin(phi)]).astype(np.float32)


def isobar_mesh(x, y, hpa, to_lonlat=None, wrap=False):
    """Isobars as lines on the globe, contoured on the model's own grid.

    ``x``/``y`` are the cell centres (degrees, or projection metres with
    ``to_lonlat`` turning line points into longitude/latitude). With
    ``wrap`` the first column is repeated at the end so lines close
    across the date line (whole-world Global)."""
    if wrap:
        x = np.append(x, x[0] + 360.0)
        hpa = np.column_stack([hpa, hpa[:, 0]])
    gen = contourpy.contour_generator(x, y, hpa, line_type="Separate")
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
    lon, lat = (to_lonlat(xy[:, 0], xy[:, 1]) if to_lonlat
                else (xy[:, 0], xy[:, 1]))
    mesh = pv.PolyData(xyz(lon, lat), lines=np.concatenate(cells))
    mesh.point_data["hPa"] = np.concatenate(vals)
    return mesh


def isobar_labels(isobars, spacing=0.12):
    """Isobar values at points along the lines, about one per
    ``spacing``-sized box (globe radii; 0.12 is about 750 km), lifted
    just off the globe so the layers under them do not hide them."""
    if isobars.n_points == 0:
        return None, []
    pts = isobars.points
    hpa = isobars.point_data["hPa"]
    box = np.floor(pts / spacing).astype(np.int64)
    off = np.sum((pts / spacing - box - 0.5) ** 2, axis=1)
    key = np.column_stack([box, np.rint(hpa).astype(np.int64)])
    order = np.lexsort((off, *key.T[::-1]))
    _, first = np.unique(key[order], axis=0, return_index=True)
    kept = order[first]
    # Boxes in a chequerboard only, so neighbours' labels do not touch.
    kept = kept[np.sum(box[kept], axis=1) % 2 == 0]
    return pts[kept] * 1.004, [f"{hpa[i]:.0f}" for i in kept]


def _groups(counts):
    """For each item repeated counts[i] times: (item index, 0..n-1)."""
    idx = np.repeat(np.arange(counts.size), counts)
    starts = np.repeat(np.cumsum(counts) - counts, counts)
    return idx, np.arange(idx.size) - starts


def barb_mesh(lon, lat, knots, from_deg, length=BARB_LENGTH):
    """Standard wind barbs as lines on the globe (all barbs at once).

    The staff points to where the wind comes from; feathers sit on its
    clockwise side (northern hemisphere convention): a pennant for 50
    knots, a full feather for 10, a half for 5. Below 2.5 knots nothing
    is drawn."""
    ok = np.isfinite(knots) & np.isfinite(from_deg) & (knots >= 2.5)
    if not ok.any():
        return pv.PolyData()
    lam, phi = np.radians(lon[ok]), np.radians(lat[ok])
    d = np.radians(from_deg[ok])[:, None]
    east = np.column_stack([-np.sin(lam), np.cos(lam), np.zeros_like(lam)])
    north = np.column_stack([-np.sin(phi) * np.cos(lam),
                             -np.sin(phi) * np.sin(lam), np.cos(phi)])
    s = np.sin(d) * east + np.cos(d) * north     # towards the source
    side = np.cos(d) * east - np.sin(d) * north  # clockwise of s
    base = xyz(lon[ok], lat[ok]).astype(float) * 1.002
    tip = base + length * s
    feather = 0.38 * length * side + 0.12 * length * s

    r = (5 * np.rint(knots[ok] / 5.0)).astype(int)
    P, F, H = r // 50, (r % 50) // 10, (r % 10) >= 5
    a, b = [base], [tip]                          # staffs
    i, k = _groups(P)                              # pennants
    pos = tip[i] - (0.2 * length * k)[:, None] * s[i]
    corner = pos + 0.38 * length * side[i]
    a += [pos, corner]
    b += [corner, pos - 0.16 * length * s[i]]
    i, k = _groups(F)                              # full feathers
    pos = tip[i] - ((0.2 * P[i] + 0.13 * k) * length)[:, None] * s[i]
    a.append(pos)
    b.append(pos + feather[i])
    i = np.flatnonzero(H)                          # half feathers
    k = F[i] + ((P[i] == 0) & (F[i] == 0))         # a lone half sits in
    pos = tip[i] - ((0.2 * P[i] + 0.13 * k) * length)[:, None] * s[i]
    a.append(pos)
    b.append(pos + 0.5 * feather[i])
    a, b = np.concatenate(a), np.concatenate(b)
    pts = np.empty((2 * len(a), 3), np.float32)
    pts[0::2], pts[1::2] = a, b
    n = len(a)
    lines = np.column_stack([np.full(n, 2), 2 * np.arange(n),
                             2 * np.arange(n) + 1]).ravel()
    return pv.PolyData(pts, lines=lines)


class UkvGrid:
    """The UKV grid: cell edges and centres, and its projection."""

    def __init__(self, path):
        with xr.open_dataset(path) as ds:
            var = next(ds[k] for k in ds.data_vars if ds[k].ndim == 2)
            crs = CRS.from_cf(ds[var.attrs["grid_mapping"]].attrs)
            xb = ds["projection_x_coordinate_bnds"].values
            yb = ds["projection_y_coordinate_bnds"].values
            self.x = ds["projection_x_coordinate"].values
            self.y = ds["projection_y_coordinate"].values
        self.transform = Transformer.from_crs(crs, "EPSG:4326",
                                              always_xy=True).transform
        self.x_edges = np.append(xb[:, 0], xb[-1, 1])
        self.y_edges = np.append(yb[:, 0], yb[-1, 1])
        # Barb positions: a regular subset of cell centres.
        self.barb_rows = slice(BARB_STRIDE // 2, None, BARB_STRIDE)
        self.barb_cols = slice(BARB_STRIDE // 2, None, BARB_STRIDE)
        bx, by = np.meshgrid(self.x[self.barb_cols], self.y[self.barb_rows])
        self.barb_lon, self.barb_lat = self.transform(bx.ravel(), by.ravel())
        self._mesh = None

    def mesh(self):
        if self._mesh is None:
            xx, yy = np.meshgrid(self.x_edges, self.y_edges)
            lon, lat = self.transform(xx, yy)
            shape = (self.y.size, self.x.size)
            self._mesh = gv.Transform.from_2d(
                lon, lat, data=np.full(shape, np.nan), name="value")
        return self._mesh.copy(deep=True)

    def cut(self, values):
        return values

    def barbs(self, values):
        return values[self.barb_rows, self.barb_cols].ravel()


class GlobalGrid:
    """The rows and columns of the Global grid inside the chosen area."""

    def __init__(self, path, area):
        with xr.open_dataset(path) as ds:
            lonb = ds["longitude_bnds"].values
            latb = ds["latitude_bnds"].values
            lon = ds["longitude"].values
            lat = ds["latitude"].values
        self.world = area is None
        if self.world:
            rows = np.arange(lat.size)
            cols = np.arange(lon.size)
        else:
            west, east, south, north = area
            rows = np.flatnonzero((latb[:, 1] > south) & (latb[:, 0] < north))
            cols = np.flatnonzero((lonb[:, 1] > west) & (lonb[:, 0] < east))
        # Contiguous blocks, so a plain slice picks the cells out.
        self.rows = slice(rows[0], rows[-1] + 1)
        self.cols = slice(cols[0], cols[-1] + 1)
        self.x, self.y = lon[self.cols], lat[self.rows]
        lb, tb = lonb[self.cols], latb[self.rows]
        self.lon_edges = np.append(lb[:, 0], lb[-1, 1])
        self.lat_edges = np.append(tb[:, 0], tb[-1, 1])
        self.transform = None

    def cut(self, values):
        return values[self.rows, self.cols]

    def mesh(self):
        shape = (self.y.size, self.x.size)
        return gv.Transform.from_1d(self.lon_edges, self.lat_edges,
                                    data=np.full(shape, np.nan),
                                    name="value")


def grey_texture():
    """Natural Earth I shaded relief, in light grey so coloured
    forecast layers stand out. Made once and saved."""
    saved = os.path.join(CACHE, "grey_base.npy")
    try:
        return pv.Texture(np.load(saved))
    except (OSError, ValueError):
        pass
    rgb = gv.natural_earth_1().to_array()[..., :3].astype(float)
    grey = rgb @ [0.299, 0.587, 0.114]
    grey = 95.0 + grey * 0.6                     # lighter, less contrast
    image = np.dstack([grey] * 3).astype(np.uint8)
    os.makedirs(CACHE, exist_ok=True)
    np.save(saved, image)
    return pv.Texture(image)


# ---- prepared hours on disk -------------------------------------------

def _key(name):
    return name.replace(" ", "_")


def _save_hour(path, ready):
    out = {}
    for name, value in ready.items():
        k = _key(name)
        if value is None:
            continue
        if LAYERS[name]["kind"] == "raster":
            out[f"r_{k}"] = value
            continue
        mesh, (pts, txt) = value
        out[f"p_{k}"] = np.asarray(mesh.points, dtype=np.float32)
        out[f"l_{k}"] = np.asarray(mesh.lines, dtype=np.int32)
        out[f"v_{k}"] = np.asarray(mesh.point_data.get("hPa", []),
                                   dtype=np.float32)
        out[f"lp_{k}"] = (np.zeros((0, 3), np.float32) if pts is None
                          else pts.astype(np.float32))
        out[f"lt_{k}"] = np.array(txt, dtype="U8")
    tmp = path + ".part.npz"
    np.savez(tmp, **out)
    os.replace(tmp, path)


def _load_hour(path):
    ready = {}
    with np.load(path) as f:
        for name, spec in LAYERS.items():
            k = _key(name)
            if spec["kind"] == "raster":
                ready[name] = f[f"r_{k}"] if f"r_{k}" in f else None
                continue
            if f"p_{k}" not in f:
                ready[name] = None
                continue
            mesh = pv.PolyData()
            if f[f"p_{k}"].size:
                mesh = pv.PolyData(f[f"p_{k}"], lines=f[f"l_{k}"])
                if f[f"v_{k}"].size:
                    mesh.point_data["hPa"] = f[f"v_{k}"]
            pts = f[f"lp_{k}"]
            ready[name] = (mesh, (pts if len(pts) else None,
                                  list(f[f"lt_{k}"])))
    return ready


def _prune_cache():
    """Keep the newest few runs' prepared hours."""
    try:
        runs = [d for d in os.scandir(CACHE) if d.is_dir()]
    except OSError:
        return
    runs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for d in runs[KEEP_CACHED_RUNS:]:
        shutil.rmtree(d.path, ignore_errors=True)


class Scene:
    """All layers on one GeoVista plotter, stepped through valid times."""

    def __init__(self, pl, ukv_run=None, global_run=None, valid=None,
                 area="atlantic", base="grey", view="ireland"):
        self.pl = pl
        self.ukv_run = ukv_run or latest_run("ukv")
        self.glo_run = global_run or latest_run("global10km")
        self.files = {}
        for name, spec in LAYERS.items():
            run = self.ukv_run if spec["model"] == "ukv" else self.glo_run
            model = "ukv" if spec["model"] == "ukv" else "global10km"
            if spec["kind"] == "raster":
                self.files[name] = {"v": _index(model, run, spec["source"][0])}
            elif spec["kind"] == "isobars":
                self.files[name] = {"v": _index(model, run, MSLP[0])}
            else:
                self.files[name] = {"v": _index(model, run, WIND[0]),
                                    "d": _index(model, run, WDIR[0])}
        glo = self.files["Global rain"]["v"]
        self.times = sorted(set(glo) & set(self.files["Global MSLP"]["v"]))
        if not self.times:
            raise SystemExit(f"No Global rain/MSLP in {self.glo_run}")
        ukv_rain = self.files["UKV rain"]["v"]
        if valid:
            start = stamp(valid)
        else:
            both = [t for t in self.times if t in ukv_rain]
            start = both[0] if both else self.times[0]
        if start not in self.times:
            raise SystemExit(f"No Global data valid {valid}")
        self.i = self.times.index(start)

        any_ukv = next((f for n, s in LAYERS.items() if s["model"] == "ukv"
                        for f in self.files[n]["v"].values()), None)
        self.grids = {"global": GlobalGrid(glo[self.times[0]], AREAS[area]),
                      "ukv": UkvGrid(any_ukv) if any_ukv else None}
        self.area = area

        self.visible = {n: s["visible"] for n, s in LAYERS.items()}
        self.opacity = {n: s["opacity"] for n, s in LAYERS.items()}
        self.actors = {n: [] for n in LAYERS}
        # Every hour is prepared in the background, nearest to the
        # shown hour first, so stepping only swaps in ready data.
        # Prepared hours are also saved, so reopening a run is quick.
        self.cache = {}
        self.claimed = set()
        self.lock = threading.Lock()
        self.disk = os.path.join(
            CACHE, f"v{CACHE_VERSION}_{self.glo_run}_{self.ukv_run}_{area}")
        os.makedirs(self.disk, exist_ok=True)
        os.utime(self.disk)                     # newest for pruning
        _prune_cache()

        # GeoVista checks the graphics card's opacity support (about
        # 1.4 s, by starting a program) on every add_mesh that sets an
        # opacity, including nan_opacity. Once per window is enough.
        check = getattr(pl, "_warn_opacity", None)
        if check is not None:
            done = []

            def check_once():
                if not done:
                    done.append(True)
                    check()
            pl._warn_opacity = check_once
        pl.set_background("#0b1020")
        texture = gv.natural_earth_1() if base == "colour" else grey_texture()
        pl.add_base_layer(texture=texture, zlevel=0)
        # Raster layers keep one mesh each; a time step only changes the
        # class values on it. Opacity is set on the actor afterwards:
        # passing it to GeoVista's add_mesh starts a slow graphics-card
        # check (about 1.4 s) every time.
        for name, spec in LAYERS.items():
            grid = self.grids[spec["model"]]
            if spec["kind"] != "raster" or grid is None:
                continue
            n = len(spec["colours"])
            actor = pl.add_mesh(grid.mesh(), zlevel=spec["z"],
                                scalars="value", cmap=spec["colours"],
                                clim=(-0.5, n - 0.5), n_colors=n,
                                nan_opacity=0.0, show_scalar_bar=False)
            _set_opacity(actor, spec["opacity"])
            self.actors[name] = [actor]
            actor.SetVisibility(self.visible[name])
        pl.add_coastlines(color="black", line_width=1, zlevel=8)

        lon, lat, zoom = ATLANTIC if view == "atlantic" else HOME
        self.go_to(lon, lat, zoom)
        self.home = pl.camera_position
        # Terrain style turns the globe about its axis and tilts it,
        # never rolls it: north stays up while dragging.
        pl.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)
        self.show()

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

    # ---- layers ----
    def available(self, name):
        return (self.grids[LAYERS[name]["model"]] is not None
                and bool(self.files[name]["v"]))

    def set_visible(self, name, on, render=True):
        self.visible[name] = bool(on)
        for actor in self.actors[name]:
            actor.SetVisibility(bool(on))
        if render:
            self.pl.render()

    def set_opacity(self, name, value, render=True):
        self.opacity[name] = float(value)
        for actor in self.actors[name]:
            _set_opacity(actor, value)
        if render:
            self.pl.render()

    # ---- time ----
    @property
    def time(self):
        return self.times[self.i]

    def is_ready(self, i):
        with self.lock:
            return self.times[i] in self.cache

    def n_ready(self):
        with self.lock:
            return len(self.cache)

    def go(self, i, render=True):
        self.i = int(np.clip(i, 0, len(self.times) - 1))
        self.show()
        if render:
            self.pl.render()

    def step(self, n, render=True):
        self.go(self.i + n, render)

    def start_workers(self, n=2):
        # File reading takes turns; the rest of the work overlaps.
        for _ in range(n):
            threading.Thread(target=self._prepare_all, daemon=True).start()

    def _prepare(self, t):
        """Everything one hour needs on screen, from the raw values."""
        saved = os.path.join(self.disk, f"{t:%Y%m%dT%H%MZ}.npz")
        try:
            ready = _load_hour(saved)
        except (OSError, KeyError, ValueError):
            ready = self._compute(t)
            try:
                _save_hour(saved, ready)
            except OSError as exc:
                print("Could not save", saved, exc)
        with self.lock:
            self.cache[t] = ready
        return ready

    def _compute(self, t):
        ready = {}
        for name, spec in LAYERS.items():
            grid = self.grids[spec["model"]]
            files = self.files[name]
            if grid is None or t not in files["v"]:
                ready[name] = None
                continue
            if spec["kind"] == "raster":
                var, scale = spec["source"][1], spec["scale"]
                v = grid.cut(_values(files["v"][t], var, scale))
                ready[name] = classes(v, spec["bounds"], spec["clear_below"])
            elif spec["kind"] == "isobars":
                hpa = grid.cut(_values(files["v"][t], MSLP[1], 0.01))
                iso = isobar_mesh(grid.x, grid.y, hpa,
                                  to_lonlat=grid.transform,
                                  wrap=getattr(grid, "world", False))
                ready[name] = (iso, isobar_labels(iso))
            elif t in files["d"]:
                kt = grid.barbs(_values(files["v"][t], WIND[1], 1.943844))
                dd = grid.barbs(_values(files["d"][t], WDIR[1], 1.0))
                ready[name] = (barb_mesh(grid.barb_lon, grid.barb_lat,
                                         kt, dd), (None, []))
            else:
                ready[name] = None
        return ready

    def _prepare_all(self):
        while True:
            with self.lock:
                todo = [k for k in range(len(self.times))
                        if self.times[k] not in self.cache
                        and self.times[k] not in self.claimed]
                if not todo:
                    return
                k = min(todo, key=lambda k: (abs(k - self.i), k < self.i))
                self.claimed.add(self.times[k])
            try:
                self._prepare(self.times[k])
            except Exception as exc:            # keep the window usable
                print("Could not prepare", self.times[k], exc)
                return

    def show(self):
        pl, t = self.pl, self.time
        with self.lock:
            ready = self.cache.get(t)
        if ready is None:
            ready = self._prepare(t)
        for name, spec in LAYERS.items():
            value = ready.get(name)
            if spec["kind"] == "raster":
                for actor in self.actors[name]:
                    mesh = actor.mapper.dataset
                    if value is None:
                        mesh.cell_data["value"][:] = np.nan
                        mesh.Modified()
                    else:
                        mesh.cell_data["value"] = shown(value)
                continue
            for actor in self.actors[name]:
                pl.remove_actor(actor, render=False)
            self.actors[name] = []
            if value is None:
                continue
            mesh, (pts, labels) = value
            if mesh.n_points:
                self.actors[name].append(pl.add_mesh(
                    mesh, color=spec["colour"], zlevel=spec["z"],
                    line_width=1.5 if spec["kind"] == "isobars" else 1.2))
            if pts is not None:
                self.actors[name].append(pl.add_point_labels(
                    pts, labels, font_size=11, text_color=spec["colour"],
                    shape=None, show_points=False, always_visible=False,
                    render=False))
            for actor in self.actors[name]:
                actor.SetVisibility(self.visible[name])
                _set_opacity(actor, self.opacity[name])
        pl.add_text(self.caption(), position="upper_left", font_size=10,
                    color="white", name="title")

    def caption(self):
        t = self.time
        ukv = (f"UKV {self.ukv_run} T+{lead_hours(t, self.ukv_run)}"
               if t in self.files["UKV rain"]["v"] else "UKV: no data")
        return (f"Valid {t:%a %d %b %H:%M} UTC\n{ukv}, Global "
                f"{self.glo_run} T+{lead_hours(t, self.glo_run)}\n"
                "Native model cells, no resampling. Prototype.")


def _set_opacity(actor, value):
    """Opacity for a mesh actor or a label actor."""
    try:
        actor.GetProperty().SetOpacity(float(value))
    except AttributeError:
        try:
            actor.GetMapper().GetLabelTextProperty().SetOpacity(float(value))
        except AttributeError:
            pass
