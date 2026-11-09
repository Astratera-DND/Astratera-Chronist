"""Astratera Chronist - chapter menu, now-playing cards and transcript panel."""
from __future__ import annotations

import bisect
from typing import Dict, List, Optional

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QStyledItemDelegate, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

import chronist_theme as th
from chronist_core import AudioEvent, Marker, Session, Transcript, fmt_time

GROUP_ORDER = ("scene", "layout", "manual", "break")


def dot_icon(color: str, size: int = 10) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size, size)
    p.end()
    return QIcon(pm)


# ==========================================================================
# chapter menu
# ==========================================================================
SHORT_KIND = {"scene": "Scene", "layout": "Layout", "manual": "Mark", "break": "Break"}


class ChapterMenu(QFrame):
    """Popup with every marker, colour coded and searchable. Sorted by time, or grouped by type (toggle)."""
    markerChosen = pyqtSignal(object)
    groupingChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("popup")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search markers...  (name or time like 1:12:05)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.chips: Dict[str, QToolButton] = {}
        for kind in GROUP_ORDER:
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(True)
            b.setIcon(dot_icon(th.KIND_COLOR[kind]))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setToolTip(f"Show / hide {th.KIND_TITLE[kind].lower()}")
            b.toggled.connect(self._apply_filter)
            row.addWidget(b)
            self.chips[kind] = b
        row.addStretch(1)
        self.group_btn = QToolButton()
        self.group_btn.setText("Group by type")
        self.group_btn.setCheckable(True)
        self.group_btn.setToolTip("Off: one list sorted by time.   On: grouped by type (scenes, layouts, ...).")
        self.group_btn.toggled.connect(self._grouping_toggled)
        row.addWidget(self.group_btn)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setIconSize(QSize(10, 10))
        self.tree.setColumnWidth(0, 92)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(2, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemActivated.connect(self._clicked)
        lay.addWidget(self.tree, 1)

        self.hint = QLabel("Click a marker to jump there")
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)
        self.resize(460, 560)
        self.search.installEventFilter(self)
        self._markers: List[Marker] = []
        self._playhead = 0.0
        self._groups: Dict[str, QTreeWidgetItem] = {}
        self._items: List[tuple] = []       # (QTreeWidgetItem, Marker) in display order

    # ---- setup ------------------------------------------------------------
    def set_grouped(self, on: bool):
        self.group_btn.blockSignals(True)
        self.group_btn.setChecked(on)
        self.group_btn.blockSignals(False)

    @property
    def grouped(self) -> bool:
        return self.group_btn.isChecked()

    def _grouping_toggled(self, on: bool):
        self._rebuild()
        self.groupingChanged.emit(on)

    def eventFilter(self, obj, ev):
        if obj is self.search and ev.type() == ev.Type.KeyPress and ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._focus_first()
            return True
        return super().eventFilter(obj, ev)

    def _focus_first(self):
        for it, _ in self._items:
            if not it.isHidden():
                self.tree.setCurrentItem(it)
                self.tree.setFocus()
                return

    def populate(self, markers: List[Marker], playhead: float):
        self._markers = sorted(markers, key=lambda m: m.t)
        self._playhead = playhead
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        counts = {k: sum(1 for m in markers if m.kind == k) for k in GROUP_ORDER}
        for kind, b in self.chips.items():
            b.setText(f" {th.KIND_TITLE[kind].split()[0]}  {counts[kind]}")
        self._rebuild()

    # ---- building -----------------------------------------------------------
    def _make_item(self, m: Marker, show_type: bool) -> QTreeWidgetItem:
        it = QTreeWidgetItem([fmt_time(m.t), m.label, SHORT_KIND[m.kind] if show_type else ""])
        it.setIcon(0, dot_icon(th.KIND_COLOR[m.kind]))
        mono = QFont("Cascadia Mono, Consolas, DejaVu Sans Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        it.setFont(0, mono)
        it.setForeground(0, QColor(th.MUTED))
        it.setForeground(2, QColor(th.KIND_COLOR[m.kind]))
        if m.beyond:
            it.setToolTip(1, "Lies beyond the end of the audio (clamped to the end)")
            it.setForeground(1, QColor("#e0a458"))
        return it

    def _bold(self, it: QTreeWidgetItem):
        f = it.font(1)
        f.setBold(True)
        it.setFont(1, f)

    def _rebuild(self):
        self.tree.clear()
        self._groups.clear()
        self._items.clear()
        grouped = self.grouped
        self.tree.setColumnHidden(2, grouped)
        current = None
        if grouped:
            for kind in GROUP_ORDER:
                ms = [m for m in self._markers if m.kind == kind]
                g = QTreeWidgetItem([f"{th.KIND_TITLE[kind].upper()}   -   {len(ms)}"])
                g.setFlags(Qt.ItemFlag.ItemIsEnabled)
                f = g.font(0)
                f.setBold(True)
                f.setPointSizeF(8.5)
                g.setFont(0, f)
                g.setForeground(0, QColor(th.KIND_COLOR[kind]))
                self.tree.addTopLevelItem(g)
                g.setFirstColumnSpanned(True)
                self._groups[kind] = g
                last = None
                for m in ms:
                    it = self._make_item(m, False)
                    g.addChild(it)
                    self._items.append((it, m))
                    if m.t <= self._playhead + 0.5:
                        last = it
                if last is not None:
                    self._bold(last)
                    if kind == "scene":
                        current = last
                g.setExpanded(True)
        else:
            last = None
            for m in self._markers:
                it = self._make_item(m, True)
                self.tree.addTopLevelItem(it)
                self._items.append((it, m))
                if m.t <= self._playhead + 0.5:
                    last = it
            if last is not None:
                self._bold(last)
                current = last
        self._apply_filter()
        if current is not None and not current.isHidden():
            self.tree.setCurrentItem(current)
            self.tree.scrollToItem(current, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _apply_filter(self, *_):
        q = self.search.text().strip().lower()
        for it, m in self._items:
            ok = self.chips[m.kind].isChecked() and (not q or q in it.text(1).lower() or q in it.text(0)
                                                      or q in SHORT_KIND[m.kind].lower())
            it.setHidden(not ok)
        for g in self._groups.values():
            g.setHidden(all(g.child(i).isHidden() for i in range(g.childCount())))

    def _clicked(self, item: QTreeWidgetItem, _col=0):
        for it, m in self._items:
            if it is item:
                self.hide()
                self.markerChosen.emit(m)
                return
        if item.parent() is None:
            item.setExpanded(not item.isExpanded())

    def open_for(self, anchor: QWidget):
        scr = QGuiApplication.screenAt(anchor.mapToGlobal(QPoint(0, 0))) or QGuiApplication.primaryScreen()
        geo = scr.availableGeometry()
        below = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        above = anchor.mapToGlobal(QPoint(0, -self.height() - 6))
        pos = below if below.y() + self.height() <= geo.bottom() else above
        x = min(max(pos.x(), geo.left() + 6), geo.right() - self.width() - 6)
        self.move(x, max(pos.y(), geo.top() + 6))
        self.show()
        self.search.setFocus()


# ==========================================================================
# what is happening at the playhead
# ==========================================================================
class _Card(QFrame):
    def __init__(self, tag: str, color: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        top = QHBoxLayout()
        self.tag = QLabel(tag)
        self.tag.setObjectName("tag")
        self.set_tag(tag, color)
        top.addWidget(self.tag)
        top.addStretch(1)
        self.meta = QLabel("")
        self.meta.setObjectName("muted")
        top.addWidget(self.meta)
        lay.addLayout(top)
        self.title = QLabel("-")
        f = self.title.font()
        f.setPointSizeF(12)
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        lay.addWidget(self.sub)

    def set_tag(self, text: str, color: str):
        c = QColor(color)
        self.tag.setText(text)
        self.tag.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},45); color: {color};")

    def set(self, title: str, sub: str, meta: str = "", dim: bool = False):
        if self.title.text() != title:
            self.title.setText(title)
        if self.sub.text() != sub:
            self.sub.setText(sub)
        if self.meta.text() != meta:
            self.meta.setText(meta)
        self.title.setStyleSheet(f"color: {th.MUTED};" if dim else "")


class NowPlayingPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.chapter = _Card("CHAPTER", th.C_SCENE)
        self.music = _Card("MUSIC", th.C_MUSIC)
        self.sfx = _Card("SFX", th.C_SFX)
        for c in (self.chapter, self.music, self.sfx):
            lay.addWidget(c, 1)
        self.session: Optional[Session] = None
        self._scenes: List[Marker] = []
        self._layouts: List[Marker] = []
        self.clear()

    def set_session(self, s: Optional[Session]):
        self.session = s
        ms = s.markers if s else []
        self._scenes = [m for m in ms if m.kind == "scene"]
        self._layouts = [m for m in ms if m.kind == "layout"]
        self._st = [m.t for m in self._scenes]
        self._lt = [m.t for m in self._layouts]
        if s is None:
            self.clear()
        else:
            self.update_at(0.0)

    def clear(self):
        self.chapter.set("-", "No session loaded")
        self.music.set("-", "")
        self.sfx.set("-", "")

    @staticmethod
    def _last(ms: List[Marker], ts: List[float], t: float) -> Optional[Marker]:
        i = bisect.bisect_right(ts, t + 0.05) - 1
        return ms[i] if i >= 0 else None

    def update_at(self, t: float):
        s = self.session
        if s is None:
            return
        sc, lo = self._last(self._scenes, self._st, t), self._last(self._layouts, self._lt, t)
        if sc:
            self.chapter.set(sc.label, f"Layout: {lo.label}" if lo else "Layout: -", f"since {fmt_time(sc.t)}")
        else:
            self.chapter.set("Before the first scene", f"Layout: {lo.label}" if lo else "Layout: -")

        a = s.audio
        mu = a.music_at(t)
        if mu:
            since = "since recording start" if mu.t < 0.5 else f"started {fmt_time(mu.t)}  ({fmt_time(t - mu.t)} ago)"
            end = a.music_end(mu, s.duration)
            nxt = f"   -   next track in {fmt_time(end - t)}" if end < s.duration - 0.5 and end > t else ""
            self.music.set(mu.name or mu.raw_name, f"{mu.folder + '  -  ' if mu.folder else ''}{since}{nxt}",
                           "started" )
        else:
            self.music.set("No music started yet", "")
        ev = a.oneshot_at(t)
        if ev:
            ago = t - ev.t
            self.sfx.set_tag("QUICK FIRE" if ev.kind == "quickfire" else "SFX", th.C_SFX)
            self.sfx.set(ev.name or ev.raw_name, f"{ev.folder + '  -  ' if ev.folder else ''}triggered {fmt_time(ago)} ago"
                         if ago >= 1 else f"{ev.folder + '  -  ' if ev.folder else ''}triggered just now",
                         dim=ago > 8)
        else:
            self.sfx.set_tag("SFX", th.C_SFX)
            self.sfx.set("No sound effect yet", "")


# ==========================================================================
# transcript
# ==========================================================================
CURRENT_LINE = QColor(61, 219, 192, 52)          # the passage that is being said right now
LEAD_IN_CLICK = 1.5                              # whisper timestamps are approximate: start a little early


class _LineDelegate(QStyledItemDelegate):
    """Paints the marker for the passage that is being said right now.
    (Drawn here instead of changing the item: changing items forces Qt to re-layout the whole list.)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = -1

    def paint(self, painter, option, index):
        if index.row() == self.current:
            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            r = option.rect.adjusted(1, 1, -1, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(CURRENT_LINE)
            painter.drawRoundedRect(r, 6, 6)
            painter.setBrush(QColor(th.ACCENT))
            painter.drawRoundedRect(QRectF(r.left(), r.top() + 3, 3, r.height() - 6), 1.5, 1.5)
            painter.restore()
        super().paint(painter, option, index)


class TranscriptPanel(QWidget):
    seekRequested = pyqtSignal(float)
    loadRequested = pyqtSignal()
    offsetChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tr_: Optional[Transcript] = None
        self.offset = 0.0
        self._items: List[QListWidgetItem] = []
        self._cur = -1
        self._t = 0.0
        self._icons: Dict[str, QIcon] = {}
        self._colors: Dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Transcript")
        f = title.font()
        f.setBold(True)
        f.setPointSizeF(12)
        title.setFont(f)
        head.addWidget(title)
        head.addStretch(1)
        self.load_btn = QToolButton()
        self.load_btn.setText("Open...")
        self.load_btn.setToolTip("Choose a transcript file (.json, .srt, .vtt or .txt)")
        self.load_btn.clicked.connect(self.loadRequested)
        head.addWidget(self.load_btn)
        lay.addLayout(head)

        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search transcript...  (Enter = next)")
        self.search.setToolTip("Shows only matching passages. Enter plays from the next match after the playhead.")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._jump_to_next_match)
        lay.addWidget(self.search)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(160)
        self._debounce.timeout.connect(self._filter)
        self.search.textChanged.connect(lambda *_: self._debounce.start())

        row = QHBoxLayout()
        self.follow = QCheckBox("Follow playback")
        self.follow.setChecked(True)
        self.follow.toggled.connect(lambda on: on and self._scroll_to_current())
        row.addWidget(self.follow)
        row.addStretch(1)
        row.addWidget(QLabel("Offset"))
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-3600, 3600)
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setSuffix(" s")
        self.offset_spin.setToolTip("Shift the transcript against the audio if they do not line up")
        self.offset_spin.valueChanged.connect(self._offset)
        row.addWidget(self.offset_spin)
        lay.addLayout(row)

        self.legend = QLabel("")
        self.legend.setWordWrap(True)
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.legend)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #e0a458;")
        self.warn.hide()
        lay.addWidget(self.warn)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        # NOTE: no "batched" layout - with it, scrolling to a line far down a long transcript silently fails
        self.delegate = _LineDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.setIconSize(QSize(10, 10))
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)
        lay.addWidget(self.list, 1)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        lay.addWidget(self.count)

    # ---- data -------------------------------------------------------------
    def _color_for(self, name: str) -> str:
        if not name:
            return th.UNKNOWN_COLOR
        if name not in self._colors:
            if name.lower().startswith("unknown"):
                self._colors[name] = th.UNKNOWN_COLOR
            else:
                used = sum(1 for c in self._colors.values() if c != th.UNKNOWN_COLOR)
                self._colors[name] = th.SPEAKER_COLORS[used % len(th.SPEAKER_COLORS)]
        return self._colors[name]

    def _text_for(self, i: int) -> str:
        s = self.tr_.segments[i]
        head = fmt_time(max(0.0, s.start + self.offset)) + (f"   {s.speaker}" if s.speaker else "")
        return f"{head}\n{s.text}"

    def set_transcript(self, tr: Optional[Transcript], warning: Optional[str] = None, info: str = ""):
        self.tr_ = tr
        self.list.clear()
        self._items.clear()
        self._cur = -1
        self.delegate.current = -1
        self._colors.clear()
        self.legend.setText("")
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.warn.setVisible(bool(warning))
        self.warn.setText(warning or "")
        self.info.setText(info)
        if tr is None:
            self.count.setText("No transcript loaded. Use Open... to pick one.")
            return
        for sp in tr.speakers:
            self._color_for(str(sp.get("name", "")))
        self.list.setUpdatesEnabled(False)
        for i, s in enumerate(tr.segments):
            it = QListWidgetItem(self._text_for(i))
            it.setData(Qt.ItemDataRole.UserRole, i)
            col = self._color_for(s.speaker)
            if col not in self._icons:
                self._icons[col] = dot_icon(col)
            it.setIcon(self._icons[col])
            self.list.addItem(it)
            self._items.append(it)
        self.list.setUpdatesEnabled(True)
        total = sum(float(sp.get("seconds", 0)) for sp in tr.speakers) or 1.0
        parts = [f'<span style="color:{self._color_for(str(sp.get("name", "")))}">&#9679;</span> '
                 f'{sp.get("name", "?")} <span style="color:{th.MUTED}">{float(sp.get("seconds", 0)) / total * 100:.0f}%</span>'
                 for sp in tr.speakers]
        self.legend.setText("&nbsp;&nbsp;&nbsp;".join(parts))
        self.count.setText(f"{len(tr.segments)} passages")
        self.set_time(self._t)

    def _offset(self, v: float):
        self.offset = v
        for i, it in enumerate(self._items):
            it.setText(self._text_for(i))
        self._cur_reset()
        self.offsetChanged.emit(v)

    def _cur_reset(self):
        """After an offset change the line that is 'current' may be a different one."""
        self._cur = -1
        self.delegate.current = -1
        self.set_time(self._t)

    # ---- search -----------------------------------------------------------
    def _filter(self):
        q = self.search.text().strip().lower()
        if not self.tr_:
            return
        shown = 0
        for i, it in enumerate(self._items):
            s = self.tr_.segments[i]
            ok = (not q) or q in s.text.lower() or q in s.speaker.lower()
            it.setHidden(not ok)
            shown += ok
        self.count.setText(f"{shown} of {len(self._items)} passages" if q else f"{len(self._items)} passages")
        if not q:
            self._scroll_to_current()           # back to where the playhead is

    def _jump_to_next_match(self):
        """Enter in the search box: play from the next matching passage after the playhead (wraps around)."""
        if not self.tr_:
            return
        visible = [i for i, it in enumerate(self._items) if not it.isHidden()]
        if not visible:
            return
        now = self._t - self.offset
        nxt = next((i for i in visible if self.tr_.segments[i].start > now + 0.2), visible[0])
        self.list.scrollToItem(self._items[nxt], QAbstractItemView.ScrollHint.PositionAtCenter)
        self.list.setCurrentItem(self._items[nxt])
        self._clicked(self._items[nxt])

    # ---- following the playhead ---------------------------------------------
    def _clicked(self, it: QListWidgetItem):
        i = it.data(Qt.ItemDataRole.UserRole)
        if self.tr_ is not None and i is not None:
            self.seekRequested.emit(max(0.0, self.tr_.segments[i].start + self.offset - LEAD_IN_CLICK))

    def _scroll_to_current(self):
        if self.follow.isChecked() and 0 <= self._cur < len(self._items) and not self._items[self._cur].isHidden():
            self.list.scrollToItem(self._items[self._cur], QAbstractItemView.ScrollHint.PositionAtCenter)

    def set_time(self, t: float):
        self._t = t
        tr = self.tr_
        if tr is None or not tr.segments:
            return
        i = tr.index_at(t - self.offset)
        if i == self._cur:
            return
        self._cur = i
        self.delegate.current = i
        self.list.viewport().update()
        if i >= 0:
            self._scroll_to_current()
