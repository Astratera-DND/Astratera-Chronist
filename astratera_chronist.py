"""Astratera Chronist - review a recorded D&D session on one timeline.

Waveform of all audio parts, chapter markers (scenes / layouts / manual marks / breaks),
the music and SFX that was running, and the transcript. Click anywhere to play from there.

Read-only: the recording folder is never modified. Everything stays on your computer.

    python astratera_chronist.py [session_folder]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMainWindow, QProgressBar, QPushButton, QSizePolicy, QSlider, QSplitter,
                             QToolButton, QVBoxLayout, QWidget)

import chronist_theme as th
from chronist_audio import AudioEngine
from chronist_core import (PART_RE, Marker, PeakPyramid, Session, Transcript, TranscriptCandidate, WavError,
                           check_transcript_alignment, choose_default, compute_part_peaks, fmt_time, load_session,
                           load_transcript, load_transcript_group, merge_part_peaks, samples_per_bin)
from chronist_panels import ChapterMenu, NowPlayingPanel, TranscriptPanel, dot_icon
from chronist_timeline import OverviewBar, TimelineView

APP_NAME = "Astratera Chronist"
APP_VERSION = "1.0.0"
SPEEDS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


# --------------------------------------------------------------------------
# background loading: session files first (instant), then the waveform peaks
# --------------------------------------------------------------------------
class LoadWorker(QThread):
    sessionReady = pyqtSignal(object)
    progress = pyqtSignal(float)
    peaksDone = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            s = load_session(self.folder)
        except Exception as e:                       # noqa: BLE001 - shown to the user
            self.failed.emit(str(e))
            return
        if self._cancel:
            return
        self.sessionReady.emit(s)
        try:
            spb = samples_per_bin(s.sample_rate)
            total = sum(p.frames for p in s.parts)
            done, res = 0, []
            for p in s.parts:
                base = done

                def prog(f, p=p, base=base):
                    self.progress.emit((base + f * p.frames) / total)

                r = compute_part_peaks(p, spb, prog, lambda: self._cancel)
                if r is None:
                    return
                res.append(r)
                done += p.frames
            mins, maxs = merge_part_peaks(res)
            self.peaksDone.emit(PeakPyramid(mins, maxs, spb / s.sample_rate))
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"Could not analyse the audio: {e}")


def sessions_in(folder: Path) -> List[Path]:
    out = []
    for d in folder.iterdir():
        if d.is_dir() and any(PART_RE.match(f.name) for f in d.iterdir() if f.is_file()):
            out.append(d)
    return sorted(out, key=lambda d: d.name, reverse=True)


# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, start_folder: Optional[str] = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(th.app_icon())
        self.setAcceptDrops(True)
        self.resize(1480, 900)
        self.settings = QSettings("Astratera", "Chronist")
        self.session: Optional[Session] = None
        self.worker: Optional[LoadWorker] = None
        self._old_workers: List[LoadWorker] = []
        self.engine = AudioEngine(self)
        self.menu = ChapterMenu(self)

        self._build_ui()
        self._wire()
        self._shortcuts()
        self._restore()
        if start_folder:
            self.path_edit.setText(start_folder)
            self.open_folder(start_folder)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setHandleWidth(1)
        self.split.setChildrenCollapsible(False)
        outer.addWidget(self.split)

        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(18, 16, 14, 16)
        lay.setSpacing(10)
        self.split.addWidget(left)

        # --- top bar: path -------------------------------------------------
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("ASTRATERA CHRONIST")
        title.setStyleSheet(f"font-weight: 700; letter-spacing: 2px; color: {th.ACCENT}; font-size: 9pt;")
        top.addWidget(title)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path to the session folder (contains the _part000.wav, markers, audio log ...)")
        self.path_edit.returnPressed.connect(lambda: self.open_folder(self.path_edit.text()))
        top.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton(" Browse")
        self.browse_btn.setIcon(th.icon("folder"))
        self.browse_btn.clicked.connect(self.browse)
        top.addWidget(self.browse_btn)
        self.load_btn = QPushButton("Load")
        self.load_btn.setObjectName("primary")
        self.load_btn.clicked.connect(lambda: self.open_folder(self.path_edit.text()))
        top.addWidget(self.load_btn)
        self.tr_btn = QToolButton()
        self.tr_btn.setIcon(th.icon("text"))
        self.tr_btn.setCheckable(True)
        self.tr_btn.setChecked(True)
        self.tr_btn.setToolTip("Show / hide transcript  (T)")
        top.addWidget(self.tr_btn)
        lay.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.notice = QFrame()
        self.notice.setStyleSheet("QFrame { background: rgba(224,164,88,28); border: 1px solid rgba(224,164,88,90); border-radius: 10px; }"
                                  "QLabel { color: #f0c27f; border: none; background: transparent; }")
        nl = QHBoxLayout(self.notice)
        nl.setContentsMargins(12, 8, 8, 8)
        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        nl.addWidget(self.notice_label, 1)
        close = QToolButton()
        close.setText("x")
        close.setObjectName("flat")
        close.clicked.connect(self.notice.hide)
        nl.addWidget(close)
        self.notice.hide()
        lay.addWidget(self.notice)

        self.info = QLabel("")
        self.info.setObjectName("muted")
        lay.addWidget(self.info)

        # --- waveform area -------------------------------------------------
        self.overview = OverviewBar()
        lay.addWidget(self.overview)
        self.timeline = TimelineView()
        lay.addWidget(self.timeline, 1)

        # --- transport -----------------------------------------------------
        tp = QHBoxLayout()
        tp.setSpacing(6)
        self.b_prev = self._tool("prev", "Previous chapter  ([ or PageUp)")
        self.b_rew = self._tool("rew", "Back 10 s  (Left = 5 s, Shift+Left = 30 s)")
        self.b_play = QToolButton()
        self.b_play.setObjectName("play")
        self.b_play.setFixedSize(44, 44)
        self.b_play.setIconSize(self.b_play.size() * 0.62)
        self.b_play.setIcon(th.icon("play", "#06241f", 28))
        self.b_play.setToolTip("Play / pause  (Space)")
        self.b_fwd = self._tool("fwd", "Forward 10 s  (Right = 5 s, Shift+Right = 30 s)")
        self.b_next = self._tool("next", "Next chapter  (] or PageDown)")
        for b in (self.b_prev, self.b_rew, self.b_play, self.b_fwd, self.b_next):
            tp.addWidget(b)
        tp.addSpacing(8)
        lbl = QLabel("Skip to")
        lbl.setObjectName("muted")
        tp.addWidget(lbl)
        self.chips = {}
        defaults = {"scene": True, "layout": False, "manual": True, "break": True}
        for kind, on in defaults.items():
            c = QToolButton()
            c.setCheckable(True)
            c.setChecked(on)
            c.setIcon(dot_icon(th.KIND_COLOR[kind], 12))
            c.setToolTip(f"Previous / next chapter stops at: {th.KIND_TITLE[kind]}")
            tp.addWidget(c)
            self.chips[kind] = c
        tp.addSpacing(14)
        self.time_label = QLabel("0:00.0 / 0:00")
        self.time_label.setStyleSheet(f"font-family: {th.MONO_STACK}; font-size: 15pt; font-weight: 600;")
        self.time_label.setMinimumWidth(240)
        tp.addWidget(self.time_label)
        tp.addStretch(1)

        lead_tip = ("Manual marks (Ctrl+M) are set a few seconds AFTER the moment the DM meant.\n"
                    "Jumping to a manual mark - from the marker menu, the timeline or the previous / next\n"
                    "buttons - therefore starts this many seconds earlier.\n"
                    "Scenes, layouts and breaks are exact and never use it.")
        sl = QLabel("Mark lead-in")
        sl.setObjectName("muted")
        sl.setToolTip(lead_tip)
        tp.addWidget(sl)
        self.lead = QComboBox()
        for v in (0, 2, 5, 10):
            self.lead.addItem(f"{v} s", v)
        self.lead.setCurrentIndex(2)
        self.lead.setToolTip(lead_tip)
        tp.addWidget(self.lead)
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}x", s)
        self.speed.setCurrentIndex(1)
        self.speed.setToolTip("Playback speed (voices keep their pitch)  ( , / . )")
        tp.addWidget(self.speed)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(90)
        self.vol.setToolTip("Volume")
        tp.addWidget(self.vol)
        self.b_zout = self._tool(None, "Zoom out  (-)", "-")
        self.b_zin = self._tool(None, "Zoom in  (+ or mouse wheel)", "+")
        self.b_fit = self._tool("fit", "Show the whole session  (0 or double-click the overview)")
        self.b_follow = self._tool(None, "Scroll the view along with the playhead", "Follow")
        self.b_follow.setCheckable(True)
        self.b_follow.setChecked(True)
        self.b_follow.setMinimumWidth(84)
        for b in (self.b_zout, self.b_zin, self.b_fit, self.b_follow):
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            tp.addWidget(b)
        self.b_jump = QPushButton(" Jump to marker")
        self.b_jump.setIcon(th.icon("flag"))
        self.b_jump.setObjectName("primary")
        self.b_jump.setToolTip("All markers grouped by type, searchable  (M)")
        tp.addWidget(self.b_jump)
        lay.addLayout(tp)

        self.now = NowPlayingPanel()
        lay.addWidget(self.now)

        # --- transcript ----------------------------------------------------
        self.transcript = TranscriptPanel()
        side = QWidget()
        sl_ = QVBoxLayout(side)
        sl_.setContentsMargins(14, 16, 18, 16)
        sl_.addWidget(self.transcript)
        self.side = side
        self.split.addWidget(side)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.setSizes([1100, 380])
        self.tr_btn.toggled.connect(self.side.setVisible)

    def _tool(self, icon_name, tip, text=None) -> QToolButton:
        b = QToolButton()
        if icon_name:
            b.setIcon(th.icon(icon_name))
        if text:
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        b.setToolTip(tip)
        b.setIconSize(QSize(22, 22))
        b.setMinimumHeight(36)
        b.setMinimumWidth(40 if not text or len(text) < 3 else 64)
        return b

    # ---------------------------------------------------------------- wiring
    def _wire(self):
        e, tl = self.engine, self.timeline
        e.positionChanged.connect(self.on_position)
        e.playingChanged.connect(self.on_playing)
        e.error.connect(lambda m: self.show_notice(m, error=True))
        self.b_play.clicked.connect(e.toggle)
        self.b_rew.clicked.connect(lambda: self.skip(-10))
        self.b_fwd.clicked.connect(lambda: self.skip(10))
        self.b_prev.clicked.connect(lambda: self.chapter_jump(-1))
        self.b_next.clicked.connect(lambda: self.chapter_jump(1))
        self.speed.currentIndexChanged.connect(lambda: e.set_rate(self.speed.currentData()))
        self.vol.valueChanged.connect(lambda v: e.set_volume((v / 100.0) ** 2))
        self.b_jump.clicked.connect(self.open_menu)
        self.menu.markerChosen.connect(self.goto_marker)
        self.menu.groupingChanged.connect(lambda on: self.settings.setValue("menu_grouped", "true" if on else "false"))
        self.b_fit.clicked.connect(tl.fit)
        self.b_zin.clicked.connect(lambda: self.zoom(0.6))
        self.b_zout.clicked.connect(lambda: self.zoom(1 / 0.6))
        self.b_follow.toggled.connect(lambda on: setattr(tl, "follow", on))
        tl.seekRequested.connect(e.seek)
        tl.markerClicked.connect(self.goto_marker)
        tl.viewChanged.connect(self.overview.set_view)
        self.overview.panRequested.connect(lambda s: tl.set_view(s, tl.view_span))
        self.overview.fitRequested.connect(tl.fit)
        self.overview.seekRequested.connect(e.seek)
        self.transcript.seekRequested.connect(self.seek_and_show)
        self.transcript.loadRequested.connect(self.pick_transcript)

    def _shortcuts(self):
        def sc(keys, fn):
            for k in (keys if isinstance(keys, (list, tuple)) else [keys]):
                QShortcut(QKeySequence(k), self, activated=fn)
        sc("Space", self.engine.toggle)
        sc("Left", lambda: self.skip(-5))
        sc("Right", lambda: self.skip(5))
        sc("Shift+Left", lambda: self.skip(-30))
        sc("Shift+Right", lambda: self.skip(30))
        sc("J", lambda: self.skip(-10))
        sc("L", lambda: self.skip(10))
        sc(["[", "PgUp"], lambda: self.chapter_jump(-1))
        sc(["]", "PgDown"], lambda: self.chapter_jump(1))
        sc("M", self.open_menu)
        sc("T", self.tr_btn.toggle)
        sc(["+", "="], lambda: self.zoom(0.6))
        sc("-", lambda: self.zoom(1 / 0.6))
        sc("0", self.timeline.fit)
        sc("Home", lambda: self.seek_and_show(0))
        sc("End", lambda: self.seek_and_show(self.engine.duration))
        sc(",", lambda: self.step_speed(-1))
        sc(".", lambda: self.step_speed(1))
        sc("Up", lambda: self.vol.setValue(min(100, self.vol.value() + 5)))
        sc("Down", lambda: self.vol.setValue(max(0, self.vol.value() - 5)))
        sc("Ctrl+O", self.browse)

    # -------------------------------------------------------------- settings
    def _restore(self):
        s = self.settings
        self.path_edit.setText(s.value("last_folder", "", str))
        try:
            self.vol.setValue(int(s.value("volume", 80)))
            self.speed.setCurrentIndex(max(0, min(len(SPEEDS) - 1, int(s.value("speed_idx", 1)))))
            self.lead.setCurrentIndex(max(0, min(3, int(s.value("lead_idx", 2)))))
        except (TypeError, ValueError):
            pass
        self.engine.set_volume((self.vol.value() / 100.0) ** 2)
        self.engine.set_rate(self.speed.currentData())
        self.menu.set_grouped(s.value("menu_grouped", "false") == "true")      # default: one list sorted by time
        g = s.value("geometry")
        if g:
            self.restoreGeometry(g)
        self.tr_btn.setChecked(s.value("transcript_visible", "true") != "false")
        self.side.setVisible(self.tr_btn.isChecked())

    def closeEvent(self, ev):
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("volume", self.vol.value())
        s.setValue("speed_idx", self.speed.currentIndex())
        s.setValue("lead_idx", self.lead.currentIndex())
        s.setValue("transcript_visible", "true" if self.tr_btn.isChecked() else "false")
        self.engine.unload()
        for w in [self.worker] + self._old_workers:
            if w is not None:
                w.cancel()
                w.wait(3000)
        super().closeEvent(ev)

    # --------------------------------------------------------------- loading
    def browse(self):
        start = self.path_edit.text().strip() or self.settings.value("last_folder", str(Path.home()), str)
        d = QFileDialog.getExistingDirectory(self, "Choose the session folder", start)
        if d:
            self.path_edit.setText(d)
            self.open_folder(d)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = ev.mimeData().urls()
        if urls:
            p = Path(urls[0].toLocalFile())
            self.open_folder(str(p if p.is_dir() else p.parent))

    def open_folder(self, text: str):
        text = text.strip().strip('"')
        if not text:
            return
        folder = Path(text)
        if not folder.is_dir():
            self.show_notice(f"Folder not found: {folder}", error=True)
            return
        if not any(PART_RE.match(f.name) for f in folder.iterdir() if f.is_file()):
            subs = sessions_in(folder)
            if not subs:
                self.show_notice("No *_partNNN.wav files found in this folder or its sub-folders.", error=True)
                return
            if len(subs) == 1:
                folder = subs[0]
            else:
                pick, ok = QInputDialog.getItem(self, "Choose a session", "Sessions in this folder (newest first):",
                                                [d.name for d in subs], 0, False)
                if not ok:
                    return
                folder = folder / pick
        self.path_edit.setText(str(folder))
        self.notice.hide()
        self.engine.unload()
        self.timeline.clear()
        self.overview.set_data(0, [])
        self.now.set_session(None)
        self.transcript.set_transcript(None)
        self.session = None
        if self.worker is not None:
            w = self.worker
            for sig in (w.sessionReady, w.progress, w.peaksDone, w.failed):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            w.cancel()
            self._old_workers = [x for x in self._old_workers if x.isRunning()] + [w]
        self.worker = LoadWorker(folder)
        self.worker.sessionReady.connect(self.on_session)
        self.worker.progress.connect(self.on_progress)
        self.worker.peaksDone.connect(self.on_peaks)
        self.worker.failed.connect(lambda m: (self.progress.hide(), self.show_notice(m, error=True)))
        self.progress.setValue(0)
        self.progress.show()
        self.info.setText("Reading session ...")
        self.worker.start()

    def on_session(self, s: Session):
        self.session = s
        self.settings.setValue("last_folder", str(s.root))
        self.setWindowTitle(f"{s.title} - {APP_NAME}")
        self.engine.load(s.parts, s.sample_rate)
        self.timeline.set_data(s.duration, s.parts, s.markers)
        self.overview.set_data(s.duration, s.markers)
        self.now.set_session(s)
        counts = {k: sum(1 for m in s.markers if m.kind == k) for k in ("scene", "layout", "manual", "break")}
        tracks = sum(1 for e in s.audio.music)
        self.info.setText(f"{s.title}   -   {len(s.parts)} part{'s' if len(s.parts) != 1 else ''}   -   {fmt_time(s.duration)}   -   "
                          f"{counts['scene']} scenes   -   {counts['layout']} layouts   -   {counts['manual']} marks   -   "
                          f"{counts['break']} breaks   -   {tracks} music tracks")
        if s.warnings:
            self.show_notice("\n".join(s.warnings))
        self.on_position(0.0)
        QTimer.singleShot(0, lambda: self.resolve_transcript(s))        # after the window has painted

    def on_progress(self, f: float):
        self.progress.setValue(int(f * 1000))
        self.timeline.set_progress(f)

    def on_peaks(self, pyr: PeakPyramid):
        self.timeline.set_pyramid(pyr)
        self.overview.set_pyramid(pyr, self.timeline.gain)
        self.progress.hide()

    def resolve_transcript(self, s: Session):
        """Opens the session's transcript by itself; asks only if there are several different ones."""
        if s is not self.session:
            return
        cands = s.transcript_candidates
        if not cands:
            self.transcript.set_transcript(None, info="No transcript found in the session's transcript folder.")
            return
        pick = choose_default(cands)
        if pick is None:
            browse = "Browse for another file..."
            labels = [c.label for c in cands] + [browse]
            text, ok = QInputDialog.getItem(self, "Which transcript?",
                                            f"{len(cands)} transcripts were found for this session. Open:",
                                            labels, 0, False)
            if not ok:
                self.transcript.set_transcript(None, info=f"{len(cands)} transcripts found - use Open... to choose one.")
                return
            if text == browse:
                self.pick_transcript()
                return
            pick = cands[labels.index(text)]
        self.load_transcript_candidate(pick)

    def load_transcript_candidate(self, c: TranscriptCandidate):
        tr, problems = load_transcript_group(c)
        if tr is None:
            self.transcript.set_transcript(None, "Could not read the transcript:\n" + "\n".join(problems))
            return
        self.show_transcript(tr, problems)

    def show_transcript(self, tr: Transcript, problems=()):
        if not self.session:
            return
        warn = check_transcript_alignment(tr, self.session)
        if problems:
            warn = ((warn + "\n") if warn else "") + "Skipped: " + "; ".join(problems) + f" - showing {tr.path.name} instead."
        info = tr.path.name if tr.fmt == "json" else (
            f"{tr.path.name} - a plain {tr.fmt} file: times are assumed to start at the first audio part")
        self.transcript.offset_spin.blockSignals(True)
        self.transcript.offset_spin.setValue(0)
        self.transcript.offset_spin.blockSignals(False)
        self.transcript.offset = 0.0
        self.transcript.set_transcript(tr, warn, info)
        self.transcript.set_time(self.engine.position())

    def pick_transcript(self):
        start = Path.home()
        if self.session:
            start = self.session.root / "transcript" if (self.session.root / "transcript").is_dir() else self.session.root
        f, _ = QFileDialog.getOpenFileName(self, "Open transcript", str(start),
                                           "Transcript files (*.json *.srt *.vtt *.txt);;All files (*)")
        if not f:
            return
        try:
            self.show_transcript(load_transcript(Path(f)))
        except Exception as e:                       # noqa: BLE001
            self.transcript.set_transcript(None, f"Could not read {Path(f).name}: {e}")

    # ---------------------------------------------------------------- notices
    def show_notice(self, text: str, error: bool = False):
        color = "255,92,92" if error else "224,164,88"
        fg = "#ff9a9a" if error else "#f0c27f"
        self.notice.setStyleSheet(f"QFrame {{ background: rgba({color},28); border: 1px solid rgba({color},90); border-radius: 10px; }}"
                                  f"QLabel {{ color: {fg}; border: none; background: transparent; }}")
        self.notice_label.setText(text)
        self.notice.show()

    # --------------------------------------------------------------- playback
    def on_position(self, t: float):
        if self.timeline._mode == "scrub":
            t = self.timeline.playhead
        else:
            self.timeline.set_playhead(t)
        self.overview.set_playhead(t)
        self.time_label.setText(f"{fmt_time(t, ms=False)}.{int((t % 1) * 10)} / {fmt_time(self.engine.duration)}")
        self.now.update_at(t)
        self.transcript.set_time(t)

    def on_playing(self, playing: bool):
        self.b_play.setIcon(th.icon("pause" if playing else "play", "#06241f", 28))

    def skip(self, secs: float):
        if self.engine.loaded:
            self.seek_and_show(self.engine.position() + secs)

    def seek_and_show(self, t: float):
        if not self.engine.loaded:
            return
        self.engine.seek(t)
        self.timeline.center_on(max(0.0, min(t, self.engine.duration)))

    def step_speed(self, d: int):
        self.speed.setCurrentIndex(max(0, min(len(SPEEDS) - 1, self.speed.currentIndex() + d)))

    def zoom(self, factor: float):
        tl = self.timeline
        if tl.duration <= 0:
            return
        inside = tl.view_start <= tl.playhead <= tl.view_start + tl.view_span
        tl.zoom_at(tl.playhead if inside else tl.view_start + tl.view_span / 2, factor)

    # ---------------------------------------------------------------- markers
    def jump_time(self, m: Marker) -> float:
        lead = float(self.lead.currentData()) if m.kind == "manual" else 0.0
        return max(0.0, m.t - lead)

    def goto_marker(self, m: Marker):
        self.seek_and_show(self.jump_time(m))

    def chapter_jump(self, direction: int):
        if not self.session:
            return
        kinds = {k for k, c in self.chips.items() if c.isChecked()}
        ms = [m for m in self.session.markers if m.kind in kinds]
        if not ms:
            return
        pos = self.engine.position()
        if direction > 0:
            nxt = [m for m in ms if self.jump_time(m) > pos + 0.5]
            self.seek_and_show(self.jump_time(nxt[0]) if nxt else self.engine.duration)
        else:
            prv = [m for m in ms if self.jump_time(m) < pos - 2.0]
            self.seek_and_show(self.jump_time(prv[-1]) if prv else 0.0)

    def open_menu(self):
        if not self.session:
            return
        self.menu.populate(self.session.markers, self.engine.position())
        self.menu.open_for(self.b_jump)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Astratera.Chronist")
        except Exception:                            # noqa: BLE001
            pass
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Astratera")
    app.setStyleSheet(th.STYLE)
    if "--export-icon" in argv:                      # used to (re)create assets/chronist.ico
        out = argv[argv.index("--export-icon") + 1]
        th.save_ico(out)
        return 0
    folder = next((a for a in argv[1:] if not a.startswith("--")), None)
    win = MainWindow(folder)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
