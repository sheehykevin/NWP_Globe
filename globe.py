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


def settings_path():
    from globe_paths import APP_DIR
    return os.path.join(APP_DIR, "globe_settings.json")


def load_settings():
    """The layout saved when the globe last closed ({} if none)."""
    import json
    try:
        with open(settings_path(), encoding="utf-8") as f:
            out = json.load(f)
        return out if isinstance(out, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(values):
    import json
    try:
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=1)
        os.replace(tmp, settings_path())
    except OSError:
        pass


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

    from datetime import datetime, timezone

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

    saved = load_settings()                     # the last session's layout
    collapsed = {m: saved.get("collapsed", {}).get(m, True)
                 for m in data.MODELS}          # model lists folded
    panel_order = [m for m in saved.get("order", []) if m in data.MODELS]
    panel_order += [m for m in core.MODEL_ORDER if m not in panel_order]
    done = queue.Queue()                        # (model, LoadedModel|error)
    running = {}                                # model -> QProcess
    waiting = {m: [] for m in data.MODELS}      # model -> [(layers, latest)]
    progress = {}                               # model -> text
    icon = download_icon(QtGui, QtCore)
    small_icon = download_icon(QtGui, QtCore, 14)

    # ---- fetch jobs (separate processes; the store keeps the results) ----
    # At most MAX_JOBS at once: each has several workers and its own
    # drawing engine, and the servers ask for modest use.
    MAX_JOBS = max(1, int(os.environ.get("GLOBE_MAX_JOBS", "2")))
    def fetch(model, layers=None, latest=False):
        """Queue a job: ``layers`` of ``model`` (default: all)."""
        layers = [s for s in (layers or core.capable_styles(model))
                  if s in core.capable_styles(model)]
        if not layers:
            return
        if waiting[model]:                   # not started: merge into it
            old_layers, old_latest = waiting[model][0]
            waiting[model][0] = (old_layers + [s for s in layers
                                               if s not in old_layers],
                                 old_latest or latest)
        else:
            waiting[model].append((layers, latest))
        pump()
        refresh_all()

    def pump():
        """Start queued jobs while fewer than MAX_JOBS run (one per
        model, panel order), and number the rest."""
        for m in panel_order:
            if len(running) >= MAX_JOBS:
                break
            if waiting[m] and m not in running:
                start_next(m)
        n = 0
        for m in panel_order:
            if waiting[m] and m not in running:
                n += 1
                progress[m] = ("queued (next)" if n == 1
                               else f"queued ({n} in line)")

    def describe(layers):
        return (", ".join(STYLES[s]["name"] for s in layers)
                if len(layers) <= 2 else f"{len(layers)} layers")

    def follow(model, pid, names):
        """Follow a job running in the background (started here or
        found running)."""
        running[model] = dict(pid=pid, pos=0, names=names, last="")
        progress[model] = f"fetching {names} ..."

    def start_next(model):
        if not waiting[model]:
            return
        layers, latest = waiting[model].pop(0)
        follow(model, jobs.start_job(model, layers, latest),
               describe(layers))

    def read_job(model):
        """Take in a job's new log lines; True when the job has ended."""
        job = running[model]
        alive = jobs.pid_alive(job["pid"])
        try:
            with open(jobs.job_paths(model)[0], encoding="utf-8",
                      errors="replace") as f:
                f.seek(job["pos"])
                text = f.read()
                job["pos"] = f.tell()
        except OSError:
            text = ""
        names = job["names"]
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("PROGRESS "):
                d, w = line.split()[2:4]
                progress[model] = f"fetching {names}: {d}/{w}"
            elif line.startswith("DONE "):
                job["last"] = "DONE"
            elif line.startswith("NOTE "):
                progress[model] = line.split(" ", 2)[2]
            elif line.startswith("FAILED "):
                job["last"] = line.split(" ", 2)[2]
            elif line and not line.startswith(("Traceback", "File ")):
                progress[model] = f"fetching {names}: {line[:60]}"
        return not alive

    def finished(model):
        job = running.pop(model)
        measure()
        was_stopped = model in stopped
        stopped.discard(model)
        if was_stopped and jobs.stored_run(model) is None:
            progress[model] = "stopped"
        elif was_stopped or job["last"] == "DONE":   # load what is there
            progress[model] = (("stopped; " if was_stopped else "")
                               + "loading ...")
            run = jobs.stored_run(model)

            def work():
                try:
                    done.put((model, core.load_model(model, run=run,
                                                     area=args.area)))
                except Exception as exc:
                    done.put((model, exc))
            threading.Thread(target=work, daemon=True).start()
        else:
            progress[model] = ("fetch failed: " + (
                job["last"] or "it ended without finishing"))[:120]
        pump()
        refresh_all()

    adopt_tick = [0]

    def poll_jobs():
        for model in list(running):
            if read_job(model):
                read_job(model)                  # anything written last
                finished(model)
        # Jobs started elsewhere (a queue left by a closed window), every
        # few seconds.
        adopt_tick[0] += 1
        if adopt_tick[0] % 6 == 1:
            for model in data.MODELS:
                if model not in running:
                    pid = jobs.job_pid(model)
                    if pid:
                        follow(model, pid, "(started earlier)")

    stopped = set()

    def stop_fetch(model):
        """Cancel a model's queued and running fetch."""
        waiting[model].clear()
        job = running.get(model)
        if job is not None:
            stopped.add(model)
            jobs.kill(job["pid"])
        else:
            progress.pop(model, None)
        pump()
        refresh_all()

    def stop_jobs():
        for m in list(running):
            jobs.kill(running[m]["pid"])
            waiting[m].clear()

    def close_event(ev):
        queued = [(m, *waiting[m][0]) for m in panel_order if waiting[m]]
        if not running and not queued:
            ev.accept()
            return
        box = QtWidgets.QMessageBox(win)
        box.setWindowTitle("Fetches are running")
        box.setText(f"{len(running)} fetch(es) running, {len(queued)} "
                    "queued.\nKeep them going in the background? The "
                    "results are there when the globe opens again.")
        keep = box.addButton("Keep fetching",
                             QtWidgets.QMessageBox.AcceptRole)
        stop = box.addButton("Stop them",
                             QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec_()
        if box.clickedButton() is keep:
            if queued:
                jobs.start_queue(queued)
            ev.accept()
        elif box.clickedButton() is stop:
            stop_jobs()
            ev.accept()
        else:
            ev.ignore()
    win.closeEvent = close_event

    # ---- layer panel: one group per model, drag to reorder ----
    ui = {}                          # model -> its group's live widgets
    sizes = {}                       # model -> bytes in the store
    wanted_on = set()                # layers ticked before they had data

    def human(n):
        return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"

    def measure():
        """Disk use per model, in a thread; shown when it is done."""
        def work():
            for m in data.MODELS:
                sizes[m] = jobs.model_size(m)
            size_ready.set()
        threading.Thread(target=work, daemon=True).start()
    size_ready = threading.Event()

    def explain(log):
        """A plain-English cause for common failures, or ''."""
        low = log.lower()
        if "401" in low or "403" in low or "unauthorized" in low or \
                "forbidden" in low:
            return ("The server refused the request: check the API key "
                    "(docs/CREDENTIALS.md).")
        if "copernicus" in low and ("credential" in low or "login" in low
                                    or "username" in low):
            return ("Copernicus Marine needs a login on this computer: run "
                    "'copernicusmarine login' (docs/CREDENTIALS.md).")
        if any(s in low for s in ("getaddrinfo", "name or service",
                                  "connectionerror", "urlopen error",
                                  "timed out", "max retries")):
            return ("Could not reach the server: check the internet "
                    "connection, then try again.")
        if "no space left" in low or "errno 28" in low:
            return "The disk is full: free some space (or clear data)."
        return ""

    def show_log(model):
        """The model's last fetch output, with a likely cause."""
        try:
            with open(jobs.job_paths(model)[0], encoding="utf-8",
                      errors="replace") as f:
                log = f.read()
        except OSError:
            log = "(no output was kept)"
        lines = [s for s in log.splitlines()
                 if not s.startswith("PROGRESS ")]
        dlg = QtWidgets.QDialog(win)
        dlg.setWindowTitle(f"{data.MODELS[model]['label']}: last fetch")
        dlg.resize(760, 460)
        v = QtWidgets.QVBoxLayout(dlg)
        cause = explain(log)
        if cause:
            lab = QtWidgets.QLabel(cause)
            lab.setWordWrap(True)
            lab.setStyleSheet("font-weight:bold;color:#c0392b")
            v.addWidget(lab)
        txt = QtWidgets.QPlainTextEdit("\n".join(lines[-300:]))
        txt.setReadOnly(True)
        txt.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        txt.moveCursor(QtGui.QTextCursor.End)
        v.addWidget(txt, 1)
        ok = QtWidgets.QPushButton("Close")
        ok.clicked.connect(dlg.accept)
        v.addWidget(ok, 0, QtCore.Qt.AlignRight)
        dlg.exec_()

    def model_menu(model, where):
        menu = QtWidgets.QMenu(win)
        log_act = menu.addAction("Show the last fetch's output")
        log_act.setEnabled(os.path.exists(jobs.job_paths(model)[0]))
        act = menu.addAction("Clear this model's data")
        act.setEnabled(model not in running and not waiting[model]
                       and bool(sizes.get(model)))
        chosen = menu.exec_(where)
        if chosen is log_act:
            show_log(model)
        if chosen is act:
            if QtWidgets.QMessageBox.question(
                    win, "Clear model data",
                    f"Delete {data.MODELS[model]['label']}'s downloaded "
                    f"data ({human(sizes.get(model, 0))})?") \
                    != QtWidgets.QMessageBox.Yes:
                return
            scene.remove_model(model)
            jobs.clear_model(model)
            progress.pop(model, None)
            sizes[model] = 0
            build_items()
            sync_slider()
            update_store_label()
    newest = {}                      # model -> newest published run seen
    def layer_state(model):
        """({layer: hours prepared in the store}, hours in the run)."""
        if model not in scene.models:
            return {}, 0
        run = scene.runs[model]
        with scene.lock:
            params = {p for s in scene.styles[model] for p in core.needs(s)}
            hours = len({v for p in params
                         for v, _ in scene.files[model].expected(p)})
        counts = jobs.prepared_counts(model, run, scene.disk)
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
        get.clicked.connect(lambda _=False, m=model: header_clicked(m))
        badge = QtWidgets.QPushButton()
        badge.setFlat(True)
        badge.setCursor(QtCore.Qt.PointingHandCursor)
        badge.setStyleSheet("color:#ffffff;background:#2b6fd6;"
                            "border-radius:6px;padding:0 6px;font-size:8pt")
        badge.setToolTip("A newer run is published: click to fetch it "
                         "(your fetched layers come with it)")
        badge.clicked.connect(lambda _=False, m=model: fetch(m, latest=True))
        badge.hide()
        ui[model] = dict(get=get, rows={}, title=title, badge=badge)
        for w in (box, title):
            w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(
                lambda pos, m=model, w=w: model_menu(m, w.mapToGlobal(pos)))
        head.addWidget(grip)
        head.addWidget(fold)
        head.addWidget(on)
        head.addWidget(title, 1)
        head.addWidget(badge)
        head.addWidget(get)
        lay.addLayout(head)
        note = QtWidgets.QLabel()
        note.setStyleSheet("color:#2b5fb4;font-size:8pt;margin-left:40px")
        note.setWordWrap(True)
        note.linkActivated.connect(lambda _l, m=model: show_log(m))
        lay.addWidget(note)
        ui[model]["note"] = note
        body = QtWidgets.QWidget()
        rows = QtWidgets.QGridLayout(body)
        rows.setContentsMargins(40, 0, 0, 0)
        rows.setVerticalSpacing(1)
        r = 0
        for name in reversed(core.capable_styles(model)):  # top first
            lid = core.layer_id(model, name)
            style = STYLES[name]
            has = lid in scene.visible
            tick = QtWidgets.QCheckBox(style["name"])
            tick.setChecked((has and scene.visible[lid]) or lid in wanted_on)
            state = QtWidgets.QLabel()
            ui[model]["rows"][name] = (tick, state)
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

            def toggled(v, lid=lid, key=key, m=model, s=name):
                key.setVisible(v)
                resize_items()
                if v and not layer_state(m)[0].get(s) and                         m not in running and not waiting[m]:
                    fetch(m, [s])        # nothing fetched yet: fetch it
                if lid in scene.layers:
                    scene.set_visible(lid, v)
                elif v:                  # shown once the model reloads
                    wanted_on.add(lid)
                else:
                    wanted_on.discard(lid)

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
        refresh_group(model, resize=False)
        return box

    def header_clicked(model):
        if model in running or waiting[model]:
            stop_fetch(model)
        else:
            fetch(model, latest=True)

    def refresh_group(model, resize=True):
        """Bring one model's group up to date in place: progress line,
        header button, each layer's fetched state."""
        w = ui.get(model)
        if not w:
            return
        try:
            _refresh_group(model, w, resize)
        except RuntimeError:             # widgets dropped by a reorder
            pass

    def run_text(model):
        """Header text: name, run and its age."""
        label = data.MODELS[model]["label"]
        if model not in scene.models:
            return label + "  (no data)"
        run = scene.runs[model]
        age = (datetime.now(timezone.utc).replace(tzinfo=None)
               - data.stamp(run)).total_seconds() / 3600
        return f"{label}  {run[9:11]}Z {run[6:8]}/{run[4:6]}  ({age:.0f} h old)"

    def _refresh_group(model, w, resize):
        w["title"].setText(run_text(model))
        last = data.last_event(model)
        w["title"].setToolTip(
            "Open / fold this model's layers (right-click for more)"
            + (f"\nOn disk: {human(sizes[model])}" if model in sizes else "")
            + (f"\nLast store change: {last}" if last else ""))
        newer = newest.get(model)
        show = bool(newer and model in scene.models
                    and newer > scene.runs[model]
                    and model not in running and not waiting[model])
        if show:
            w["badge"].setText(f"{newer[9:11]}Z available")
        w["badge"].setVisible(show)
        text = progress.get(model, "")
        if text.startswith("fetch failed"):
            import html
            text = (f"<span style='color:#c0392b'>{html.escape(text)}</span>"
                    " &nbsp;<a href='log'>details</a>")
        changed = w["note"].text() != text or w["note"].isVisible() != bool(
            text)
        w["note"].setText(text)
        w["note"].setVisible(bool(text))
        busy = model in running or bool(waiting[model])
        if w.get("busy") != busy:
            w["busy"] = busy
            if busy:
                w["get"].setIcon(win.style().standardIcon(
                    QtWidgets.QStyle.SP_MediaStop))
                w["get"].setToolTip("Stop this model's fetch (hours "
                                    "already prepared are kept)")
            else:
                w["get"].setIcon(icon)
                w["get"].setToolTip("Fetch and prepare everything of this "
                                    "model's latest run")
        prepared, hours = layer_state(model)
        for name, (tick, state) in w["rows"].items():
            n = prepared.get(name, 0)
            if hours and n >= hours:
                look = ("●", "color:#2e9e3e",
                        f"Fetched: all {hours} hours ready", "", None)
            elif n:
                look = (f"{n}/{hours}", "color:#c07a00;font-size:8pt",
                        f"{n} of {hours} hours ready: press the download "
                        "button for the rest", "", None)
            else:
                look = ("", "", "", "color:#8a8a8a",
                        "Not fetched: tick it (or press its download button) to "
                "fetch it")
            if state.text() != look[0]:
                state.setText(look[0])
                state.setStyleSheet(look[1])
                state.setToolTip(look[2])
                tick.setStyleSheet(look[3])
                tick.setToolTip(look[4] or "")
        if changed and resize:
            resize_items()

    def refresh_all():
        for m in panel_order:
            refresh_group(m, resize=False)
        resize_items()

    groups = QtWidgets.QListWidget()
    groups.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
    groups.setDefaultDropAction(QtCore.Qt.MoveAction)
    groups.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
    groups.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)

    def groups_resized(ev, base=groups.resizeEvent):
        base(ev)
        QtCore.QTimer.singleShot(0, resize_items)
    groups.resizeEvent = groups_resized

    def resize_items():
        for k in range(groups.count()):
            item = groups.item(k)
            w = groups.itemWidget(item)
            if w is not None:
                # Full panel width, so the header button sits at the edge.
                size = QtCore.QSize(groups.viewport().width(),
                                    w.sizeHint().height())
                w.resize(size)
                item.setSizeHint(size)

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
            # A tick box in the menu: the menu stays open while ticking.
            box = QtWidgets.QCheckBox(label)
            box.setChecked(checked)
            box.setStyleSheet("padding:3px 10px")
            wa = QtWidgets.QWidgetAction(menu)
            wa.setDefaultWidget(box)
            menu.addAction(wa)
            actions[key] = box
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

    def label_params():
        """Each parameter with how many of the ticked models have it."""
        models = [m for m, a in model_acts.items() if a.isChecked()]
        for s, box in param_acts.items():
            n = sum(s in core.capable_styles(m) for m in models)
            box.setText(f"{STYLES[s]['name']}  ({n} of {len(models)} "
                        "models)")
            box.setEnabled(n > 0)
    params_btn.menu().aboutToShow.connect(label_params)

    def fetch_menus():
        layers = [s for s, a in param_acts.items() if a.isChecked()]
        models = [m for m, a in model_acts.items() if a.isChecked()]
        if not layers or not models:
            status_note.setText("  Tick models and parameters in the menus "
                                "first")
            return
        skipped = []
        for m in models:
            have = core.capable_styles(m)
            mine = [s for s in layers if s in have]
            if mine:
                fetch(m, mine)
            missing = [STYLES[s]["name"] for s in layers if s not in have]
            if missing:
                skipped.append(f"{data.MODELS[m]['label']}: no "
                               + ", ".join(missing))
        if skipped:
            status_note.setText(f"  Skipped (not published): "
                                f"{len(skipped)} model(s), see tooltip")
            status_note.setToolTip("\n".join(skipped))
        else:
            status_note.setText("")
            status_note.setToolTip("")
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
    store_label = QtWidgets.QLabel()
    store_label.setStyleSheet("color:#555;font-size:8pt")
    store_label.setToolTip("Disk used by downloaded data and prepared "
                           "layers")
    fetch_row.addWidget(store_label)
    fetch_row.addWidget(clear_btn)

    def update_store_label():
        if sizes:
            store_label.setText(f"Store: {human(sum(sizes.values()))}")

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
    # Under the slider: one segment per time, green where every layer
    # switched on is ready, grey where something is missing.
    class ReadyStrip(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.flags = []
            self.setFixedHeight(5)
            self.setToolTip("Green: every layer switched on is ready for "
                            "that time. Grey: something is not fetched or "
                            "not prepared yet.")

        def paintEvent(self, _ev):
            n = len(self.flags)
            if not n:
                return
            qp = QtGui.QPainter(self)
            # Line the segments up with the slider's groove.
            pad = slider.style().pixelMetric(
                QtWidgets.QStyle.PM_SliderLength) // 2
            w = self.width() - 2 * pad
            for k, ok in enumerate(self.flags):
                x0 = pad + int(w * (k - 0.5) / max(n - 1, 1))
                x1 = pad + int(w * (k + 0.5) / max(n - 1, 1))
                qp.fillRect(max(x0, 0), 0, max(x1 - x0, 1), 5,
                            QtGui.QColor("#3aa54a" if ok else "#c8c8c8"))
            qp.end()

    strip = ReadyStrip()
    time_box = QtWidgets.QWidget()
    tb = QtWidgets.QVBoxLayout(time_box)
    tb.setContentsMargins(0, 2, 0, 0)
    tb.setSpacing(0)
    tb.addWidget(slider)
    tb.addWidget(strip)
    bar.addWidget(time_box)
    strip_state = dict(key=None, tick=0)

    def update_strip():
        """Which times are ready for the layers switched on (in memory or
        saved prepared), checked every couple of seconds."""
        strip_state["tick"] += 1
        on = [lid for lid in scene.layers if scene.is_on(lid)]
        key = (tuple(on), len(scene.times), strip_state["tick"] // 4)
        if key == strip_state["key"]:
            return
        strip_state["key"] = key
        try:
            names = set(os.listdir(scene.disk)) if on else set()
        except OSError:
            names = set()
        flags = []
        with scene.lock:
            has = {}                     # lid -> times its model publishes
            for lid in on:
                m, s = core.split_id(lid)
                has[lid] = {v for p in core.needs(s)
                            for v, _ in scene.files[m].expected(p)}
            for tt in scene.times:
                ok = bool(on)
                for lid in on:
                    if tt not in has[lid]:
                        continue
                    if (lid, tt) in scene.cache and \
                            scene.cache[(lid, tt)] is not None:
                        continue
                    run = scene.runs[core.split_id(lid)[0]]
                    if (f"{lid.replace(':', '_')}_{run}_"
                            f"{tt:%Y%m%dT%H%MZ}.npz") not in names:
                        ok = False
                        break
                flags.append(ok)
        strip.flags = flags if on else []
        strip.update()
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

    # ---- point readout: double-click the globe ----
    point = dict(lonlat=None, key=None, marker=None)
    readout = QtWidgets.QLabel()
    readout.setTextFormat(QtCore.Qt.RichText)
    readout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
    readout.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    readout.setMargin(6)
    readout_dock = QtWidgets.QDockWidget("Point values", win)
    readout_dock.setWidget(readout)
    win.addDockWidget(QtCore.Qt.RightDockWidgetArea, readout_dock)
    readout_dock.hide()

    def readout_closed(visible):
        if not visible and point["marker"] is not None:
            scene.pl.remove_actor(point["marker"], render=True)
            point.update(lonlat=None, key=None, marker=None)
    readout_dock.visibilityChanged.connect(readout_closed)

    def picked(pos, *_):
        import numpy as np
        p = np.asarray(pos, dtype=float)
        r = float(np.linalg.norm(p))
        if r < 0.5 or r > 1.1:                   # not on the globe
            return
        p /= r
        lat = float(np.degrees(np.arcsin(p[2])))
        lon = float(np.degrees(np.arctan2(p[1], p[0])))
        point.update(lonlat=(lon, lat), key=None)
        if point["marker"] is not None:
            scene.pl.remove_actor(point["marker"], render=False)
        import pyvista as pv
        point["marker"] = scene.pl.add_mesh(
            pv.PolyData(p[None, :] * scene.label_radius()), color="#d01c1c",
            point_size=11, render_points_as_spheres=True, render=False)
        readout_dock.show()
        update_readout(force=True)

    def update_readout(force=False):
        if point["lonlat"] is None or not scene.times:
            return
        key = (point["lonlat"], scene.i,
               tuple(lid for lid in scene.layers if scene.is_on(lid)))
        if key == point["key"] and not force:
            return
        point["key"] = key
        lon, lat = point["lonlat"]
        rows = scene.values_at(lon, lat)
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        html = (f"<b>{abs(lat):.2f}\u00b0{ns} {abs(lon):.2f}\u00b0{ew}</b>"
                f"<br>{scene.time:%a %d %b %H:%M} UTC<br>")
        if not rows:
            html += "<i>Switch layers on to see their values here.</i>"
        else:
            html += "<table cellspacing=4>"
            last = None
            for model, name, text in rows:
                label = data.MODELS[model]["label"] if model != last else ""
                last = model
                html += (f"<tr><td><b>{label}</b></td><td>{name}</td>"
                         f"<td align=right>{text}</td></tr>")
            html += "</table>"
        html += ("<br><span style='color:#666;font-size:8pt'>Double-click "
                 "another place to move; close this panel to remove the "
                 "marker.</span>")
        readout.setText(html)

    view.track_click_position(picked, side="left", double=True)

    # ---- newer runs: checked quietly in the background ----
    def check_newer():
        models = [m for m in panel_order if m in scene.models]

        def work():
            for m in models:
                if data.CLOSING.is_set():
                    return
                try:
                    run = jobs.latest_published(m)
                except Exception:             # offline, server busy
                    continue
                if run:
                    newest[m] = run
        threading.Thread(target=work, daemon=True).start()
    newer_timer = QtCore.QTimer(win)
    newer_timer.timeout.connect(check_newer)
    newer_timer.start(15 * 60 * 1000)
    QtCore.QTimer.singleShot(20 * 1000, check_newer)
    # Ages and badges: refreshed once a minute.
    age_timer = QtCore.QTimer(win)
    age_timer.timeout.connect(refresh_all)
    age_timer.start(60 * 1000)

    last_cam = [None, None]
    shown_progress = [None]

    def poll():
        poll_jobs()
        update_strip()
        if size_ready.is_set():
            size_ready.clear()
            update_store_label()
            refresh_all()
        update_readout()
        # A model finished loading?
        while not done.empty():
            model, result = done.get()
            if isinstance(result, Exception):
                progress[model] = f"could not load: {result}"[:120]
            else:
                scene.add_model(result)
                apply_saved(model)
                for lid in [x for x in wanted_on if x in scene.layers]:
                    scene.visible[lid] = True
                    wanted_on.discard(lid)
                scene.set_order(panel_order)
                if model not in running and not waiting[model]:
                    progress.pop(model, None)
            build_items()
            sync_slider()
        # Fetch progress changed: update those groups in place.
        snap = dict(progress)
        old = shown_progress[0] or {}
        if snap != old:
            shown_progress[0] = snap
            for m in set(snap) | set(old):
                if snap.get(m) != old.get(m):
                    refresh_group(m)
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
        status.setText("  " + text.strip(" ,") if text else "")
    poller = QtCore.QTimer(win)
    poller.timeout.connect(poll)
    poller.start(500)

    # ---- the session's layout, kept between runs ----
    def apply_saved(model):
        layers = saved.get("layers", {})
        for lid in scene.layers:
            if core.split_id(lid)[0] == model and lid in layers:
                v = layers[lid]
                scene.visible[lid] = bool(v.get("on", False))
                scene.opacity[lid] = float(v.get("opacity",
                                                 scene.opacity[lid]))
                scene.label_on[lid] = bool(v.get("labels", False))
        if model in saved.get("model_on", {}):
            scene.model_on[model] = bool(saved["model_on"][model])
        scene.wake.set()

    def save_layout():
        layers = dict(saved.get("layers", {}))  # models not loaded: kept
        for lid in scene.layers:
            layers[lid] = dict(on=scene.visible[lid],
                               opacity=round(scene.opacity[lid], 2),
                               labels=scene.label_on[lid])
        cam = scene.pl.camera
        out = dict(layers=layers, order=panel_order, collapsed=collapsed,
                   model_on={**saved.get("model_on", {}),
                             **scene.model_on},
                   graticule=grid.isChecked(), speed=speed.currentIndex(),
                   camera=[list(cam.position), list(cam.focal_point),
                           list(cam.up)],
                   geometry=bytes(win.saveGeometry().toHex()).decode(),
                   state=bytes(win.saveState().toHex()).decode())
        save_settings(out)

    def reset_layout():
        saved.clear()
        try:
            os.remove(settings_path())
        except OSError:
            pass
        for lid in scene.layers:
            scene.visible[lid] = False
            scene.opacity[lid] = STYLES[core.split_id(lid)[1]].get(
                "opacity", 1.0)
            scene.label_on[lid] = False
        for m in scene.model_on:
            scene.model_on[m] = True
        panel_order[:] = list(core.MODEL_ORDER)
        collapsed.update({m: True for m in data.MODELS})
        grid.setChecked(True)
        speed.setCurrentIndex(2)
        scene.set_order(panel_order, render=False)
        scene.go_home()
        scene.update_labels(render=False)
        scene.show()
        build_items()
    reset = bar.addAction("Reset layout")
    reset.setToolTip("All layers off, default opacities, model order and "
                     "view (the layout is otherwise kept between runs)")
    reset.triggered.connect(reset_layout)
    app.aboutToQuit.connect(save_layout)

    if "geometry" in saved:
        win.restoreGeometry(QtCore.QByteArray.fromHex(
            saved["geometry"].encode()))
        win.restoreState(QtCore.QByteArray.fromHex(saved["state"].encode()))
    grid.setChecked(saved.get("graticule", True))
    speed.setCurrentIndex(saved.get("speed", 2))
    if saved.get("camera"):
        cam = scene.pl.camera
        cam.position, cam.focal_point, cam.up = (
            tuple(v) for v in saved["camera"])

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
                 ("G", grid.toggle),
                 ("F1", lambda: show_help()))
    for keys, slot in shortcuts:
        sc = QtWidgets.QShortcut(keys, win)
        sc.setContext(QtCore.Qt.ApplicationShortcut)
        sc.activated.connect(slot)

    KEY_HELP = {"Right": "next time", "Left": "previous time",
                "Up": "six steps on", "Down": "six steps back",
                "PgUp": "six steps on", "PgDown": "six steps back",
                "Home": "first time", "End": "last time",
                "Space": "play / pause", "H": "home view (Ireland)",
                "N": "north up", "G": "graticule on / off",
                "F1": "this help"}

    def show_help():
        keys = "".join(f"<tr><td><b>{k}</b></td><td>{KEY_HELP.get(k, '')}"
                       "</td></tr>" for k, _ in shortcuts)
        html = f'''
<h3>NWP Globe</h3>
<p><b>Mouse</b>: drag to turn the globe (north stays up), scroll to zoom,
Shift+drag to pan, <b>double-click</b> a place for every shown layer's
value there.</p>
<p><b>Layers panel</b>: click a model's name (or the arrow) to open its
layers; drag \u2630 to draw a model above the others; right-click a
model for more (clear its data).</p>
<table cellspacing=6>
<tr><td style='color:#2e9e3e'>\u25cf</td><td>fetched: every hour ready</td></tr>
<tr><td style='color:#c07a00'>12/31</td><td>partly fetched: hours ready of
the run</td></tr>
<tr><td style='color:#8a8a8a'>grey name</td><td>not fetched: tick it or press
its download button</td></tr>
<tr><td><span style='color:#fff;background:#2b6fd6'>&nbsp;18Z
available&nbsp;</span></td><td>a newer run is published: click to fetch
it</td></tr></table>
<p><b>Download buttons</b>: a model's header button fetches every layer
of its latest run (it turns into a stop button while fetching); a
layer's button fetches that layer for every hour. The <b>Models</b> and
<b>Parameters</b> menus with <b>Fetch</b> fetch several at once. Fetches
can keep running after the window closes.</p>
<p><b>Time bar</b>: the strip under the slider is green where every
layer switched on is ready.</p>
<p>Downloads are kept in <code>data\\store</code> until a newer run
replaces them; <b>Clear data</b> deletes them all. The layout (layers,
order, view) is kept between runs; <b>Reset layout</b> restores the
defaults.</p>
<h4>Keys</h4><table cellspacing=4>{keys}</table>'''
        box = QtWidgets.QMessageBox(win)
        box.setWindowTitle("Help")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(html)
        box.exec_()
    help_act = bar.addAction("?")
    help_act.setToolTip("Help: mouse, keys, what the panel shows (F1)")
    help_act.triggered.connect(show_help)

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
    measure()
    for model in args.load:                     # optional, at start
        fetch(model, latest=True)
    if args.smoke:
        # Testing: save a picture of the whole window, then close.
        def grab():
            scene.refresh()
            win.grab().save(args.smoke)
            print("wrote", args.smoke)
            stop_jobs()
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
    ap.add_argument("--log", action="store_true",
                    help="with --job: output to data/store/jobs/MODEL.log "
                    "(used by the window)")
    ap.add_argument("--run-queue", action="store_true",
                    help="run the fetches left queued when the window "
                    "closed (used by the window)")
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
        run = globe_jobs.run_logged if args.log else globe_jobs.run_job
        return run(args.job[0], args.job[1:] or None, latest=args.latest)
    if args.run_queue:
        import globe_jobs
        return globe_jobs.run_queue()
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
