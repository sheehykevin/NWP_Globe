"""3D globe for weather and wave model output (prototype).

A Qt window around the globe in ``globe_core.py``. It opens empty: each
model's group in the layer panel has a download button that fetches its
latest run, and everything downloaded is deleted when the window closes.
Drag a group above another to draw that model on top; each layer has
on/off, value labels, transparency and its colour key. The time bar has
first, back, play, forward, last, a time slider and play speed, as in
the UKMO NWP viewer.

    conda env create -f environment.yml
    conda run -n ukmo_globe python globe.py

Keys: Left/Right one step, Up/Down six hours, Space play/pause, Home and
End first/last time, h home view (Ireland, north up), n north up.
"""

from __future__ import annotations

import argparse
import os
import sys

import globe_core as core
import globe_data as data
from globe_core import STYLES

STYLE_SHEET = """
QWidget { font-size: 9pt; }
QListWidget::item { border-bottom: 1px solid #c8c8c8; }
QListWidget::item:selected { background: #e8eef8; }
"""
ALL_LAYERS = [core.layer_id(m, s) for m in data.MODELS for s in STYLES]


def colour_key(style, QtGui, QtCore):
    """A colour key as a picture: one swatch per band with its lower
    bound under it and the units; a line sample for line layers."""
    w = 250
    if style["kind"] != "raster":
        pix = QtGui.QPixmap(w, 16)
        pix.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pix)
        font = p.font()
        font.setPointSize(7)
        p.setFont(font)
        if style["kind"] == "centres":
            for x, (letter, colour) in zip((0, 18),
                                           core.CENTRE_COLOURS.items()):
                p.setPen(QtGui.QColor(colour))
                p.drawText(x, 0, 16, 14, QtCore.Qt.AlignLeft, letter)
            text = "closed highs and lows, as the viewer"
        else:
            pen = QtGui.QPen(QtGui.QColor(style["colour"]))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawLine(0, 7, 30, 7)
            text = {"isobars": "every 4 hPa",
                    "arrows": "where the waves are going"}.get(
                style["kind"], "knots: half 5, full 10, pennant 50")
        p.setPen(QtGui.QColor("#555555"))
        p.drawText(40, 0, w - 40, 14, QtCore.Qt.AlignLeft, text)
        p.end()
        return pix
    pix = QtGui.QPixmap(w, 40)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    font = p.font()
    font.setPointSize(7)
    p.setFont(font)
    n = len(style["colours"])
    cw = (w - 10) // n
    for k, colour in enumerate(style["colours"]):
        p.fillRect(k * cw, 0, cw, 11, QtGui.QColor(colour))
        p.setPen(QtGui.QColor("#888888"))
        p.drawRect(k * cw, 0, cw, 11)
        p.setPen(QtGui.QColor("#202020"))
        p.drawText(k * cw, 13, cw, 12, QtCore.Qt.AlignLeft,
                   f"{style['levels'][k]:g}")
    p.setPen(QtGui.QColor("#555555"))
    p.drawText(0, 26, w, 13, QtCore.Qt.AlignLeft, style["units"])
    p.end()
    return pix


