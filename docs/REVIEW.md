# NWP Globe – review and improvement plan

Review of the globe as it stands (commit `784d5ca`, 18 Nov 2026): what
works, what could go wrong, what would make it easier to use, and what
would add most value. Part 1 explains each item in plain English. Part 2
gives an implementation plan for each one.

## Summary

**What already works well**

- Twelve models from seven sources on one globe, each drawn on its own
  native grid (no resampling), with a consistent set of layers and
  colours.
- Downloads are economical: every source except the wave models fetches
  only the fields asked for. ECMWF and GFS use their published index
  files; HARMONIE and AROME use the header index the globe builds itself
  (MSLP for a whole HARMONIE run is about 50 MB of a 3.4 GB archive).
- Fetching happens in separate processes, so the window stays responsive,
  and everything fetched is kept until a newer run replaces it.
- Layers are drawn in a sensible order (fills, then graticule under
  them, coastlines, contours and barbs on top), and the order of models
  can be dragged.
- A standalone build runs on a computer without Python.

**Priority table**

| ID | Title | Type | Effort | Value |
|---|---|---|---|---|
| A1 | Moving to the store can lose data | Bug | S | High |
| A2 | "Clear data" restart fails in the built program | Bug | S | High |
| A3 | Deletions leave no record | Robustness | S | High |
| A4 | HARMONIE has two different "keep" rules | Bug | S | Medium |
| A5 | Fetching a newer run quietly drops other layers | UX / Bug | M | High |
| A6 | Too many fetch processes at once | Robustness | M | High |
| A7 | No way to cancel a fetch | UX | S | Medium |
| A8 | Closing the window kills fetches | UX | M | Medium |
| B1 | The panel is rebuilt on every progress update | UX / Speed | M | High |
| B2 | Point readout: values under the mouse | Enhancement | M | High |
| B3 | Run age and "newer run available" badge | UX | M | High |
| B4 | Remember the session | UX | M | Medium |
| B5 | Show disk use; clear one model | UX | S | Medium |
| B6 | Feedback from the fetch menus | UX | S | Medium |
| B7 | Ticking a layer that has no data | UX | S | Medium |
| B8 | Show which hours are ready on the time slider | UX | M | Medium |
| B9 | Help panel | UX | S | Low |
| B10 | Clearer failed-fetch messages | UX | S | Medium |
| C1 | Export a picture or animation | Enhancement | M | High |
| C2 | Point meteogram across models | Enhancement | L | High |
| C3 | Difference between two models | Enhancement | L | Medium |
| C4 | Scheduled pre-download | Enhancement | S | Medium |
| C5 | Graticule labels | Enhancement | M | Low |
| C6 | Warnings before keys and datasets expire | Robustness | S | Medium |
| C7 | Automated tests | Robustness | M | High |

Effort: **S** under an hour, **M** half a day to a day, **L** more than a
day.

**Recommended order**: A1, A2, A3, A4, A6, B1, A5, A7, A8, B3, B2, B4,
then the rest as wanted.

**Status (18 Nov 2026)**: A1–A8 and B1–B10 are implemented and tested
(commits `d3750f5` … `bf3e8bc`). While doing B1 a crash was found and
fixed: GRIB decoding is not thread-safe, so every decoder now holds one
lock (`globe_paths.GRIB_LOCK`). C1–C7 are not started.

**Since the review (21 Nov 2026)**: speed work and map overlays
(D1–D5 below), measured off screen with UKV, HARMONIE and Global loaded
and 12 layers on.

| ID | Change | Before | After |
|---|---|---|---|
| D1 | Drawings kept and reused (`Scene.show`): layers hidden rather than removed, rebuilt only when their item changes, moved up or down by scaling the actor instead of re-adding the mesh; coastlines read once; graticule made once; value labels redone only when time, layers, view or data change | layer on/off ~270 ms, redraw 69 ms, graticule ~450 ms | ~3 ms, 0.9 ms, 0 ms |
| D2 | Shown time read at once from saved files; prepared items kept in memory up to `GLOBE_MEMORY_MB` (LRU), rain fields capped (`RAIN_KEEP`) | step to an hour not in memory: blank until a worker loaded it | drawn at once (~120 ms for 12 layers) |
| D3 | UKV / Global downloads over a kept-open `requests.Session` per thread (`globe_data._session`) | ~520 ms per file | ~170 ms per file |
| D4 | **Bug**: see-through layers were blended by VTK's order-independent translucency (its default without depth peeling), so overlapping models were averaged, not stacked, and dragging a model on top had little effect. Now a plain `vtkRenderStepsPass` with the actors sorted by planned height (`Scene._sort_layers`) | top model's colours washed into the one below | top model drawn over the lower one |
| D5 | Map overlays: hillshade, sea mask, boundary lines (`globe_overlays.py`, `tools/prepare_overlays.py`, panel section, saved with the layout, `--overlays` for screenshots) | – | see README, Map overlays |

