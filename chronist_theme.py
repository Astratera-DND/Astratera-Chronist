"""Astratera Chronist - colours, stylesheet and vector icons (no image files needed)."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

BG = "#0f1117"
PANEL = "#171a22"
PANEL2 = "#1f232e"
BORDER = "#2a2f3c"
TEXT = "#e8eaf0"
MUTED = "#8d93a5"
ACCENT = "#3ddbc0"
WAVE = "#5b6580"
WAVE_PLAYED = "#3ddbc0"

C_SCENE = "#f5c542"      # yellow
C_LAYOUT = "#4da3ff"     # blue
C_MANUAL = "#ff5c5c"     # red
C_BREAK = "#b48cff"      # violet
C_MUSIC = "#7bd88f"
C_SFX = "#ff9f5a"

KIND_COLOR = {"scene": C_SCENE, "layout": C_LAYOUT, "manual": C_MANUAL, "break": C_BREAK}
KIND_TITLE = {"scene": "Scenes", "layout": "Layouts", "manual": "Manual markers", "break": "Breaks"}

SPEAKER_COLORS = ["#6ea8ff", "#ff8a7a", "#7bd88f", "#f5c542", "#c79bff", "#4fd1e8", "#ff9f5a", "#e58fc2"]
UNKNOWN_COLOR = "#7d8396"

FONT_STACK = '"Segoe UI Variable", "Segoe UI", "Inter", "Helvetica Neue", sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace'

STYLE = f"""
* {{ font-family: {FONT_STACK}; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 8px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {ACCENT}; selection-color: #06241f; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: #2b3242; outline: 0; }}
QPushButton, QToolButton {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 14px; }}
QPushButton:hover, QToolButton:hover {{ background: #283042; border-color: #3a4256; }}
QPushButton:pressed, QToolButton:pressed {{ background: #323b50; }}
QPushButton:disabled, QToolButton:disabled {{ color: #5a6072; background: {PANEL}; }}
QPushButton#primary {{ background: {ACCENT}; color: #06241f; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #62e8d0; }}
QToolButton#play {{ background: {ACCENT}; border: none; border-radius: 22px; }}
QToolButton#play:hover {{ background: #62e8d0; }}
QToolButton#flat {{ background: transparent; border: none; padding: 6px; border-radius: 8px; }}
QToolButton#flat:hover {{ background: {PANEL2}; }}
QToolButton:checked {{ background: #2b3a45; border-color: {ACCENT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3a4256; background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333a4a; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #46506a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTreeWidget, QListWidget {{ background: transparent; border: none; outline: 0; }}
QTreeWidget::item, QListWidget::item {{ padding: 5px 6px; border-radius: 6px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: #232835; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: #2b3446; }}
QProgressBar {{ background: {PANEL}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#popup {{ background: {PANEL}; border: 1px solid #3a4256; border-radius: 12px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#tag {{ font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding: 2px 7px; border-radius: 6px; }}
"""


def _pm(size: int) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def icon(name: str, color: str = TEXT, size: int = 22) -> QIcon:
    """Small crisp vector icons drawn at runtime."""
    pm = _pm(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    s = float(size)

    def tri(x0, x1, cy=s / 2, h=s * 0.32):
        path = QPainterPath()
        path.moveTo(x0, cy - h)
        path.lineTo(x1, cy)
        path.lineTo(x0, cy + h)
        path.closeSubpath()
        p.drawPath(path)

    if name == "play":
        tri(s * 0.30, s * 0.78, h=s * 0.28)
    elif name == "pause":
        p.drawRoundedRect(QRectF(s * 0.28, s * 0.22, s * 0.16, s * 0.56), 2, 2)
        p.drawRoundedRect(QRectF(s * 0.56, s * 0.22, s * 0.16, s * 0.56), 2, 2)
    elif name == "rew":
        tri(s * 0.52, s * 0.06, h=s * 0.32)
        tri(s * 0.94, s * 0.48, h=s * 0.32)
    elif name == "fwd":
        tri(s * 0.06, s * 0.52, h=s * 0.32)
        tri(s * 0.48, s * 0.94, h=s * 0.32)
    elif name == "prev":
        p.drawRoundedRect(QRectF(s * 0.12, s * 0.16, s * 0.12, s * 0.68), 1.5, 1.5)
        tri(s * 0.90, s * 0.30, h=s * 0.34)
    elif name == "next":
        p.drawRoundedRect(QRectF(s * 0.76, s * 0.16, s * 0.12, s * 0.68), 1.5, 1.5)
        tri(s * 0.10, s * 0.70, h=s * 0.34)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(s * 0.12, s * 0.28)
        path.lineTo(s * 0.4, s * 0.28)
        path.lineTo(s * 0.5, s * 0.38)
        path.lineTo(s * 0.88, s * 0.38)
        path.lineTo(s * 0.88, s * 0.78)
        path.lineTo(s * 0.12, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "flag":
        p.drawRoundedRect(QRectF(s * 0.22, s * 0.14, s * 0.07, s * 0.72), 1.5, 1.5)
        path = QPainterPath()
        path.moveTo(s * 0.29, s * 0.16)
        path.lineTo(s * 0.82, s * 0.16)
        path.lineTo(s * 0.68, s * 0.36)
        path.lineTo(s * 0.82, s * 0.56)
        path.lineTo(s * 0.29, s * 0.56)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "text":
        for i, w in enumerate((0.66, 0.5, 0.62, 0.38)):
            p.drawRoundedRect(QRectF(s * 0.16, s * (0.22 + i * 0.16), s * w, s * 0.07), 2, 2)
    elif name == "fit":
        pen = QPen(c, 1.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for sx in (0.16, 0.84):
            d = 1 if sx < 0.5 else -1
            path = QPainterPath()
            path.moveTo(s * (sx + d * 0.0), s * 0.3)
            path.lineTo(s * sx, s * 0.5)
            path.lineTo(s * (sx + d * 0.0), s * 0.7)
            p.drawPath(path)
        p.drawLine(QPointF(s * 0.16, s * 0.5), QPointF(s * 0.84, s * 0.5))
    p.end()
    return QIcon(pm)


def app_pixmap(size: int = 256) -> QPixmap:
    """The application icon: dark tile, mint waveform bars, a yellow marker pin."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, QColor("#202636"))
    g.setColorAt(1, QColor("#0c0f16"))
    p.setBrush(QBrush(g))
    p.setPen(QPen(QColor("#2f3750"), s * 0.012))
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.22, s * 0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    heights = (0.22, 0.42, 0.62, 0.34, 0.5, 0.26)
    bw, gap = s * 0.075, s * 0.045
    x = (s - (len(heights) * bw + (len(heights) - 1) * gap)) / 2
    for h in heights:
        p.drawRoundedRect(QRectF(x, s * 0.58 - s * h / 2 + s * 0.06, bw, s * h), bw / 2, bw / 2)
        x += bw + gap
    p.setBrush(QColor(C_SCENE))
    p.drawEllipse(QPointF(s * 0.5, s * 0.22), s * 0.055, s * 0.055)
    p.setPen(QPen(QColor(C_SCENE), s * 0.02))
    p.drawLine(QPointF(s * 0.5, s * 0.27), QPointF(s * 0.5, s * 0.36))
    p.end()
    return pm


def save_ico(path: str) -> None:
    """Write a multi-size Windows .ico (PNG compressed) without relying on Qt's ico plugin."""
    import struct
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    sizes = (16, 24, 32, 48, 64, 128, 256)
    blobs = []
    for sz in sizes:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        app_pixmap(sz).save(buf, "PNG")
        blobs.append(bytes(ba))
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for sz, data in zip(sizes, blobs):
        out += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in blobs:
        out += data
    with open(path, "wb") as f:
        f.write(out)


def app_icon() -> QIcon:
    ic = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_pixmap(sz))
    return ic
