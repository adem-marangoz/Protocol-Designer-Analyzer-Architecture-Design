"""Render the application icon and installer artwork with Qt.

Writes:
  src/protocol_designer/resources/app.png   (256x256, window icon)
  installer/app.ico                         (16..256 px, exe + installer icon)
  installer/wizard_images/wizard.bmp        (Inno Setup side image, 164x314 @100%)
  installer/wizard_images/wizard_small.bmp  (Inno Setup header image, 55x55 @100%)
  (+ @2x variants for high-DPI screens)

Run:  QT_QPA_PLATFORM=offscreen python tools/make_icons.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BLUE_DARK = QColor("#0b3d91")
BLUE = QColor("#1f6feb")
CYAN = QColor("#58d5f5")


def render_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    margin = s * 0.04
    rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, BLUE)
    grad.setColorAt(1.0, BLUE_DARK)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect, s * 0.2, s * 0.2)

    # packet: row of byte cells
    cells = 4
    cell_w = s * 0.15
    gap = s * 0.035
    total = cells * cell_w + (cells - 1) * gap
    x0 = (s - total) / 2
    y0 = s * 0.2
    colors = [CYAN, QColor("#ffffff"), QColor("#ffffff"), QColor("#ffd33d")]
    for i in range(cells):
        p.setBrush(colors[i])
        p.drawRoundedRect(QRectF(x0 + i * (cell_w + gap), y0, cell_w, s * 0.17), s * 0.03, s * 0.03)

    # signal waveform below
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(max(1.5, s * 0.055))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    hi, lo = s * 0.52, s * 0.74
    xs = [0.16, 0.28, 0.28, 0.42, 0.42, 0.56, 0.56, 0.70, 0.70, 0.84]
    ys = [lo, lo, hi, hi, lo, lo, hi, hi, lo, lo]
    path.moveTo(QPointF(xs[0] * s, ys[0]))
    for x, y in zip(xs[1:], ys[1:]):
        path.lineTo(QPointF(x * s, y))
    p.drawPath(path)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def dib_bytes(img: QImage) -> bytes:
    """32-bit BGRA DIB (bottom-up) with an AND mask, as stored inside .ico files."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            argb = img.pixel(x, y)
            a, r, g, b = (argb >> 24) & 0xFF, (argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    mask_stride = ((w + 31) // 32) * 4
    mask = bytes(mask_stride * h)  # alpha channel is authoritative; mask all zero
    return header + b"".join(rows) + mask


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    images = []
    for size in sizes:
        img = render_icon(size)
        images.append((size, png_bytes(img) if size >= 256 else dib_bytes(img)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


def render_wizard(width: int, height: int) -> QImage:
    img = QImage(width, height, QImage.Format.Format_RGB888)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, 0, height)
    grad.setColorAt(0.0, BLUE)
    grad.setColorAt(1.0, BLUE_DARK)
    p.fillRect(0, 0, width, height, grad)
    icon = render_icon(int(width * 0.62))
    p.drawImage(int((width - icon.width()) / 2), int(height * 0.12), icon)
    p.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI")
    font.setPixelSize(max(10, int(width * 0.11)))
    font.setBold(True)
    p.setFont(font)
    text_rect = QRectF(0, height * 0.52, width, height * 0.3)
    p.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
               "Protocol\nDesigner &\nAnalyzer")
    p.end()
    return img


def render_small(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_RGB888)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.drawImage(0, 0, render_icon(size))
    p.end()
    return img


def main() -> int:
    if QGuiApplication.instance() is None:  # fonts need an application object
        main.app = QGuiApplication(sys.argv)
    resources = ROOT / "src" / "protocol_designer" / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    render_icon(256).save(str(resources / "app.png"))
    write_ico(ROOT / "installer" / "app.ico")
    wiz = ROOT / "installer" / "wizard_images"
    wiz.mkdir(parents=True, exist_ok=True)
    render_wizard(164, 314).save(str(wiz / "wizard.bmp"))
    render_wizard(328, 628).save(str(wiz / "wizard@2x.bmp"))
    render_small(55).save(str(wiz / "wizard_small.bmp"))
    render_small(110).save(str(wiz / "wizard_small@2x.bmp"))
    print("icons written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