`GLOBE_PROFILE=1` prints redraw times. Frame time while dragging was
already small (about 11 ms with everything on, including all overlays),
so navigation itself was left alone. `GLOBE_MAX_JOBS` stays at 2 (the
servers ask for modest use).

---

# Part 1 – Review items

## A. Correctness and robustness

### A1 Moving to the store can lose data

When the window starts, it moves anything left in the older folders
(`data\prefetched`, `cache\prefetched`) into the new store. If moving
any one run fails (for example because a file in it is open in another
program, or Windows is still scanning it), the error is ignored. The old
folder is then deleted anyway, including the run that could not be
moved. That run is lost without any message.

**Suggested**: only delete the old folder once it is empty; anything that
could not be moved stays where it is and is tried again at the next
start.

### A2 "Clear data" restart fails in the built program

After **Clear data**, the window deletes the store and starts itself
again. It does this by running "this program with the same arguments".
From source this works. In the built program the first "argument" is the
program's own path, so the new copy receives its own path as an
unexpected option and stops at once. The user sees the window close and
nothing reopen.

**Suggested**: pass only the real arguments in the built program, and
test the restart in both cases.

### A3 Deletions leave no record

Several things delete data on purpose: a newer run replacing an older
one, the HARMONIE fetch's own tidy-up, **Clear data**, and the move to
the store. None of them write down what they removed or why. Earlier in
this project most of the store disappeared and the cause could only be
guessed from file times.

**Suggested**: a small log file in the store (`data\store\log.txt`) with
one line per deletion: time, what, which model and run, and why. It costs
almost nothing and makes any future surprise easy to explain.

### A4 HARMONIE has two different "keep" rules

The store keeps one run per model. The HARMONIE (and wave) download code
also has its own older tidy-up that keeps two runs. The two rules
disagree, so for these models an extra run can linger or be removed at
an unexpected moment.

**Suggested**: one rule, owned by the fetch job; remove the older tidy-up
from the download code.

### A5 Fetching a newer run quietly drops other layers

Say you fetched isobars and 6-hour rain for UKV's 06Z run. Later you
press UKV's model button, which fetches the latest run (12Z) and
everything in it, and that is fine. But if you press only the
**temperature** button and the latest run is newer, the older run, with
your isobars and rain, is deleted. Those layers turn grey again without
any warning.

**Suggested**: when a newer run is fetched, automatically refresh every
layer that had been fetched for the older run, and say so under the
model's name ("new run 12Z: refreshing 3 layers"). The older run stays
on screen until the new one is ready.

### A6 Too many fetch processes at once

With all models ticked in the **Models** menu, **Fetch** starts twelve
separate processes at once. Each one has six workers and its own
off-screen drawing engine. That can use several GB of memory, competes
for the same network connection, and can trip the download limits of
some servers (ECMWF and NOAA both ask clients to be modest).

**Suggested**: run at most two fetches at a time (adjustable); the rest
wait their turn and show "queued".

### A7 No way to cancel a fetch

Once a fetch has started, the only way to stop it is to close the
window. If you started the wrong one, you have to wait or lose
everything else in progress.

**Suggested**: while a model is fetching, its download button becomes a
stop button. Hours already prepared are kept.

### A8 Closing the window kills fetches

Because fetched data is now kept, it would often be useful to start a
large fetch, close the window, and come back later. At the moment
closing the window stops every fetch.

**Suggested**: if fetches are running when the window closes, ask whether
to keep them running in the background. If yes, they continue on their
own and the next time the window opens, the results are there.

