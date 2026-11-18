"""The globe itself: data, layers and drawing, with no window code.

Each model is drawn on its own native cells: every cell is a quad built
from the file's cell bounds (UKV in its Lambert equal-area metres
turned into longitude/latitude by pyproj, Global straight from its
latitude/longitude bounds). Nothing is resampled; the colour of a cell
comes from the value in the file (or a sum of the model's own files for
rainfall windows). Isobars and high/low centres are found on each
model's own grid; wind barbs are drawn at a subset of cell centres with
those cells' own speed and direction.

Every layer exists once per model ("ukv:rain_6h", "global:rain_6h"),
with the UKMO NWP viewer's bands, colours and default opacity. Layers
are prepared only while switched on, in background threads, nearest
hours first; Global files that are not on disk are downloaded then.

``Scene`` works with any GeoVista plotter: the Qt window in ``globe.py``
or an off-screen plotter for screenshots.
"""

from __future__ import annotations

import os
import shutil
import threading
import warnings
from datetime import datetime, timedelta, timezone

import contourpy
import geovista as gv
import numpy as np
import pyvista as pv
import xarray as xr
from pyproj import CRS, Transformer

import globe_data as data
from globe_pressure import find_pressure_centres

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
CACHE_VERSION = 7                   # bump when prepared data changes

RAIN = "precipitation_accumulation"
MSLP = "pressure_at_mean_sea_level"
WIND = "wind_speed_at_10m"
WDIR = "wind_direction_at_10m"
ISOBARS = np.arange(940, 1061, 4)
MS_TO_KMH = 3.6
MS_TO_KT = 1.943844

_RAIN_A = ["#BFEFFF", "#3FA7FF", "#244BFF", "#28D978", "#00A930", "#FFF200",
           "#FF9900", "#E51C23"]
_WIND = ["#FFFFFF", "#BFEFFF", "#73D0FF", "#FFF200", "#FFA500", "#EE1111",
         "#8B0015"]
# The viewer's layers (ukmo_nwp config.yaml forecast_layers), bottom to
# top as its zorder. "levels" has one more value than "colours".
STYLES = {
    "rain_1h": dict(name="1-hour rainfall", kind="raster", hours=1,
                    units="mm", levels=[0.2, 0.5, 1, 2, 4, 8, 16, 32, 64],
                    colours=_RAIN_A, clear_below=0.2, opacity=0.88),
    "rain_3h": dict(name="3-hour rainfall", kind="raster", hours=3,
                    units="mm", levels=[0.5, 1, 2, 4, 8, 12, 16, 24, 40],
                    colours=_RAIN_A, clear_below=0.5, opacity=0.88),
    "rain_6h": dict(name="6-hour rainfall", kind="raster", hours=6,
                    units="mm", levels=[1, 2, 4, 8, 12, 16, 24, 32, 50],
                    colours=["#2B55FF", "#4BEB84", "#00C900", "#EFFE00",
                             "#FFFF8B", "#B44BC4", "#FF33FE", "#FF0000"],
                    clear_below=1, opacity=0.88, visible=True),
    "rain_run": dict(name="Run-total rainfall", kind="raster", hours="run",
                     units="mm", levels=[1, 5, 10, 20, 40, 60, 80, 120, 180],
                     colours=["#dce3e7", "#a3c9e5", "#4696cc", "#0096a1",
                              "#99d5ce", "#619b3d", "#ecac62", "#da5863"],
                     clear_below=1, opacity=0.88),
    "rain_24h": dict(name="24-hour rainfall", kind="raster", hours=24,
                     units="mm", levels=[1, 5, 10, 20, 30, 40, 50, 80, 120],
                     colours=["#BFEFFF", "#5AC8FA", "#2878FF", "#33CC66",
                              "#FFF200", "#FFAA00", "#FF4B2B", "#B00020"],
                     clear_below=1, opacity=0.88),
    "cape": dict(name="Most-unstable CAPE", kind="raster",
                 param="CAPE_most_unstable_below_500hPa", units="J/kg",
                 levels=[50, 300, 600, 900, 1200, 1500, 1800, 2100, 2400,
                         3000],
                 colours=["#565175", "#020183", "#0002FD", "#00A3FF",
                          "#00FCEA", "#0DAA52", "#02FC02", "#E4FF00",
                          "#EB0000"], clear_below=50, opacity=0.82),
    "temperature": dict(name="Screen temperature", kind="raster",
                        param="temperature_at_screen_level", offset=-273.15,
                        units="°C",
                        levels=[-15, -10, -5, 0, 5, 10, 15, 20, 25, 30, 35],
                        colours=["#4A148C", "#3949AB", "#2196F3", "#80DEEA",
                                 "#DCE775", "#66BB6A", "#FFEB3B", "#FF9800",
                                 "#F44336", "#880E4F"], opacity=0.82),
    "wind_speed": dict(name="10 m wind speed", kind="raster", param=WIND,
                       scale=MS_TO_KMH, units="km/h",
                       levels=[0, 20, 40, 55, 65, 80, 100, 130],
                       colours=_WIND, opacity=0.76),
    "gust": dict(name="10 m wind gust", kind="raster",
                 param="wind_gust_at_10m", scale=MS_TO_KMH, units="km/h",
                 levels=[0, 30, 60, 90, 110, 130, 160, 200], colours=_WIND,
                 opacity=0.76),
    "mslp": dict(name="MSLP contours", kind="isobars", colour="#1a1a1a",
                 units="hPa", visible=True),
    "barbs": dict(name="10 m wind barbs", kind="barbs", colour="#202020",
                  units="knots"),
    "waves": dict(name="Significant wave height", kind="raster",
                  param="sea_surface_wave_significant_height", units="m",
                  levels=[0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 14],
                  colours=["#dff3ff", "#b7e3fb", "#84cbf2", "#4fa9e3",
                           "#2b7fd0", "#2a9d6f", "#8cc63f", "#f5e33a",
                           "#f7a02c", "#e8452c", "#b51f5b", "#6b1a7a"],
                  opacity=0.85, visible=True),
    "wave_dir": dict(name="Mean wave direction", kind="arrows",
                     param="sea_surface_wave_from_direction",
                     colour="#202020", units="degrees", visible=True),
    "centres": dict(name="High / low centres", kind="centres",
                    units="hPa", visible=True),
}
RASTERS = [k for k, s in STYLES.items() if s["kind"] == "raster"]
CENTRE_COLOURS = {"H": "#1f4fbf", "L": "#d01c1c"}
BARB_STRIDE = {"ukv": 24}      # UKV cells: about 48 km
BARB_KM = 48.0                 # barb spacing on lat/lon grids
ARROW_KM = 30.0                # wave arrow spacing
BARB_LENGTH = 0.0055                           # globe radii, about 35 km

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
PENDING = object()                  # cache marker: not prepared yet


