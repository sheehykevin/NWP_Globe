# UKMO NWP Globe

A 3D globe for weather and wave model output, for the larger-domain
models where a flat map distorts. The UKMO NWP viewer's layers for UKV,
HARMONIE (KNMI) and the Met Office Global 10 km model, plus IBI waves
(Copernicus Marine), on one globe you can spin and zoom, with a layer
panel per model and time controls like the viewer. It does not replace
the flat viewer in the UKMO NWP tools.

![Window](docs/screenshots/globe_window.png)

## Principle

Each model is drawn on its own native cells, built from the cell bounds
in its NetCDF files (UKV's Lambert equal-area grid is converted to
longitude/latitude with pyproj). Nothing is resampled; a cell's colour
comes from the value in the file, or from a sum of the model's own
files for rainfall windows. Isobars and high/low centres are found on
each model's own grid. Wind barbs are drawn at a subset of cell centres
with those cells' own speed and direction, about 48 km apart.

## Install and run

```
conda env create -f environment.yml
conda run -n ukmo_globe python globe.py
```

## Layers

Each weather model has these layers (those it has data for), with the
viewer's bands, colours, default opacity and default on/off.

| Layer | From | On at start |
|---|---|---|
| High / low centres | MSLP; closed highs (blue H) and lows (red L), the viewer's method | yes |
| 10 m wind barbs | 10 m speed and direction, knots | |
| MSLP contours | black isobars every 4 hPa, labelled | yes |
| 10 m wind gust | km/h, gust warning bands | |
| 10 m wind speed | km/h | |
| Screen temperature | °C | |
| Most-unstable CAPE | J/kg | |
| 24-hour rainfall | sum of the model's files ending at the shown time | |
| Run-total rainfall | sum from the start of the run | |
| 6-hour rainfall | sum | yes |
| 3-hour rainfall | sum | |
| 1-hour rainfall | hourly file | |

Rain windows are summed from the model's own accumulation files: 1-hour
files, and for Global the 3-hour files from T+57 to T+69. A total is
shown only when the files cover the window exactly and none is missing;
it is never made from part of its hours. So 1-hour rain has no Global
data after T+54.

Times run hourly, then 3-hourly in the Global tail to T+69 (UKV to its
own last lead).

## Models

The globe opens empty. Each model's group in the layer panel has a
download button (a box with a down arrow) that fetches that model's
latest run; nothing is downloaded until you press it. Everything
downloaded is kept in `data\session` (prepared layers in
`cache\session`) and deleted when the globe closes, or at the next start
if it did not close cleanly.

| Model | Grid | Latest run and download |
|---|---|---|
| UKV | 2 km Lambert equal area | Newest run (every 3 h) whose T+54 is published on the Met Office AWS bucket; files come as layers need them |
| HARMONIE (KNMI) | P3 (deterministic): rotated pole, 0.05 deg (about 5.5 km), Europe | Newest P3 run on the KNMI Open Data API; about 3.4 GB streamed, only the fields drawn are kept (a few minutes to load) |
| ICON-EU (DWD) | 0.0625 deg lat/lon (about 7 km), 23.5W-62.5E, 29.5-70.5N | Newest run (every 3 h; 00/06/12/18Z to T+120, the others to T+30) whose last file is on opendata.dwd.de; files (bz2 GRIB2, about 1 MB each) come as layers need them |
| Global | 10 km lat/lon | Newest run (every 6 h) whose T+54 is published; files come as layers need them (about 6 MB per field per hour) |
| IBI waves | about 3 km lat/lon, 19W-5E, 26-56N | Copernicus Marine `IBI_ANALYSISFORECAST_WAV_005_005`, today 00Z to +72 h; significant wave height and mean wave direction (arrows point where the waves go) |

ICON-EU has the same layers as UKV, with two notes: its CAPE is
mixed-layer CAPE (ICON-EU publishes no most-unstable CAPE), and its
rain over a period is the step in its run accumulation (`TOT_PREC`).
Its winds are on a true lat/lon grid.

HARMONIE winds are turned from the rotated grid to true north; its
hourly rain is the step in its run accumulation (field 61). P3 has no
CAPE. Each HARMONIE fetch writes `docs\harmonie_inventory.md`, every GRIB
field in the run.

- **KNMI**: an API key from https://developer.dataplatform.knmi.nl, in the
  `KNMI_API_KEY` environment variable or a `.env` file here with
  `KNMI_API_KEY=...` (git-ignored).
- **Copernicus Marine**: run `copernicusmarine login` once.

## The window

**Layers panel** (right): one group per model. Its header has a drag
handle (☰: drag above another model to draw it on top), an on/off tick
for the whole model, the run, the download button and an arrow to fold
it. Each layer has a tick, a **123** tick box for value labels (coloured
layers), an opacity slider and (when on) its colour key.

**Value labels**: the cells' own values (no interpolation), about 10 km
apart, for the part of the globe in view. Zoomed out, the spacing widens
so no more than about 400 labels are drawn (the status bar says e.g.
"labels every 40 km"); labels follow time steps and are redrawn after
the view stops moving. Dry or see-through cells are not labelled.

**Time bar** (bottom): first, back, play/pause, forward, last; the time
slider; play speed; the valid time and each model's lead; Home view;
North up; how many layer-hours are ready, downloads in progress, and
fetch messages. Play waits for a time that is not ready yet and stops at
the last one. When the first model loads, the time nearest to now is
shown.

**Mouse**: drag to turn the globe (north stays up), scroll to zoom,
Shift+drag to pan.

| Key | Action |
|---|---|
| Right / Left | Next / previous time |
| Up / Down, Page Up / Page Down | Six steps on / back |
| Home / End | First / last time |
| Space | Play / pause |
| h | Home view: Ireland, north up |
| n | North up from the current view |

## Options

| Option | Meaning |
|---|---|
| `--load MODEL ...` | Fetch these models at start (default: none) |
| `--base grey` / `colour` | Background: Natural Earth I shaded relief in grey (default) or in colour |
| `--area atlantic` / `world` | Global area drawn (default `atlantic`: 80°W-50°E, 15-80°N) |
| `--view ireland` / `atlantic` | Starting view |

For testing: `--screenshot FILE --load MODEL ...` draws off screen and
writes a PNG (with `--valid TIME`, `--show` / `--hide` / `--labels
MODEL:LAYER ...`, `--order`, `--steps N`, `--keep`); `--smoke FILE`
opens the window, saves a picture of it and closes. `globe_fetch.py
harmonie|ibi` fetches from the command line.

## Speed

Layers are prepared only while switched on, by three background
workers, nearest times first; stepping through prepared times is
near-instant.

## Files

| File | What it holds |
|---|---|
| `globe_core.py` | Layers, rain sums, drawing and background preparation; no window code |
| `globe_data.py` | Session folder, latest runs, downloading Met Office files |
| `globe_pressure.py` | High/low centres, copied from the UKMO NWP tools |
| `globe_icon.py` | ICON-EU (DWD): latest run, fetching and converting files |
| `globe_fetch.py` | Fetching HARMONIE (KNMI) and IBI waves (Copernicus Marine) |
| `globe.py` | The Qt window and the command line |

## Notes

- GeoVista downloads its background picture and coastlines once, into
  `%LOCALAPPDATA%\geovista`; offline use needs these bundled.
- Needs working OpenGL; untested on remote desktops and virtual
  machines.
- Barbs are a fixed spacing; they do not thin or thicken with zoom.
- With both models' isobars on, inside the UKV area you see both sets.
- Not yet: hover values, MOGREPS, other models (ICON, HARMONIE).