## B. User experience

### B1 The panel is rebuilt on every progress update

Every time a fetch reports progress, the whole layer panel is thrown
away and drawn again. That causes flicker, can interrupt an opacity
slider while you are dragging it, and resets tooltips. Each rebuild also
lists the whole prepared-layer folder (thousands of files) to work out
the green dots and counts.

**Suggested**: build the panel once and then only change the bits that
change (the progress line, a dot, a count). Keep the counts in memory
and update them from the fetch progress messages instead of listing the
folder each time.

### B2 Point readout: values under the mouse

There is no way to read the exact value at a place except turning on the
**123** labels, which cover the whole view. Comparing models at one
place (say, gusts at Shannon in UKV, ECMWF and HARMONIE) means switching
layers on and off.

**Suggested**: click a point on the globe (or hold a key and move the
mouse) to show a small box listing, for every layer that is switched on,
each model's value at that point with units, plus the location and the
time.

### B3 Run age and "newer run available"

The model header shows the run time, but not how old it is, and nothing
tells you when a newer run has been published. You have to remember to
press the button.

**Suggested**: show the run's age ("12Z, 7 h old"). Every 15 minutes or
so, check quietly in the background whether a newer run is out; if it
is, show a small badge such as "18Z available" that fetches it when
clicked (see A5 for keeping your layers).

### B4 Remember the session

Every time the window opens, all layers start off, opacities return to
their defaults, the model order resets and the view goes back to
Ireland. If you usually look at the same few layers, you set them up
every time.

**Suggested**: remember layers switched on, opacities, value labels,
model order, which groups are open, the graticule, the view and the
play speed, and restore them at the next start. A "Reset layout" item
puts the defaults back.

### B5 Show disk use; clear one model

The store can grow to many GB (all models, all layers was about 15 GB),
but the window does not say how much is used, and the only way to free
space is **Clear data**, which removes everything.

**Suggested**: show each model's size in its header tooltip and the total
beside **Clear data**. Right-clicking a model header offers "Clear this
model's data".

### B6 Feedback from the fetch menus

If you tick a parameter in the **Parameters** menu that some ticked
models do not have (AROME 1.3 km has no MSLP; P3 HARMONIE has no CAPE),
those models are skipped without a word.

**Suggested**: show in the Parameters menu how many of the ticked models
have each parameter, and after **Fetch** say in the status bar which
models were skipped and why.

### B7 Ticking a layer that has no data

A layer that has not been fetched shows a greyed-out tick that cannot be
ticked. It is not obvious why, unless you hover over it.

**Suggested**: let it be ticked. If it has no data, the globe starts that
layer's fetch (or asks first) and shows it when ready.

### B8 Show which hours are ready on the time slider

When a layer is only partly ready (an amber count such as 12/31), the
missing hours simply show nothing, and you only discover them by
stepping through.

**Suggested**: a thin coloured strip under the time slider: green where
everything switched on is ready, grey where something is missing.

### B9 Help panel

Keys, mouse controls and the meaning of the dots, counts and buttons are
only in the README.

**Suggested**: a "?" button in the toolbar that opens a short help window
with the same information.

### B10 Clearer failed-fetch messages

When a fetch fails, a single line (often cut short) appears under the
model's name. The full output of the fetch, which says what went wrong,
is not kept.

**Suggested**: keep each fetch's full output in a log file. On failure,
the model header shows "Fetch failed – details", which opens that log.

## C. Enhancements

### C1 Export a picture or animation

There is no way to save what is on the globe from the window. Sharing a
view in a briefing or chat needs a screen capture tool.

**Suggested**: toolbar items "Save picture" (PNG of the globe at screen
or higher resolution, with the valid time and run in the corner) and
"Save animation" (GIF or MP4 over a chosen range of times).

### C2 Point meteogram across models

For a single place, a chart of one parameter through time with a line
per model (for example 10 m gust at Dublin Airport from UKV, ECMWF,
ICON-EU, HARMONIE and GFS) is one of the quickest ways to see how much
the models agree.

**Suggested**: from the point readout (B2), "Show through time" opens a
chart window for that point, a chosen parameter and every loaded model.

### C3 Difference between two models

Seeing where two models disagree (for example ECMWF minus UKV MSLP) is
hard by eye.