MODEL_ORDER = ["ukv", "harmonie", "global", "ibi"]
WAVES = ("waves", "wave_dir")               # the IBI model's only layers


def needs(style):
    """Parameters a style is drawn from."""
    s = STYLES[style]
    if "hours" in s:
        return [RAIN]
    if s["kind"] in ("raster", "arrows"):
        return [s["param"]]
    if s["kind"] == "barbs":
        return [WIND, WDIR]
    return [MSLP]


def layer_id(model, style):
    return f"{model}:{style}"


def split_id(lid):
    model, style = lid.split(":")
    return model, style


def lead_hours(t, run):
    return int((t - data.stamp(run)) / timedelta(hours=1))


def read_field(path, scale=1.0, offset=0.0):
    """The one 2D field in a file, scaled (to mm, km/h, °C, hPa) only."""
    with READ_LOCK, xr.open_dataset(path) as ds:
        var = next(ds[k] for k in ds.data_vars if ds[k].ndim >= 2)
        return (var.values.astype(np.float32) * scale + offset)


def classes(values, levels, clear_below=None):
    """Values -> colour class as bytes (values beyond the ends take the
    end colours); missing values, and values below ``clear_below``,
    become CLEAR (see-through)."""
    n = len(levels) - 1
    out = np.clip(np.digitize(values, levels[1:-1]), 0, n - 1)
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


class Lines:
    """Line segments as plain arrays (VTK cell layout). Workers make
    these; VTK objects are made only on the window thread (``mesh``),
    because VTK is not safe to use from two threads at once."""

    def __init__(self, points=None, lines=None, hpa=None):
        self.points = (np.zeros((0, 3), np.float32) if points is None
                       else np.asarray(points, np.float32))
        self.lines = (np.zeros(0, np.int64) if lines is None
                      else np.asarray(lines, np.int64))
        self.hpa = None if hpa is None else np.asarray(hpa, np.float32)

    @property
    def n_points(self):
        return len(self.points)

    def mesh(self):
        m = pv.PolyData(self.points.copy(), lines=self.lines.copy())
        if self.hpa is not None:
            m.point_data["hPa"] = self.hpa
        return m


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
        return Lines()
    xy = np.concatenate(pts)
    lon, lat = (to_lonlat(xy[:, 0], xy[:, 1]) if to_lonlat
                else (xy[:, 0], xy[:, 1]))
    return Lines(xyz(lon, lat), np.concatenate(cells), np.concatenate(vals))


