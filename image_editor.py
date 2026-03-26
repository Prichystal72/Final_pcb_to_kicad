"""Modal image-editor dialog for preprocessing PCB photos before import.

This module provides the ``ImageEditorDialog`` that opens when the user loads
or re-edits a top/bottom PCB photo.  The processing pipeline (implemented in
``apply_pipeline``) applies the following steps in order:

    0. Barrel / pincushion distortion compensation  (cv2.undistort)
    1. Crop  (normalised rectangle)
    2. Free rotation  (-180 .. +180 degrees)
    3. Scale / resize  (5% .. 500%)
    4. Horizontal / vertical mirror
    5. Brightness & contrast
    6. Gamma correction
    7. Unsharp-mask sharpening
    8. Non-local-means denoising
    9. Greyscale conversion
   10. Colour inversion

The dialog also supports:
- EXIF auto-orientation (mobile photos)
- Interactive crop via rubber-band on the preview
- Live preview (reprocessed on every parameter change)
- Template overlay of the other layer for visual alignment
- Zoom / pan via mouse wheel, right-click drag, and keyboard shortcuts
- Returns the processed QImage **and** a parameter dict so the editor
  state can be saved in the project and reopened for re-editing.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image as PILImage, ImageOps

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPixmap, QTransform, QPen, QColor, QBrush, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QRubberBand,
    QMessageBox,
    QSizePolicy,
)

# ---------------------------------------------------------------------------
# Default parameters (used when the dialog opens fresh)
# ---------------------------------------------------------------------------

DEFAULT_PARAMS: dict[str, Any] = {
    "rotation": 0.0,        # degrees, -180..180
    "mirror_h": False,       # horizontal mirror
    "mirror_v": False,       # vertical mirror
    "distortion": 0,         # -100..100  (neg=barrel, pos=pincushion)
    "brightness": 0,         # -100..100
    "contrast": 100,         # 50..300 (100 = unchanged)
    "gamma": 100,            # 20..500 (100 = 1.0)
    "sharpen": 0,            # 0..100
    "denoise": 0,            # 0..100
    "grayscale": False,
    "invert": False,
    "crop": None,            # (x, y, w, h) normalised 0..1 or None
    "scale": 100,            # output scale in % (100 = original size)
}


# ---------------------------------------------------------------------------
# Numpy ↔ QImage helpers
# ---------------------------------------------------------------------------

def _qimage_to_numpy(qimg: QImage) -> np.ndarray:
    """Convert QImage (any format) → BGR numpy array for OpenCV."""
    qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = qimg.width(), qimg.height()
    ptr = qimg.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)


def _numpy_to_qimage(arr: np.ndarray) -> QImage:
    """Convert BGR/Gray numpy array → QImage."""
    if arr.ndim == 2:
        h, w = arr.shape
        return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    h, w, ch = arr.shape
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _load_with_exif(path: str) -> QImage:
    """Load image with EXIF auto-orientation via Pillow, return QImage."""
    pil = PILImage.open(path)
    pil = ImageOps.exif_transpose(pil)
    if pil.mode == "RGBA":
        pass
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    # PIL → numpy → QImage
    arr = np.array(pil)
    if arr.ndim == 3 and arr.shape[2] == 3:
        h, w, _ = arr.shape
        return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    elif arr.ndim == 3 and arr.shape[2] == 4:
        h, w, _ = arr.shape
        return QImage(arr.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
    # grayscale
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8).copy()


# ---------------------------------------------------------------------------
# Image processing pipeline
# ---------------------------------------------------------------------------

def apply_pipeline(src: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """Apply the full filter pipeline to a BGR numpy array.

    Order: crop → rotate → mirror → brightness/contrast → gamma →
           sharpen → denoise → grayscale → invert.
    """
    img = src.copy()

    # 0. Barrel / pincushion distortion compensation
    distortion = params.get("distortion", 0)
    if distortion != 0:
        h, w = img.shape[:2]
        k1 = distortion / 100.0 * 0.5  # map -100..100 → -0.5..0.5
        cx, cy = w / 2.0, h / 2.0
        cam = np.array([[cx, 0, cx], [0, cy, cy], [0, 0, 1]], dtype=np.float64)
        dist_coeffs = np.array([k1, 0, 0, 0, 0], dtype=np.float64)
        img = cv2.undistort(img, cam, dist_coeffs)

    # 1. Crop (normalised coordinates)
    crop = params.get("crop")
    if crop:
        h, w = img.shape[:2]
        cx, cy, cw, ch_ = crop
        x1 = max(0, int(cx * w))
        y1 = max(0, int(cy * h))
        x2 = min(w, int((cx + cw) * w))
        y2 = min(h, int((cy + ch_) * h))
        if x2 > x1 and y2 > y1:
            img = img[y1:y2, x1:x2].copy()

    # 2. Rotation
    angle = params.get("rotation", 0.0)
    if abs(angle) > 0.01:
        h, w = img.shape[:2]
        center = (w / 2, h / 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        cos_a = abs(M[0, 0])
        sin_a = abs(M[0, 1])
        nw = int(h * sin_a + w * cos_a)
        nh = int(h * cos_a + w * sin_a)
        M[0, 2] += (nw - w) / 2
        M[1, 2] += (nh - h) / 2
        img = cv2.warpAffine(img, M, (nw, nh),
                             borderMode=cv2.BORDER_REPLICATE)

    # 3. Scale / resize
    scale_pct = params.get("scale", 100)
    if scale_pct != 100 and scale_pct > 0:
        factor = scale_pct / 100.0
        h, w = img.shape[:2]
        nw = max(1, int(w * factor))
        nh = max(1, int(h * factor))
        interp = cv2.INTER_AREA if factor < 1.0 else cv2.INTER_CUBIC
        img = cv2.resize(img, (nw, nh), interpolation=interp)

    # 4. Mirror
    if params.get("mirror_h", False):
        img = cv2.flip(img, 1)
    if params.get("mirror_v", False):
        img = cv2.flip(img, 0)

    # 5. Brightness / Contrast
    brightness = params.get("brightness", 0)
    contrast = params.get("contrast", 100)
    if brightness != 0 or contrast != 100:
        alpha = contrast / 100.0
        beta = brightness
        img = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)

    # 6. Gamma
    gamma_val = params.get("gamma", 100) / 100.0
    if abs(gamma_val - 1.0) > 0.01:
        inv_gamma = 1.0 / max(gamma_val, 0.01)
        table = np.array(
            [((i / 255.0) ** inv_gamma) * 255 for i in range(256)],
            dtype=np.uint8,
        )
        img = cv2.LUT(img, table)

    # 7. Sharpen
    sharpen = params.get("sharpen", 0)
    if sharpen > 0:
        amount = sharpen / 100.0 * 2.0  # max 2.0
        blurred = cv2.GaussianBlur(img, (0, 0), 3)
        img = cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)

    # 8. Denoise
    denoise = params.get("denoise", 0)
    if denoise > 0:
        strength = int(denoise / 100.0 * 30)  # max h=30
        if img.ndim == 3:
            img = cv2.fastNlMeansDenoisingColored(img, None, strength, strength, 7, 21)
        else:
            img = cv2.fastNlMeansDenoising(img, None, strength, 7, 21)

    # 9. Grayscale
    if params.get("grayscale", False) and img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    # 10. Invert
    if params.get("invert", False):
        img = cv2.bitwise_not(img)

    return img


# ---------------------------------------------------------------------------
# Preview graphics view with crop rubber-band
# ---------------------------------------------------------------------------

class _CropView(QGraphicsView):
    """QGraphicsView with zoom (mouse-wheel) and optional crop rubber-band."""

    crop_changed = Signal(object)  # emits (x, y, w, h) normalised or None

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(self.renderHints().SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._cropping = False
        self._crop_enabled = False
        self._origin = QPointF()
        self._rect_item: Optional[QGraphicsRectItem] = None
        self._image_rect = QRectF()
        self._zoom_level: int = 0
        self._first_fit_done: bool = False
        self._pan_active: bool = False
        self._pan_start = None

    # -- Zoom ---------------------------------------------------------

    def wheelEvent(self, event) -> None:
        """Mouse-wheel zoom."""
        if event.angleDelta().y() > 0:
            factor = 1.25
            self._zoom_level += 1
        else:
            factor = 1 / 1.25
            self._zoom_level = max(self._zoom_level - 1, 0)
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_F:
            self.zoom_fit()
        elif key == Qt.Key.Key_1:
            self.zoom_100()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.scale(1.3, 1.3)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.scale(1 / 1.3, 1 / 1.3)
        else:
            super().keyPressEvent(event)

    def zoom_fit(self) -> None:
        r = self.scene().itemsBoundingRect()
        if r.isNull():
            return
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 0

    def zoom_100(self) -> None:
        self.resetTransform()
        self._zoom_level = 0

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._first_fit_done:
            self.zoom_fit()
            self._first_fit_done = True

    # -- Crop ---------------------------------------------------------

    def set_crop_mode(self, enabled: bool) -> None:
        self._crop_enabled = enabled
        if enabled:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        if not enabled and self._rect_item:
            self.scene().removeItem(self._rect_item)
            self._rect_item = None
            self.crop_changed.emit(None)

    def set_image_rect(self, rect: QRectF) -> None:
        self._image_rect = rect

    def clear_crop_rect(self) -> None:
        if self._rect_item:
            self.scene().removeItem(self._rect_item)
            self._rect_item = None

    def mousePressEvent(self, event):
        # Right-click or middle-click pan
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_active = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._crop_enabled and event.button() == Qt.MouseButton.LeftButton:
            self._origin = self.mapToScene(event.position().toPoint())
            if self._rect_item:
                self.scene().removeItem(self._rect_item)
            pen = QPen(QColor(255, 0, 0), 2, Qt.PenStyle.DashLine)
            self._rect_item = self.scene().addRect(QRectF(self._origin, self._origin), pen)
            self._cropping = True
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_active:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            hs = self.horizontalScrollBar()
            vs = self.verticalScrollBar()
            hs.setValue(hs.value() - delta.x())
            vs.setValue(vs.value() - delta.y())
            return
        if self._cropping:
            cur = self.mapToScene(event.position().toPoint())
            r = QRectF(self._origin, cur).normalized()
            if self._rect_item:
                self._rect_item.setRect(r)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_active and event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_active = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if self._cropping:
            self._cropping = False
            if not self._rect_item:
                return
            r = self._rect_item.rect()
            ir = self._image_rect
            if ir.width() > 0 and ir.height() > 0 and r.width() > 2 and r.height() > 2:
                nx = (r.x() - ir.x()) / ir.width()
                ny = (r.y() - ir.y()) / ir.height()
                nw = r.width() / ir.width()
                nh = r.height() / ir.height()
                nx = max(0.0, min(1.0, nx))
                ny = max(0.0, min(1.0, ny))
                nw = min(nw, 1.0 - nx)
                nh = min(nh, 1.0 - ny)
                self.crop_changed.emit((nx, ny, nw, nh))
            else:
                self.crop_changed.emit(None)
        else:
            super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Main Dialog
# ---------------------------------------------------------------------------

class ImageEditorDialog(QDialog):
    """Modal image editor for preprocessing a PCB photograph.

    Usage::

        dlg = ImageEditorDialog(image_path, params=existing_params, parent=win)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            processed_qimage = dlg.result_image()
            params = dlg.result_params()
    """

    def __init__(
        self,
        image_path: str,
        params: dict[str, Any] | None = None,
        layer_name: str = "Photo",
        template_qimage: QImage | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Image Editor — {layer_name}")
        self.setMinimumSize(800, 500)
        # Size the dialog to ~90% of parent / screen
        if parent is not None:
            pg = parent.geometry()
            w = int(pg.width() * 0.92)
            h = int(pg.height() * 0.92)
            self.resize(max(w, 900), max(h, 600))
        else:
            self.resize(1100, 750)

        self._image_path = image_path
        self._params = copy.deepcopy(params) if params else copy.deepcopy(DEFAULT_PARAMS)
        self._original_bgr: Optional[np.ndarray] = None
        self._result_qimage: Optional[QImage] = None
        self._template_qimage: Optional[QImage] = template_qimage

        # Load image with EXIF auto-orient
        qimg = _load_with_exif(image_path)
        if qimg.isNull():
            QMessageBox.critical(self, "Error", f"Cannot load: {image_path}")
            return
        self._original_bgr = _qimage_to_numpy(qimg)

        self._build_ui()
        self._apply_params_to_controls()
        self._update_preview()

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def result_image(self) -> Optional[QImage]:
        return self._result_qimage

    def result_params(self) -> dict[str, Any]:
        return copy.deepcopy(self._params)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # ---- Left: controls panel ----
        ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_widget.setFixedWidth(280)

        # Transform group
        grp_transform = QGroupBox("Transform")
        tl = QVBoxLayout(grp_transform)

        # Rotation — free angle via spin box
        tl.addWidget(QLabel("Rotation (°):"))
        self._spin_rotation = QDoubleSpinBox()
        self._spin_rotation.setRange(-180.0, 180.0)
        self._spin_rotation.setSingleStep(0.1)
        self._spin_rotation.setDecimals(1)
        self._spin_rotation.setWrapping(True)
        self._spin_rotation.setValue(self._params.get("rotation", 0.0))
        self._spin_rotation.valueChanged.connect(self._on_rotation_changed)
        tl.addWidget(self._spin_rotation)

        # Quick rotate buttons
        rot_row = QHBoxLayout()
        for angle, label in [(-90, "↺ 90"), (-1, "↺ 1"), (1, "↻ 1"), (90, "↻ 90"), (180, "180°")]:
            btn = QPushButton(label)
            btn.setFixedWidth(42)
            btn.clicked.connect(lambda _, a=angle: self._quick_rotate(a))
            rot_row.addWidget(btn)
        tl.addLayout(rot_row)

        # Distortion (barrel / pincushion)
        tl.addWidget(QLabel("Distortion:"))
        self._sl_distortion = QSlider(Qt.Orientation.Horizontal)
        self._sl_distortion.setRange(-100, 100)
        self._sl_distortion.setValue(self._params.get("distortion", 0))
        self._lbl_distortion = QLabel("0")
        tl.addWidget(self._sl_distortion)
        tl.addWidget(self._lbl_distortion)
        self._sl_distortion.valueChanged.connect(self._on_param_changed)

        # Scale
        tl.addWidget(QLabel("Scale (%):"))
        self._spin_scale = QDoubleSpinBox()
        self._spin_scale.setRange(5.0, 500.0)
        self._spin_scale.setSingleStep(1.0)
        self._spin_scale.setDecimals(1)
        self._spin_scale.setValue(self._params.get("scale", 100))
        self._spin_scale.valueChanged.connect(self._on_param_changed)
        tl.addWidget(self._spin_scale)

        # Mirror
        self._chk_mirror_h = QCheckBox("Mirror Horizontal")
        self._chk_mirror_v = QCheckBox("Mirror Vertical")
        tl.addWidget(self._chk_mirror_h)
        tl.addWidget(self._chk_mirror_v)
        self._chk_mirror_h.toggled.connect(self._on_param_changed)
        self._chk_mirror_v.toggled.connect(self._on_param_changed)

        # Crop
        self._btn_crop = QPushButton("Enable Crop")
        self._btn_crop.setCheckable(True)
        self._btn_crop.toggled.connect(self._on_crop_toggled)
        tl.addWidget(self._btn_crop)
        self._btn_clear_crop = QPushButton("Clear Crop")
        self._btn_clear_crop.clicked.connect(self._on_clear_crop)
        tl.addWidget(self._btn_clear_crop)

        ctrl_layout.addWidget(grp_transform)

        # Filters group
        grp_filters = QGroupBox("Filters")
        fl = QVBoxLayout(grp_filters)

        self._sliders: dict[str, tuple[QSlider, QLabel]] = {}
        slider_defs = [
            ("brightness", "Brightness", -100, 100, 0),
            ("contrast", "Contrast", 50, 300, 100),
            ("gamma", "Gamma", 20, 500, 100),
            ("sharpen", "Sharpen", 0, 100, 0),
            ("denoise", "Denoise", 0, 100, 0),
        ]
        for key, label, lo, hi, default in slider_defs:
            fl.addWidget(QLabel(f"{label}:"))
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(self._params.get(key, default))
            lbl = QLabel(str(sl.value()))
            fl.addWidget(sl)
            fl.addWidget(lbl)
            sl.valueChanged.connect(self._on_param_changed)
            self._sliders[key] = (sl, lbl)

        self._chk_grayscale = QCheckBox("Grayscale")
        self._chk_invert = QCheckBox("Invert")
        fl.addWidget(self._chk_grayscale)
        fl.addWidget(self._chk_invert)
        self._chk_grayscale.toggled.connect(self._on_param_changed)
        self._chk_invert.toggled.connect(self._on_param_changed)

        ctrl_layout.addWidget(grp_filters)

        # Template overlay group (only if template is available)
        self._template_item: Optional[QGraphicsPixmapItem] = None
        if self._template_qimage is not None:
            grp_tpl = QGroupBox("Template Overlay")
            tpl_lay = QVBoxLayout(grp_tpl)
            self._chk_template = QCheckBox("Show other layer")
            self._chk_template.setChecked(False)
            self._chk_template.toggled.connect(self._on_template_toggled)
            tpl_lay.addWidget(self._chk_template)
            tpl_lay.addWidget(QLabel("Template opacity:"))
            self._sl_template_opacity = QSlider(Qt.Orientation.Horizontal)
            self._sl_template_opacity.setRange(5, 80)
            self._sl_template_opacity.setValue(30)
            self._sl_template_opacity.valueChanged.connect(self._on_template_opacity)
            tpl_lay.addWidget(self._sl_template_opacity)
            ctrl_layout.addWidget(grp_tpl)

        # Reset button
        self._btn_reset = QPushButton("Reset All")
        self._btn_reset.clicked.connect(self._on_reset)
        ctrl_layout.addWidget(self._btn_reset)

        ctrl_layout.addStretch()

        # Dialog buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        ctrl_layout.addWidget(btns)

        splitter.addWidget(ctrl_widget)

        # ---- Right: preview + zoom toolbar ----
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Zoom toolbar
        zoom_bar = QHBoxLayout()
        btn_fit = QPushButton("Fit")
        btn_fit.setFixedWidth(50)
        btn_fit.clicked.connect(lambda: self._preview_view.zoom_fit())
        btn_zin = QPushButton("+")
        btn_zin.setFixedWidth(30)
        btn_zin.clicked.connect(lambda: (self._preview_view.scale(1.3, 1.3),))
        btn_zout = QPushButton("−")
        btn_zout.setFixedWidth(30)
        btn_zout.clicked.connect(lambda: (self._preview_view.scale(1/1.3, 1/1.3),))
        btn_100 = QPushButton("1:1")
        btn_100.setFixedWidth(40)
        btn_100.clicked.connect(lambda: self._preview_view.zoom_100())
        zoom_bar.addWidget(btn_fit)
        zoom_bar.addWidget(btn_zout)
        zoom_bar.addWidget(btn_zin)
        zoom_bar.addWidget(btn_100)
        zoom_bar.addStretch()
        right_layout.addLayout(zoom_bar)

        self._preview_scene = QGraphicsScene()
        self._preview_view = _CropView(self._preview_scene, self)
        self._preview_view.crop_changed.connect(self._on_crop_selected)
        self._preview_pixmap_item = QGraphicsPixmapItem()
        self._preview_scene.addItem(self._preview_pixmap_item)

        # Template overlay item (on top of the edited image for alignment reference)
        if self._template_qimage is not None:
            self._template_item = QGraphicsPixmapItem()
            self._template_item.setPixmap(QPixmap.fromImage(self._template_qimage))
            self._template_item.setOpacity(0.3)
            self._template_item.setVisible(False)
            self._template_item.setZValue(1)
            self._preview_scene.addItem(self._template_item)

        right_layout.addWidget(self._preview_view)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ------------------------------------------------------------------
    # Controls → params sync
    # ------------------------------------------------------------------

    def _read_params_from_controls(self) -> dict[str, Any]:
        p = copy.deepcopy(self._params)
        p["rotation"] = self._spin_rotation.value()
        p["distortion"] = self._sl_distortion.value()
        p["scale"] = self._spin_scale.value()
        p["mirror_h"] = self._chk_mirror_h.isChecked()
        p["mirror_v"] = self._chk_mirror_v.isChecked()
        for key, (sl, _) in self._sliders.items():
            p[key] = sl.value()
        p["grayscale"] = self._chk_grayscale.isChecked()
        p["invert"] = self._chk_invert.isChecked()
        # crop is set separately by the rubber-band
        return p

    def _apply_params_to_controls(self) -> None:
        self._spin_rotation.blockSignals(True)
        self._spin_rotation.setValue(self._params.get("rotation", 0.0))
        self._spin_rotation.blockSignals(False)
        self._sl_distortion.blockSignals(True)
        self._sl_distortion.setValue(self._params.get("distortion", 0))
        self._sl_distortion.blockSignals(False)
        self._lbl_distortion.setText(str(self._params.get("distortion", 0)))
        self._spin_scale.blockSignals(True)
        self._spin_scale.setValue(self._params.get("scale", 100))
        self._spin_scale.blockSignals(False)
        self._chk_mirror_h.blockSignals(True)
        self._chk_mirror_h.setChecked(self._params.get("mirror_h", False))
        self._chk_mirror_h.blockSignals(False)
        self._chk_mirror_v.blockSignals(True)
        self._chk_mirror_v.setChecked(self._params.get("mirror_v", False))
        self._chk_mirror_v.blockSignals(False)
        for key, (sl, lbl) in self._sliders.items():
            sl.blockSignals(True)
            sl.setValue(self._params.get(key, sl.value()))
            sl.blockSignals(False)
            lbl.setText(str(sl.value()))
        self._chk_grayscale.blockSignals(True)
        self._chk_grayscale.setChecked(self._params.get("grayscale", False))
        self._chk_grayscale.blockSignals(False)
        self._chk_invert.blockSignals(True)
        self._chk_invert.setChecked(self._params.get("invert", False))
        self._chk_invert.blockSignals(False)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_rotation_changed(self, val: float) -> None:
        self._on_param_changed()

    def _quick_rotate(self, angle: int) -> None:
        cur = self._spin_rotation.value()
        new = cur + angle
        while new > 180:
            new -= 360
        while new < -180:
            new += 360
        self._spin_rotation.setValue(new)

    def _on_crop_toggled(self, checked: bool) -> None:
        self._preview_view.set_crop_mode(checked)
        self._btn_crop.setText("Crop Mode ON" if checked else "Enable Crop")

    def _on_clear_crop(self) -> None:
        self._params["crop"] = None
        self._preview_view.clear_crop_rect()
        self._btn_crop.setChecked(False)
        self._update_preview()

    def _on_crop_selected(self, crop_rect) -> None:
        self._params["crop"] = crop_rect  # (nx, ny, nw, nh) or None
        self._update_preview()

    def _on_param_changed(self) -> None:
        self._params = self._read_params_from_controls()
        # Update slider labels
        for key, (sl, lbl) in self._sliders.items():
            lbl.setText(str(sl.value()))
        self._lbl_distortion.setText(str(self._sl_distortion.value()))
        self._update_preview()

    def _on_reset(self) -> None:
        self._params = copy.deepcopy(DEFAULT_PARAMS)
        self._apply_params_to_controls()
        self._preview_view.clear_crop_rect()
        self._btn_crop.setChecked(False)
        self._update_preview()

    def _on_accept(self) -> None:
        self._params = self._read_params_from_controls()
        if self._original_bgr is not None:
            processed = apply_pipeline(self._original_bgr, self._params)
            self._result_qimage = _numpy_to_qimage(processed)
        self.accept()

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _update_preview(self) -> None:
        if self._original_bgr is None:
            return
        preview_params = copy.deepcopy(self._params)
        processed = apply_pipeline(self._original_bgr, preview_params)
        qimg = _numpy_to_qimage(processed)
        pm = QPixmap.fromImage(qimg)
        self._preview_pixmap_item.setPixmap(pm)
        self._preview_view.set_image_rect(
            QRectF(0, 0, pm.width(), pm.height())
        )
        # Only auto-fit on first preview; afterwards the user controls zoom
        if not self._preview_view._first_fit_done:
            self._preview_view.zoom_fit()
            self._preview_view._first_fit_done = True

    # ------------------------------------------------------------------
    # Template overlay
    # ------------------------------------------------------------------

    def _on_template_toggled(self, checked: bool) -> None:
        if self._template_item is not None:
            self._template_item.setVisible(checked)

    def _on_template_opacity(self, val: int) -> None:
        if self._template_item is not None:
            self._template_item.setOpacity(val / 100.0)