**Suggested**: a "difference" layer: model A minus model B for one
parameter, with A moved onto B's grid (or both onto the coarser one), in
a blue-white-red colour scale.

### C4 Scheduled pre-download

`prefetch.bat` exists, but has to be run by hand.

**Suggested**: a small `schedule_prefetch.bat` that sets up a Windows
scheduled task (for example 05:00 and 17:00) running `prefetch.bat` with
chosen models and layers, plus a line in the README on removing it.

### C5 Graticule labels

The graticule has no degree labels. Labels drawn on the globe would sit
on top of the data.

**Suggested**: optional labels at the edges of the view only (for example
"50°N" on the left edge, "10°W" on the bottom), so the middle of the
picture stays clear.

### C6 Warnings before keys and datasets expire

Two things will stop working on known dates: the bundled anonymous KNMI
key ends on 1 August 2027, and Copernicus retires the IBI wave dataset
version the globe uses on 12 January 2027.

**Suggested**: from 30 days before each date, show a warning in the
status bar (and in `prefetch.bat` output), and note both in
`docs/CREDENTIALS.md`.

### C7 Automated tests

There are no automated tests. The new byte-range code (HARMONIE, AROME)
reads binary headers by position, so a mistake would show only as wrong
or missing fields. The store, migration and pruning code deletes data.

**Suggested**: a small `tests` folder run with pytest, covering the
header readers (using made-up byte strings), the store's keep and
delete rules (in a temporary folder), the list of layers each model can
have, and the dot and count logic. `build.bat` runs them before
building.

---

# Part 2 – Implementation plans

Each plan names the files and functions, the steps, the edge cases and
how to test.

## A1 Safe move to the store

**Files**: `globe_data.py: migrate`, `globe_jobs.py: migrate_cache`.

**Steps**
1. In `migrate`, after the loop, remove each emptied model folder
   (`os.rmdir`, ignoring errors) and then the old root only if
   `os.listdir(old)` is empty. Replace `shutil.rmtree(old, ...)`.
2. Where the destination run already exists, keep whichever run is
   newer and delete the other only after the move succeeded (log it,
   A3).
3. Same pattern in `migrate_cache`: move each file; remove the folder
   only if empty.
4. Log every failure (A3) with the error text.

**Edge cases**: file locked by antivirus (retry next start); destination
on a different drive (not possible here: both under `APP_DIR`).

**Test**: pytest in a temporary folder with one file opened for reading
(Windows locks it) – the run must still exist afterwards.

**Effort**: S.

## A2 Restart after Clear data

**Files**: `globe.py: run_window.clear_store`.

**Steps**
1. Build the argument list: frozen → `sys.argv[1:]`; from source →
   `sys.argv` (the script path first, then its arguments).
2. Call `QtCore.QProcess.startDetached(sys.executable, args)`.
3. Drop `--load` from the arguments so a restart does not start fetches
   again.

**Edge cases**: the user started the window from `prefetch.bat`'s folder
(working directory differs) – pass `APP_DIR` as the working directory
(`startDetached` overload with `workingDirectory`).

**Test**: built exe → Clear data → window reopens empty; same from
source.

**Effort**: S.

## A3 Deletion log

**Files**: `globe_data.py` (new `log_event`, used by `drop_old_runs`,
`clear_session`, `migrate`), `globe_jobs.py: drop_old_cache,
migrate_cache`, `globe_fetch.py: _prune` (or its removal, A4).

**Steps**
1. `log_event(action, model="", run="", detail="")` appends
   `2026-11-18T19:45Z  drop-run  ukv  20261118T0600Z  newer run 12Z` to
   `data/store/log.txt` (UTF-8, line-buffered, guarded by a file lock or
   simple retry since several processes write).
2. Call it from every delete helper with a reason.
3. Keep the file small: when it passes 1 MB, rename to `log.old.txt`.
4. Window: model header tooltip shows the last log line for that model.

**Edge cases**: Clear data deletes the store folder itself – write the
log line after deletion (the folder is recreated).

**Test**: run a job twice across a run change; check the log lines.

**Effort**: S.

## A4 One keep rule

**Files**: `globe_fetch.py: fetch_harmonie, fetch_waves, _prune`.

