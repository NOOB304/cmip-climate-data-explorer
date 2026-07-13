from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from shapely import from_wkb
from shapely.geometry import MultiPolygon, Polygon


class EmptyPage(QWidget):
    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet("color: #617178; max-width: 680px;")
        layout.addWidget(heading)
        layout.addSpacing(8)
        layout.addWidget(body)
        layout.addStretch()


class RegionPreview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._wkb_hex: str | None = None
        self.setMinimumHeight(320)
        self.setAutoFillBackground(True)

    def set_geometry(self, wkb_hex: str | None) -> None:
        self._wkb_hex = wkb_hex
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#eef3f4"))
        painter.setPen(QPen(QColor("#d5dee1"), 1))
        for index in range(1, 6):
            x = self.width() * index / 6
            y = self.height() * index / 6
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        if not self._wkb_hex:
            painter.setPen(QColor("#66777d"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "尚未导入研究区")
            return
        geometry = from_wkb(bytes.fromhex(self._wkb_hex))
        west, south, east, north = geometry.bounds
        if east == west or north == south:
            return
        margin = 24.0
        drawing = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        def point(x: float, y: float) -> QPointF:
            px = drawing.left() + (x - west) / (east - west) * drawing.width()
            py = drawing.bottom() - (y - south) / (north - south) * drawing.height()
            return QPointF(px, py)

        path = QPainterPath()
        polygons = geometry.geoms if isinstance(geometry, MultiPolygon) else (geometry,)
        for polygon in polygons:
            if not isinstance(polygon, Polygon):
                continue
            coordinates = list(polygon.exterior.coords)
            if not coordinates:
                continue
            path.moveTo(point(*coordinates[0][:2]))
            for coordinate in coordinates[1:]:
                path.lineTo(point(*coordinate[:2]))
            path.closeSubpath()
        painter.setPen(QPen(QColor("#236b70"), 2))
        painter.setBrush(QColor(52, 124, 128, 70))
        painter.drawPath(path)