def isobar_labels(isobars, spacing=0.12):
    """Isobar values at points along the lines, about one per
    ``spacing``-sized box (globe radii; 0.12 is about 750 km), lifted
    just off the globe so the layers under them do not hide them."""
    if isobars.n_points == 0:
        return None, []
    pts = isobars.points
    hpa = isobars.hpa
    box = np.floor(pts / spacing).astype(np.int64)
    off = np.sum((pts / spacing - box - 0.5) ** 2, axis=1)
    key = np.column_stack([box, np.rint(hpa).astype(np.int64)])
    order = np.lexsort((off, *key.T[::-1]))
    _, first = np.unique(key[order], axis=0, return_index=True)
    kept = order[first]
    # Boxes in a chequerboard only, so neighbours' labels do not touch.
    kept = kept[np.sum(box[kept], axis=1) % 2 == 0]
    return pts[kept] * 1.006, [f"{hpa[i]:.0f}" for i in kept]


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
        return Lines()
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
    return Lines(pts, lines)


def arrow_mesh(lon, lat, from_deg, length=0.004):
    """Arrows on the globe pointing where something goes (the opposite
    of a "from" direction), all the same length."""
    ok = np.isfinite(from_deg)
    if not ok.any():
        return Lines()
    lam, phi = np.radians(lon[ok]), np.radians(lat[ok])
    d = np.radians(from_deg[ok] + 180.0)[:, None]
    east = np.column_stack([-np.sin(lam), np.cos(lam), np.zeros_like(lam)])
    north = np.column_stack([-np.sin(phi) * np.cos(lam),
                             -np.sin(phi) * np.sin(lam), np.cos(phi)])
    s = np.sin(d) * east + np.cos(d) * north     # direction of travel
    side = np.cos(d) * east - np.sin(d) * north
    centre = xyz(lon[ok], lat[ok]).astype(float) * 1.002
    tail, tip = centre - 0.5 * length * s, centre + 0.5 * length * s
    back = tip - 0.35 * length * s
    a = np.concatenate([tail, tip, tip])
    b = np.concatenate([tip, back + 0.2 * length * side,
                        back - 0.2 * length * side])
    pts = np.empty((2 * len(a), 3), np.float32)
    pts[0::2], pts[1::2] = a, b
    n = len(a)
    lines = np.column_stack([np.full(n, 2), 2 * np.arange(n),
                             2 * np.arange(n) + 1]).ravel()
    return Lines(pts, lines)


