"""Closed-isobar high/low centres, as in the UKMO NWP viewer.

Copied from ukmo_nwp ``core/pressure.py`` (same method and thresholds:
``pressure_centres`` in its config.yaml) so this project stays
independent of that repository. The original description follows.

Closed-isobar high/low detection for every grid (review Plan 5.3).

One implementation for the viewer and the products pipeline, combining
what each copy did better:

* missing values cannot become false extrema (viewer);
* distances are physical on both projected grids (UKV, metres) and
  latitude/longitude grids (Global 10 km, geodesic) (pipeline);
* detection can use a larger field than the area shown, so a cropped
  edge does not create false centres (pipeline ``display_extent``);
* every threshold comes from the ``pressure_centres`` settings shared in
  ``config.yaml``.

A candidate is kept only if some complete ring around it, at one of a
fixed set of radii, lies entirely at least ``minimum_prominence_hpa``
above (low) or below (high) the centre: by continuity the isobar between
them is then closed inside the ring.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter

DEFAULTS = {
    "local_radius_km": 150.0,
    "minimum_prominence_hpa": 4.0,
    "minimum_spacing_km": 300.0,
    "maximum_spacing_km": 600.0,
    "spacing_fraction_of_short_side": 0.12,
    "maximum_closure_radius_km": 600.0,
    "low_max_hpa": None,
    "high_min_hpa": None,
}
RING_RADII_KM = (100.0, 150.0, 225.0, 300.0, 400.0, 500.0)
RING_AZIMUTH_STEP_DEG = 5.0
EXTREMUM_TOLERANCE_HPA = 0.01


class _Metric:
    """Physical distances on the field's grid."""

    def __init__(self, crs=None, unit_to_metre=None):
        self.geographic = False
        self.factor = 1.0
        if crs is not None:
            from pyproj import CRS
            crs = CRS.from_user_input(crs)
            self.geographic = bool(crs.is_geographic)
            if not self.geographic:
                self.factor = float(
                    crs.axis_info[0].unit_conversion_factor or 1.0)
        if unit_to_metre is not None and not self.geographic:
            self.factor = float(unit_to_metre)
        if self.geographic:
            from pyproj import Geod
            self.geod = Geod(ellps="WGS84")

    def km(self, x1, y1, x2, y2) -> float:
        if self.geographic:
            _, _, metres = self.geod.inv(float(x1), float(y1),
                                         float(x2), float(y2))
            return abs(float(metres)) / 1000.0
        return float(np.hypot(x2 - x1, y2 - y1) * self.factor / 1000.0)

    def ring(self, x0, y0, radius_km, azimuths_deg):
        if self.geographic:
            n = len(azimuths_deg)
            rx, ry, _ = self.geod.fwd(np.full(n, x0, dtype=float),
                                      np.full(n, y0, dtype=float),
                                      azimuths_deg,
                                      np.full(n, radius_km * 1000.0))
            return np.asarray(rx), np.asarray(ry)
        theta = np.deg2rad(azimuths_deg)
        r = radius_km * 1000.0 / self.factor
        return x0 + r * np.sin(theta), y0 + r * np.cos(theta)


def _ascending_grid(field, x, y):
    """The field with ascending one-dimensional x and y coordinates."""
    arr = np.asarray(field, dtype=np.float32)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if arr.shape != (len(y), len(x)):
        raise ValueError(f"field shape {arr.shape} does not match y/x grid "
                         f"({len(y)}, {len(x)})")
    if x[0] > x[-1]:
        x, arr = x[::-1], arr[:, ::-1]
    if y[0] > y[-1]:
        y, arr = y[::-1], arr[::-1, :]
    return np.ascontiguousarray(arr), x, y


def _nearest_indices(axis, values):
    """Vectorised nearest-cell lookup on an ascending axis."""
    values = np.asarray(values, dtype=float)
    idx = np.clip(np.searchsorted(axis, values), 1, len(axis) - 1)
    left = axis[idx - 1]
    use_left = np.abs(values - left) <= np.abs(axis[idx] - values)
    return np.where(use_left, idx - 1, idx)


def _filter_size(x, y, metric, radius_km):
    """A raster window approximating a physical radius."""
    mid_x = 0.5 * (x[0] + x[-1])
    mid_y = 0.5 * (y[0] + y[-1])
    dx_km = metric.km(mid_x, mid_y, mid_x + abs(x[1] - x[0]), mid_y)
    dy_km = metric.km(mid_x, mid_y, mid_x, mid_y + abs(y[1] - y[0]))
    rx = max(1, int(round(radius_km / max(dx_km, 1e-6))))
    ry = max(1, int(round(radius_km / max(dy_km, 1e-6))))
    return 2 * ry + 1, 2 * rx + 1