**Steps**
1. Remove the `_prune(...)` calls from `fetch_harmonie` and the wave
   fetch; delete `_prune`.
2. Running `globe_fetch.py` directly (not through a job) then keeps all
   runs; add a note to its docstring, or call
   `data.drop_old_runs(model, run)` at the end of its `main`.

**Test**: HARMONIE job for two runs leaves exactly one.

**Effort**: S.

## A5 Keep layers across a new run

**Files**: `globe_jobs.py: run_job`, `globe.py: fetch, start_next`.

**Steps**
1. In `run_job`, before fetching, record which layers are complete for
   the stored run: count prepared files per layer (same rule as
   `layer_state` in `globe.py`; move that function into `globe_jobs`
   so both use it).
2. After `load_model` returns a different (newer) run, add those layers
   to `layers` before preparing, and print
   `NOTE <model> new run <run>: refreshing <n> layers`.
3. Delete the older run (`drop_old_runs`, `drop_old_cache`) only after
   preparation finished, not before, so the window keeps showing it
   meanwhile.
4. Window: show the NOTE line as the model's progress text.

**Edge cases**: the job is cancelled half-way (A7) – the old run is still
there; the next job finishes the change. Layers the new run cannot have
(e.g. different products) are skipped.

**Test**: fetch UKV mslp for an old run (`--job ukv mslp` with a stored
older run), then `--job ukv temperature --latest`; both layers end up
green on the new run.

**Effort**: M.

## A6 Limit concurrent fetches

**Files**: `globe.py: fetch, start_next, finished`.

**Steps**
1. Add `MAX_JOBS = 2` (and an environment override `GLOBE_MAX_JOBS`).
2. `fetch` only queues; a new `pump()` starts jobs while
   `len(running) < MAX_JOBS`, taking models in panel order and at most
   one job per model.
3. Call `pump()` from `fetch` and from each job's `finished`.
4. Progress text "queued (3rd)" for waiting models.
5. In `globe_jobs.prefetch` jobs already run one after another – no
   change.

**Edge cases**: the same model queued twice with different layers –
merge into one job when neither has started (union of layers, `latest`
if either asked).

**Test**: tick all models and one parameter, Fetch – only two
`nwp-globe-fetch` / `python` processes run at any time (Task Manager).

**Effort**: M.

## A7 Cancel a fetch

**Files**: `globe.py: model_group, stop_jobs`, `globe_jobs.py`.

**Steps**
1. When `model in running`, the header button shows a stop icon
   (`QStyle.SP_MediaStop`) with tooltip "Stop this fetch".
2. Clicking it: `proc.kill()`, clear `waiting[model]`, progress text
   "stopped".
3. On the next job for the model, `run_job` removes leftover
   `*.part.npz` in the store cache (already written atomically, so
   partial files are only these).
4. Downloaded source files stay; they are complete files or absent
   (written via temporary names in `globe_data`).

**Test**: start an ECMWF all-layers fetch, stop after a few seconds,
start again – it continues and finishes.

**Effort**: S.

## A8 Fetches that outlive the window

**Files**: `globe.py: stop_jobs, start_next`, `globe_jobs.py`.

**Steps**
1. On close with jobs running or queued, ask: "Keep fetching in the
   background?" (Yes / Stop them / Cancel close).
2. Yes: for each running job do nothing (detach: the processes keep
   running if started with `startDetached`); queued jobs are written to
   `data/store/queue.json` and a detached `--prefetch-queue` run takes
   them one by one.
3. To allow detaching, start jobs with `QProcess.startDetached` writing
   progress to `data/store/jobs/<model>.log`, and have the window follow
   that file (QFileSystemWatcher or the existing 500 ms poll) instead
   of reading the pipe. This also gives B10 its log.
4. A lock file per model (`jobs/<model>.lock` with the process id)
   prevents a second window or job starting the same model; at start the
   window adopts running jobs it finds.

**Edge cases**: process killed by shutdown – stale lock file (check the
process id is alive).

**Test**: start a fetch, close the window, choose Yes, reopen – progress
continues, model loads when finished.

**Effort**: M.

## B1 Update the panel in place

**Files**: `globe.py: model_group, build_items, poll, layer_state`.

