"""Image layer engine – manages top/bottom PCB photos as overlay layers.

Uses only Qt primitives (QPixmap / QImage) – no OpenCV dependency.
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
)


class LayerRole(Enum):
    TOP = auto()
    BOTTOM = auto()


class ImageLayer:
    """Wrapper around a QGraphicsPixmapItem that holds layer metadata."""

    def __init__(self, role: LayerRole, scene: QGraphicsScene) -> None:
        self.role = role
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._item.setZValue(0.0 if role == LayerRole.TOP else -1.0)
        scene.addItem(self._item)

        self._source_pixmap: Optional[QPixmap] = None
        self._source_path: str = ""
        self._mirrored: bool = False
        self._opacity: float = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: str | Path) -> bool:
        """Load an image from *path*. Returns ``True`` on success."""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._source_pixmap = pixmap
        self._source_path = str(path)
        self._apply()
        return True

    def clear(self) -> None:
        """Unload the layer image and reset its graphics item."""
        self._source_pixmap = None
        self._source_path = ""
        self._item.setPixmap(QPixmap())
        self._item.setPos(0, 0)

    def set_opacity(self, value: float) -> None:
        """Set layer opacity (0.0 – fully transparent … 1.0 – fully opaque)."""
        self._opacity = max(0.0, min(1.0, value))
        self._item.setOpacity(self._opacity)

    def set_mirrored(self, mirrored: bool) -> None:
        """Horizontally mirror the image (useful for bottom layer)."""
        self._mirrored = mirrored
        self._apply()

    def set_visible(self, visible: bool) -> None:
        self._item.setVisible(visible)

    def set_offset(self, dx: float, dy: float) -> None:
        """Translate the layer for manual alignment."""
        self._item.setPos(dx, dy)

    def offset(self) -> QPointF:
        return self._item.pos()

    @property
    def is_loaded(self) -> bool:
        return self._source_pixmap is not None

    @property
    def source_path(self) -> str:
        return self._source_path

    @property
    def mirrored(self) -> bool:
        return self._mirrored

    @property
    def opacity(self) -> float:
        return self._opacity

    @property
    def item(self) -> QGraphicsPixmapItem:
        return self._item

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        """Re-render the pixmap with current mirror state."""
        if self._source_pixmap is None:
            return
        pm = self._source_pixmap
        if self._mirrored:
            transform = QTransform()
            transform.scale(-1, 1)
            pm = pm.transformed(transform)
        self._item.setPixmap(pm)


class ImageLayerSignals(QObject):
    """Signals emitted by the image engine."""
    layer_loaded = Signal(str)  # role name


class ImageEngine:
    """Manages the two PCB photo layers and provides overlay controls."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self.signals = ImageLayerSignals()
        self.layers: dict[LayerRole, ImageLayer] = {
            LayerRole.TOP: ImageLayer(LayerRole.TOP, scene),
            LayerRole.BOTTOM: ImageLayer(LayerRole.BOTTOM, scene),
        }

    # ------------------------------------------------------------------
    # Layer access
    # ------------------------------------------------------------------

    def top(self) -> ImageLayer:
        return self.layers[LayerRole.TOP]

    def bottom(self) -> ImageLayer:
        return self.layers[LayerRole.BOTTOM]

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_top(self, path: str | Path) -> bool:
        ok = self.top().load(path)
        if ok:
            self.signals.layer_loaded.emit("TOP")
        return ok

    def load_bottom(self, path: str | Path) -> bool:
        ok = self.bottom().load(path)
        if ok:
            self.signals.layer_loaded.emit("BOTTOM")
        return ok

    def clear(self) -> None:
        """Unload both layers."""
        for layer in self.layers.values():
            layer.clear()

    # ------------------------------------------------------------------
    # Alignment placeholder
    # ------------------------------------------------------------------

    def align_layers(self) -> None:
        """Placeholder – future implementation will auto-align top/bottom.

        Possible approaches:
        * Manual 3-point alignment (user picks matching landmarks).
        * AI-based feature matching (ORB / SIFT via OpenCV, optional).
        """
        pass