def _short_side_km(x, y, metric):
    mid_x = 0.5 * (x[0] + x[-1])
    mid_y = 0.5 * (y[0] + y[-1])
    width = metric.km(x[0], mid_y, x[-1], mid_y)
    height = metric.km(mid_x, y[0], mid_x, y[-1])
    return max(min(width, height), 1.0)


def _ring_closed(field, x, y, j, i, letter, prominence, metric,
                 maximum_radius_km):
    centre = float(field[j, i])
    azimuths = np.arange(0.0, 360.0, RING_AZIMUTH_STEP_DEG)
    radii = sorted({r for r in (*RING_RADII_KM, float(maximum_radius_km))
                    if r <= maximum_radius_km})
    for radius_km in radii:
        ring_x, ring_y = metric.ring(x[i], y[j], radius_km, azimuths)
        if (ring_x.min() < x[0] or ring_x.max() > x[-1]
                or ring_y.min() < y[0] or ring_y.max() > y[-1]):
            continue          # a ring leaving the data proves nothing
        ring = field[_nearest_indices(y, ring_y), _nearest_indices(x, ring_x)]
        if not np.all(np.isfinite(ring)):
            continue
        if letter == "L" and np.min(ring) >= centre + prominence:
            return True
        if letter == "H" and np.max(ring) <= centre - prominence:
            return True
    return False


def find_pressure_centres(field, x, y, crs=None, display_extent=None,
                          settings=None, coordinate_unit_to_metre=None):
    """Closed, sufficiently prominent highs and lows, as dicts.

    ``field`` is MSLP in hPa on 1-D ``x``/``y`` coordinates. ``crs``
    identifies the grid (default: projected, metres). Detection uses the
    whole field; ``display_extent`` (xmin, xmax, ymin, ymax) only limits
    which centres are returned. Each result is
    ``{"letter": "H"|"L", "pressure": hPa, "x": ..., "y": ...}``.
    """
    s = {**DEFAULTS, **(settings or {})}
    metric = _Metric(crs, coordinate_unit_to_metre)
    arr, x, y = _ascending_grid(field, x, y)
    finite = np.isfinite(arr)
    if not finite.any() or len(x) < 2 or len(y) < 2:
        return []

    size = _filter_size(x, y, metric, float(s["local_radius_km"]))
    # Missing cells take the value that can never win the comparison.
    highs = np.where(finite, arr, -np.inf)
    lows = np.where(finite, arr, np.inf)
    high_mask = finite & np.isclose(
        arr, maximum_filter(highs, size=size, mode="nearest"),
        atol=EXTREMUM_TOLERANCE_HPA)
    low_mask = finite & np.isclose(
        arr, minimum_filter(lows, size=size, mode="nearest"),
        atol=EXTREMUM_TOLERANCE_HPA)

    if display_extent is None:
        display_extent = (x[0], x[-1], y[0], y[-1])
    xmin, xmax, ymin, ymax = (float(v) for v in display_extent)
    in_view = ((x[np.newaxis, :] >= xmin) & (x[np.newaxis, :] <= xmax)
               & (y[:, np.newaxis] >= ymin) & (y[:, np.newaxis] <= ymax))
    high_mask &= in_view
    low_mask &= in_view
    if s["high_min_hpa"] is not None:
        high_mask &= arr >= float(s["high_min_hpa"])
    if s["low_max_hpa"] is not None:
        low_mask &= arr <= float(s["low_max_hpa"])

    view_x = x[(x >= xmin) & (x <= xmax)]
    view_y = y[(y >= ymin) & (y <= ymax)]
    if len(view_x) < 2 or len(view_y) < 2:
        return []
    spacing = float(np.clip(
        float(s["spacing_fraction_of_short_side"])
        * _short_side_km(view_x, view_y, metric),
        float(s["minimum_spacing_km"]), float(s["maximum_spacing_km"])))
    prominence = float(s["minimum_prominence_hpa"])
    maximum_radius = float(s["maximum_closure_radius_km"])

    accepted = []
    for mask, letter in ((low_mask, "L"), (high_mask, "H")):
        rows, cols = np.where(mask)
        if not len(rows):
            continue
        order = np.argsort(arr[rows, cols])
        if letter == "H":
            order = order[::-1]
        for k in order:
            j, i = int(rows[k]), int(cols[k])
            if any(metric.km(x[i], y[j], c["x"], c["y"]) < spacing
                   for c in accepted):
                continue
            if not _ring_closed(arr, x, y, j, i, letter, prominence, metric,
                                maximum_radius):
                continue
            accepted.append({"letter": letter, "pressure": float(arr[j, i]),
                             "x": float(x[i]), "y": float(y[j])})
    return accepted
