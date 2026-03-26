"""Draggable footprint item with real KiCad geometry rendering.

Parses .kicad_mod pad/line/arc/circle data and renders it as QGraphicsItems
so that footprints appear on the PCB canvas with their real physical geometry.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPainterPath
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsRectItem,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsTextItem,
    QGraphicsSceneMouseEvent,
)

from library_bridge import (
    FootprintData, PadData, LineData, CircleData, ArcData, RectData, PolyData,
)
from color_manager import cm


def _layer_color(layer: str) -> QColor:
    ll = layer.lower()
    if "silk" in ll:
        return cm.silkscreen()
    if "fab" in ll:
        return cm.fab()
    if "crtyd" in ll or "courtyard" in ll:
        return cm.courtyard()
    return cm.silkscreen()


class _Signals(QObject):
    position_changed = Signal(str, float, float)
    selected_changed = Signal(str, bool)
    move_finished = Signal(str, float, float, float, float)
    # emitted when the user clicks a pad in connect-net mode
    pad_clicked = Signal(str, str)        # fp_uid, pad_number  (left-click)
    pad_right_clicked = Signal(str, str)  # fp_uid, pad_number  (right-click)


class PadGraphicsItem(QGraphicsItem):
    """A single pad rendered as a clickable shape.

    When ``connect_mode`` is True on the parent FootprintItem, clicks are
    forwarded as ``pad_clicked`` signals instead of moving the footprint.
    """

    def __init__(self, pad: PadData, parent: "FootprintItem") -> None:
        super().__init__(parent)
        self.pad = pad
        self._fp: "FootprintItem" = parent
        self._net_name: str = ""
        self._hover: bool = False
        self._ppm: float = parent._ppm

        # Pre-compute the bounding rect in local (footprint) coordinates
        pw = pad.width * self._ppm
        ph = pad.height * self._ppm
        px = pad.x * self._ppm
        py = pad.y * self._ppm
        self._rect = QRectF(px - pw / 2, py - ph / 2, pw, ph)

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(10)
        self._pending: bool = False  # highlighted as source in two-click connect

    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None) -> None:  # type: ignore[override]
        pad = self.pad
        pw = pad.width * self._ppm
        ph = pad.height * self._ppm
        px = pad.x * self._ppm
        py = pad.y * self._ppm

        if self._pending:
            color = cm.pad_pending()
        elif self._hover:
            color = cm.pad_hover()
        elif self._net_name:
            color = cm.pad_net()
        elif pad.pad_type == "thru_hole":
            color = cm.pad_tht()
        else:
            color = cm.pad_smd()

        pen = QPen(color.darker(130), 1.5)
        painter.setPen(pen)
        painter.setBrush(QBrush(color))

        if pad.shape == "circle" or (pad.shape == "oval" and abs(pw - ph) < 0.01):
            r = min(pw, ph) / 2
            painter.drawEllipse(QPointF(px, py), r, r)
        elif pad.shape == "oval":
            path = QPainterPath()
            path.addRoundedRect(px - pw / 2, py - ph / 2, pw, ph,
                                min(pw, ph) / 2, min(pw, ph) / 2)
            painter.drawPath(path)
        elif pad.shape == "roundrect":
            rr = pad.roundrect_rratio * min(pw, ph) / 2
            path = QPainterPath()
            path.addRoundedRect(px - pw / 2, py - ph / 2, pw, ph, rr, rr)
            painter.drawPath(path)
        else:
            painter.drawRect(QRectF(px - pw / 2, py - ph / 2, pw, ph))

        # Net name label
        if self._net_name:
            painter.setPen(QPen(cm.text_label()))
            f = painter.font()
            f.setPixelSize(max(int(min(pw, ph) * 0.55), 6))
            painter.setFont(f)
            painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter,
                             self._net_name)

    def set_net(self, net_name: str) -> None:
        self._net_name = net_name
        self.update()

    def set_pending(self, active: bool) -> None:
        self._pending = active
        self.update()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._fp.connect_mode:
            self._hover = True
            self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._fp.connect_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self._fp.signals.pad_clicked.emit(self._fp.uid, self.pad.number)
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self._fp.signals.pad_right_clicked.emit(self._fp.uid, self.pad.number)
                event.accept()
                return
        # Not in connect mode – let the parent group handle dragging
        event.ignore()


class FootprintItem(QGraphicsItemGroup):
    """Visual representation of a real KiCad footprint on the PCB canvas."""

    _counter: int = 0

    def __init__(
        self,
        footprint_data: Optional[FootprintData] = None,
        uid: str = "",
        footprint_lib: str = "",
        footprint_name: str = "",
        reference: str = "REF**",
        value: str = "VAL**",
        symbol_lib: str = "",
        symbol_name: str = "",
        pixels_per_mm: float = 10.0,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)

        if uid:
            self.uid = uid
            try:
                FootprintItem._counter = max(FootprintItem._counter, int(uid.split("_")[1]))
            except (IndexError, ValueError):
                pass
        else:
            FootprintItem._counter += 1
            self.uid = f"FP_{FootprintItem._counter}"

        self.footprint_lib = footprint_lib
        self.footprint_name = footprint_name
        self.reference = reference
        self.value = value
        self.symbol_lib = symbol_lib
        self.symbol_name = symbol_name
        self.rotation_deg: float = 0.0
        self.layer: str = "F.Cu"
        self._ppm = pixels_per_mm
        self._fp_data = footprint_data

        # Net assignments: pad_number -> net_name
        self.pad_nets: dict[str, str] = {}
        # Pin-to-pad mapping: symbol_pin_number -> footprint_pad_number
        self.pin_map: dict[str, str] = {}
        # Connect-net interaction mode
        self.connect_mode: bool = False
        # Map pad_number -> PadGraphicsItem (only when built from real data)
        self._pad_items: dict[str, PadGraphicsItem] = {}

        self.signals = _Signals()
        self._drag_start_pos: Optional[QPointF] = None

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setHandlesChildEvents(False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        if footprint_data:
            self._build_from_data(footprint_data)
        else:
            self._build_placeholder()

        self._label = QGraphicsTextItem(self.reference, self)
        self._label.setDefaultTextColor(cm.text_label())
        font = QFont("Monospace", 7)
        self._label.setFont(font)
        lbl_scale = 1.0 / max(self._ppm * 0.4, 0.01)
        self._label.setScale(lbl_scale)
        self._label.setPos(0, -3 * self._ppm)

    def _s(self, mm: float) -> float:
        return mm * self._ppm

    def _build_from_data(self, data: FootprintData) -> None:
        for line in data.lines:
            pen = QPen(_layer_color(line.layer), max(self._s(line.width), 0.5))
            it = QGraphicsLineItem(self._s(line.x1), self._s(line.y1),
                                   self._s(line.x2), self._s(line.y2))
            it.setPen(pen)
            self.addToGroup(it)

        for rect in data.rects:
            pen = QPen(_layer_color(rect.layer), max(self._s(rect.width), 0.5))
            x = min(self._s(rect.x1), self._s(rect.x2))
            y = min(self._s(rect.y1), self._s(rect.y2))
            w = abs(self._s(rect.x2 - rect.x1))
            h = abs(self._s(rect.y2 - rect.y1))
            it = QGraphicsRectItem(x, y, w, h)
            it.setPen(pen)
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self.addToGroup(it)

        for circ in data.circles:
            pen = QPen(_layer_color(circ.layer), max(self._s(circ.width), 0.5))
            r = self._s(circ.radius)
            it = QGraphicsEllipseItem(self._s(circ.cx) - r, self._s(circ.cy) - r,
                                      2 * r, 2 * r)
            it.setPen(pen)
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self.addToGroup(it)

        for arc in data.arcs:
            self._draw_arc(arc)

        for poly in data.polys:
            if len(poly.points) < 2:
                continue
            color = _layer_color(poly.layer)
            pen = QPen(color, max(self._s(poly.width), 0.5))
            path = QPainterPath()
            path.moveTo(self._s(poly.points[0][0]), self._s(poly.points[0][1]))
            for px, py in poly.points[1:]:
                path.lineTo(self._s(px), self._s(py))
            path.closeSubpath()
            it = QGraphicsPathItem(path)
            it.setPen(pen)
            it.setBrush(QBrush(color.lighter(170)))
            self.addToGroup(it)

        for pad in data.pads:
            self._draw_pad(pad)

    def _draw_pad(self, pad: PadData) -> None:
        # The rich PadGraphicsItem replaces the old static drawing
        pad_item = PadGraphicsItem(pad, self)
        self._pad_items[pad.number] = pad_item
        # Drill hole for THT (drawn purely in paint of PadGraphicsItem,
        # but we still add the static hole circle for visibility)
        if pad.pad_type == "thru_hole" and pad.drill > 0:
            dr = self._s(pad.drill) / 2
            px, py = self._s(pad.x), self._s(pad.y)
            hole = QGraphicsEllipseItem(px - dr, py - dr, 2 * dr, 2 * dr, self)
            hole.setPen(QPen(cm.pad_drill(), 0.5))
            hole.setBrush(QBrush(cm.pad_drill()))
            hole.setZValue(11)

    def _draw_arc(self, arc: ArcData) -> None:
        sx, sy = self._s(arc.start_x), self._s(arc.start_y)
        mx, my = self._s(arc.mid_x), self._s(arc.mid_y)
        ex, ey = self._s(arc.end_x), self._s(arc.end_y)

        D = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))
        if abs(D) < 1e-10:
            it = QGraphicsLineItem(sx, sy, ex, ey)
            it.setPen(QPen(_layer_color(arc.layer), max(self._s(arc.width), 0.5)))
            self.addToGroup(it)
            return

        ux = ((sx**2 + sy**2) * (my - ey) + (mx**2 + my**2) * (ey - sy) +
              (ex**2 + ey**2) * (sy - my)) / D
        uy = ((sx**2 + sy**2) * (ex - mx) + (mx**2 + my**2) * (sx - ex) +
              (ex**2 + ey**2) * (mx - sx)) / D
        r = math.hypot(sx - ux, sy - uy)

        start_a = math.degrees(math.atan2(-(sy - uy), sx - ux))
        mid_a = math.degrees(math.atan2(-(my - uy), mx - ux))
        end_a = math.degrees(math.atan2(-(ey - uy), ex - ux))

        def _norm(a: float) -> float:
            return a % 360

        sa, ma, ea = _norm(start_a), _norm(mid_a), _norm(end_a)

        def _between(s: float, m: float, e: float) -> bool:
            if s <= e:
                return s <= m <= e
            return m >= s or m <= e

        if _between(sa, ma, ea):
            span = ea - sa
            if span <= 0:
                span += 360
        else:
            span = ea - sa
            if span >= 0:
                span -= 360

        path = QPainterPath()
        rect = QRectF(ux - r, uy - r, 2 * r, 2 * r)
        path.arcMoveTo(rect, start_a)
        path.arcTo(rect, start_a, span)
        it = QGraphicsPathItem(path)
        it.setPen(QPen(_layer_color(arc.layer), max(self._s(arc.width), 0.5)))
        self.addToGroup(it)

    def _build_placeholder(self) -> None:
        w, h = self._s(3.0), self._s(2.0)
        rect = QGraphicsRectItem(-w / 2, -h / 2, w, h)
        rect.setPen(QPen(cm.border_normal(), 2))
        rect.setBrush(QBrush(QColor(0, 200, 0, 40)))
        self.addToGroup(rect)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos: QPointF = value
            self.signals.position_changed.emit(self.uid, pos.x(), pos.y())
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.signals.selected_changed.emit(self.uid, bool(value))
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_start_pos = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self._drag_start_pos is not None:
            old_pos = self._drag_start_pos
            new_pos = self.pos()
            if (old_pos - new_pos).manhattanLength() > 0.01:
                self.signals.move_finished.emit(
                    self.uid,
                    old_pos.x(), old_pos.y(),
                    new_pos.x(), new_pos.y(),
                )
        self._drag_start_pos = None

    def set_reference(self, ref: str) -> None:
        self.reference = ref
        self._label.setPlainText(ref)

    def set_value(self, val: str) -> None:
        self.value = val

    def set_rotation(self, degrees: float) -> None:
        self.rotation_deg = degrees % 360
        self.setRotation(self.rotation_deg)

    def center_scene_pos(self) -> QPointF:
        return self.mapToScene(self.boundingRect().center())

    def set_pad_net(self, pad_number: str, net_name: str) -> None:
        """Assign *net_name* to pad *pad_number* and refresh its visual."""
        if net_name:
            self.pad_nets[pad_number] = net_name
        else:
            self.pad_nets.pop(pad_number, None)
        item = self._pad_items.get(pad_number)
        if item:
            item.set_net(net_name)

    def highlight_pad(self, pad_number: str, active: bool) -> None:
        """Mark pad as the pending source in two-click connect mode."""
        item = self._pad_items.get(pad_number)
        if item:
            item.set_pending(active)

    def pad_scene_pos(self, pad_number: str) -> Optional[QPointF]:
        """Scene position of the centre of a pad (for wire drawing)."""
        item = self._pad_items.get(pad_number)
        if item:
            pad = item.pad
            local = QPointF(pad.x * self._ppm, pad.y * self._ppm)
            return self.mapToScene(local)
        return None

    def pad_numbers(self) -> list[str]:
        return list(self._pad_items.keys())

    @property
    def footprint_full_name(self) -> str:
        return f"{self.footprint_lib}:{self.footprint_name}"

    @property
    def symbol_full_name(self) -> str:
        if self.symbol_lib and self.symbol_name:
            return f"{self.symbol_lib}:{self.symbol_name}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        pos = self.pos()
        return {
            "uid": self.uid,
            "footprint_lib": self.footprint_lib,
            "footprint_name": self.footprint_name,
            "symbol_lib": self.symbol_lib,
            "symbol_name": self.symbol_name,
            "reference": self.reference,
            "value": self.value,
            "x_px": pos.x(),
            "y_px": pos.y(),
            "rotation": self.rotation_deg,
            "layer": self.layer,
            "pad_nets": dict(self.pad_nets),
            "pin_map": dict(self.pin_map),
        }
