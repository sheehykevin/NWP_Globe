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

HARMONIE and the wave models need free accounts; the other models need
nothing. See [docs/CREDENTIALS.md](docs/CREDENTIALS.md).


```
conda env create -f environment.yml
conda run -n ukmo_globe python globe.py
```

## Fetching and the store

Nothing is downloaded until asked for, and everything fetched is kept.

- **In the window**: each model's download button (header) fetches and
  prepares every layer of its latest run; each layer's own button
  fetches that layer for every hour. The **Models** and **Parameters**
  menus at the top of the panel with **Fetch** fetch the ticked
  parameters for every ticked model that has them (each parameter
  says how many of the ticked models have it; models without it are
  listed in the status bar). Ticking a layer that has not been fetched
  fetches it. At most two fetches run at once (`GLOBE_MAX_JOBS` to
  change); the others wait in line. A fetching model's download button
  becomes a stop button. Fetches run as separate processes with their
  output in `data\store\jobs\<model>.log`; progress shows under the
  model's name and the model reloads when done. Closing the window
  while fetches run asks whether to keep them going in the background;
  the next window picks them up. A failed fetch shows a **details**
  link with its output and the likely cause.
- **From the command line**: `prefetch.bat` (or `globe.py --prefetch`)
  fetches the latest run of every model, or only those named
  (`prefetch.bat ukv ecmwf`), all layers or only those given
  (`prefetch.bat ecmwf --layers rain_6h mslp`). `globe.py --job MODEL
  [LAYER ...] [--latest]` runs one fetch job.