def download_icon(QtGui, QtCore, size=18):
    """A box with a down arrow, for the per-model download buttons."""
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor("#2b5fb4"))
    pen.setWidth(2)
    p.setPen(pen)
    s = size
    p.drawRect(2, int(s * 0.45), s - 4, int(s * 0.5) - 2)    # the box
    p.drawLine(s // 2, 1, s // 2, int(s * 0.7))               # shaft
    p.drawLine(s // 2, int(s * 0.7), s // 2 - 4, int(s * 0.7) - 4)
    p.drawLine(s // 2, int(s * 0.7), s // 2 + 4, int(s * 0.7) - 4)
    p.end()
    return QtGui.QIcon(pix)


def restart_args(argv=None, frozen=None):
    """Arguments to start this window again: the built program gets only
    its options (sys.argv[0] is the exe itself), Python gets the script
    first. --load is dropped so a restart fetches nothing."""
    argv = list(sys.argv if argv is None else argv)
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    out, skip = [], False
    for a in argv[1:]:
        if a == "--load":
            skip = True
            continue
        if skip and not a.startswith("--"):
            continue
        skip = False
        out.append(a)
    return out if frozen else [os.path.abspath(argv[0])] + out


def run_window(args):
    import os
    import queue
    import threading

    from geovista.qt import GeoQtInteractor
    from qtpy import QtCore, QtGui, QtWidgets

    import globe_jobs as jobs

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    data.migrate()                           # older layouts -> the store
    jobs.migrate_cache()
    data.ALLOW_DOWNLOAD = False              # only fetch jobs download

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("UKMO NWP Globe")
    win.resize(1500, 950)
    view = GeoQtInteractor()
    win.setCentralWidget(view)
    scene = core.Scene(view, area=args.area, base=args.base, view=args.view)
    # PyVista zooms on Up/Down by default; here they step time.
    for key in ("Up", "Down"):
        view.clear_events_for_key(key)

    collapsed = {m: True for m in data.MODELS}  # model lists folded
    panel_order = list(core.MODEL_ORDER)        # top first, loaded or not
    done = queue.Queue()                        # (model, LoadedModel|error)
    running = {}                                # model -> QProcess
    waiting = {m: [] for m in data.MODELS}      # model -> [(layers, latest)]
    progress = {}                               # model -> text
    icon = download_icon(QtGui, QtCore)
    small_icon = download_icon(QtGui, QtCore, 14)

    # ---- fetch jobs (separate processes; the store keeps the results) ----
    def job_command(model, layers, latest):
        argv = ["--job", model, *layers] + (["--latest"] if latest else [])
        if getattr(sys, "frozen", False):
            return (os.path.join(os.path.dirname(sys.executable),
                                 "nwp-globe-fetch.exe"), argv)
        return sys.executable, [os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "globe.py"), *argv]

    def fetch(model, layers=None, latest=False):
        """Queue a job: ``layers`` of ``model`` (default: all)."""
        layers = [s for s in (layers or core.capable_styles(model))
                  if s in core.capable_styles(model)]
        if not layers:
            return
        waiting[model].append((layers, latest))
        progress[model] = "queued"
        if model not in running:
            start_next(model)
        build_items()

    def start_next(model):
        if not waiting[model]:
            return
        layers, latest = waiting[model].pop(0)
        proc = QtCore.QProcess(win)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        last = [""]
        names = ", ".join(STYLES[s]["name"] for s in layers) \
            if len(layers) <= 2 else f"{len(layers)} layers"
        progress[model] = f"fetching {names} ..."

        def output():
            text = bytes(proc.readAll()).decode("utf-8", "replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("PROGRESS "):
                    _, _, d, w = line.split()
                    progress[model] = f"fetching {names}: {d}/{w}"
                elif line.startswith("DONE "):
                    last[0] = "DONE"
                elif line.startswith("FAILED "):
                    last[0] = line.split(" ", 2)[2]
                elif line:
                    progress[model] = (f"fetching {names}: "
                                       f"{line[:60]}")

        def finished(code, _status):
            running.pop(model, None)
            if last[0] == "DONE":
                progress[model] = "loading ..."
                run = jobs.stored_run(model)

                def work():
                    try:
                        done.put((model, core.load_model(model, run=run,
                                                         area=args.area)))
                    except Exception as exc:
                        done.put((model, exc))
                threading.Thread(target=work, daemon=True).start()
            else:
                progress[model] = ("fetch failed: " + (last[0] or
                                   f"exit code {code}"))[:120]
            if waiting[model]:
                start_next(model)
            build_items()
        proc.readyRead.connect(output)
        proc.finished.connect(finished)
        running[model] = proc
        prog, argv = job_command(model, layers, latest)
        proc.start(prog, argv)

    def stop_jobs():
        for proc in list(running.values()):
            proc.kill()
            proc.waitForFinished(3000)
    app.aboutToQuit.connect(stop_jobs)

    # ---- layer panel: one group per model, drag to reorder ----
    def layer_state(model):
        """({layer: hours prepared in the store}, hours in the run)."""
        if model not in scene.models:
            return {}, 0
        run = scene.runs[model]
        with scene.lock:
            params = {p for s in scene.styles[model] for p in core.needs(s)}
            hours = len({v for p in params
                         for v, _ in scene.files[model].expected(p)})
        counts = {}
        tail = f"_{run}_"
        try:
            names = os.listdir(scene.disk)
        except OSError:
            names = []
        for f in names:
            if f.startswith(model + "_") and tail in f and                     f.endswith(".npz") and not f.endswith(".part.npz"):
                style = f[len(model) + 1:f.index(tail)]
                counts[style] = counts.get(style, 0) + 1
        return counts, hours

    def model_group(model):
        box = QtWidgets.QFrame()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(1)
        head = QtWidgets.QHBoxLayout()
        grip = QtWidgets.QLabel("☰")
        grip.setToolTip("Drag to draw this model above or below the others")
        grip.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        grip.setStyleSheet("color:#777;font-size:12pt")
        loaded = model in scene.models
        fold = QtWidgets.QToolButton()
        fold.setArrowType(QtCore.Qt.RightArrow if collapsed[model]
                          else QtCore.Qt.DownArrow)
        fold.setAutoRaise(True)
        fold.setToolTip("Show / hide this model's layers")
        on = QtWidgets.QCheckBox()
        on.setChecked(scene.model_on.get(model, True))
        on.setEnabled(loaded)
        on.setToolTip("Show / hide the whole model")
        on.toggled.connect(lambda v, m=model: scene.set_model_on(m, v))
        title = QtWidgets.QPushButton(
            data.MODELS[model]["label"] + (f"  {scene.runs[model]}"
                                           if loaded else "  (no data)"))
        title.setFlat(True)
        last = data.last_event(model)
        title.setToolTip("Open / fold this model's layers"
                         + (f"\nLast store change: {last}" if last else ""))
        title.setStyleSheet("font-weight:bold;text-align:left;padding:0")
        get = QtWidgets.QToolButton()
        get.setIcon(icon)
        get.setToolTip("Fetch and prepare everything of this model's "
                       "latest run")
        get.clicked.connect(lambda _=False, m=model: fetch(m, latest=True))
        head.addWidget(grip)
        head.addWidget(fold)
        head.addWidget(on)
        head.addWidget(title, 1)
        head.addWidget(get)
        lay.addLayout(head)
        if model in progress:
            note = QtWidgets.QLabel(progress[model])
            note.setStyleSheet("color:#2b5fb4;font-size:8pt;margin-left:40px")
            note.setWordWrap(True)
            lay.addWidget(note)
        body = QtWidgets.QWidget()
        rows = QtWidgets.QGridLayout(body)
        rows.setContentsMargins(40, 0, 0, 0)
        rows.setVerticalSpacing(1)
        r = 0
        prepared, hours = layer_state(model)
        for name in reversed(core.capable_styles(model)):  # top first
            lid = core.layer_id(model, name)
            style = STYLES[name]
            has = lid in scene.visible
            tick = QtWidgets.QCheckBox(style["name"])
            tick.setChecked(has and scene.visible[lid])
            tick.setEnabled(has)
            n = prepared.get(name, 0)
            state = QtWidgets.QLabel()
            if hours and n >= hours:
                state.setText("●")
                state.setStyleSheet("color:#2e9e3e")
                state.setToolTip(f"Fetched: all {hours} hours ready")
            elif n:
                state.setText(f"{n}/{hours}")
                state.setStyleSheet("color:#c07a00;font-size:8pt")
                state.setToolTip(f"{n} of {hours} hours ready: press the "
                                 "download button for the rest")
            else:
                tick.setStyleSheet("color:#8a8a8a")
                tick.setToolTip("Not fetched: press this layer's download "
                                "button")
            values = QtWidgets.QCheckBox("123")
            values.setToolTip("Label the cells' own values (about 10 km "
                              "apart when zoomed in)")
            values.setChecked(has and scene.label_on[lid])
            values.setVisible(style["kind"] == "raster")
            values.setEnabled(has)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(round(scene.opacity.get(
                lid, style.get("opacity", 1.0)) * 100)))
            slider.setFocusPolicy(QtCore.Qt.NoFocus)
            slider.setToolTip("Opacity")
            slider.setMaximumWidth(60)
            slider.setEnabled(has)
            pct = QtWidgets.QLabel(f"{slider.value()}%")
            pct.setMinimumWidth(30)
            one = QtWidgets.QToolButton()
            one.setIcon(small_icon)
            one.setAutoRaise(True)
            one.setToolTip(f"Fetch {style['name']} for every hour "
                           "(downloads only what is not here yet)")
            one.clicked.connect(lambda _=False, m=model, s=name: fetch(m, [s]))
            key = QtWidgets.QLabel()
            key.setPixmap(colour_key(style, QtGui, QtCore))
            key.setVisible(tick.isChecked())

            def toggled(v, lid=lid, key=key):
                key.setVisible(v)
                resize_items()
                scene.set_visible(lid, v)

            def moved(v, lid=lid, label=pct):
                label.setText(f"{v}%")
                scene.set_opacity(lid, v / 100.0)
            tick.toggled.connect(toggled)
            values.toggled.connect(lambda v, lid=lid: scene.set_labels(lid, v))
            slider.valueChanged.connect(moved)
            rows.addWidget(tick, r, 0)
            rows.addWidget(values, r, 1)
            rows.addWidget(slider, r, 2)
            rows.addWidget(pct, r, 3)
            rows.addWidget(state, r, 4)
            rows.addWidget(one, r, 5)
            rows.addWidget(key, r + 1, 0, 1, 6)
            r += 2
        body.setVisible(not collapsed[model])
        lay.addWidget(body)

        def fold_clicked():
            collapsed[model] = not collapsed[model]
            body.setVisible(not collapsed[model])
            fold.setArrowType(QtCore.Qt.RightArrow if collapsed[model]
                              else QtCore.Qt.DownArrow)
            resize_items()
        fold.clicked.connect(fold_clicked)
        title.clicked.connect(fold_clicked)
        return box

    groups = QtWidgets.QListWidget()
    groups.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
    groups.setDefaultDropAction(QtCore.Qt.MoveAction)
    groups.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
    groups.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)

    def resize_items():
        for k in range(groups.count()):
            item = groups.item(k)
            w = groups.itemWidget(item)
            if w is not None:
                w.adjustSize()
                item.setSizeHint(w.sizeHint())

    def build_items():
        bar_pos = groups.verticalScrollBar().value()
        groups.blockSignals(True)
        groups.clear()
        for model in panel_order:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, model)
            groups.addItem(item)
            groups.setItemWidget(item, model_group(model))
        groups.blockSignals(False)
        resize_items()
        groups.verticalScrollBar().setValue(bar_pos)

    def reordered(*_):
        panel_order[:] = [groups.item(k).data(QtCore.Qt.UserRole)
                          for k in range(groups.count())]
        scene.set_order(panel_order)
        # Qt may drop the widgets of moved items: rebuild them all
        # (their state lives in the scene).
        QtCore.QTimer.singleShot(0, build_items)
    groups.model().rowsMoved.connect(reordered)

    # ---- fetch menus above the panel ----
    def checklist(text, items, checked):
        btn = QtWidgets.QToolButton()
        btn.setText(text)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(btn)
        actions = {}
        for key, label in items:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(checked)
            actions[key] = act
        menu.addSeparator()
        menu.addAction("All", lambda: [a.setChecked(True)
                                       for a in actions.values()])
        menu.addAction("None", lambda: [a.setChecked(False)
                                        for a in actions.values()])
        btn.setMenu(menu)
        return btn, actions

    models_btn, model_acts = checklist(
        "Models ▾", [(m, data.MODELS[m]["label"])
                          for m in core.MODEL_ORDER], True)
    params_btn, param_acts = checklist(
        "Parameters ▾", [(s, STYLES[s]["name"]) for s in core.STYLES],
        False)
    go_fetch = QtWidgets.QPushButton("Fetch")
    go_fetch.setToolTip("Fetch the ticked parameters for every ticked model "
                        "that has them (latest run if none is stored)")

    def fetch_menus():
        layers = [s for s, a in param_acts.items() if a.isChecked()]
        if not layers:
            status_note.setText("  Tick parameters in the Parameters menu")
            return
        for m, a in model_acts.items():
            if a.isChecked():
                fetch(m, [s for s in layers
                          if s in core.capable_styles(m)])
    go_fetch.clicked.connect(fetch_menus)
    clear_btn = QtWidgets.QPushButton("Clear data")
    clear_btn.setToolTip("Delete all downloaded data (the window restarts)")

    def clear_store():
        if running or any(waiting.values()):
            status_note.setText("  Fetches are running: wait or close them "
                                "first")
            return
        if QtWidgets.QMessageBox.question(
                win, "Clear downloaded data",
                "Delete every downloaded model run and prepared layer?") \
                != QtWidgets.QMessageBox.Yes:
            return
        import shutil
        data.clear_session(wait=5.0)
        shutil.rmtree(core.STORE_CACHE, ignore_errors=True)
        data.log_event("clear", detail="Clear data pressed")
        QtCore.QProcess.startDetached(sys.executable, restart_args(),
                                      core.HERE)
        app.quit()
    clear_btn.clicked.connect(clear_store)
    fetch_row = QtWidgets.QHBoxLayout()
    for w in (models_btn, params_btn, go_fetch):
        fetch_row.addWidget(w)
    fetch_row.addStretch(1)
    fetch_row.addWidget(clear_btn)

    panel = QtWidgets.QWidget()
    pl_lay = QtWidgets.QVBoxLayout(panel)
    pl_lay.setContentsMargins(2, 2, 2, 2)
    pl_lay.addLayout(fetch_row)
    hint = QtWidgets.QLabel(
        "▸ opens a model's layers. Download buttons: the whole model "
        "(header) or one layer\n(row). Downloads are kept until a newer "
        "run is fetched. Drag ☰ to draw a model on top.")
    hint.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(hint)
    pl_lay.addWidget(groups, 1)
    note = QtWidgets.QLabel("Native model cells, no resampling. Rain "
                            "windows end at the shown time.")
    note.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(note)
    dock = QtWidgets.QDockWidget("Layers", win)
    dock.setWidget(panel)
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                     | QtWidgets.QDockWidget.DockWidgetFloatable)
    dock.setMinimumWidth(400)
    win.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    build_items()

    # ---- time bar ----
    bar = QtWidgets.QToolBar("Time")
    bar.setMovable(False)
    win.addToolBar(QtCore.Qt.BottomToolBarArea, bar)
    style = win.style()
    sp = QtWidgets.QStyle

    def button(icon_id, tip, slot):
        act = bar.addAction(style.standardIcon(icon_id), tip)
        act.triggered.connect(slot)
        return act

    timer = QtCore.QTimer(win)
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setFocusPolicy(QtCore.Qt.NoFocus)
    slider.setMinimumWidth(300)
    label = QtWidgets.QLabel()
    label.setMinimumWidth(330)
    status = QtWidgets.QLabel()
    status_note = QtWidgets.QLabel()

    def sync_slider():
        slider.blockSignals(True)
        slider.setRange(0, max(0, len(scene.times) - 1))
        slider.setValue(scene.i)
        slider.blockSignals(False)
        label.setText(f"  {scene.time:%a %d %b %H:%M} UTC   "
                      f"{scene.leads_text()}" if scene.times
                      else "  no model loaded")

    def go(i):
        scene.go(i)
        sync_slider()

    def ready_now():
        with scene.lock:
            return all((lid, scene.times[scene.i + 1]) in scene.cache
                       for lid in scene.layers if scene.is_on(lid))

    def play_pause():
        if timer.isActive():
            timer.stop()
            play.setIcon(style.standardIcon(sp.SP_MediaPlay))
        elif scene.times:
            if scene.i == len(scene.times) - 1:
                go(0)
            timer.start()
            play.setIcon(style.standardIcon(sp.SP_MediaPause))

    def tick():
        if scene.i + 1 >= len(scene.times):
            play_pause()                     # stop at the last time
        elif ready_now():                    # wait for it otherwise
            go(scene.i + 1)

    timer.timeout.connect(tick)
    button(sp.SP_MediaSkipBackward, "First time (Home)", lambda: go(0))
    button(sp.SP_MediaSeekBackward, "Back one step (Left)",
           lambda: go(scene.i - 1))
    play = button(sp.SP_MediaPlay, "Play / pause (Space)", play_pause)
    button(sp.SP_MediaSeekForward, "On one step (Right)",
           lambda: go(scene.i + 1))
    button(sp.SP_MediaSkipForward, "Last time (End)",
           lambda: go(len(scene.times) - 1))
    bar.addWidget(slider)
    slider.valueChanged.connect(go)
    speed = QtWidgets.QComboBox()
    for text, ms in (("0.5 /s", 2000), ("1 /s", 1000), ("2 /s", 500),
                     ("4 /s", 250)):
        speed.addItem(text, ms)
    speed.setCurrentIndex(2)
    speed.setToolTip("Play speed: time steps a second")
    speed.setFocusPolicy(QtCore.Qt.NoFocus)
    timer.setInterval(speed.currentData())
    speed.currentIndexChanged.connect(
        lambda _: timer.setInterval(speed.currentData()))
    bar.addWidget(speed)
    bar.addWidget(label)
    bar.addSeparator()
    home = bar.addAction("Home view")
    home.setToolTip("Ireland, north up (h)")
    home.triggered.connect(scene.go_home)
    north = bar.addAction("North up")
    north.setToolTip("Turn north back to up (n)")
    north.triggered.connect(lambda: scene.north_up())
    grid = bar.addAction("Graticule")
    grid.setCheckable(True)
    grid.setChecked(True)
    grid.setToolTip("Latitude / longitude lines every 10°, under the "
                    "model layers (g)")
    grid.toggled.connect(lambda v: scene.set_graticule(v))
    bar.addSeparator()
    bar.addWidget(status)
    bar.addWidget(status_note)

    last_cam = [None, None]
    shown_progress = [None]

    def poll():
        # A model finished loading?
        while not done.empty():
            model, result = done.get()
            if isinstance(result, Exception):
                progress[model] = f"could not load: {result}"[:120]
            else:
                scene.add_model(result)
                scene.set_order(panel_order)
                if model not in running and not waiting[model]:
                    progress.pop(model, None)
            build_items()
            sync_slider()
        # Fetch progress changed: refresh the panel (not too often).
        snap = tuple(sorted(progress.items()))
        if snap != shown_progress[0]:
            shown_progress[0] = snap
            build_items()
        scene.refresh()
        # Relabel values after the camera has moved and settled.
        cam = tuple(round(v, 4) for v in scene.pl.camera.position)
        if cam != last_cam[0]:
            last_cam[0] = cam
        elif cam != last_cam[1]:
            last_cam[1] = cam
            if any(scene.label_on.values()):
                scene.update_labels()
        ready, wanted, _ = scene.status()
        text = (f"  {ready}/{wanted} shown layers ready" if ready < wanted
                else "  all shown layers ready") if wanted else ""
        if running:
            text += f", fetching {len(running)} model(s)"
        if scene.label_note and any(scene.label_on.values()):
            text += f", {scene.label_note}"
        status.setText(text)
    poller = QtCore.QTimer(win)
    poller.timeout.connect(poll)
    poller.start(500)

    shortcuts = (("Right", lambda: go(scene.i + 1)),
                 ("Left", lambda: go(scene.i - 1)),
                 ("Up", lambda: go(scene.i + 6)),
                 ("Down", lambda: go(scene.i - 6)),
                 ("PgUp", lambda: go(scene.i + 6)),
                 ("PgDown", lambda: go(scene.i - 6)),
                 ("Home", lambda: go(0)),
                 ("End", lambda: go(len(scene.times) - 1)),
                 ("Space", play_pause),
                 ("H", scene.go_home),
                 ("N", lambda: scene.north_up()),
                 ("G", grid.toggle))
    for keys, slot in shortcuts:
        sc = QtWidgets.QShortcut(keys, win)
        sc.setContext(QtCore.Qt.ApplicationShortcut)
        sc.activated.connect(slot)

    sync_slider()
    scene.start_workers()
    win.show()
    # Models already in the store open at once.
    for model in core.MODEL_ORDER:
        run = jobs.stored_run(model)
        if run:
            progress[model] = "loading ..."

            def work(model=model, run=run):
                try:
                    done.put((model, core.load_model(model, run=run,
                                                     area=args.area)))
                except Exception as exc:
                    done.put((model, exc))
            threading.Thread(target=work, daemon=True).start()
    build_items()
    for model in args.load:                     # optional, at start
        fetch(model, latest=True)
    if args.smoke:
        # Testing: save a picture of the whole window, then close.
        def grab():
            scene.refresh()
            win.grab().save(args.smoke)
            print("wrote", args.smoke)
            app.quit()
        QtCore.QTimer.singleShot(args.smoke_wait * 1000, grab)
    code = app.exec_()
    data.CLOSING.set()                       # stop the window's workers
    return code