**Steps**
1. Keep `widgets[model] = {"note": QLabel, "title": QPushButton,
   "rows": {style: (tick, state, ...)}}` when a group is built.
2. New `refresh_group(model)` updates title, note text and each row's
   dot or count and tick enabled state.
3. `poll` calls `refresh_group` for models whose progress or counts
   changed; `build_items` only on reorder, model loaded, or first build.
4. Counts: keep `counts[model][style]`; start from one folder listing at
   load; update from `PROGRESS` lines (send the layer name with each:
   `PROGRESS <model> <layer> <done> <wanted>`) and from the scene's own
   saves (`Scene.prepare` reports via a callback).

**Test**: drag an opacity slider while a fetch is running – no jump;
panel does not flicker.

**Effort**: M.

## B2 Point readout

**Files**: `globe.py` (mouse handling), `globe_core.py` (new
`Scene.values_at(lon, lat)`).

**Steps**
1. Pick: on left click without drag (or with Ctrl), use VTK's cell
   picker on the base globe (`pl.pick_click_position` / a
   `vtkCellPicker`) to get the 3D point; convert to lon/lat with
   `geovista.common.from_cartesian`.
2. `values_at`: for each visible raster layer, find the cell of each
   model's grid containing the point (lat/lon grids: index arithmetic;
   UKV Lambert and HARMONIE rotated: transform the point with the same
   projection used to build the grid, then index), read the value from
   the prepared item (rain totals included), and format with the layer's
   units. Line layers: MSLP value from the MSLP field; barbs: speed and
   direction.
3. Show a small floating panel near the cursor, updating with time steps
   until closed; a marker on the globe at the point.

**Edge cases**: point outside a model's domain ("outside"); missing hour
("not ready").

**Test**: click Dublin with UKV and ECMWF temperature on; values match
`--labels` output for that cell.

**Effort**: M.

## B3 Run age and newer run

**Files**: `globe.py: model_group`, `globe_data.py: latest_run` (reuse),
field sources' `latest_run`.

**Steps**
1. Header text: `UKV 12Z (7 h old)`; age from the run name and UTC now,
   updated by the poll once a minute.
2. A `QTimer` every 15 min starts a thread calling the model's existing
   latest-run function (Met Office: `globe_data.latest_run`; others:
   `field_source(model).latest_run`; HARMONIE: KNMI listing; waves: date).
   Network errors are ignored.
3. If newer than stored: a small blue "18Z available" button in the
   header that queues `fetch(model, latest=True)` with A5's layer
   carry-over.

**Edge cases**: offline – no badge; latest-run check is slow (HEAD
requests) – one model at a time.

**Test**: with an older stored run, badge appears within a minute of
start (do the first check 30 s after start).

**Effort**: M.

## B4 Remember the session

**Files**: `globe.py: run_window` (save on `aboutToQuit`, restore after
`add_model`).

**Steps**
1. Use `QSettings` with an INI file in `APP_DIR` (`globe_settings.ini`)
   so it travels with the build.
2. Save: per layer id `visible`, `opacity`, `labels`; `panel_order`;
   `collapsed`; graticule; play speed; camera position, focal point and
   view-up; window geometry and dock state.
3. Restore: geometry and dock at start; per-model settings in `poll`
   when that model is added (`scene.add_model` then apply); camera after
   the first model loads.
4. Menu item (toolbar) "Reset layout": delete the file and restore the
   defaults.

**Test**: set layers and view, close, reopen – same picture.

**Effort**: M.

## B5 Disk use and clear one model

**Files**: `globe.py: model_group, clear_store`, `globe_jobs.py` (new
`model_size`, `clear_model`).

**Steps**
1. `model_size(model)`: walk `data/store/<dir>` plus
   `cache/store/<model>_*`; computed in a thread at load and after each
   job; cached.
2. Header tooltip "2.3 GB on disk"; label beside Clear data "Store:
   14.8 GB".
3. Header context menu: "Clear this model's data" (confirmation; not
   while fetching), removes both folders/files, logs (A3), removes the
   model from the scene (new `Scene.remove_model`: drop actors, layers,
   cache keys, recompute times).

**Test**: clear ECMWF only; others unaffected; size totals update.

**Effort**: S (without `remove_model`), M with it.

