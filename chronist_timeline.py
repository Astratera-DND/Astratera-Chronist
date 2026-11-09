"""Astratera Chronist - timeline widgets (zoomable waveform with marker lanes, overview strip)."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QToolTip, QWidget

import chronist_theme as th
from chronist_core import Marker, PeakPyramid, WavInfo, fmt_time

LANES = ("scene", "layout", "manual")          # breaks are drawn in the "manual" lane
LANE_TITLES = {"scene": "SCENES", "layout": "LAYOUTS", "manual": "MARKS"}
RULER_H, LANE_H, LANE_GAP, PARTS_H = 24, 20, 2, 22
MIN_PIP_GAP = 11            # markers closer than this (px) collapse into one badge
TICK_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400)


def _lane_of(m: Marker) -> str:
    return "manual" if m.kind == "break" else m.kind


class TimelineView(QWidget):
    seekRequested = pyqtSignal(float)           # user scrubbed / clicked: audio time
    markerClicked = pyqtSignal(object)          # a single marker pip/label was clicked
    viewChanged = pyqtSignal(float, float)      # start, span

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(250)
        self.duration = 0.0
        self.parts: List[WavInfo] = []
        self.markers: List[Marker] = []
        self.pyramid: Optional[PeakPyramid] = None
        self.gain = 1.0
        self.progress = 0.0                  # peak analysis progress 0..1
        self.playhead = 0.0
        self.view_start = 0.0
        self.view_span = 1.0
        self.follow = True
        self.shown_lanes = {"scene": True, "layout": True, "manual": True}

        self._base: Optional[QPixmap] = None
        self._played: Optional[QPixmap] = None
        self._hits: List[Tuple[QRectF, str, List[Marker]]] = []
        self._hover_x: Optional[float] = None
        self._mode: Optional[str] = None     # "scrub" | "pan"
        self._pan_origin = (0.0, 0.0)
        self._last_seek = QElapsedTimer()
        self._last_seek.start()
        self._font = QFont()
        self._font.setPointSizeF(8.5)
        self._small = QFont()
        self._small.setPointSizeF(7.0)
        self._small.setBold(True)
        self._small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)

    # ------------------------------------------------------------------ data
    def set_data(self, duration: float, parts: List[WavInfo], markers: List[Marker]):
        self.duration = duration
        self.parts = parts
        self.markers = markers
        self.pyramid = None
        self.progress = 0.0
        self.playhead = 0.0
        self.view_start, self.view_span = 0.0, max(duration, 1.0)
        self._invalidate()
        self.viewChanged.emit(self.view_start, self.view_span)

    def clear(self):
        self.set_data(0.0, [], [])

    def set_pyramid(self, pyr: PeakPyramid):
        self.pyramid = pyr
        top = max(int(np.abs(pyr.levels[-1][0].astype(np.int32)).max(initial=0)),
                  int(np.abs(pyr.levels[-1][1].astype(np.int32)).max(initial=0)))
        peak = max(top / 32768.0, 0.02)
        self.gain = min(0.92 / peak, 12.0)
        self.progress = 1.0
        self._invalidate()

    def set_progress(self, p: float):
        self.progress = p
        if self.pyramid is None:
            self.update()

    def set_lane_visible(self, lane: str, on: bool):
        self.shown_lanes[lane] = on
        self._invalidate()

    # ------------------------------------------------------------------ view
    def _clamp_view(self, start: float, span: float) -> Tuple[float, float]:
        d = max(self.duration, 1.0)
        span = max(min(span, d), min(4.0, d))
        start = max(0.0, min(start, d - span))
        return start, span

    def set_view(self, start: float, span: float):
        start, span = self._clamp_view(start, span)
        if abs(start - self.view_start) < 1e-6 and abs(span - self.view_span) < 1e-6:
            return
        self.view_start, self.view_span = start, span
        self._invalidate()
        self.viewChanged.emit(start, span)

    def fit(self):
        self.set_view(0.0, self.duration)

    def zoom_at(self, t: float, factor: float):
        """factor < 1 zooms in."""
        span = self.view_span * factor
        rel = (t - self.view_start) / self.view_span
        self.set_view(t - rel * span, span)

    def center_on(self, t: float, only_if_hidden: bool = True):
        if only_if_hidden and self.view_start <= t <= self.view_start + self.view_span:
            return
        self.set_view(t - self.view_span / 2, self.view_span)

    def set_playhead(self, t: float):
        self.playhead = t
        if self.follow and self._mode is None and self.duration > 0 and self.view_span < self.duration:
            if t > self.view_start + self.view_span * 0.97 or t < self.view_start:
                self.set_view(t - self.view_span * 0.08, self.view_span)
        self.update()

    # --------------------------------------------------------------- geometry
    def t2x(self, t: float) -> float:
        return (t - self.view_start) / self.view_span * self.width()

    def x2t(self, x: float) -> float:
        return self.view_start + x / max(self.width(), 1) * self.view_span

    def _lanes_visible(self) -> List[str]:
        return [l for l in LANES if self.shown_lanes.get(l, True)]

    def _lane_top(self, lane: str) -> float:
        return RULER_H + 3 + self._lanes_visible().index(lane) * (LANE_H + LANE_GAP)

    def _wave_rect(self) -> QRectF:
        top = RULER_H + 5 + len(self._lanes_visible()) * (LANE_H + LANE_GAP)
        return QRectF(0, top, self.width(), max(40.0, self.height() - top - PARTS_H - 6))

    def _parts_rect(self) -> QRectF:
        return QRectF(0, self.height() - PARTS_H - 2, self.width(), PARTS_H)

    def _invalidate(self):
        self._base = self._played = None
        self.update()

    def resizeEvent(self, e):
        self._invalidate()

    # --------------------------------------------------------------- rendering
    def _render_layers(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        base = QPixmap(int(w * dpr), int(h * dpr))
        base.setDevicePixelRatio(dpr)
        base.fill(Qt.GlobalColor.transparent)
        played = QPixmap(base.size())
        played.setDevicePixelRatio(dpr)
        played.fill(Qt.GlobalColor.transparent)
        self._hits = []

        p = QPainter(base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(th.PANEL))
        p.drawRoundedRect(QRectF(0, 0, w, h), 12, 12)
        if self.duration <= 0:
            p.end()
            self._base, self._played = base, played
            return

        wave = self._wave_rect()
        self._draw_lane_backgrounds(p, w)
        self._draw_waveform(p, QPainter(played), wave)
        self._draw_part_lines(p, wave)
        self._draw_markers(p, wave)
        self._draw_parts_strip(p)
        self._draw_ruler(p, w)
        p.end()
        self._base, self._played = base, played

    def _draw_lane_backgrounds(self, p: QPainter, w: int):
        p.setFont(self._small)
        for lane in self._lanes_visible():
            y = self._lane_top(lane)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 7))
            p.drawRoundedRect(QRectF(4, y, w - 8, LANE_H), 6, 6)
            # the lane title only shows where no marker sits on top of it
            if any(0 <= self.t2x(m.t) < 96 for m in self.markers if _lane_of(m) == lane):
                continue
            c = QColor(th.KIND_COLOR[lane])
            c.setAlpha(80)
            p.setPen(c)
            p.drawText(QRectF(10, y, 90, LANE_H), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       LANE_TITLES[lane])
        p.setFont(self._font)

    def _draw_waveform(self, p: QPainter, pp: QPainter, wave: QRectF):
        w = self.width()
        cy = wave.center().y()
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawLine(QPointF(0, cy), QPointF(w, cy))
        if self.pyramid is None:
            pp.end()
            p.setPen(QColor(th.MUTED))
            p.drawText(wave, Qt.AlignmentFlag.AlignCenter, f"Analysing audio...  {int(self.progress * 100)} %")
            return
        # The waveform is built as a pixel image with numpy (much faster than a 3000-point polygon);
        # the top/bottom rows get fractional coverage so the edges stay smooth.
        dpr = self.devicePixelRatioF()
        wp, hp = max(int(round(w * dpr)), 1), max(int(round(wave.height() * dpr)), 1)
        px_t = self.view_start + (np.arange(wp) + 0.5) * self.view_span / wp
        valid = ((px_t >= 0) & (px_t <= self.duration)).astype(np.float32)
        lo, hi = self.pyramid.render(self.view_start, self.view_start + self.view_span, wp)
        amp = hp / 2 - 3 * dpr
        mid_y = hp / 2
        top = mid_y - np.clip(hi * self.gain, -1, 1) * amp
        bot = mid_y - np.clip(lo * self.gain, -1, 1) * amp
        mid, thick = (top + bot) / 2, np.maximum(bot - top, 1.4 * dpr)
        top, bot = (mid - thick / 2).astype(np.float32), (mid + thick / 2).astype(np.float32)
        rows = np.arange(hp, dtype=np.float32)[:, None]
        cov = np.clip(np.minimum(rows + 1, bot[None, :]) - np.maximum(rows, top[None, :]), 0, 1) * valid[None, :]
        for painter, color in ((p, th.WAVE), (pp, th.WAVE_PLAYED)):
            c = QColor(color)
            arr = np.empty((hp, wp, 4), np.uint8)
            arr[..., 0] = cov * c.blue()
            arr[..., 1] = cov * c.green()
            arr[..., 2] = cov * c.red()
            arr[..., 3] = cov * 255
            img = QImage(arr.data, wp, hp, wp * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
            img.setDevicePixelRatio(dpr)
            painter.drawImage(QPointF(0, wave.top()), img)
        pp.end()

    def _draw_part_lines(self, p: QPainter, wave: QRectF):
        pen = QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        for part in self.parts[1:]:
            x = self.t2x(part.start)
            if 0 <= x <= self.width():
                p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))

    def _draw_parts_strip(self, p: QPainter):
        r = self._parts_rect()
        fm = QFontMetrics(self._font)
        for i, part in enumerate(self.parts):
            x0, x1 = self.t2x(part.start), self.t2x(part.end)
            if x1 < 0 or x0 > self.width():
                continue
            rect = QRectF(max(x0, 0) + 1, r.top(), min(x1, self.width()) - max(x0, 0) - 2, r.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#222838" if i % 2 == 0 else "#2a3144"))
            p.drawRoundedRect(rect, 5, 5)
            label = f"Part {i + 1}   {fmt_time(part.start)} - {fmt_time(part.end)}"
            if fm.horizontalAdvance(label) + 14 > rect.width():
                label = f"Part {i + 1}"
            if fm.horizontalAdvance(label) + 10 <= rect.width():
                p.setPen(QColor(th.MUTED))
                p.drawText(rect.adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

    def _draw_ruler(self, p: QPainter, w: int):
        step = next((s for s in TICK_STEPS if s / self.view_span * w >= 90), TICK_STEPS[-1])
        minor = step / 5 if step >= 5 else step
        p.setFont(self._font)
        t = math.floor(self.view_start / minor) * minor
        while t <= self.view_start + self.view_span + minor:
            x = self.t2x(t)
            major = abs(t / step - round(t / step)) < 1e-6
            if 0 <= x <= w:
                p.setPen(QPen(QColor(255, 255, 255, 90 if major else 35), 1))
                p.drawLine(QPointF(x, RULER_H - (9 if major else 4)), QPointF(x, RULER_H))
                if major and t >= 0:
                    p.setPen(QColor(th.MUTED))
                    p.drawText(QPointF(x + 4, RULER_H - 9), fmt_time(t))
            t += minor

    # ---- marker lanes: clustering + collision aware labels ------------------
    def _draw_markers(self, p: QPainter, wave: QRectF):
        fm = QFontMetrics(self._font)
        w = self.width()
        for lane in self._lanes_visible():
            items = sorted((m for m in self.markers if _lane_of(m) == lane
                            and self.view_start - 60 <= m.t <= self.view_start + self.view_span + 60),
                           key=lambda m: m.t)
            clusters: List[List[Marker]] = []
            for m in items:
                if clusters and self.t2x(m.t) - self.t2x(clusters[-1][-1].t) < MIN_PIP_GAP:
                    clusters[-1].append(m)
                else:
                    clusters.append([m])
            y = self._lane_top(lane)
            cy = y + LANE_H / 2
            for ci, cl in enumerate(clusters):
                x = self.t2x(cl[0].t)
                if x < -80 or x > w + 80:
                    continue
                color = QColor(th.KIND_COLOR[cl[0].kind if len(cl) == 1 else lane])
                # line through the waveform: scenes strong, the rest quieter
                for m in cl[:1] if len(cl) > 1 else cl:
                    lc = QColor(th.KIND_COLOR[m.kind])
                    lc.setAlpha({"scene": 190, "manual": 150, "break": 170, "layout": 70}[m.kind])
                    pen = QPen(lc, 2 if m.kind == "scene" else 1)
                    if m.kind == "break":
                        pen.setStyle(Qt.PenStyle.DashLine)
                    p.setPen(pen)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)     # crisp + fast
                    xi = round(x) + 0.5
                    p.drawLine(QPointF(xi, y + LANE_H), QPointF(xi, wave.bottom()))
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                if len(cl) > 1:
                    last_x = self.t2x(cl[-1].t)
                    badge = QRectF(x - 8, y + 2, max(last_x - x, 0) + 16, LANE_H - 4)
                    txt = str(len(cl))
                    badge.setWidth(max(badge.width(), fm.horizontalAdvance(txt) + 12))
                    bc = QColor(color)
                    bc.setAlpha(60)
                    p.setPen(QPen(color, 1))
                    p.setBrush(bc)
                    p.drawRoundedRect(badge, 7, 7)
                    p.setPen(color)
                    p.drawText(badge, Qt.AlignmentFlag.AlignCenter, txt)
                    self._hits.append((badge.adjusted(-2, -2, 2, 2), "cluster", cl))
                    continue
                m = cl[0]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                if m.kind == "break":
                    p.drawRoundedRect(QRectF(x - 4.5, cy - 4.5, 9, 9), 2, 2)
                elif m.beyond:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(color, 1.6))
                    p.drawEllipse(QPointF(x, cy), 4.5, 4.5)
                else:
                    p.drawEllipse(QPointF(x, cy), 4.5, 4.5)
                nxt = self.t2x(clusters[ci + 1][0].t) if ci + 1 < len(clusters) else w + 400
                avail = min(nxt - x - 16, 240)
                label = ("! " if m.beyond else "") + m.label
                text_w = 0.0
                if avail >= 38:
                    shown = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(avail))
                    text_w = fm.horizontalAdvance(shown)
                    p.setPen(color)
                    p.drawText(QPointF(x + 9, cy + fm.ascent() / 2 - 1), shown)
                self._hits.append((QRectF(x - 7, y, 14 + (text_w + 4 if text_w else 0), LANE_H), "single", [m]))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _):
        if self._base is None:
            self._render_layers()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._base)
        if self.duration <= 0:
            p.setPen(QColor(th.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Enter or browse to a session folder above")
            return
        px = self.t2x(self.playhead)
        wave = self._wave_rect()
        if self._played is not None and px > 0:
            p.save()
            p.setClipRect(QRectF(0, wave.top(), min(px, self.width()), wave.height()))
            p.drawPixmap(0, 0, self._played)
            p.restore()
        if self._hover_x is not None and self._mode is None:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(self._hover_x, RULER_H), QPointF(self._hover_x, wave.bottom()))
            label = fmt_time(self.x2t(self._hover_x))
            fm = QFontMetrics(self._font)
            tw = fm.horizontalAdvance(label) + 12
            bx = min(max(self._hover_x + 6, 2), self.width() - tw - 2)
            r = QRectF(bx, wave.bottom() - 22, tw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 17, 23, 220))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(th.TEXT))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
        if -4 <= px <= self.width() + 4:
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(QPointF(px, RULER_H - 2), QPointF(px, self.height() - PARTS_H - 4))
            label = fmt_time(self.playhead)
            fm = QFontMetrics(self._font)
            tw = fm.horizontalAdvance(label) + 14
            bx = min(max(px - tw / 2, 2), self.width() - tw - 2)
            r = QRectF(bx, 2, tw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor("#0f1117"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

    # ------------------------------------------------------------------ mouse
    def _hit_at(self, pos) -> Optional[Tuple[QRectF, str, List[Marker]]]:
        for h in self._hits:
            if h[0].contains(pos):
                return h
        return None

    def _scrub_to(self, x: float, final: bool = False):
        t = max(0.0, min(self.duration, self.x2t(x)))
        self.playhead = t
        if final or self._last_seek.elapsed() > 80:
            self._last_seek.restart()
            self.seekRequested.emit(t)
        self.update()

    def mousePressEvent(self, e):
        if self.duration <= 0:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_at(pos)
            if hit:
                if hit[1] == "single":
                    self.markerClicked.emit(hit[2][0])
                else:
                    ts = [m.t for m in hit[2]]
                    span = max((max(ts) - min(ts)) * 2.2, 20.0)
                    self.set_view((min(ts) + max(ts)) / 2 - span / 2, span)
                return
            self._mode = "scrub"
            self._scrub_to(pos.x())
        elif e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._mode = "pan"
            self._pan_origin = (pos.x(), self.view_start)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._mode == "scrub":
            self._scrub_to(pos.x())
            return
        if self._mode == "pan":
            dx = pos.x() - self._pan_origin[0]
            self.set_view(self._pan_origin[1] - dx / self.width() * self.view_span, self.view_span)
            return
        self._hover_x = pos.x() if pos.y() > RULER_H else None
        hit = self._hit_at(pos)
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
        if hit:
            ms = hit[2]
            if hit[1] == "single":
                m = ms[0]
                extra = f"\nbreak length {m.detail}" if m.kind == "break" and m.detail else ""
                if m.beyond:
                    extra += "\nlies beyond the end of the audio (clamped)"
                QToolTip.showText(e.globalPosition().toPoint(), f"{fmt_time(m.t)}   {m.label}{extra}", self)
            else:
                lines = [f"{fmt_time(m.t)}   {m.label}" for m in ms[:12]]
                if len(ms) > 12:
                    lines.append(f"... and {len(ms) - 12} more")
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{len(ms)} markers - click to zoom in\n" + "\n".join(lines), self)
        elif self._parts_rect().contains(pos):
            for i, part in enumerate(self.parts):
                if part.start <= self.x2t(pos.x()) < part.end:
                    QToolTip.showText(e.globalPosition().toPoint(),
                                      f"{part.path.name}\n{fmt_time(part.start)} - {fmt_time(part.end)}  "
                                      f"({fmt_time(part.duration)} long)", self)
                    break
        else:
            QToolTip.hideText()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._mode == "scrub":
            self._scrub_to(e.position().x(), final=True)
        self._mode = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _):
        self._hover_x = None
        self.update()

    def wheelEvent(self, e):
        if self.duration <= 0:
            return
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier or abs(dx) > abs(dy):
            d = dx if abs(dx) > abs(dy) else dy
            self.set_view(self.view_start - d / 120 * self.view_span * 0.12, self.view_span)
        else:
            self.zoom_at(self.x2t(e.position().x()), 0.8 ** (dy / 120))
        e.accept()


class OverviewBar(QWidget):
    """Whole session at a glance. Drag the frame to scroll the main view."""
    panRequested = pyqtSignal(float)             # new view start
    fitRequested = pyqtSignal()
    seekRequested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.duration = 0.0
        self.pyramid: Optional[PeakPyramid] = None
        self.markers: List[Marker] = []
        self.playhead = 0.0
        self.view_start = 0.0
        self.view_span = 1.0
        self.gain = 1.0
        self._cache: Optional[QPixmap] = None
        self._grab: Optional[float] = None

    def set_data(self, duration: float, markers: List[Marker]):
        self.duration, self.markers, self.pyramid = duration, markers, None
        self.view_start, self.view_span = 0.0, max(duration, 1.0)
        self._cache = None
        self.update()

    def set_pyramid(self, pyr: PeakPyramid, gain: float):
        self.pyramid, self.gain, self._cache = pyr, gain, None
        self.update()

    def set_view(self, start: float, span: float):
        self.view_start, self.view_span = start, span
        self.update()

    def set_playhead(self, t: float):
        self.playhead = t
        self.update()

    def resizeEvent(self, _):
        self._cache = None

    def _render(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(th.PANEL))
        p.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)
        if self.pyramid is not None and self.duration > 0:
            lo, hi = self.pyramid.render(0, self.duration, w)
            cy, amp = h / 2 + 3, h / 2 - 9
            p.setPen(QPen(QColor(th.WAVE), 1))
            for x in range(w):
                y0 = cy - min(hi[x] * self.gain, 1.0) * amp
                y1 = cy - max(lo[x] * self.gain, -1.0) * amp
                p.drawLine(QPointF(x + 0.5, y0), QPointF(x + 0.5, max(y1, y0 + 1)))
        for m in self.markers:
            if m.kind == "layout" or self.duration <= 0:
                continue
            x = m.t / self.duration * w
            p.setPen(QPen(QColor(th.KIND_COLOR[m.kind]), 2 if m.kind == "scene" else 1))
            p.drawLine(QPointF(x, 2), QPointF(x, 8 if m.kind == "scene" else 6))
        p.end()
        self._cache = pm

    def _frame(self) -> QRectF:
        w = self.width()
        if self.duration <= 0:
            return QRectF()
        x0 = self.view_start / self.duration * w
        x1 = (self.view_start + self.view_span) / self.duration * w
        return QRectF(x0, 1, max(x1 - x0, 6), self.height() - 2)

    def paintEvent(self, _):
        if self._cache is None:
            self._render()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._cache)
        if self.duration <= 0:
            return
        f = self._frame()
        shade = QColor(0, 0, 0, 110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shade)
        p.drawRect(QRectF(0, 1, f.left(), f.height()))
        p.drawRect(QRectF(f.right(), 1, self.width() - f.right(), f.height()))
        p.setBrush(QColor(61, 219, 192, 28))
        p.setPen(QPen(QColor(th.ACCENT), 1.5))
        p.drawRoundedRect(f, 5, 5)
        px = self.playhead / self.duration * self.width()
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(px, 1), QPointF(px, self.height() - 1))

    def _t(self, x: float) -> float:
        return max(0.0, min(self.duration, x / max(self.width(), 1) * self.duration))

    def mousePressEvent(self, e):
        if self.duration <= 0 or e.button() != Qt.MouseButton.LeftButton:
            return
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.seekRequested.emit(self._t(e.position().x()))
            return
        f = self._frame()
        if f.contains(e.position()):
            self._grab = self._t(e.position().x()) - self.view_start
        else:
            self._grab = self.view_span / 2
            self.panRequested.emit(self._t(e.position().x()) - self._grab)

    def mouseMoveEvent(self, e):
        if self._grab is not None:
            self.panRequested.emit(self._t(e.position().x()) - self._grab)

    def mouseReleaseEvent(self, _):
        self._grab = None

    def mouseDoubleClickEvent(self, _):
        self.fitRequested.emit()