def _latlon_cut(x, y, km, deg=True):
    """Indices of cells about ``km`` apart on a lon/lat-like grid:
    columns get narrower towards the poles, so each row takes a wider
    column step."""
    dy = abs(y[1] - y[0]) * 111.2
    dx = abs(x[1] - x[0]) * 111.2
    rows, cols = [], []
    for j in range(0, y.size, max(1, round(km / dy))):
        shrink = np.cos(np.radians(y[j])) if deg else 1.0
        step = max(1, round(km / max(dx * shrink, 1e-3)))
        c = np.arange(step // 2, x.size, step)
        rows.append(np.full(c.size, j))
        cols.append(c)
    return np.concatenate(rows), np.concatenate(cols)


class RotatedGrid:
    """A rotated-pole lat/lon grid (HARMONIE), from a stored field."""

    world = False

    def __init__(self, path):
        with xr.open_dataset(path) as ds:
            var = next(ds[k] for k in ds.data_vars if ds[k].ndim >= 2)
            attrs = {k: var.attrs[k] for k in (
                "grid_mapping_name", "grid_north_pole_latitude",
                "grid_north_pole_longitude")}
            self.x = ds["rlon"].values.astype(float)
            self.y = ds["rlat"].values.astype(float)
        self.crs = CRS.from_cf(attrs)
        self.transform = Transformer.from_crs(self.crs, "EPSG:4326",
                                              always_xy=True).transform
        dx, dy = self.x[1] - self.x[0], self.y[1] - self.y[0]
        self.x_edges = np.append(self.x - dx / 2, self.x[-1] + dx / 2)
        self.y_edges = np.append(self.y - dy / 2, self.y[-1] + dy / 2)
        self.barb_cut = _latlon_cut(self.x, self.y, BARB_KM, deg=False)
        self.barb_lon, self.barb_lat = self.transform(
            self.x[self.barb_cut[1]], self.y[self.barb_cut[0]])
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

    def lonlat(self, x, y):
        return self.transform(np.asarray(x), np.asarray(y))


class UkvGrid:
    """The UKV grid: cell edges and centres, and its projection."""

    world = False

    def __init__(self, path):
        with xr.open_dataset(path) as ds:
            var = next(ds[k] for k in ds.data_vars if ds[k].ndim >= 2)
            self.crs = CRS.from_cf(ds[var.attrs["grid_mapping"]].attrs)
            xb = ds["projection_x_coordinate_bnds"].values
            yb = ds["projection_y_coordinate_bnds"].values
            self.x = ds["projection_x_coordinate"].values
            self.y = ds["projection_y_coordinate"].values
        self.transform = Transformer.from_crs(self.crs, "EPSG:4326",
                                              always_xy=True).transform
        self.x_edges = np.append(xb[:, 0], xb[-1, 1])
        self.y_edges = np.append(yb[:, 0], yb[-1, 1])
        s = BARB_STRIDE["ukv"]
        self.barb_cut = (slice(s // 2, None, s), slice(s // 2, None, s))
        bx, by = np.meshgrid(self.x[self.barb_cut[1]],
                             self.y[self.barb_cut[0]])
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

    def lonlat(self, x, y):
        return self.transform(np.asarray(x), np.asarray(y))


class GlobalGrid:
    """The rows and columns of the Global grid inside the chosen area."""

    transform = None
    crs = "EPSG:4326"

    def __init__(self, path, area, wrap=None, km=BARB_KM):
        with xr.open_dataset(path) as ds:
            lonb = ds["longitude_bnds"].values
            latb = ds["latitude_bnds"].values
            lon = ds["longitude"].values
            lat = ds["latitude"].values
        self.world = (area is None) if wrap is None else wrap
        if area is None:
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
        self.barb_cut = _latlon_cut(self.x, self.y, km)
        self.barb_lon = self.x[self.barb_cut[1]]
        self.barb_lat = self.y[self.barb_cut[0]]

    def cut(self, values):
        return values[self.rows, self.cols]

    def lonlat(self, x, y):
        return np.asarray(x), np.asarray(y)

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


# ---- prepared items on disk ---------------------------------------------

def _save_item(path, value):
    if isinstance(value, np.ndarray):
        out = {"raster": value}
    else:
        mesh, (pts, txt) = value
        out = {"points": mesh.points,
               "lines": mesh.lines,
               "hpa": (np.zeros(0, np.float32) if mesh.hpa is None
                       else mesh.hpa),
               "label_pts": (np.zeros((0, 3), np.float32) if pts is None
                             else np.asarray(pts, np.float32)),
               "label_txt": np.array(txt, dtype="U16")}
    tmp = path + ".part.npz"
    np.savez(tmp, **out)
    os.replace(tmp, path)


def _load_item(path):
    with np.load(path) as f:
        if "raster" in f:
            return f["raster"]
        mesh = Lines(f["points"], f["lines"],
                     f["hpa"] if f["hpa"].size else None)
        pts = f["label_pts"]
        return mesh, (pts if len(pts) else None, list(f["label_txt"]))


def _set_opacity(actor, value):
    """Opacity for a mesh actor or a label actor."""
    try:
        actor.GetProperty().SetOpacity(float(value))
    except AttributeError:
        try:
            actor.GetMapper().GetLabelTextProperty().SetOpacity(float(value))
        except AttributeError:
            pass


class LoadedModel:
    """A model's run, files and grid, made off the window thread."""

    def __init__(self, model, run, area="atlantic", archive=None):
        self.model, self.run = model, run
        self.files = data.Files(model, run, archive)
        self.grid = _make_grid(model, self.files, run, area)
        self.styles = [s for s in STYLES
                       if ((model == "ibi") == (s in WAVES))
                       and all(self.files.expected(p) for p in needs(s))]


def _make_grid(model, files, run, area):
    params = {"ibi": [STYLES["waves"]["param"]]}.get(model,
                                                     [MSLP, RAIN, WIND])
    path = None
    for param in params:
        held = files.held(param)
        if held:
            path = next(iter(held.values()))
            break
    if path is None and files.can_download:
        path = files.path(MSLP, data.stamp(run))       # one small file
    if path is None:
        raise RuntimeError(f"No {data.MODELS[model]['label']} files for "
                           f"{run}")
    if model == "ukv":
        return UkvGrid(path)
    if model == "harmonie":
        return RotatedGrid(path)
    if model == "global":
        return GlobalGrid(path, AREAS[area])
    return GlobalGrid(path, None, wrap=False, km=ARROW_KM)


def load_model(model, run=None, area="atlantic", archive=None):
    """Find the latest run (Met Office models) and build the model; for
    HARMONIE and IBI the run must already be fetched into the session."""
    if run is None:
        if data.MODELS[model]["prefix"]:
            run = data.latest_run(model)
        else:
            runs = data.held_runs(model)
            run = runs[-1] if runs else None
    if run is None:
        raise RuntimeError(f"No {data.MODELS[model]['label']} run found")
    return LoadedModel(model, run, area, archive)


# Value labels: format per layer, and at most this many on screen.
LABEL_FORMAT = {"rain_1h": ".1f", "rain_3h": ".1f", "rain_6h": ".1f",
                "rain_24h": ".0f", "rain_run": ".0f", "waves": ".1f"}
MAX_LABELS = 400
LABEL_KM = 10.0
EARTH_KM = 6371.0


class Scene:
    """All loaded models' layers on one GeoVista plotter."""

    def __init__(self, pl, area="atlantic", base="grey", view="ireland"):
        self.pl = pl
        self.area = area
        self.runs, self.files, self.grids, self.styles = {}, {}, {}, {}
        self.models, self.order = [], []      # loaded; order top first
        self.times = []
        self.i = 0
        self.layers = []
        self.visible, self.opacity, self.model_on = {}, {}, {}
        self.label_on = {}
        self.rasters = {}                       # lid -> drawn mesh actor
        self.lines = {}
        self.value_actors = {}
        self.label_note = ""
        self.cache = {}                         # (lid, t) -> item or None
        self.claimed = set()
        self.failures = {}
        self.rain = {}                          # (model, end, period)
        self.centres_xyz = {}                   # model -> cell centres
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.dirty = False
        self.disk = os.path.join(CACHE, "session")
        shutil.rmtree(self.disk, ignore_errors=True)
        os.makedirs(self.disk, exist_ok=True)

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
        pl.add_coastlines(color="black", line_width=1, zlevel=self._z_top())

        lon, lat, zoom = ATLANTIC if view == "atlantic" else HOME
        self.go_to(lon, lat, zoom)
        self.home = pl.camera_position
        # Terrain style turns the globe about its axis and tilts it,
        # never rolls it: north stays up while dragging.
        pl.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)
        self.show()

    # ---- models ----
    def add_model(self, lm):
        """Add a loaded model (window thread). Its layers start with the
        viewer's defaults; the time axis grows to cover it."""
        m = lm.model
        with self.lock:
            self.runs[m], self.files[m] = lm.run, lm.files
            self.grids[m], self.styles[m] = lm.grid, lm.styles
            if m not in self.models:
                self.models.append(m)
                rank = MODEL_ORDER.index
                self.order = sorted(self.models, key=rank)
            self.model_on.setdefault(m, True)
            for s in lm.styles:
                lid = layer_id(m, s)
                if lid not in self.layers:
                    self.layers.append(lid)
                    self.visible[lid] = STYLES[s].get("visible", False)
                    self.opacity[lid] = STYLES[s].get("opacity", 1.0)
                    self.label_on[lid] = False
                    self.lines[lid] = []
                    self.value_actors[lid] = []
        old = self.times[self.i] if self.times else None
        times = set()
        for mm in self.models:
            for param in {p for s in self.styles[mm] for p in needs(s)}:
                times |= {v for v, _ in self.files[mm].expected(param)}
        self.times = sorted(times)
        if old in self.times:
            self.i = self.times.index(old)
        else:                                    # nearest to now
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self.i = int(np.argmin([abs((t - now).total_seconds())
                                    for t in self.times]))
        self.wake.set()
        self.show()

    # ---- view ----
    def go_to(self, lon, lat, zoom):
        self.pl.view_poi(x=lon, y=lat)
        self.pl.camera.zoom(zoom)
        self.north_up(render=False)

    def go_home(self):
        self.pl.camera_position = self.home
        self.pl.render()
        self.update_labels()

    def north_up(self, render=True):
        cam = self.pl.camera
        cam.up = (0.0, 0.0, 1.0)
        cam.OrthogonalizeViewUp()
        if render:
            self.pl.render()

    # ---- layers ----
    def is_on(self, lid):
        return self.visible[lid] and self.model_on[split_id(lid)[0]]

    def set_visible(self, lid, on, render=True):
        self.visible[lid] = bool(on)
        self.wake.set()
        self.show(render=render)

    def set_model_on(self, model, on, render=True):
        self.model_on[model] = bool(on)
        self.wake.set()
        self.show(render=render)

    def set_labels(self, lid, on, render=True):
        self.label_on[lid] = bool(on)
        self.update_labels(render=render)

    def set_opacity(self, lid, value, render=True):
        self.opacity[lid] = float(value)
        actors = self.lines[lid] + ([self.rasters[lid]]
                                    if lid in self.rasters else [])
        for actor in actors:
            _set_opacity(actor, value)
        if render:
            self.pl.render()

    @staticmethod
    def _z_top():
        return 2 + 2 * len(RASTERS) * len(MODEL_ORDER) + 2

    def _z(self, lid):
        """Height of a layer: rasters of the upper model above those of
        the lower one; lines above all rasters. GeoVista heights are
        whole steps of 1/10000 of the radius (about 0.6 km); two steps
        apart keeps the depth order clear."""
        model, style = split_id(lid)
        rank = len(self.order) - 1 - self.order.index(model)
        if STYLES[style]["kind"] == "raster":
            return 2 + 2 * (rank * len(RASTERS) + RASTERS.index(style))
        return self._z_top() + 2 + {"isobars": 0, "barbs": 10, "arrows": 10,
                                    "centres": 20}[STYLES[style]["kind"]] \
            + 2 * rank

    def _add_raster(self, lid, mesh):
        style = STYLES[split_id(lid)[1]]
        n = len(style["colours"])
        actor = self.pl.add_mesh(mesh, zlevel=self._z(lid), scalars="value",
                                 cmap=style["colours"],
                                 clim=(-0.5, n - 0.5), n_colors=n,
                                 nan_opacity=0.0, show_scalar_bar=False)
        _set_opacity(actor, self.opacity[lid])
        self.rasters[lid] = actor
        return actor

    def set_order(self, order, render=True):
        """Models top first; redraws the rasters at their new heights."""
        self.order = [m for m in order if m in self.models] + [
            m for m in self.order if m not in order]
        for lid, actor in list(self.rasters.items()):
            mesh = actor.mapper.dataset
            visible = actor.GetVisibility()
            self.pl.remove_actor(actor, render=False)
            self._add_raster(lid, mesh).SetVisibility(visible)
        self.show(render=render)

    # ---- time ----
    @property
    def time(self):
        return self.times[self.i] if self.times else None

    def go(self, i, render=True):
        if not self.times:
            return
        self.i = int(np.clip(i, 0, len(self.times) - 1))
        self.wake.set()
        self.show(render=render)

    def step(self, n, render=True):
        self.go(self.i + n, render)

    def status(self):
        """(ready, wanted, downloading) for switched-on layers."""
        on = [lid for lid in self.layers if self.is_on(lid)]
        with self.lock:
            ready = sum((lid, t) in self.cache for lid in on
                        for t in self.times)
        downloading = sum(f.downloads for f in self.files.values())
        return ready, len(on) * len(self.times), downloading

    # ---- preparing ----
    def start_workers(self, n=3):
        # Three at once: downloads overlap; file reading takes turns.
        for _ in range(n):
            threading.Thread(target=self._work, daemon=True).start()

    def _next_job(self):
        best = None
        with self.lock:
            on = [lid for lid in self.layers if self.is_on(lid)]
            for k, t in enumerate(self.times):
                for rank, lid in enumerate(on):
                    key = (lid, t)
                    if key in self.cache or key in self.claimed:
                        continue
                    score = (abs(k - self.i), k < self.i, rank)
                    if best is None or score < best[0]:
                        best = (score, key)
            if best:
                self.claimed.add(best[1])
        return best[1] if best else None

    def _work(self):
        while not data.CLOSING.is_set():
            job = self._next_job()
            if job is None:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            try:
                self.prepare(*job)
            except Exception as exc:            # keep the window usable
                print("Could not prepare", job, exc)
                with self.lock:
                    self.failures[job] = self.failures.get(job, 0) + 1
                    if self.failures[job] >= 3:
                        self.cache[job] = None
            with self.lock:
                self.claimed.discard(job)
            if job[1] == self.time:
                self.dirty = True

    def prepare(self, lid, t):
        """One layer at one time, from disk if saved, else computed."""
        model = split_id(lid)[0]
        saved = os.path.join(self.disk, f"{lid.replace(':', '_')}_"
                                        f"{self.runs[model]}_"
                                        f"{t:%Y%m%dT%H%MZ}.npz")
        try:
            value = _load_item(saved)
        except (OSError, KeyError, ValueError):
            value = self._compute(lid, t)
            if value is not None and not data.CLOSING.is_set():
                try:
                    _save_item(saved, value)
                except OSError as exc:
                    print("Could not save", saved, exc)
        with self.lock:
            self.cache[(lid, t)] = value
        return value

    def _field(self, model, param, t, scale=1.0, offset=0.0):
        path = self.files[model].path(param, t)
        if path is None:
            return None
        return self.grids[model].cut(read_field(path, scale, offset))

    def _rain(self, model, end, period):
        key = (model, end, period)
        with self.lock:
            if key in self.rain:
                return self.rain[key]
        path = self.files[model].path(RAIN, end, period)
        value = None if path is None else self.grids[model].cut(
            read_field(path, 1000.0))
        with self.lock:
            self.rain[key] = value
        return value

    def rain_total(self, model, t, hours):
        """Rainfall in the ``hours`` ending at ``t`` from the model's own
        accumulation files (1-hour, or 3-hour in the Global tail). None
        if the files do not tile the window exactly or any is missing:
        a total is never made from part of its hours."""
        periods = {}
        for end, period in self.files[model].expected(RAIN):
            periods.setdefault(end, set()).add(period)
        total, end, left = None, t, hours
        while left > 0:
            fits = [p for p in sorted(periods.get(end, ()), reverse=True)
                    if p is not None and p <= left]
            if not fits:
                return None
            arr = self._rain(model, end, fits[0])
            if arr is None:
                return None
            total = arr.copy() if total is None else total + arr
            end -= timedelta(hours=fits[0])
            left -= fits[0]
        return total

    def raster_values(self, lid, t):
        """The values a raster layer shows at ``t`` (model units after
        the layer's scaling), on the model's own cells."""
        model, name = split_id(lid)
        style = STYLES[name]
        if "hours" in style:
            hours = style["hours"]
            if hours == "run":
                hours = lead_hours(t, self.runs[model])
            if hours <= 0:
                return None
            return self.rain_total(model, t, hours)
        return self._field(model, style["param"], t,
                           style.get("scale", 1.0), style.get("offset", 0.0))

    def _compute(self, lid, t):
        model, name = split_id(lid)
        style, grid = STYLES[name], self.grids[model]
        if style["kind"] == "raster":
            values = self.raster_values(lid, t)
            if values is None:
                return None
            return classes(values, style["levels"], style.get("clear_below"))
        if style["kind"] in ("barbs", "arrows"):
            if style["kind"] == "barbs":
                speed = self._field(model, WIND, t, MS_TO_KT)
                dirn = self._field(model, WDIR, t)
            else:
                speed = None
                dirn = self._field(model, style["param"], t)
            if dirn is None or (style["kind"] == "barbs" and speed is None):
                return None
            cut = grid.barb_cut
            if style["kind"] == "barbs":
                mesh = barb_mesh(grid.barb_lon, grid.barb_lat,
                                 speed[cut].ravel(), dirn[cut].ravel())
            else:
                mesh = arrow_mesh(grid.barb_lon, grid.barb_lat,
                                  dirn[cut].ravel())
            return mesh, (None, [])
        hpa = self._field(model, MSLP, t, 0.01)
        if hpa is None:
            return None
        if style["kind"] == "isobars":
            iso = isobar_mesh(grid.x, grid.y, hpa, to_lonlat=grid.transform,
                              wrap=grid.world)
            return iso, isobar_labels(iso)
        if isinstance(grid, RotatedGrid):
            # Rotated degrees are close to equal distances on this grid.
            found = find_pressure_centres(hpa, grid.x, grid.y,
                                          coordinate_unit_to_metre=111195.0)
        else:
            found = find_pressure_centres(hpa, grid.x, grid.y,
                                          crs=grid.crs)
        if not found:
            return Lines(), (None, [])
        lon, lat = grid.lonlat([c["x"] for c in found],
                               [c["y"] for c in found])
        pts = xyz(np.asarray(lon), np.asarray(lat)) * 1.007
        return Lines(), (pts, [f"{c['letter']} {c['pressure']:.0f}"
                                     for c in found])

    # ---- value labels ----
    def _cell_centres(self, model):
        """Unit-globe points of every cell centre of a model's grid."""
        if model not in self.centres_xyz:
            g = self.grids[model]
            xx, yy = np.meshgrid(g.x, g.y)
            lon, lat = g.lonlat(xx.ravel(), yy.ravel())
            self.centres_xyz[model] = xyz(np.asarray(lon), np.asarray(lat))
        return self.centres_xyz[model]

    def update_labels(self, render=True):
        """Label raw cell values of layers with labels on, for the part
        of the globe in view: cells about 10 km apart, or wider when that
        would mean more than MAX_LABELS labels."""
        pl, t = self.pl, self.time
        for lid, actors in self.value_actors.items():
            for actor in actors:
                pl.remove_actor(actor, render=False)
            actors.clear()
        wanted = [lid for lid in self.layers
                  if self.label_on.get(lid) and self.is_on(lid)
                  and STYLES[split_id(lid)[1]]["kind"] == "raster"]
        self.label_note = ""
        if not wanted or t is None:
            if render:
                pl.render()
            return
        cam = pl.camera
        pos = np.asarray(cam.position, dtype=float)
        dist = np.linalg.norm(pos)
        look = pos / dist                        # globe point under view
        half = np.radians(cam.view_angle) / 2.0
        # Angular radius of the globe in view (small-angle estimate).
        cap = min(np.arcsin(min(1.0, 1.0 / dist)),
                  (dist - 1.0) * np.tan(half) * 1.3)
        area = np.pi * (cap * EARTH_KM) ** 2
        km = max(LABEL_KM, float(np.sqrt(area / MAX_LABELS)))
        self.label_note = f"labels every {km:.0f} km"
        box = km / EARTH_KM
        for lid in wanted:
            model, name = split_id(lid)
            values = self.raster_values(lid, t)
            if values is None:
                continue
            values = np.asarray(values).ravel()
            pts = self._cell_centres(model)
            inside = np.flatnonzero(pts @ look > np.cos(cap))
            if not inside.size:
                continue
            style = STYLES[name]
            v = values[inside]
            keep = np.isfinite(v)
            if style.get("clear_below") is not None:
                keep &= v >= style["clear_below"]
            inside = inside[keep]
            if not inside.size:
                continue
            # One cell per box of the label spacing.
            cell = np.floor(pts[inside] / box).astype(np.int64)
            _, first = np.unique(cell, axis=0, return_index=True)
            chosen = inside[first][:MAX_LABELS * 2]
            fmt = LABEL_FORMAT.get(name, ".0f")
            self.value_actors[lid].append(pl.add_point_labels(
                pts[chosen] * 1.003,
                [format(values[k], fmt) for k in chosen],
                font_size=9, text_color="#101010", shape=None,
                show_points=False, always_visible=False, render=False))
        if render:
            pl.render()

    # ---- drawing ----
    def refresh(self):
        """Redraw if something for the shown time became ready."""
        if self.dirty:
            self.dirty = False
            self.show()

    def show(self, render=True, block=False):
        pl, t = self.pl, self.time
        for lid in list(self.layers):
            model, name = split_id(lid)
            style = STYLES[name]
            on = self.is_on(lid)
            with self.lock:
                value = self.cache.get((lid, t), PENDING)
            if on and value is PENDING and block:
                value = self.prepare(lid, t)
            if style["kind"] == "raster":
                actor = self.rasters.get(lid)
                if actor is None and on:
                    actor = self._add_raster(lid, self.grids[model].mesh())
                if actor is None:
                    continue
                mesh = actor.mapper.dataset
                if on and isinstance(value, np.ndarray):
                    mesh.cell_data["value"] = shown(value)
                else:
                    mesh.cell_data["value"][:] = np.nan
                    mesh.Modified()
                actor.SetVisibility(on)
                continue
            for actor in self.lines[lid]:
                pl.remove_actor(actor, render=False)
            self.lines[lid] = []
            if not on or value is PENDING or value is None:
                continue
            mesh, (pts, labels) = value
            if mesh.n_points:
                self.lines[lid].append(pl.add_mesh(
                    mesh.mesh(), color=style["colour"], zlevel=self._z(lid),
                    line_width=1.5 if style["kind"] == "isobars" else 1.2))
            if pts is not None and style["kind"] == "centres":
                for letter, colour in CENTRE_COLOURS.items():
                    keep = [k for k, s in enumerate(labels)
                            if s.startswith(letter)]
                    if keep:
                        self.lines[lid].append(pl.add_point_labels(
                            np.asarray(pts)[keep],
                            [labels[k].replace(" ", "\n") for k in keep],
                            font_size=15, bold=True, text_color=colour,
                            shape=None, show_points=False,
                            always_visible=False, justification_horizontal=
                            "center", render=False))
            elif pts is not None:
                self.lines[lid].append(pl.add_point_labels(
                    pts, labels, font_size=11, text_color=style["colour"],
                    shape=None, show_points=False, always_visible=False,
                    render=False))
            for actor in self.lines[lid]:
                _set_opacity(actor, self.opacity[lid])
        pl.add_text(self.caption(), position="upper_left", font_size=10,
                    color="white", name="title")
        self.update_labels(render=False)
        if render:
            pl.render()

    def leads_text(self):
        t, out = self.time, []
        for m in self.order:
            label = data.MODELS[m]["label"]
            lead = lead_hours(t, self.runs[m])
            has = any(v == t for p in {q for s in self.styles[m]
                                       for q in needs(s)}
                      for v, _ in self.files[m].expected(p))
            out.append(f"{label} T+{lead}" if has and lead >= 0
                       else f"{label}: no data")
        return ", ".join(out)

    def caption(self):
        if self.time is None:
            return ("No model loaded: press a model's download button in "
                    "the layer panel.\nPrototype.")
        return (f"Valid {self.time:%a %d %b %H:%M} UTC\n{self.leads_text()}"
                "\nNative model cells, no resampling. Prototype.")