## B6 Fetch menu feedback

**Files**: `globe.py: checklist, fetch_menus`.

**Steps**
1. When either menu is about to show (`aboutToShow`), relabel parameter
   actions: "MSLP contours (10 of 11 models)" counting ticked models
   whose `core.capable_styles` include it.
2. In `fetch_menus`, collect skipped (model, layer) pairs and write
   "Skipped: AROME 1.3 km – no MSLP contours" to `status_note`
   (shortened if long, full list in its tooltip).

**Test**: tick AROME 1.3 km and MSLP contours only – message names it.

**Effort**: S.

## B7 Ticking a layer with no data

**Files**: `globe.py: model_group`.

**Steps**
1. Enable the tick always. If the layer has no data (`lid not in
   scene.visible` or zero prepared), ticking queues `fetch(model,
   [name])` and records the wish to show it; when the model reloads,
   switch that layer on.
2. Tooltip: "Not fetched: ticking fetches it".

**Edge cases**: model with no run at all – the same, the job fetches the
latest run.

**Test**: tick a grey layer – fetch starts, layer appears when done.

**Effort**: S.

## B8 Ready strip on the time slider

**Files**: `globe.py` (new small `ReadyStrip(QWidget)` under the slider).

**Steps**
1. `paintEvent` draws one segment per time in `scene.times`: green if
   every visible layer is in `scene.cache` or prepared on disk for that
   time, light grey otherwise, current time outlined.
2. Updated by the poll when the ready set changes (`scene.status()`
   already counts).
3. Same width and margins as the slider so segments line up.

**Test**: partially fetched layer – grey gaps where hours are missing.

**Effort**: M.

## B9 Help window

**Files**: `globe.py`.

**Steps**
1. Toolbar action "?" opening a `QMessageBox` or small dialog with rich
   text: keys (from the `shortcuts` table, so it stays in step), mouse,
   dots and counts, download buttons, where data is kept.
2. Key F1 opens it.

**Effort**: S.

## B10 Failed fetch details

**Files**: `globe.py: start_next`, `globe_jobs.py`.

**Steps**
1. Write each job's whole output to `data/store/jobs/<model>.log`
   (window side: append in `output()`; or the job itself, which A8
   needs anyway).
2. On failure the progress line becomes a link "Fetch failed – details"
   (`QLabel` with `linkActivated`) opening the log in a read-only
   dialog, with the error text highlighted.
3. Common errors get plain messages: no network, 401/403 (key),
   Copernicus login missing (point to `docs/CREDENTIALS.md`).

**Test**: rename the Copernicus credentials file, fetch IBI – clear
message and log.

**Effort**: S.

## C1 Export picture and animation

**Files**: `globe.py` (toolbar), `globe_core.py` (reuse `show`).

**Steps**
1. "Save picture…": `pl.screenshot(path, scale=2)` after `scene.show`
   with all items ready (`block=True` as `run_screenshot` does).
2. "Save animation…": dialog with from/to time and frames per second;
   for each time: `scene.go(i)`, wait until ready, `pl.screenshot`;
   write with `imageio` (GIF) or `imageio-ffmpeg` (MP4; add to
   `environment.yml` and the build).
3. Run the frame loop from a `QTimer` so the window stays responsive;
   progress in the status bar, cancel button.

**Edge cases**: hours not ready – wait up to a limit, then skip with a
note.

**Test**: save a 12-frame GIF of ECMWF MSLP; frames show successive
times.

**Effort**: M.

## C2 Point meteogram

**Files**: new `globe_meteogram.py` (Qt chart with matplotlib's
`FigureCanvasQTAgg`, already available via dependencies, or pyqtgraph);
`globe_core.py: values_at` from B2.

**Steps**
1. From the readout, "Through time…" opens a window: parameter
   drop-down (temperature, gust, wind speed, MSLP, hourly rain, CAPE),
   one line per loaded model with that parameter, x axis valid time,
   current time marked.
2. Values come from the prepared items in the store cache (fast) or are
   computed on demand with `Scene.prepare` in a worker; missing hours
   left as gaps.
3. Rain as bars for one model, lines for comparisons.
4. Export the chart as PNG/CSV.

**Test**: Dublin gust for UKV/ECMWF/ICON-EU; values equal the readout at
each time.

