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


def run_window(args):
    import os
    import queue
    import threading

    from geovista.qt import GeoQtInteractor
    from qtpy import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    data.clear_session()                     # left over from a crash

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("UKMO NWP Globe")
    win.resize(1500, 950)
    view = GeoQtInteractor()
    win.setCentralWidget(view)
    scene = core.Scene(view, area=args.area, base=args.base, view=args.view)
    # PyVista zooms on Up/Down by default; here they step time.
    for key in ("Up", "Down"):
        view.clear_events_for_key(key)

    # ---- layer panel: one group per model, drag to reorder ----
    collapsed = {m: False for m in data.MODELS}
    panel_order = list(core.MODEL_ORDER)        # top first, loaded or not
    fetching = {}                               # model -> note text
    done = queue.Queue()                        # (model, LoadedModel|error)

    def model_group(model):
        box = QtWidgets.QFrame()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        grip = QtWidgets.QLabel("☰")
        grip.setToolTip("Drag to draw this model above or below the others")
        grip.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        grip.setStyleSheet("color:#777;font-size:12pt")
        loaded = model in scene.models
        title = data.MODELS[model]["label"] + (
            f"  {scene.runs[model]}" if loaded else "")
        on = QtWidgets.QCheckBox(title)
        on.setStyleSheet("font-weight:bold")
        on.setChecked(scene.model_on.get(model, True))
        on.setEnabled(loaded)
        on.toggled.connect(lambda v, m=model: scene.set_model_on(m, v))
        get = QtWidgets.QToolButton()
        get.setIcon(download_icon(QtGui, QtCore))
        get.setToolTip("Fetch the latest run" if not loaded
                       else "Fetch the latest run again")
        get.setEnabled(model not in fetching)
        get.clicked.connect(lambda _=False, m=model: fetch(m))
        fold = QtWidgets.QToolButton()
        fold.setArrowType(QtCore.Qt.RightArrow if collapsed[model]
                          else QtCore.Qt.DownArrow)
        fold.setAutoRaise(True)
        fold.setVisible(loaded)
        head.addWidget(grip)
        head.addWidget(on, 1)
        head.addWidget(get)
        head.addWidget(fold)
        lay.addLayout(head)
        if not loaded:
            note = QtWidgets.QLabel(fetching.get(
                model, "Not loaded: press the download button."))
            note.setStyleSheet("color:#555;font-size:8pt;margin-left:18px")
            note.setWordWrap(True)
            lay.addWidget(note)
            return box
        body = QtWidgets.QWidget()
        rows = QtWidgets.QGridLayout(body)
        rows.setContentsMargins(18, 0, 0, 0)
        rows.setVerticalSpacing(1)
        r = 0
        for name in reversed(scene.styles[model]):  # top layer first
            lid = core.layer_id(model, name)
            style = STYLES[name]
            tick = QtWidgets.QCheckBox(style["name"])
            tick.setChecked(scene.visible[lid])
            values = QtWidgets.QCheckBox("123")
            values.setToolTip("Label the cells' own values (about 10 km "
                              "apart when zoomed in)")
            values.setChecked(scene.label_on[lid])
            values.setVisible(style["kind"] == "raster")
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(round(scene.opacity[lid] * 100)))
            slider.setFocusPolicy(QtCore.Qt.NoFocus)
            slider.setToolTip("Opacity")
            slider.setMaximumWidth(70)
            pct = QtWidgets.QLabel(f"{slider.value()}%")
            pct.setMinimumWidth(30)
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
            rows.addWidget(key, r + 1, 0, 1, 4)
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
        groups.blockSignals(True)
        groups.clear()
        for model in panel_order:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, model)
            groups.addItem(item)
            groups.setItemWidget(item, model_group(model))
        groups.blockSignals(False)
        resize_items()

    def reordered(*_):
        panel_order[:] = [groups.item(k).data(QtCore.Qt.UserRole)
                          for k in range(groups.count())]
        scene.set_order(panel_order)
        # Qt may drop the widgets of moved items: rebuild them all
        # (their state lives in the scene).
        QtCore.QTimer.singleShot(0, build_items)
    groups.model().rowsMoved.connect(reordered)

    # ---- fetching a model on request ----
    procs = {}

    def fetch(model):
        if model in fetching:
            return
        if data.MODELS[model]["prefix"]:
            # Met Office: find the latest run; files then come as layers
            # need them.
            fetching[model] = "Finding the latest run ..."
            build_items()

            def work():
                try:
                    done.put((model, core.load_model(model, area=args.area)))
                except Exception as exc:
                    done.put((model, exc))
            threading.Thread(target=work, daemon=True).start()
            return
        # HARMONIE / IBI: fetched whole by globe_fetch.py.
        fetching[model] = "Fetching ..."
        build_items()
        proc = QtCore.QProcess(win)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        last = [""]

        def output():
            text = bytes(proc.readAll()).decode("utf-8", "replace")
            lines = [s.strip() for s in text.splitlines() if s.strip()]
            if lines:
                last[0] = lines[-1]
                fetching[model] = last[0][:120]
                status_note.setText(f"  {data.MODELS[model]['label']}: "
                                    f"{last[0][:70]}")

        def finished(code, _status):
            if code == 0:
                def work():
                    try:
                        done.put((model, core.load_model(
                            model, area=args.area)))
                    except Exception as exc:
                        done.put((model, exc))
                threading.Thread(target=work, daemon=True).start()
            else:
                done.put((model, RuntimeError(last[0] or "fetch failed")))
        proc.readyRead.connect(output)
        proc.finished.connect(finished)
        procs[model] = proc
        what = model                 # harmonie, ibi or nws
        proc.start(sys.executable, [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "globe_fetch.py"), what])

    def stop_fetches():
        for proc in procs.values():
            if proc.state() != QtCore.QProcess.NotRunning:
                proc.kill()
                proc.waitForFinished(3000)
    app.aboutToQuit.connect(stop_fetches)

    panel = QtWidgets.QWidget()
    pl_lay = QtWidgets.QVBoxLayout(panel)
    pl_lay.setContentsMargins(2, 2, 2, 2)
    hint = QtWidgets.QLabel("Press a model's download button to fetch its "
                            "latest run.\nDrag a model by ☰ to draw it "
                            "on top.")
    hint.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(hint)
    pl_lay.addWidget(groups, 1)
    note = QtWidgets.QLabel("Native model cells, no resampling. Rain "
                            "windows end at the shown time.\nDownloads are "
                            "deleted when the globe closes.")
    note.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(note)
    dock = QtWidgets.QDockWidget("Layers", win)
    dock.setWidget(panel)
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                     | QtWidgets.QDockWidget.DockWidgetFloatable)
    dock.setMinimumWidth(370)
    win.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    build_items()

    # ---- time bar ----
    bar = QtWidgets.QToolBar("Time")
    bar.setMovable(False)
    win.addToolBar(QtCore.Qt.BottomToolBarArea, bar)
    style = win.style()
    sp = QtWidgets.QStyle

    def button(icon, tip, slot):
        act = bar.addAction(style.standardIcon(icon), tip)
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
    bar.addSeparator()
    bar.addWidget(status)
    bar.addWidget(status_note)

    last_cam = [None, None]

    def poll():
        # A model finished fetching?
        while not done.empty():
            model, result = done.get()
            fetching.pop(model, None)
            if isinstance(result, Exception):
                fetching_msg = f"Fetch failed: {result}"
                status_note.setText(f"  {data.MODELS[model]['label']}: "
                                    f"{fetching_msg[:80]}")
            else:
                scene.add_model(result)
                scene.set_order(panel_order)
                status_note.setText(f"  {data.MODELS[model]['label']} "
                                    f"{result.run} loaded")
            build_items()
            sync_slider()
        scene.refresh()
        # Relabel values after the camera has moved and settled.
        cam = tuple(round(v, 4) for v in scene.pl.camera.position)
        if cam != last_cam[0]:
            last_cam[0] = cam
        elif cam != last_cam[1]:
            last_cam[1] = cam
            if any(scene.label_on.values()):
                scene.update_labels()
        ready, wanted, downloading = scene.status()
        text = (f"  {ready}/{wanted} ready" if ready < wanted
                else "  all ready") if wanted else ""
        if downloading:
            text += f", downloading {downloading}"
        if scene.label_note and any(scene.label_on.values()):
            text += f", {scene.label_note}"
        status.setText(text)
    poller = QtCore.QTimer(win)
    poller.timeout.connect(poll)
    poller.start(300)

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
                 ("N", lambda: scene.north_up()))
    for keys, slot in shortcuts:
        sc = QtWidgets.QShortcut(keys, win)
        sc.setContext(QtCore.Qt.ApplicationShortcut)
        sc.activated.connect(slot)

    sync_slider()
    scene.start_workers()
    win.show()
    for model in args.load:                     # optional, at start
        fetch(model)
    if args.smoke:
        # Testing: save a picture of the whole window, then close.
        def grab():
            scene.refresh()
            win.grab().save(args.smoke)
            print("wrote", args.smoke)
            app.quit()
        QtCore.QTimer.singleShot(args.smoke_wait * 1000, grab)
    code = app.exec_()
    # Downloads are for this session only.
    data.clear_session(wait=10.0)
    import shutil
    shutil.rmtree(scene.disk, ignore_errors=True)
    return code


def run_screenshot(args):
    import geovista as gv
    data.clear_session()
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
    if not args.keep:
        data.clear_session()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--load", nargs="*", default=[],
                    choices=list(data.MODELS), metavar="MODEL",
                    help="models to fetch at start (default: none; use the "
                    "download buttons)")
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
    ap.add_argument("--keep", action="store_true",
                    help="with --screenshot: keep the downloads")
    ap.add_argument("--smoke", help="testing: open the window, save a "
                    "picture of it and close")
    ap.add_argument("--smoke-wait", type=int, default=8,
                    help="seconds before --smoke saves (default 8)")
    args = ap.parse_args()
    if args.screenshot:
        run_screenshot(args)
        return 0
    return run_window(args)


if __name__ == "__main__":
    sys.exit(main())