def run_screenshot(args):
    import geovista as gv
    pl = gv.GeoPlotter(off_screen=True, window_size=(1400, 1000))
    scene = core.Scene(pl, area=args.area, base=args.base, view=args.view)
    for model in args.load:
        scene.add_model(core.load_model(model, area=args.area))
    for lid in args.show:
        scene.visible[lid] = True
    for lid in args.hide:
        scene.visible[lid] = False
    for lid in args.labels:
        scene.label_on[lid] = True
    if args.order:
        scene.order = [m for m in args.order if m in scene.models] + [
            m for m in scene.order if m not in args.order]
    if args.valid:
        scene.i = scene.times.index(data.stamp(args.valid))
    if args.steps:
        scene.i = min(scene.i + args.steps, len(scene.times) - 1)
    scene.show(render=False, block=True)
    pl.show(screenshot=args.screenshot)
    print("wrote", args.screenshot, "valid", scene.time)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--load", nargs="*", default=[],
                    choices=list(data.MODELS), metavar="MODEL",
                    help="models to fetch (latest run, all layers) at start")
    ap.add_argument("--base", choices=["grey", "colour"], default="grey")
    ap.add_argument("--area", choices=sorted(core.AREAS), default="atlantic",
                    help="Global area drawn (default: %(default)s)")
    ap.add_argument("--view", choices=["ireland", "atlantic"],
                    default="ireland")
    ap.add_argument("--screenshot", help="testing: draw off screen the "
                    "--load models, write a PNG and exit")
    ap.add_argument("--valid", help="with --screenshot: valid time, e.g. "
                    "20261118T1200Z")
    ap.add_argument("--show", nargs="*", default=[], choices=ALL_LAYERS,
                    metavar="MODEL:LAYER",
                    help="with --screenshot: layers on, e.g. global:cape")
    ap.add_argument("--hide", nargs="*", default=[], choices=ALL_LAYERS,
                    metavar="MODEL:LAYER",
                    help="with --screenshot: layers off")
    ap.add_argument("--labels", nargs="*", default=[], choices=ALL_LAYERS,
                    metavar="MODEL:LAYER",
                    help="with --screenshot: layers with value labels")
    ap.add_argument("--order", nargs="+", choices=list(data.MODELS),
                    help="with --screenshot: models to draw on top, top "
                    "first (the rest follow)")
    ap.add_argument("--steps", type=int, default=0,
                    help="with --screenshot: time steps to move first")
    ap.add_argument("--smoke", help="testing: open the window, save a "
                    "picture of it and close")
    ap.add_argument("--smoke-wait", type=int, default=8,
                    help="seconds before --smoke saves (default 8)")
    ap.add_argument("--job", nargs="+", metavar=("MODEL", "LAYER"),
                    help="fetch and prepare layers of one model (default: "
                    "all) into the store; used by the window")
    ap.add_argument("--latest", action="store_true",
                    help="with --job: the latest published run")
    ap.add_argument("--prefetch", nargs="*", metavar="MODEL",
                    help="latest run of these models (default: all), every "
                    "layer or those given with --layers, into the store")
    ap.add_argument("--layers", nargs="*", default=None, metavar="LAYER",
                    help="with --prefetch: only these layers, e.g. rain_6h "
                    "mslp")
    ap.add_argument("--fetch", nargs=argparse.REMAINDER,
                    help="run globe_fetch.py (HARMONIE / waves) directly")
    args = ap.parse_args()
    if args.job:
        import globe_jobs
        return globe_jobs.run_job(args.job[0], args.job[1:] or None,
                                  latest=args.latest)
    if args.prefetch is not None:
        import globe_jobs
        return globe_jobs.prefetch(args.prefetch or None, args.layers)
    if args.fetch is not None:
        import globe_fetch
        return globe_fetch.main(args.fetch)
    if args.screenshot:
        run_screenshot(args)
        return 0
    return run_window(args)


if __name__ == "__main__":
    sys.exit(main())