**Effort**: L.

## C3 Model difference layer

**Files**: `globe_core.py` (new style kind `diff`), `globe.py` (a
"Compare…" dialog).

**Steps**
1. Dialog: parameter, model A, model B. Adds a virtual model
   `diff:A-B` to the scene with one raster layer.
2. Regrid A onto B's grid (nearest cell for equal or finer A,
   area-mean when A is finer: `scipy.ndimage` / simple block mean on
   regular grids; for projected grids, KD-tree on cell centres with
   `scipy.spatial.cKDTree`).
3. Palette: diverging bands (e.g. −8 … +8 hPa in 1 hPa steps) defined
   per parameter.
4. Times: only those both models have.

**Edge cases**: different domains – only the overlap; GFS sflux
Gaussian grid – KD-tree path.

**Test**: ECMWF − UKV MSLP near zero over most of the domain; sign checks
on a known low.

**Effort**: L.

## C4 Scheduled pre-download

**Files**: new `schedule_prefetch.bat`, `README.md`.

**Steps**
1. `schedule_prefetch.bat [time ...] [-- prefetch arguments]` runs
   `schtasks /Create /TN "NWP Globe prefetch HHMM" /SC DAILY /ST HH:MM
   /TR "\"%~dp0prefetch.bat\" ..."` for each time (default 05:00 and
   17:00).
2. `schedule_prefetch.bat /remove` deletes those tasks.
3. Prefetch output appended to `data/store/jobs/prefetch.log`.
4. Copy it into the build in `build.bat` (next to `prefetch.bat`).

**Test**: create a task a few minutes ahead; check the log and store.

**Effort**: S.

## C5 Graticule labels

**Files**: `globe_core.py: set_graticule`, `update_labels` pattern.

**Steps**
1. After the camera settles (same trigger as value labels), find where
   each 10° line crosses the left and bottom edges of the view: sample
   points along each line, project with the renderer
   (`vtkCoordinate` world → display), keep the ones within a margin of
   the edge.
2. Draw them as 2D text actors (`pl.add_text` positioned in pixels or
   `vtkTextActor`), small, white with dark outline.
3. Toggle with the Graticule button (labels follow it) or a separate
   option.

**Test**: labels match coastline positions (e.g. 50°N through Cornwall).

**Effort**: M.

## C6 Expiry warnings

**Files**: `globe_fetch.py` (KNMI key), wave model settings, `globe.py`
(status bar), `globe_jobs.py: prefetch`, `docs/CREDENTIALS.md`.

**Steps**
1. Table `EXPIRES = {"KNMI anonymous key": date(2027, 8, 1),
   "IBI waves dataset 202411": date(2027, 1, 12)}`; the KNMI entry only
   applies when the anonymous key is in use.
2. Within 30 days: status-bar note (orange) and a line at the start of
   `prefetch` output, with what to do.
3. After the date: the fetch error message names the cause.

**Test**: temporarily set a date 10 days ahead – note shows.

**Effort**: S.

## C7 Automated tests

**Files**: new `tests/` (`test_headers.py`, `test_store.py`,
`test_styles.py`), `build.bat` step 4, `environment.yml` (pytest).

**Steps**
1. Headers: build GRIB1/GRIB2 header bytes by hand (or cut the first
   kilobyte of a real message saved under `tests/data`) and check
   `_grib1_index` keys, `_grib2_key` for templates 4.0 and 4.8, and
   `_tar_members` on a small TAR made with `tarfile` (including a PAX
   long name), serving bytes through a fake session whose `get` honours
   `Range`.
2. Store: point `globe_data.DATA` and `globe_core.STORE_CACHE` at a
   `tmp_path`; check `drop_old_runs`, `drop_old_cache`, `migrate` (A1
   case with a locked file), deletion log (A3).
3. Styles: `capable_styles` per model (e.g. AROME 1.3 km has no
   `mslp`, ECMWF has no `rain_1h`, waves only wave layers).
4. Layer state: counts from a fake cache folder.
5. `build.bat`: run `python -m pytest -q tests` after pyflakes; stop the
   build on failure.

**Test**: `conda run -n ukmo_globe python -m pytest -q tests`.

**Effort**: M.
