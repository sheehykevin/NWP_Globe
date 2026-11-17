"""3D globe for UKV and Global model output (prototype).

A Qt window around the globe in ``globe_core.py``: a layer panel with
one group per model (drag a group above the other to draw that model on
top; each layer has on/off, transparency and its colour key) and a time
bar (first, back, play, forward, last, a time slider and play speed),
as in the UKMO NWP viewer.

    conda env create -f environment.yml
    conda run -n ukmo_globe python globe.py [--archive PATH]

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


def run_window(args):
    from geovista.qt import GeoQtInteractor
    from qtpy import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("UKMO NWP Globe")
    win.resize(1500, 950)
    view = GeoQtInteractor()
    win.setCentralWidget(view)
    scene = core.Scene(view, ukv_run=args.ukv_run,
                       global_run=args.global_run, valid=args.valid,
                       area=args.area, base=args.base, view=args.view,
                       archive=args.archive)
    # PyVista zooms on Up/Down by default; here they step time.
    for key in ("Up", "Down"):
        view.clear_events_for_key(key)

    # ---- layer panel: one group per model, drag to reorder ----
    collapsed = {m: False for m in scene.models}

    def model_group(model):
        box = QtWidgets.QFrame()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        grip = QtWidgets.QLabel("☰")
        grip.setToolTip("Drag to draw this model above or below the other")
        grip.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        grip.setStyleSheet("color:#777;font-size:12pt")
        on = QtWidgets.QCheckBox(f"{data.MODELS[model]['label']}  "
                                 f"{scene.runs[model]}")
        on.setStyleSheet("font-weight:bold")
        on.setChecked(scene.model_on[model])
        on.toggled.connect(lambda v, m=model: scene.set_model_on(m, v))
        fold = QtWidgets.QToolButton()
        fold.setArrowType(QtCore.Qt.RightArrow if collapsed[model]
                          else QtCore.Qt.DownArrow)
        fold.setAutoRaise(True)
        head.addWidget(grip)
        head.addWidget(on, 1)
        head.addWidget(fold)
        lay.addLayout(head)
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
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(int(round(scene.opacity[lid] * 100)))
            slider.setFocusPolicy(QtCore.Qt.NoFocus)
            slider.setToolTip("Opacity")
            slider.setMaximumWidth(90)
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
            slider.valueChanged.connect(moved)
            rows.addWidget(tick, r, 0)
            rows.addWidget(slider, r, 1)
            rows.addWidget(pct, r, 2)
            rows.addWidget(key, r + 1, 0, 1, 3)
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
        for model in scene.order:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, model)
            groups.addItem(item)
            groups.setItemWidget(item, model_group(model))
        groups.blockSignals(False)
        resize_items()

    def reordered(*_):
        order = [groups.item(k).data(QtCore.Qt.UserRole)
                 for k in range(groups.count())]
        scene.set_order(order)
        # Qt may drop the widgets of moved items: rebuild them all
        # (their state lives in the scene).
        QtCore.QTimer.singleShot(0, build_items)
    groups.model().rowsMoved.connect(reordered)
    build_items()

    panel = QtWidgets.QWidget()
    pl_lay = QtWidgets.QVBoxLayout(panel)
    pl_lay.setContentsMargins(2, 2, 2, 2)
    hint = QtWidgets.QLabel("Drag a model by ☰ to draw it on top.")
    hint.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(hint)
    pl_lay.addWidget(groups, 1)
    note = QtWidgets.QLabel("Native model cells, no resampling. Rain "
                            "windows end at the shown time.\nGlobal files "
                            "not on disk are downloaded while a layer is "
                            "on.")
    note.setStyleSheet("color:#555;font-size:8pt")
    pl_lay.addWidget(note)
    dock = QtWidgets.QDockWidget("Layers", win)
    dock.setWidget(panel)
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                     | QtWidgets.QDockWidget.DockWidgetFloatable)
    dock.setMinimumWidth(320)
    win.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)

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
    slider.setRange(0, len(scene.times) - 1)
    slider.setFocusPolicy(QtCore.Qt.NoFocus)
    slider.setMinimumWidth(360)
    label = QtWidgets.QLabel()
    label.setMinimumWidth(330)
    status = QtWidgets.QLabel()

    def go(i):
        scene.go(i)
        if slider.value() != scene.i:
            slider.blockSignals(True)
            slider.setValue(scene.i)
            slider.blockSignals(False)
        label.setText(f"  {scene.time:%a %d %b %H:%M} UTC   "
                      f"{scene.leads_text()}")

    def ready_now():
        with scene.lock:
            return all((lid, scene.times[scene.i + 1]) in scene.cache
                       for lid in scene.layers if scene.is_on(lid))

    def play_pause():
        if timer.isActive():
            timer.stop()
            play.setIcon(style.standardIcon(sp.SP_MediaPlay))
        else:
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
    bar.addSeparator()

    # ---- fetching HARMONIE and IBI (a separate process) ----
    import os
    fetch_proc = QtCore.QProcess(win)
    fetch_proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
    fetch_btn = QtWidgets.QToolButton()
    fetch_btn.setText("Fetch")
    fetch_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    fetch_menu = QtWidgets.QMenu(fetch_btn)
    fetch_btn.setMenu(fetch_menu)
    bar.addWidget(fetch_btn)
    fetch_note = QtWidgets.QLabel()
    bar.addWidget(fetch_note)

    def fetch(what):
        if fetch_proc.state() != QtCore.QProcess.NotRunning:
            return
        fetch_note.setText(f"  fetching {what} ...")
        fetch_proc.start(sys.executable, [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "globe_fetch.py"), what])

    def fetch_output():
        text = bytes(fetch_proc.readAll()).decode("utf-8", "replace")
        lines = [s for s in text.splitlines() if s.strip()]
        if lines:
            fetch_note.setText("  " + lines[-1][:80])

    def fetch_done(code, _status):
        fetch_note.setText("  fetched: Reload to show it" if code == 0
                           else "  fetch failed (see the last message)")
    fetch_proc.readyRead.connect(fetch_output)
    fetch_proc.finished.connect(fetch_done)
    fetch_menu.addAction("HARMONIE: latest KNMI run (about 6 GB "
                         "download)", lambda: fetch("harmonie"))
    fetch_menu.addAction("IBI waves: today 00Z to +72 h",
                         lambda: fetch("ibi"))
    fetch_menu.addSeparator()

    def reload():
        # A fresh window reads every model's newest run again.
        QtCore.QProcess.startDetached(sys.executable, sys.argv)
        app.quit()
    fetch_menu.addAction("Reload the globe", reload)

    def poll():
        scene.refresh()
        ready, wanted, downloading = scene.status()
        text = (f"  {ready}/{wanted} ready" if ready < wanted
                else "  all ready")
        if downloading:
            text += f", downloading {downloading}"
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

    go(scene.i)
    scene.start_workers()
    win.show()
    if args.smoke:
        # Testing: save a picture of the whole window, then close.
        def grab():
            scene.refresh()
            win.grab().save(args.smoke)
            print("wrote", args.smoke)
            app.quit()
        QtCore.QTimer.singleShot(args.smoke_wait * 1000, grab)
    return app.exec_()


def run_screenshot(args):
    import geovista as gv
    pl = gv.GeoPlotter(off_screen=True, window_size=(1400, 1000))
    scene = core.Scene(pl, ukv_run=args.ukv_run, global_run=args.global_run,
                       valid=args.valid, area=args.area, base=args.base,
                       view=args.view, archive=args.archive)
    for lid in args.show:
        scene.visible[lid] = True
    for lid in args.hide:
        scene.visible[lid] = False
    if args.order:
        scene.order = [m for m in args.order if m in scene.models] + [
            m for m in scene.order if m not in args.order]
    if args.steps:
        scene.i = min(scene.i + args.steps, len(scene.times) - 1)
    scene.show(render=False, block=True)
    pl.show(screenshot=args.screenshot)
    print("wrote", args.screenshot, "valid", scene.time)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=core.ARCHIVE,
                    help="UKMO NWP tools' download archive, read only "
                    "(default: %(default)s)")
    ap.add_argument("--ukv-run", help="default: newest held")
    ap.add_argument("--global-run", help="default: newest held; any "
                    "published run can be given (it is downloaded)")
    ap.add_argument("--valid", help="start time, e.g. 20261116T0300Z "
                    "(default: first hour both models cover)")
    ap.add_argument("--base", choices=["grey", "colour"], default="grey")
    ap.add_argument("--area", choices=sorted(core.AREAS), default="atlantic",
                    help="Global area drawn (default: %(default)s)")
    ap.add_argument("--view", choices=["ireland", "atlantic"],
                    default="ireland")
    ap.add_argument("--screenshot", help="testing: draw off screen, write "
                    "a PNG of the globe and exit")
    ap.add_argument("--show", nargs="*", default=[], choices=ALL_LAYERS,
                    metavar="MODEL:LAYER",
                    help="with --screenshot: layers on, e.g. global:cape")
    ap.add_argument("--hide", nargs="*", default=[], choices=ALL_LAYERS,
                    metavar="MODEL:LAYER",
                    help="with --screenshot: layers off")
    ap.add_argument("--order", nargs="+", choices=list(data.MODELS),
                    help="with --screenshot: models to draw on top, top "
                    "first (the rest follow)")
    ap.add_argument("--steps", type=int, default=0,
                    help="with --screenshot: time steps to move first")
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
