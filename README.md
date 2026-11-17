# UKMO NWP Globe

A 3D globe for Met Office model output, for the larger-domain models
where a flat map distorts. Rainfall, MSLP and wind from UKV and the
Global 10 km model on one globe you can spin and zoom, with a layer
panel and time controls like the UKMO NWP viewer. It does not replace
the flat viewer in the UKMO NWP tools.

![Window](docs/screenshots/globe_window.png)

## Principle

Each model is drawn on its own native cells, built from the cell bounds
in its NetCDF files (UKV's Lambert equal-area grid is converted to
longitude/latitude with pyproj). Nothing is resampled; a cell's colour
comes from the value in the file. Isobars are contoured on each model's
own grid. Wind barbs are drawn at every 24th UKV cell centre (about
48 km apart) with that cell's own speed and direction.

## Install and run

```
conda env create -f environment.yml
conda run -n ukmo_globe python globe.py
```

## The window

**Layers panel** (right): tick a layer on or off; the slider sets its
opacity; each layer shows its colour key.

| Layer | From | Colours / style | Default |
|---|---|---|---|
| UKV wind barbs | UKV 10 m speed and direction | Knots: half 5, full 10, pennant 50 | off |
| UKV MSLP | UKV | Blue isobars, every 4 hPa | on |
| Global MSLP | Global | Black isobars, every 4 hPa | on |
| UKV rain | UKV hourly | Radar-style, 0.1 to 16+ mm | on |
| UKV gust | UKV 10 m gust | Gust warning bands, as the viewer (km/h) | off, 76 % |
| UKV wind speed | UKV 10 m mean | Viewer wind bands (km/h) | off, 76 % |
| Global rain | Global hourly | Radar-style, 0.1 to 16+ mm | on |

Wind is UKV only: the archive holds no Global wind (the products
pipeline downloads only Global rain and MSLP).

**Time bar** (bottom): first, back, play/pause, forward, last; the time
slider; play speed (0.5 to 4 hours a second); the valid time and each
model's lead; Home view; North up; how many hours are ready. Play waits
for an hour that is not ready yet and stops at the last hour.

**Mouse**: drag to turn the globe (north stays up), scroll to zoom,
Shift+drag to pan.

| Key | Action |
|---|---|
| Right / Left | Next / previous hour |
| Up / Down, Page Up / Page Down | Six hours on / back |
| Home / End | First / last hour |
| Space | Play / pause |
| h | Home view: Ireland, north up |
| n | North up from the current view |

## Options

| Option | Meaning |
|---|---|
| `--archive PATH` | Download archive of the UKMO NWP tools (default `..\ukmo_nwp\data\archive`) |
| `--ukv-run`, `--global-run` | Runs to show, e.g. `20261115T2100Z` (default: newest in the archive) |
| `--valid TIME` | Starting valid time, e.g. `20261116T0300Z` (default: first hour both models cover) |
| `--base grey` / `colour` | Background: Natural Earth I shaded relief in grey (default) or in colour |
| `--area atlantic` / `world` | Global area drawn (default `atlantic`: 80°W-50°E, 15-80°N) |
| `--view ireland` / `atlantic` | Starting view |

For testing: `--screenshot FILE` draws the globe off screen and writes
a PNG (with `--show LAYER ...`, `--hide LAYER ...`, `--steps N`);
`--smoke FILE` opens the window, saves a picture of it and closes.

## Speed

Once the window is open, two background workers prepare every hour,
nearest hours first, and save them in `cache\` (newest 6 runs kept), so
reopening a run is quick and stepping is near-instant. The grey
background is made once and saved there too.

## Files

| File | What it holds |
|---|---|
| `globe_core.py` | Data, layers, drawing and background preparation; no window code |
| `globe.py` | The Qt window (layer panel, time bar, keys) and the command line |

The archive layout is `<archive>/<model>/<run>/<parameter>/*.nc`, as
written by the UKMO NWP tools.

## Notes

- GeoVista downloads its background picture and coastlines once, into
  `%LOCALAPPDATA%\geovista`; offline use needs these bundled.
- Needs working OpenGL; untested on remote desktops and virtual
  machines.
- Barbs are a fixed spacing; they do not thin or thicken with zoom.
- UKV MSLP over high ground shows small closed loops, as in the model
  field.
- Not yet: hover values, Global wind, MOGREPS, other models (ICON,
  HARMONIE).
