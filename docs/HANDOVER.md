# Handover – speed work and map overlays (21 Nov 2026)

What was done in this session, the state it is left in, what has and has
not been checked, and what to do next. What each change is and why is
in [REVIEW.md](REVIEW.md) (items D1–D5); how to use it is in the
[README](../README.md) (Map overlays, Speed).

## State

- Branch `main`, pushed to GitHub: `f1f68d3` speed work and the
  layer-order fix (D1–D4), `2b29c37` map overlays (D5, with this file),
  `419ea9c` this file's update, then the sea mask change (23 Nov). The
  first commit was checked on its own (pyflakes, timings, window smoke
  test).
- Changed: `globe_core.py`, `globe.py`, `globe_data.py`, `README.md`,
  `docs/REVIEW.md`, `packaging/NWP_Globe.spec`.
- New: `globe_overlays.py`, `tools/prepare_overlays.py`, this file.
- Made, not in git (`data/` is ignored): `data/overlays/` with
  `hillshade_ie.npz` (7.8 MB), `counties.npz`, `hydrometric_areas.npz`,
  `sea.npz`, from `C:\Python\Current_UKMO\ukmo_nwp\assets` with the
  commands in the README. **Not in git**: on a new machine copy
  `data\overlays` across by hand (or remake it with the prep tool).
- `globe_settings.json` was used for a test and put back byte for byte.
- `dist\NWP_Globe` rebuilt from `2b29c37` (21 Nov, 22:1x): all six
  `build.bat` steps passed, 1.40 GB; `data\overlays` copied next to the
  exe, and the exe draws them (`--screenshot ... --overlays`).
- The store's UKV run is now 21 Nov 18Z, fetched with the new download
  code (see Checked).

## What changed, in short

| ID | Change | Where |
|---|---|---|
| D1 | Actors reused: hide/show, rebuild only on new data, heights by `actor.SetScale` | `Scene.show`, `_show_raster`, `_show_lines`, `_place_coastlines`, `set_graticule`, `update_labels` |
| D2 | Shown time read straight from saved files; memory cap `GLOBE_MEMORY_MB` (LRU); rain cache `RAIN_KEEP` | `Scene._load_shown`, `_remember`, `_forget`, `_rain` |
| D3 | Kept-open HTTP session per download thread | `globe_data._session`, `Files._download` |
| D4 | Bug fix: see-through layers stacked in height order, not averaged by VTK's order-independent translucency | `Scene.__init__` (`vtkRenderStepsPass`), `Scene._sort_layers` |
| D5 | Map overlays: prep tool, loader, panel, settings, `--overlays` | `tools/prepare_overlays.py`, `globe_overlays.py`, `Scene.add_overlays` / `_show_overlays`, `globe.py` (`overlay_box`) |

Also: `GLOBE_PROFILE=1` (redraw timings); F1 help mentions overlays;
`globe_overlays` added to the spec's hidden imports.

## Checked

- pyflakes clean on every changed file.
- Off-screen timings, before → after (3 models, 12 layers): layer on/off
  ~270 → ~3 ms; redraw with no change 69 → 0.9 ms; graticule toggle
  ~450 → 0 ms; time step (in memory) 17 → 10 ms; time step from saved
  files ~120 ms (was blank until a worker loaded it). Frame time while
  dragging about 11 ms with everything on.
- Downloads: same 12 UKV files, urllib ~520 ms/file vs session
  ~170 ms/file; a downloaded file is byte-identical to the stored one;
  a not-published file (404) is still marked missing.
- Pictures: old vs new screenshot after toggles and steps matches,
  except (intended) coastlines now always on top and the top model's
  colours no longer averaged with the one below (D4).
- Overlays: hillshade lines up with the coastline; rain draws over the
  sea mask; county and hydrometric lines follow the coast; names shown.
- Real window (`--smoke`) with overlays switched on in the settings:
  panel section appears, saved state restored and written back.
- `globe.py --screenshot ... --overlays hillshade_ie counties` works.
- Real fetch job, `globe.py --job ukv --latest`: the whole 21 Nov 18Z
  run (660 layer-hours) in 49 s, no errors; 384 files (392 MB), no
  `.part` files left, 625 prepared items (the rest are rain windows that
  cannot exist yet, e.g. 24-hour rain before T+24); the older run was
  dropped and logged; the new run loads.
- `build.bat`: passed, including its smoke test of both programs; the
  built exe draws the overlays.

- By hand in the window (user, 23 Nov): overlays, sliders, ticks.
- Another computer (user, 23 Nov): laptop with an Intel Core 135U and
  integrated graphics works, though less smoothly than the desktop. If
  the hillshade weighs on it, remake that with `--res 0.002`.

## Not checked

- Nothing outstanding from this work.

## Known points and loose ends

- **Sea mask**: remade on 23 Nov over the global models' area
  (`--bounds -80 50 15 80 --res 0.02`, 4392×3250 pixels), so its edge
  is the global models' edge, not a box round Ireland. Overlay pictures'
  surfaces are now capped at 400×400 cells (`globe_overlays.image_mesh`;
  this box would have been about 2.6M points, now 161k, made in 0.35 s).
  The colour `#9cc3e0` is still close to the lightest rain band
  (`#BFEFFF`).
- **First step to a new time** can take ~0.6 s once (first raster actor
  made); afterwards ~120 ms from disk, ~10 ms from memory.
- **`RAIN_KEEP` = 240 fields**: with several models' run-total rain on,
  old hourly fields may be read again; raise it if run totals feel slow.
- **Prep tool environment**: run it in `conda base`. Base's rasterio
  cannot look up EPSG codes (its PROJ database is from another
  install), so the tool reprojects with pyproj and uses rasterio only to
  read pixels and to fill masks.
- **The hillshade file's projection** is stored only as a name
  ("IRENET95 / Irish Transverse Mercator"); the tool maps it to
  EPSG:2157. Use `--crs` for other such files.
- **Line endings**: one scripted edit had turned `globe_core.py` into
  CRLF; it was put back to LF. Git's `autocrlf` warnings on commit are
  normal for this repo.
- The timing and comparison scripts used for the numbers above were
  temporary (session scratch folder) and are not kept. Worth turning
  into `tools/bench.py` as part of C7 (automated tests).

## Next steps

1. Rebuild the exe (`build.bat`) to carry the mesh cap; the new
   `sea.npz` is already copied next to it.
2. Carry on with C1–C7 in [REVIEW.md](REVIEW.md); C7 (tests) could start
   from the checks listed above.
