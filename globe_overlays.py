"""Map overlays: the user's own hillshade, masks and boundary lines.

``tools/prepare_overlays.py`` turns GeoTIFFs and shapefiles into
``data/overlays/<name>.npz``; this module reads those with numpy alone
(no GIS libraries in the globe or its build). Two kinds:

image  An RGBA picture over a longitude/latitude box (hillshade, sea
       mask), draped on the globe above the base map and under every
       model layer.
lines  Points on the unit globe and VTK line cells (county or catchment
       boundaries), drawn with the coastlines above the model layers,
       optionally with a label per feature.
"""

from __future__ import annotations

import os

import numpy as np
import pyvista as pv

from globe_paths import APP_DIR

OVERLAY_DIR = os.path.join(APP_DIR, "data", "overlays")
IMAGE_STEP = 0.05          # degrees between the draped box's vertices


class Overlay:
    """One overlay file: what it is is read at once, its data when it is
    first switched on."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        with np.load(path) as f:
            self.kind = str(f["kind"])
            self.label = str(f["label"]) if "label" in f else self.name
            self.colour = str(f["colour"]) if "colour" in f else "#3a3a3a"
            self.width = float(f["width"]) if "width" in f else 1.0
            self.opacity = float(f["opacity"]) if "opacity" in f else 1.0
            self.order = int(f["order"]) if "order" in f else 0
            self.has_labels = "label_txt" in f

    def load(self):
        with np.load(self.path) as f:
            return {k: f[k] for k in f.files}


def find(folder=None):
    """The overlays in ``folder`` (default data/overlays): pictures
    first, then lines; by their order, then name."""
    folder = folder or OVERLAY_DIR
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".npz") or name.endswith(".part.npz"):
            continue
        try:
            ov = Overlay(os.path.join(folder, name))
        except (OSError, KeyError, ValueError) as exc:
            print("Could not read overlay", name, exc)
            continue
        if ov.kind in ("image", "lines"):
            out.append(ov)
    out.sort(key=lambda o: (o.kind != "image", o.order, o.label.lower()))
    return out


def image_mesh(bounds):
    """A surface over the longitude/latitude box (west, east, south,
    north) on the unit globe, with texture coordinates that put a
    north-up picture on it."""
    import geovista as gv
    from geovista.common import from_cartesian
    w, e, s, n = (float(v) for v in bounds)
    nx = int(np.clip(np.ceil((e - w) / IMAGE_STEP), 2, 400))
    ny = int(np.clip(np.ceil((n - s) / IMAGE_STEP), 2, 400))
    mesh = gv.Transform.from_1d(np.linspace(w, e, nx + 1),
                                np.linspace(s, n, ny + 1))
    lonlat = from_cartesian(mesh)
    u = (lonlat[:, 0] - w) / (e - w)
    v = (lonlat[:, 1] - s) / (n - s)
    mesh.active_texture_coordinates = np.column_stack(
        [np.clip(u, 0, 1), np.clip(v, 0, 1)]).astype(np.float32)
    return mesh


def texture(rgba):
    """A VTK texture from a north-up RGBA picture (row 0 the north
    edge); smoothed, with mipmaps so it does not shimmer zoomed out."""
    tex = pv.Texture(np.ascontiguousarray(rgba))
    tex.interpolate = True
    try:
        tex.mipmap = True
    except AttributeError:
        pass
    return tex


def line_mesh(points, lines):
    return pv.PolyData(np.asarray(points, np.float32).copy(),
                       lines=np.asarray(lines, np.int64).copy())
