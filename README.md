# UKMO NWP Globe

A 3D globe for Met Office model output, for the larger-domain models
where a flat map distorts. Prototype: rainfall from UKV (on top) and the
Global 10 km model, with Global MSLP isobars, on one globe you can spin
and zoom. It does not replace the flat viewer in the UKMO NWP tools.

![North Atlantic](docs/screenshots/globe_atlantic.png)

## Principle

Each model is drawn on its own native cells, built from the cell bounds
in its NetCDF files (UKV's Lambert equal-area grid is converted to
longitude/latitude with pyproj). Nothing is resampled; the value on a
cell is the value in the file. Dry cells (< 0.1 mm) are see-through.

## Install and run

```
conda env create -f environment.yml
conda run -n ukmo_globe python globe.py
```

Opens on Ireland. Drag to turn the globe (north stays up), scroll to
zoom, Shift+drag to pan.

| Key | Action |
|---|---|
| Right / Left | Next / previous hour |
| Up / Down | Six hours on / back |
| h | Home: the Ireland view, north up |
| n | North up from the current view |
| q | Quit |

| Option | Meaning |
|---|---|
| `--archive PATH` | Download archive of the UKMO NWP tools (default `..\ukmo_nwp\data\archive`) |
| `--ukv-run`, `--global-run` | Runs to show, e.g. `20261115T2100Z` (default: newest in the archive) |
| `--valid TIME` | Starting valid time, e.g. `20261116T0300Z` (default: first hour both models cover) |
| `--base grey` / `colour` | Background: Natural Earth I shaded relief in grey (default) or in colour |
| `--view ireland` / `atlantic` | Starting view |
| `--screenshot FILE` | Write a PNG and exit (`--steps N` steps N hours first) |

Times step through the Global run's hours; UKV is shown for the hours
its run covers. Once the window is open every hour is prepared in the
background, nearest hours first (about 0.7 s each, so a 54-hour run is
ready after roughly 40 s); a prepared hour appears at once. All hours
together take roughly 0.5-0.6 GB of memory. Isobars (Global MSLP, every 4 hPa) are labelled about
every 750 km.

The archive layout is `<archive>/<model>/<run>/<parameter>/*.nc`, as
written by the UKMO NWP tools.

## Notes

- GeoVista downloads its background picture and coastlines once, into
  `%LOCALAPPDATA%\geovista`; offline use needs these bundled.
- Needs working OpenGL; untested on remote desktops and virtual
  machines.
- Not yet: hover values, colour bar, time slider, wind, MOGREPS, other
  models (ICON, HARMONIE).
