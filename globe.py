"""3D globe for UKV and Global model output (prototype).

A Qt window around the globe in ``globe_core.py``: a layer panel (on/off
and transparency for each layer, with its colour key) and a time bar
(first, back, play, forward, last, a time slider and play speed), as in
the UKMO NWP viewer.

    conda env create -f environment.yml
    conda run -n ukmo_globe python globe.py [--archive PATH]

Keys: Left/Right one hour, Up/Down six hours, Space play/pause, Home and
End first/last hour, h home view (Ireland, north up), n north up.
"""

from __future__ import annotations

import argparse
import sys

import globe_core as core
from globe_core import LAYERS

STYLE = """
QWidget { font-size: 10pt; }
QGroupBox { font-weight: bold; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 6px; }
"""


def _colour_key(spec, QtGui, QtCore):
    """A colour key as a picture: one swatch per band, its lower bound
    under it, units below; a line sample for line layers."""
    w, h = 250, 44
    pix = QtGui.QPixmap(w, h)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    font = p.font()
    font.setPointSize(7)
    p.setFont(font)
    if spec["kind"] == "raster":
        n = len(spec["colours"])
        cw = (w - 20) // n
        for k, colour in enumerate(spec["colours"]):
            p.fillRect(k * cw, 0, cw, 12, QtGui.QColor(colour))
            p.setPen(QtGui.QColor("#888888"))
            p.drawRect(k * cw, 0, cw, 12)
            p.setPen(QtGui.QColor("#202020"))
            p.drawText(k * cw, 14, cw, 12, QtCore.Qt.AlignLeft,
                       f"{spec['bounds'][k]:g}")
        p.setPen(QtGui.QColor("#555555"))
        p.drawText(0, 28, w, 14, QtCore.Qt.AlignLeft, spec["units"])
    else:
        pen = QtGui.QPen(QtGui.QColor(spec["colour"]))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawLine(0, 7, 40, 7)
        p.setPen(QtGui.QColor("#555555"))
        p.drawText(48, 0, w - 48, 14, QtCore.Qt.AlignLeft,
                   "isobars every 4 hPa" if spec["kind"] == "isobars"
                   else "knots: half 5, full 10, pennant 50")
        h = 16
    p.end()
    return pix.copy(0, 0, w, h)


