"""Image layer engine – manages top/bottom PCB photos as canvas overlay layers.

Each PCB side (top / bottom) is represented by an ``ImageLayer`` instance that
wraps a ``QGraphicsPixmapItem``.  The engine provides:

- Loading from file path or pre-processed QPixmap
- Per-layer opacity, brightness, mirror, scale, and offset controls
- Z-ordering (top layer above bottom layer)

Brightness is applied at display time via QPainter composition modes
(Screen for brightening, Multiply for darkening) – lightweight and
GPU-friendly without requiring an OpenCV dependency.

Uses only Qt primitives (QPixmap / QImage) – no OpenCV dependency.
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
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
        self._scale: float = 1.0
        self._brightness: int = 0  # -100..100 for canvas display

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

    def load_from_pixmap(self, pixmap: QPixmap, path: str = "") -> bool:
        """Load a layer from a pre-processed QPixmap. Returns True on success."""
        if pixmap.isNull():
            return False
        self._source_pixmap = pixmap
        self._source_path = path
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

    def set_brightness(self, value: int) -> None:
        """Set canvas-level brightness adjustment (-100..100). Re-renders pixmap."""
        self._brightness = max(-100, min(100, value))
        self._apply()

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

    def set_scale(self, scale: float) -> None:
        """Set layer scale (1.0 = original size)."""
        self._scale = max(0.05, min(10.0, scale))
        self._item.setScale(self._scale)

    @property
    def scale(self) -> float:
        return self._scale

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
    def brightness(self) -> int:
        return self._brightness

    @property
    def item(self) -> QGraphicsPixmapItem:
        return self._item

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        """Re-render the pixmap with current mirror and brightness state.

        Called whenever mirror or brightness changes.  Brightness is
        implemented purely via QPainter composition modes:
        - Positive brightness: Screen-blend a semi-white overlay to lighten.
        - Negative brightness: Multiply-blend a semi-dark overlay to darken.
        This avoids per-pixel numpy loops and stays GPU-accelerated.
        """
        if self._source_pixmap is None:
            return
        pm = self._source_pixmap

        # Apply horizontal mirror transform (used for viewing the bottom layer)
        if self._mirrored:
            transform = QTransform()
            transform.scale(-1, 1)
            pm = pm.transformed(transform)

        # Apply canvas brightness adjustment via alpha-blend compositing
        if self._brightness != 0:
            img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            painter = QPainter(img)
            if self._brightness > 0:
                # Screen mode: blend with white at proportional alpha to brighten
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
                alpha = int(self._brightness * 2.55)  # 0..255 from 0..100
                painter.fillRect(img.rect(), QColor(alpha, alpha, alpha))
            else:
                # Multiply mode: blend with dark grey to darken
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                v = 255 + int(self._brightness * 2.55)  # 255..0 from 0..-100
                painter.fillRect(img.rect(), QColor(v, v, v))
            painter.end()
            pm = QPixmap.fromImage(img)

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

    def load_top_from_pixmap(self, pixmap: QPixmap, path: str = "") -> bool:
        ok = self.top().load_from_pixmap(pixmap, path)
        if ok:
            self.signals.layer_loaded.emit("TOP")
        return ok

    def load_bottom_from_pixmap(self, pixmap: QPixmap, path: str = "") -> bool:
        ok = self.bottom().load_from_pixmap(pixmap, path)
        if ok:
            self.signals.layer_loaded.emit("BOTTOM")
        return ok

    def clear(self) -> None:
        """Unload both layers."""
        for layer in self.layers.values():
            layer.clear()

    # ------------------------------------------------------------------
    # Auto-alignment (ORB feature matching via OpenCV)
    # ------------------------------------------------------------------

    def align_layers(self, enabled: bool = True) -> bool:
        """Attempt to auto-align the bottom layer to the top layer.

        Uses ORB feature matching + homography.
        Returns True on success, False otherwise.
        Can be disabled by passing ``enabled=False``.
        """
        if not enabled:
            return False
        top_layer = self.top()
        bot_layer = self.bottom()
        if not top_layer.is_loaded or not bot_layer.is_loaded:
            return False

        try:
            import cv2
            import numpy as np

            # Convert pixmaps to grayscale numpy
            def _pm_to_gray(pm: QPixmap) -> "np.ndarray":
                img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                w, h = img.width(), img.height()
                ptr = img.bits()
                arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
                return cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY)

            top_pm = top_layer._source_pixmap
            bot_pm = bot_layer._source_pixmap
            if top_pm is None or bot_pm is None:
                return False
            top_gray = _pm_to_gray(top_pm)
            bot_gray = _pm_to_gray(bot_pm)

            # ORB feature detection
            orb = cv2.ORB_create(nfeatures=2000)
            kp1, des1 = orb.detectAndCompute(top_gray, None)
            kp2, des2 = orb.detectAndCompute(bot_gray, None)

            if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
                return False

            # BFMatcher
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = bf.match(des1, des2)
            matches = sorted(matches, key=lambda m: m.distance)

            if len(matches) < 10:
                return False

            # Use top 60% of matches
            good = matches[:max(10, int(len(matches) * 0.6))]

            src_pts = np.asarray([kp2[m.trainIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)
            dst_pts = np.asarray([kp1[m.queryIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)

            M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            if M is None:
                return False

            # Apply homography to bottom layer
            h_t, w_t = top_gray.shape
            if bot_pm is None:
                return False
            bot_img = bot_pm.toImage().convertToFormat(
                QImage.Format.Format_RGBA8888)
            w_b, h_b = bot_img.width(), bot_img.height()
            ptr = bot_img.bits()
            bot_arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h_b, w_b, 4)).copy()
            aligned = cv2.warpPerspective(bot_arr, M, (w_t, h_t))

            # Convert back to QPixmap
            h_a, w_a, _ = aligned.shape
            qimg = QImage(aligned.data, w_a, h_a, 4 * w_a,
                          QImage.Format.Format_RGBA8888).copy()
            bot_layer._source_pixmap = QPixmap.fromImage(qimg)
            bot_layer._apply()
            return True

        except ImportError:
            return False
        except Exception:
            return False
