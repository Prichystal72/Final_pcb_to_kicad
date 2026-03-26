"""Wire and junction graphics items for PCB canvas.

Wires are drawn as white line segments that can cross freely.
Junctions mark explicit connection points between wires.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject, QLineF
from PySide6.QtGui import QBrush, QColor, QPen, QPainterPath, QPainter
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from color_manager import cm

# Visual constants
_WIRE_WIDTH = 2.0
_JUNCTION_RADIUS = 3.0


class _WireSignals(QObject):
    deleted = Signal(object)          # emits the WireSegmentItem
    net_changed = Signal(object)      # emits the WireSegmentItem


class WireSegmentItem(QGraphicsLineItem):
    """A single wire segment on the PCB canvas.

    Drawn in white; becomes orange when selected.  Carries an optional
    ``net_name`` that links it to a KiCad net.
    """

    _counter: int = 0

    def __init__(self, x1: float, y1: float, x2: float, y2: float,
                 net_name: str = "", parent: QGraphicsItem | None = None,
                 uid: str = "") -> None:
        super().__init__(x1, y1, x2, y2, parent)
        if uid:
            self.uid = uid
            try:
                WireSegmentItem._counter = max(WireSegmentItem._counter, int(uid.split("_")[1]))
            except (IndexError, ValueError):
                pass
        else:
            WireSegmentItem._counter += 1
            self.uid = f"W_{WireSegmentItem._counter}"
        self.net_name: str = net_name
        self.signals = _WireSignals()

        self.setPen(QPen(cm.wire(), _WIRE_WIDTH))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setZValue(5)
        self.setAcceptHoverEvents(True)

    # ---- visual feedback ----

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        if self.isSelected():
            painter.setPen(QPen(cm.wire_selected(), _WIRE_WIDTH + 1))
        else:
            painter.setPen(QPen(cm.wire(), _WIRE_WIDTH))
        painter.drawLine(self.line())

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        ln = self.line()
        return {
            "uid": self.uid,
            "x1": ln.x1(),
            "y1": ln.y1(),
            "x2": ln.x2(),
            "y2": ln.y2(),
            "net_name": self.net_name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WireSegmentItem":
        item = cls(
            d["x1"], d["y1"], d["x2"], d["y2"],
            d.get("net_name", ""),
            uid=d.get("uid", ""),
        )
        return item

    # ---- helpers ----

    def start_pt(self) -> QPointF:
        return QPointF(self.line().x1(), self.line().y1())

    def end_pt(self) -> QPointF:
        return QPointF(self.line().x2(), self.line().y2())

    def midpoint(self) -> QPointF:
        ln = self.line()
        return QPointF((ln.x1() + ln.x2()) / 2, (ln.y1() + ln.y2()) / 2)


class JunctionItem(QGraphicsEllipseItem):
    """A junction dot that marks an explicit connection between wires."""

    _counter: int = 0

    def __init__(self, x: float, y: float,
                 uid: str = "",
                 parent: QGraphicsItem | None = None) -> None:
        r = _JUNCTION_RADIUS
        super().__init__(x - r, y - r, 2 * r, 2 * r, parent)
        if uid:
            self.uid = uid
            try:
                JunctionItem._counter = max(JunctionItem._counter, int(uid.split("_")[1]))
            except (IndexError, ValueError):
                pass
        else:
            JunctionItem._counter += 1
            self.uid = f"J_{JunctionItem._counter}"
        self._cx = x
        self._cy = y

        self.setBrush(QBrush(cm.junction()))
        self.setPen(QPen(cm.junction().darker(120), 1))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setZValue(6)

    @property
    def center_x(self) -> float:
        return self._cx

    @property
    def center_y(self) -> float:
        return self._cy

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "x": self._cx,
            "y": self._cy,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JunctionItem":
        return cls(d["x"], d["y"], uid=d.get("uid", ""))


def compute_45_route(anchor: QPointF, target: QPointF,
                     straight_first: bool = True) -> list[QPointF]:
    """Compute a two-segment route from *anchor* to *target* constrained
    to 0° / 45° / 90° angles (like KiCad PCB routing).

    Returns a list of points: [anchor, mid, target] or [anchor, target]
    if they are already aligned.

    *straight_first* = True  → horizontal/vertical first, then 45° diagonal.
    *straight_first* = False → 45° diagonal first, then horizontal/vertical.
    """
    dx = target.x() - anchor.x()
    dy = target.y() - anchor.y()
    adx, ady = abs(dx), abs(dy)
    sx = 1.0 if dx >= 0 else -1.0
    sy = 1.0 if dy >= 0 else -1.0

    # Already on a 0/45/90 line — single segment
    if adx < 0.5 or ady < 0.5 or abs(adx - ady) < 0.5:
        return [anchor, target]

    if straight_first:
        # Straight (H or V) first, then 45° diagonal
        if adx >= ady:
            # Horizontal first for (adx - ady), then diagonal
            straight_len = adx - ady
            mid = QPointF(anchor.x() + sx * straight_len, anchor.y())
        else:
            # Vertical first for (ady - adx), then diagonal
            straight_len = ady - adx
            mid = QPointF(anchor.x(), anchor.y() + sy * straight_len)
    else:
        # 45° diagonal first, then straight (H or V)
        if adx >= ady:
            # Diagonal for ady, then horizontal
            mid = QPointF(anchor.x() + sx * ady, anchor.y() + sy * ady)
        else:
            # Diagonal for adx, then vertical
            mid = QPointF(anchor.x() + sx * adx, anchor.y() + sy * adx)

    return [anchor, mid, target]


class WirePreviewItem(QGraphicsPathItem):
    """Preview line while drawing a wire, constrained to 0°/45°/90° angles.

    Shows a straight+diagonal (or diagonal+straight) routing preview
    from the anchor point to the current cursor position.
    """

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        pen = QPen(cm.wire_preview(), 1.5, Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(100)
        self._anchor = QPointF(0, 0)
        self._straight_first = True

    def set_anchor(self, pt: QPointF) -> None:
        self._anchor = pt

    def update_preview(self, cursor: QPointF) -> None:
        pts = compute_45_route(self._anchor, cursor, self._straight_first)
        path = QPainterPath()
        path.moveTo(pts[0])
        for p in pts[1:]:
            path.lineTo(p)
        self.setPath(path)

    def toggle_direction(self) -> None:
        self._straight_first = not self._straight_first
