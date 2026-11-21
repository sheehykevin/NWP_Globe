"""Turn GIS files into map overlays for the globe.

Reads a GeoTIFF or a shapefile / GeoPackage in any projection (Irish
Transverse Mercator, Irish Grid, ...) and writes one small ``.npz`` file
the globe reads with numpy alone, so the globe and its standalone build
need no GIS libraries. Run this tool in an environment that has
rasterio, geopandas and shapely (the Anaconda base environment has
them):

    conda run -n base python tools/prepare_overlays.py hillshade ie_hillshade.tif
    conda run -n base python tools/prepare_overlays.py lines counties.shp --name counties --label-field COUNTY
    conda run -n base python tools/prepare_overlays.py lines hydrometric_areas.shp --name hydrometric_areas --colour "#1f5fa8"
    conda run -n base python tools/prepare_overlays.py mask land.shp --name ocean --invert --colour "#9cc3e0"

Kinds:

hillshade  A hillshade raster draped over the base map as light and
           shadow: cells brighter than flat ground lighten the map,
           darker ones darken it, flat ground (the raster's most common
           value) leaves it as it is. No data is see-through.
lines      Polygon outlines or lines (county boundaries, catchments,
           rivers), optionally labelled from a field.
mask       Polygons filled with one colour on a raster (an ocean or land
           mask); --invert fills everything outside them (sea from a land
           polygon) within the layer's bounds or --bounds.

Files go to data/overlays next to the globe (--out to change); each one
appears under "Map overlays" in the layer panel at the next start.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "data", "overlays")
WGS84 = "EPSG:4326"


def _xyz(lon, lat):
    """Longitude/latitude -> points on the globe's unit sphere (as
    globe_core.xyz)."""
    lam, phi = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(phi) * np.cos(lam),
                            np.cos(phi) * np.sin(lam),
                            np.sin(phi)]).astype(np.float32)


def _grid(bounds, res):
    """Pixel size and shape of a lon/lat grid over bounds (west, east,
    south, north): ``res`` degrees of latitude, and the longitude step
    that makes pixels about square on the ground at the middle."""
    w, e, s, n = bounds
    dlat = res
    dlon = res / max(math.cos(math.radians((s + n) / 2)), 0.1)
    width = max(2, int(math.ceil((e - w) / dlon)))
    height = max(2, int(math.ceil((n - s) / dlat)))
    return width, height


def _save(path, **items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part.npz"
    np.savez_compressed(tmp, **items)
    os.replace(tmp, path)
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def _meta(args, kind, default_label):
    return dict(kind=np.array(kind),
                label=np.array(args.label or default_label),
                colour=np.array(args.colour),
                width=np.float32(args.width),
                opacity=np.float32(args.opacity),
                order=np.int32(args.order))


def hillshade(args):
    # rasterio only reads the pixels; the projection work is pyproj's, so
    # a rasterio built against another PROJ database does not matter.
    import rasterio
    from affine import Affine
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from scipy.ndimage import map_coordinates

    with rasterio.open(args.source) as src:
        crs = _source_crs(args.crs, src.crs, args.source)
        to_ll = Transformer.from_crs(crs, WGS84, always_xy=True)
        w, s, e, n = to_ll.transform_bounds(*src.bounds, densify_pts=21)
        width, height = _grid((w, e, s, n), args.res)
        # Read averaged down to about half the output pixel (a fine DEM
        # need not fit in memory at full size).
        out_m = args.res * 111000.0
        step = max(1, int(out_m / 2 / max(abs(src.res[0]), 1e-9)))
        shape = (max(1, src.height // step), max(1, src.width // step))
        grid = src.read(1, out_shape=shape, masked=True,
                        resampling=Resampling.average)
        grid = grid.astype(np.float32).filled(np.nan)
        transform = src.transform * Affine.scale(src.width / shape[1],
                                                 src.height / shape[0])
        print(f"{args.source}: {src.width}x{src.height} (read "
              f"{shape[1]}x{shape[0]}), {crs.name} -> {width}x{height} "
              "lon/lat")
    # Each output pixel's centre, in the source's projection, then its
    # place in the source grid; bilinear between source pixels.
    lon = w + (np.arange(width) + 0.5) * (e - w) / width
    lat = n - (np.arange(height) + 0.5) * (n - s) / height
    lon2, lat2 = np.meshgrid(lon, lat)
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True).transform(
        lon2, lat2)
    col, row = ~transform * (x, y)
    shade = map_coordinates(grid, [row - 0.5, col - 0.5], order=1,
                            cval=np.nan).astype(np.float32)
    valid = np.isfinite(shade)                  # no data became NaN
    if not valid.any():
        sys.exit("No data in the raster")
    v = shade[valid]
    lo, hi = np.percentile(v, [0.5, 99.5])
    # Flat ground: the most common value (ignoring the extremes).
    hist, edges = np.histogram(v, bins=256, range=(lo, hi))
    flat = float(edges[np.argmax(hist)] + (edges[1] - edges[0]) / 2)
    if args.flat is not None:
        flat = args.flat
    print(f"values {lo:.0f}..{hi:.0f}, flat ground {flat:.0f}")
    # Signed brightness against flat ground: -1 (deepest shadow) .. +1.
    rel = np.zeros_like(shade)
    below = valid & (shade < flat)
    above = valid & (shade >= flat)
    rel[below] = (shade[below] - flat) / max(flat - lo, 1e-6)
    rel[above] = (shade[above] - flat) / max(hi - flat, 1e-6)
    rel = np.clip(rel, -1, 1)
    rgba = np.zeros(shade.shape + (4,), np.uint8)
    rgba[..., :3] = np.where(rel > 0, 255, 0)[..., None]
    gain = np.where(rel > 0, args.light, 1.0)       # highlights softer
    rgba[..., 3] = np.clip(np.abs(rel) * gain * args.strength * 255, 0, 255)
    rgba[~valid, 3] = 0
    _save(_out(args), image=rgba,
          bounds=np.array([w, e, s, n], np.float64),
          **_meta(args, "image", "Hillshade"))


# Projections some Irish files carry only by name (GDAL reads them as a
# "local" system it cannot transform): name -> EPSG code.
KNOWN_NAMES = {"irish transverse mercator": "EPSG:2157",
               "irenet95": "EPSG:2157",
               "tm65 / irish grid": "EPSG:29902",
               "tm75 / irish grid": "EPSG:29903",
               "irish grid": "EPSG:29903"}


def _source_crs(given, found, path):
    """The source's projection: --crs if given, else the file's own; a
    local system named after a known Irish projection is taken as it."""
    from pyproj import CRS
    if given:
        return CRS.from_user_input(given)
    if found is None:
        sys.exit(f"{path} has no projection: give one with --crs "
                 "(e.g. --crs EPSG:2157 for Irish Transverse Mercator)")
    wkt = found.to_wkt() if hasattr(found, "to_wkt") else str(found)
    if wkt.upper().startswith(("LOCAL_CS", "ENGCRS", "ENGINEERINGCRS")):
        low = wkt.lower()
        for name, code in KNOWN_NAMES.items():
            if name in low:
                print(f"{path}: projection given only by name; using {code}"
                      " (--crs to change)")
                return CRS.from_user_input(code)
        sys.exit(f"{path}: unknown local projection; give one with --crs")
    return CRS.from_wkt(wkt)


def _read_vector(path, layer=None, crs=None):
    import geopandas as gpd
    gdf = gpd.read_file(path, layer=layer)
    src = _source_crs(crs, gdf.crs, path)
    gdf = gdf.set_crs(src, allow_override=True)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf


def lines(args):
    import shapely

    gdf = _read_vector(args.source, args.layer, args.crs)
    print(f"{args.source}: {len(gdf)} features, {gdf.crs}")
    if args.simplify and gdf.crs.is_projected:
        gdf = gdf.set_geometry(gdf.geometry.simplify(args.simplify))
    labels = []
    if args.label_field:
        if args.label_field not in gdf.columns:
            sys.exit(f"No field {args.label_field!r}; fields: "
                     + ", ".join(c for c in gdf.columns if c != "geometry"))
        spots = gdf.geometry.representative_point().to_crs(WGS84)
        labels = [(p.x, p.y, str(t)) for p, t in
                  zip(spots, gdf[args.label_field]) if t is not None]
    geo = np.asarray(gdf.to_crs(WGS84).geometry.values)
    if args.simplify and not gdf.crs.is_projected:
        geo = shapely.simplify(geo, args.simplify / 111000.0)
    # Polygons as their outlines; then short segments, so straight
    # stretches follow the curve of the globe.
    poly = np.isin(shapely.get_type_id(geo), (3, 6))
    geo = np.where(poly, shapely.boundary(geo), geo)
    geo = shapely.segmentize(geo, args.densify_km / 111.0)
    pts, cells, n0 = [], [], 0
    parts = shapely.get_parts(shapely.get_parts(geo))
    for part in parts:
        if part.is_empty or not hasattr(part, "coords"):
            continue                              # points: not lines
        xy = np.asarray(part.coords)[:, :2]
        if len(xy) < 2:
            continue
        pts.append(_xyz(xy[:, 0], xy[:, 1]))
        cells.append(np.concatenate([[len(xy)],
                                     np.arange(n0, n0 + len(xy))]))
        n0 += len(xy)
    if not pts:
        sys.exit("No lines found")
    points = np.vstack(pts)
    print(f"{len(cells)} lines, {len(points)} points")
    extra = {}
    if labels:
        lon, lat, txt = zip(*labels)
        extra = dict(label_pts=_xyz(np.array(lon), np.array(lat)),
                     label_txt=np.array(txt, dtype="U64"))
    _save(_out(args), points=points,
          lines=np.concatenate(cells).astype(np.int64),
          **_meta(args, "lines", os.path.splitext(
              os.path.basename(args.source))[0].replace("_", " ")),
          **extra)


def mask(args):
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds

    import pandas as pd
    gdf = pd.concat([_read_vector(src, args.layer, args.crs).to_crs(WGS84)
                     for src in args.sources], ignore_index=True)
    if args.bounds:
        w, e, s, n = args.bounds
    else:
        w, s, e, n = gdf.total_bounds
        pad = 0.25
        w, e, s, n = w - pad, e + pad, s - pad, n + pad
    width, height = _grid((w, e, s, n), args.res)
    sup = 3                                          # smooth edges
    transform = from_bounds(w, s, e, n, width * sup, height * sup)
    inside = rasterize(((g, 1) for g in gdf.geometry),
                       out_shape=(height * sup, width * sup),
                       transform=transform, fill=0, dtype="uint8")
    cover = inside.reshape(height, sup, width, sup).mean(axis=(1, 3))
    if args.invert:
        cover = 1.0 - cover
    colour = args.colour.lstrip("#")
    rgb = [int(colour[k:k + 2], 16) for k in (0, 2, 4)]
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.round(cover * 255).astype(np.uint8)
    print(f"{width}x{height} lon/lat over {w:.2f}..{e:.2f}E "
          f"{s:.2f}..{n:.2f}N")
    _save(_out(args), image=rgba, bounds=np.array([w, e, s, n], np.float64),
          **_meta(args, "image", "Sea" if args.invert else "Mask"))


def _out(args):
    name = args.name or os.path.splitext(os.path.basename(args.source))[0]
    return os.path.join(args.out, f"{name}.npz")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("kind", choices=["hillshade", "lines", "mask"])
    ap.add_argument("sources", nargs="+", metavar="source",
                    help="GeoTIFF (hillshade) or shapefile / GeoPackage "
                    "(lines; mask takes several, filled together)")
    ap.add_argument("--name", help="file name in data/overlays (default: "
                    "the source's name)")
    ap.add_argument("--label", help="name shown in the layer panel")
    ap.add_argument("--out", default=OUT, help="folder (default: %(default)s)")
    ap.add_argument("--order", type=int, default=0, help="draw order among "
                    "overlays of the same kind, higher on top")
    ap.add_argument("--opacity", type=float, default=None,
                    help="default opacity, 0-1")
    ap.add_argument("--colour", default=None, help="line or fill colour")
    ap.add_argument("--width", type=float, default=1.0, help="line width")
    ap.add_argument("--layer", help="layer of a GeoPackage")
    ap.add_argument("--crs", help="the source's projection when the file "
                    "has none or a wrong one, e.g. EPSG:2157 (ITM), "
                    "EPSG:29903 (Irish Grid)")
    g = ap.add_argument_group("hillshade")
    g.add_argument("--res", type=float, default=None,
                   help="pixel size in degrees of latitude (default 0.001, "
                   "about 110 m, for hillshade; 0.002 for mask)")
    g.add_argument("--strength", type=float, default=0.65,
                   help="how dark the deepest shadow is, 0-1")
    g.add_argument("--light", type=float, default=0.5,
                   help="highlights relative to shadows, 0-1")
    g.add_argument("--flat", type=float, default=None,
                   help="value of flat ground (default: the most common)")
    g = ap.add_argument_group("lines")
    g.add_argument("--label-field", help="field to label each feature with")
    g.add_argument("--simplify", type=float, default=50.0,
                   help="simplify to this many metres (0: keep all points)")
    g.add_argument("--densify-km", type=float, default=2.0,
                   help="longest straight segment, km")
    g = ap.add_argument_group("mask")
    g.add_argument("--invert", action="store_true",
                   help="fill outside the polygons (sea from land)")
    g.add_argument("--bounds", type=float, nargs=4,
                   metavar=("WEST", "EAST", "SOUTH", "NORTH"),
                   help="area filled (default: the polygons' extent)")
    args = ap.parse_args(argv)
    args.source = args.sources[0]
    if len(args.sources) > 1 and args.kind != "mask":
        ap.error(f"{args.kind} takes one source")
    defaults = {"hillshade": dict(res=0.001, colour="#000000", opacity=1.0),
                "lines": dict(colour="#3a3a3a", opacity=0.9),
                "mask": dict(res=0.002, colour="#9cc3e0", opacity=0.7)}
    for k, v in defaults[args.kind].items():
        if getattr(args, k) is None:
            setattr(args, k, v)
    {"hillshade": hillshade, "lines": lines, "mask": mask}[args.kind](args)


if __name__ == "__main__":
    main()