def run_window(args):
    from geovista.qt import GeoQtInteractor
    from qtpy import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("UKMO NWP Globe")
    win.resize(1500, 950)
    view = GeoQtInteractor()
    win.setCentralWidget(view)
    scene = core.Scene(view, ukv_run=args.ukv_run,
                       global_run=args.global_run, valid=args.valid,
                       area=args.area, base=args.base, view=args.view)
    # PyVista zooms on Up/Down by default; here they step time.
    for key in ("Up", "Down"):
        view.clear_events_for_key(key)

    # ---- layer panel ----
    panel = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(panel)
    for name, spec in reversed(list(LAYERS.items())):   # top layer first
        box = QtWidgets.QGroupBox()
        grid = QtWidgets.QGridLayout(box)
        tick = QtWidgets.QCheckBox(name)
        tick.setChecked(scene.visible[name])
        tick.setEnabled(scene.available(name))
        tick.toggled.connect(lambda on, n=name: scene.set_visible(n, on))
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(int(round(scene.opacity[name] * 100)))
        slider.setFocusPolicy(QtCore.Qt.NoFocus)
        slider.setToolTip("Opacity")
        value = QtWidgets.QLabel(f"{slider.value()} %")
        value.setMinimumWidth(36)

        def moved(v, n=name, label=value):
            label.setText(f"{v} %")
            scene.set_opacity(n, v / 100.0)
        slider.valueChanged.connect(moved)
        grid.addWidget(tick, 0, 0, 1, 2)
        grid.addWidget(slider, 1, 0)
        grid.addWidget(value, 1, 1)
        key = QtWidgets.QLabel()
        key.setPixmap(_colour_key(spec, QtGui, QtCore))
        grid.addWidget(key, 2, 0, 1, 2)
        if not scene.available(name):
            box.setToolTip("No data for this run in the archive")
        lay.addWidget(box)
    lay.addStretch(1)
    note = QtWidgets.QLabel("Native model cells, no resampling.\n"
                            "Wind: UKV only (no Global wind in the "
                            "archive).")
    note.setStyleSheet("color:#555;font-size:8pt")
    lay.addWidget(note)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidget(panel)
    scroll.setWidgetResizable(True)
    dock = QtWidgets.QDockWidget("Layers", win)
    dock.setWidget(scroll)
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                     | QtWidgets.QDockWidget.DockWidgetFloatable)
    dock.setMinimumWidth(280)
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
    slider.setMinimumWidth(400)
    label = QtWidgets.QLabel()
    label.setMinimumWidth(330)
    status = QtWidgets.QLabel()

    def go(i):
        scene.go(i)
        if slider.value() != scene.i:
            slider.blockSignals(True)
            slider.setValue(scene.i)
            slider.blockSignals(False)
        t = scene.time
        label.setText(f"  {t:%a %d %b %H:%M} UTC   Global "
                      f"T+{core.lead_hours(t, scene.glo_run)}"
                      + (f"  UKV T+{core.lead_hours(t, scene.ukv_run)}"
                         if t in scene.files["UKV rain"]["v"] else
                         "  UKV: no data"))

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
        nxt = scene.i + 1
        if nxt >= len(scene.times):
            play_pause()                     # stop at the last hour
        elif scene.is_ready(nxt):            # wait for it otherwise
            go(nxt)

    timer.timeout.connect(tick)
    button(sp.SP_MediaSkipBackward, "First hour (Home)", lambda: go(0))
    button(sp.SP_MediaSeekBackward, "Back one hour (Left)",
           lambda: go(scene.i - 1))
    play = button(sp.SP_MediaPlay, "Play / pause (Space)", play_pause)
    button(sp.SP_MediaSeekForward, "On one hour (Right)",
           lambda: go(scene.i + 1))
    button(sp.SP_MediaSkipForward, "Last hour (End)",
           lambda: go(len(scene.times) - 1))
    bar.addWidget(slider)
    slider.valueChanged.connect(go)
    speed = QtWidgets.QComboBox()
    for text, ms in (("0.5 h/s", 2000), ("1 h/s", 1000), ("2 h/s", 500),
                     ("4 h/s", 250)):
        speed.addItem(text, ms)
    speed.setCurrentIndex(2)
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

    def progress():
        n, total = scene.n_ready(), len(scene.times)
        status.setText(f"  {n}/{total} hours ready" if n < total
                       else f"  all {total} hours ready")
    poll = QtCore.QTimer(win)
    poll.timeout.connect(progress)
    poll.start(500)

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
    progress()
    scene.start_workers()
    win.show()
    if args.smoke:
        # Testing: save a picture of the whole window, then close.
        def grab():
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
                       view=args.view)
    for name in args.show:
        scene.set_visible(name, True, render=False)
    for name in args.hide:
        scene.set_visible(name, False, render=False)
    if args.steps:
        scene.step(args.steps, render=False)
    pl.show(screenshot=args.screenshot)
    print("wrote", args.screenshot, "valid", scene.time)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=core.ARCHIVE,
                    help="download archive folder (default: %(default)s)")
    ap.add_argument("--ukv-run", help="default: newest in the archive")
    ap.add_argument("--global-run", help="default: newest in the archive")
    ap.add_argument("--valid", help="start time, e.g. 20261116T0300Z "
                    "(default: first hour both models cover)")
    ap.add_argument("--base", choices=["grey", "colour"], default="grey")
    ap.add_argument("--area", choices=sorted(core.AREAS), default="atlantic",
                    help="Global area drawn (default: %(default)s)")
    ap.add_argument("--view", choices=["ireland", "atlantic"],
                    default="ireland")
    ap.add_argument("--screenshot", help="testing: draw off screen, write "
                    "a PNG of the globe and exit")
    ap.add_argument("--show", nargs="*", default=[], choices=list(LAYERS),
                    metavar="LAYER", help="with --screenshot: layers on")
    ap.add_argument("--hide", nargs="*", default=[], choices=list(LAYERS),
                    metavar="LAYER", help="with --screenshot: layers off")
    ap.add_argument("--steps", type=int, default=0,
                    help="with --screenshot: hours to step first")
    ap.add_argument("--smoke", help="testing: open the window, save a "
                    "picture of it and close")
    ap.add_argument("--smoke-wait", type=int, default=8,
                    help="seconds before --smoke saves (default 8)")
    args = ap.parse_args()
    core.ARCHIVE = args.archive
    if args.screenshot:
        run_screenshot(args)
        return 0
    return run_window(args)


if __name__ == "__main__":
    sys.exit(main())