A layer's fetch downloads only the fields it needs and skips what is
already there: isobars after MSLP has been fetched for the rain-free
layers download nothing, only prepare. Derived layers (isobars, centres,
barbs, rain windows) are prepared only when their own fetch (or the
model's) is asked for.

Everything is kept in `data\store` (prepared layers in `cache\store`)
until a newer run of that model is fetched, which replaces it; models
in the store open at start. When a newer run is fetched, every layer
already fetched for the older run is fetched for the new one too, and
the older run is deleted only after that. Each header shows the run's
age, and a blue badge (e.g. "18Z available") when the source has
published a newer run (checked every 15 minutes). The store's size is
shown beside **Clear data**, which deletes it all; right-click a model
to clear only that model or see its last fetch's output. Every
deletion is written to `data\store\log.txt` with its reason.

HARMONIE and the Météo-France packages have no byte index, so the globe
makes one: it reads the first bytes of each GRIB message with HTTP range
requests and fetches only the messages wanted (HARMONIE MSLP for a whole
run is about 50 MB of the 3.4 GB archive; checked identical to a
whole-archive decode).

## Standalone build

`build.bat` (in this folder) builds a standalone copy with PyInstaller
into `dist\NWP_Globe` (about 1.5 GB, one folder). Copy that whole folder
to another computer and run `NWP_Globe.exe`; no Python or conda is
needed there. The build:

1. creates the `ukmo_globe` conda environment if it is missing
   (`build.bat /update` updates it from `environment.yml`);
2. installs PyInstaller and pyflakes if missing (keeping pyvista 0.44.2
   and VTK 9.3.1, which GeoVista 0.5.3 needs);
3. makes sure GeoVista's background picture and coastlines are cached,
   and bundles them (the built globe downloads nothing for its map);
4. checks the code with pyflakes;
5. builds `packaging\NWP_Globe.spec`;
6. smoke-tests the two programs.

The folder holds `NWP_Globe.exe` (the window) and `nwp-globe-fetch.exe`
(a console program that runs the fetch jobs and `prefetch.bat`). Downloads, prepared layers and `.env` live
next to the exe (`data`, `cache`) and are kept (see Fetching and the
store). Close `NWP_Globe.exe` and anything open from `dist\NWP_Globe`
before rebuilding. Wave models still need a Copernicus login on the
target computer (see docs/CREDENTIALS.md).

## Layers

Each weather model has these layers (those it has data for), with the
viewer's bands, colours and default opacity. Every layer starts off.

| Layer | From | |
|---|---|---|
| High / low centres | MSLP; closed highs (blue H) and lows (red L), the viewer's method | |
| 10 m wind barbs | 10 m speed and direction, knots | |
| MSLP contours | black isobars every 4 hPa, labelled | |
| 10 m wind gust | km/h, gust warning bands | |
| 10 m wind speed | km/h | |
| Screen temperature | °C | |
| Most-unstable CAPE | J/kg | |
| 24-hour rainfall | sum of the model's files ending at the shown time | |
| Run-total rainfall | sum from the start of the run | |
| 6-hour rainfall | sum | |
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

What each source publishes (every field, from its live listings) is in
[docs/models](docs/models/), with a cross-model table in
[docs/models/SUMMARY.md](docs/models/SUMMARY.md). Regenerate with
`conda run -n ukmo_globe python tools/model_inventory.py`.

Every model is listed in the panel with all the layers it can have,
data or not; see Fetching and the store.

| Model | Grid | Latest run and download |
|---|---|---|
| UKV | 2 km Lambert equal area | Newest run (every 3 h) whose T+54 is published on the Met Office AWS bucket; files come as layers need them |
| HARMONIE (KNMI) | P3 (deterministic): rotated pole, 0.05 deg (about 5.5 km), Europe | Newest P3 run on the KNMI Open Data API; only the GRIB messages wanted are fetched from the 3.4 GB archive (byte ranges) |
| AROME 1.3 km (Météo-France) | 0.01 deg lat/lon, 12W-16E, 37.5-55.4N | Météo-France open data (data.gouv.fr "Paquets AROME"), newest run (every 3 h, to T+51) whose last file is published; one file per hour per package; only the messages wanted are fetched (byte ranges) |
| AROME 2.5 km (Météo-France) | 0.025 deg lat/lon, same domain | As above at 0.025 deg |
| AROME-IFS 2.5 km (Météo-France) | 0.025 deg lat/lon, same domain | AROME-IFS packages, newest run (every 6 h, to T+51) |
| ICON-EU (DWD) | 0.0625 deg lat/lon (about 7 km), 23.5W-62.5E, 29.5-70.5N | Newest 00, 06, 12 or 18Z run (to T+120: hourly to T+78, then 3-hourly) whose T+120 is on opendata.dwd.de; the 03/09/15/21Z runs (to T+30 only) are not used; files (bz2 GRIB2, about 1 MB each) come as layers need them |
| ECMWF IFS 0.1 | 0.1 deg lat/lon, North Atlantic and Europe block | ECMWF open data (data.ecmwf.int, `0p1/oper`), newest run whose last step is published: 00 and 12Z to T+120, 06 and 18Z to T+90, every 3 hours; only the fields a layer needs are fetched (HTTP range requests on each step's index) |
| GFS sflux 13 km (NOAA) | T1534 Gaussian (about 13 km), North Atlantic and Europe block | NOAA GFS on AWS (`noaa-gfs-bdp-pds`), newest 00/06/12/18Z run with T+120 published; hourly to T+120; 2 m temperature, 10 m wind (speed, barbs) and rain, fetched by range requests on each step's `.idx` |
| GFS 0.25 (NOAA) | 0.25 deg lat/lon, same block | Same run, 0.25 deg product: MSLP (isobars, centres), most-unstable CAPE (`CAPE:255-0 mb above ground`), gust (instantaneous: GFS has no hourly maximum gust) |
| UKMO Global | 10 km lat/lon | Newest run (every 6 h) whose T+54 is published; files come as layers need them (about 6 MB per field per hour) |
| NWS waves (1.5 km) | about 1.5 km lat/lon, 16W-13E, 46-62.7N | Copernicus Marine `NWSHELF_ANALYSISFORECAST_WAV_004_014` (`cmems_mod_nws_wav_anfc_1.5km_PT1H-i`), today 00Z to +72 h; significant wave height and mean wave direction, as IBI |
| IBI waves | about 3 km lat/lon, 19W-5E, 26-56N | Copernicus Marine `IBI_ANALYSISFORECAST_WAV_005_005`, today 00Z to +72 h; significant wave height and mean wave direction (arrows point where the waves go) |

**Area of the global models**: UKMO Global, ECMWF and both GFS products
are cut to one North Atlantic and Europe box, **80°W–50°E, 15°N–80°N**
(`globe_core.AREAS["atlantic"]`, `globe_ecmwf.AREA`, `globe_gfs.AREA`).
Their fields are still downloaded whole (global) and cut after reading;
`--area world` draws UKMO Global in full but does not change ECMWF or
GFS. The regional models are drawn on their own domains, uncut.

ICON-EU has the same layers as UKV, with two notes: its CAPE is
mixed-layer CAPE (ICON-EU publishes no most-unstable CAPE), and its
rain over a period is the step in its run accumulation (`TOT_PREC`).
Its winds are on a true lat/lon grid.

ECMWF has the same layers as UKV except 1-hour rain: its steps are
3-hourly, so rain windows are built from 3-hour steps of its run
accumulation. Its gust is the maximum over the hour before each step to
T+90 (`10fg`), then over the 3 hours before it (`10fg3`); its CAPE is
most-unstable CAPE (`mucape`). Data: ECMWF open data, CC BY 4.0.

The three Météo-France models need no account. Their files have no
byte index; only the messages wanted are fetched (see Fetching and
the store). CAPE is Météo-France's `CAPE_INS`
(instantaneous CAPE; the files do not say which parcel). Rain over a
period is the step in the run accumulation: `tp` at 0.025 deg; at
0.01 deg rain + snow + graupel (`tirf`, `tsnowp`, `tgrp`) summed. The
**1.3 km model has no MSLP** (Météo-France publishes only surface
pressure at 0.01 deg), so no isobars or centres; its gust comes from
the east/north gust components. The domain ends at 55.4N.

GFS is listed as two models because its two products are on different
grids (fields from `sflux` where it has them, otherwise from 0.25 deg,
as in `reference/NOAA_GFS_S3_surface_fields_guide.md`); each is drawn
on its own grid. GFS rain: `sflux` PRATE is a mean rate since the last
6-hourly reset, so each hour's amount is the step between consecutive
(rate x duration) totals; the hourly amounts add up to GFS's own 6-hour
total (checked to 0.01 mm). Steps below zero from rounding are set to
zero.

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
handle (☰: drag above another model to draw it on top), an arrow (or
click the name) to open or fold its layers (folded at start), an on/off
tick for the whole model, the run (or "no data") and the model's
download button. Each layer has a tick, a **123** tick box for value
labels (coloured layers), an opacity slider, its state (green dot: all
hours ready; amber count: hours ready so far; grey name: not fetched),
its own download button and (when on) its colour key. Above the groups:
the Models and Parameters fetch menus, Fetch, the store's size and
Clear data.

**Point values**: double-click the globe for every switched-on layer's
value at that place (each model's own cell), in a panel that follows
the time steps; close the panel to remove the marker.

**Map overlays** (under the model groups, when there are any): your
own hillshade, sea mask and boundary lines, each with a tick, an
opacity slider and (for labelled lines) an **abc** tick for names. See
Map overlays below.

**Layout**: the layers switched on, opacities, value labels, model
order, open groups, map overlays, graticule, view and play speed are
kept between runs (`globe_settings.json`); **Reset layout** restores the defaults.

**Value labels**: the cells' own values (no interpolation), about 10 km
apart, for the part of the globe in view. Zoomed out, the spacing widens
so no more than about 400 labels are drawn (the status bar says e.g.
"labels every 40 km"); labels follow time steps and are redrawn after
the view stops moving. Dry or see-through cells are not labelled.

**Time bar** (bottom): first, back, play/pause, forward, last; the time
slider, with a strip under it that is green where every switched-on
layer is ready; play speed; the valid time and each model's lead; Home view;
North up; how many layer-hours are ready, downloads in progress, and
fetch messages. Play waits for a time that is not ready yet and stops at
the last one. When the first model loads, the time nearest to now is
shown.

**Mouse**: drag to turn the globe (north stays up), scroll to zoom,
Shift+drag to pan, double-click for point values. **?** (or F1) opens a
short help.

| Key | Action |
|---|---|
| Right / Left | Next / previous time |
| Up / Down, Page Up / Page Down | Six steps on / back |
| Home / End | First / last time |
| Space | Play / pause |
| h | Home view: Ireland, north up |
| n | North up from the current view |
| g | Graticule on / off |
| F1 | Help |

## Options

| Option | Meaning |
|---|---|
| `--load MODEL ...` | Fetch these models (latest run, all layers) at start |
| `--base grey` / `colour` | Background: Natural Earth I shaded relief in grey (default) or in colour |
| `--area atlantic` / `world` | Global area drawn (default `atlantic`: 80°W-50°E, 15-80°N) |
| `--view ireland` / `atlantic` | Starting view |

For testing: `--screenshot FILE --load MODEL ...` draws off screen and
writes a PNG (with `--valid TIME`, `--show` / `--hide` / `--labels
MODEL:LAYER ...`, `--order`, `--steps N`, `--overlays NAME ...`);
`--smoke FILE`
opens the window, saves a picture of it and closes. `globe_fetch.py
harmonie|ibi` fetches from the command line.

## Map overlays

A hillshade, a sea (or land) mask and boundary lines of your own can be
drawn on the globe: pictures (hillshade, mask) on the base map under
every model layer, lines (counties, hydrometric areas) above them with
the coastlines. `tools/prepare_overlays.py` turns a GeoTIFF or a
shapefile / GeoPackage in any projection into a small file in
`data\overlays`; the globe reads those with numpy only, so neither it
nor the standalone build needs GIS libraries. The tool needs rasterio,
geopandas and shapely: the Anaconda base environment has them.

```
set A=C:\Python\Current_UKMO\ukmo_nwp\assets
conda run -n base python tools\prepare_overlays.py hillshade %A%\Ireland_multidirectional_hillshade_200.tif --name hillshade_ie --label "Ireland hillshade"
conda run -n base python tools\prepare_overlays.py lines %A%\counties.shp --name counties --label Counties --label-field COUNTYNAME --colour "#4a4a4a"
conda run -n base python tools\prepare_overlays.py lines %A%\Hydrometric_Areas_wgs84.shp --name hydrometric_areas --label "Hydrometric areas" --label-field HYDRO_NAME --colour "#1f5fa8"
conda run -n base python tools\prepare_overlays.py mask %A%\counties.shp %A%\ne_10m_land_ireland_removed.shp --name sea --label Sea --invert --bounds -80 50 15 80 --res 0.02
```

- **hillshade**: reprojected to longitude/latitude (default 0.001°,
  about 110 m; `--res`). Flat ground (the raster's most common value)
  leaves the base map as it is; shadows darken it and lit slopes
  lighten it (`--strength`, `--light`), so it shades the Natural Earth
  relief rather than covering it. No data is see-through.
- **lines**: polygon outlines or lines, simplified (`--simplify`
  metres) and split into short segments so they follow the globe; one
  name per feature from `--label-field`.
- **mask**: polygons filled with `--colour`, `--invert` for the outside
  (sea from land polygons) within `--bounds`; several files are filled
  together. The sea above covers the global models' area (80°W-50°E,
  15-80°N); a box that big needs a coarser `--res` (0.02°, about 2 km)
  to keep the picture a size any graphics card can hold.

A file whose projection GDAL reads only as a name (e.g. "IRENET95 /
Irish Transverse Mercator") is taken as EPSG:2157; `--crs` sets any
projection. `tools\prepare_overlays.py -h` lists every option. New
files appear at the next start; delete a file to drop it. For the
standalone build, copy `data\overlays` next to `NWP_Globe.exe`.

## Speed

Layers fetched are prepared for every hour and saved, so stepping
through them is near-instant. Drawings are kept and reused: ticking a
layer on or off only shows or hides it, a time step rewrites only the
layers whose picture changed, and layers move up or down (when others
are switched on or reordered) by scaling, without being rebuilt.
Prepared hours are kept in memory up to `GLOBE_MEMORY_MB` (default
1500); older ones are read back from their saved files when shown.
`GLOBE_PROFILE=1` prints how long each redraw takes. UKV and Global
files are downloaded over kept-open connections (about three times
faster per file than a new connection each).

See-through layers are drawn bottom first in the panel's order (the
top model's colours over the lower model's), not mixed together.

## Files

| File | What it holds |
|---|---|
| `globe_core.py` | Layers, rain sums, drawing and background preparation; no window code |
| `globe_jobs.py` | Fetch jobs (window buttons, `prefetch.bat`) and the store |
| `globe_data.py` | The store folder, latest runs, downloading Met Office files |
| `globe_pressure.py` | High/low centres, copied from the UKMO NWP tools |
| `globe_overlays.py` | Map overlays: reading `data\overlays` and draping them |
| `tools/prepare_overlays.py` | Making map overlays from GeoTIFFs and shapefiles |
| `globe_icon.py` | ICON-EU (DWD): latest run, fetching and converting files |
| `globe_ecmwf.py` | ECMWF IFS 0.1 deg open data: latest run, fetching fields |
| `globe_meteofrance.py` | Météo-France AROME 1.3 / 2.5 km and AROME-IFS: latest run, fetching packages |
| `globe_gfs.py` | NOAA GFS (sflux and 0.25 deg) on AWS: latest run, fetching fields |
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
