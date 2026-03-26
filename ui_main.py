"""Main window – professional KiCad-like interface.

Structure
---------
* **Menu bar** – File, Edit, View, Place, Tools, Help.
* **Toolbar** with primary quick-access actions.
* **Left dock** – Library browser (footprint tree + search).
* **Centre** – PCB canvas (QGraphicsView with photo overlays, footprints, and wires).
* **Right dock** – Properties panel (selected component) + Component list + Layers.
* **Status bar** – library counts, hints.

Dialogs
-------
* PathSettingsDialog – configure KiCad library paths.
* SymbolBrowserDialog – browse and pick a symbol to link.
"""

from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QPointF, Signal, QTimer, QThread, QObject
from PySide6.QtGui import (QAction, QKeySequence, QWheelEvent, QIcon, QPen,
                            QColor, QBrush, QFont, QUndoCommand, QUndoStack,
                            QPainter, QPainterPath)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QGraphicsLineItem,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from coordinate_system import CoordinateSystem
from footprint_item import FootprintItem
from image_engine import ImageEngine
from kicad_project import KiCadProjectManager
from library_bridge import LibraryBridge
from project_manager import save_project, load_project
from wire_item import WireSegmentItem, JunctionItem, WirePreviewItem, compute_45_route
from color_manager import cm


# ====================================================================
# Zoomable graphics view
# ====================================================================

class PcbGraphicsView(QGraphicsView):
    zoom_changed = Signal(float)
    # Emitted when user clicks on canvas in wire-draw mode
    wire_click = Signal(QPointF)
    wire_move = Signal(QPointF)
    wire_double_click = Signal(QPointF)
    canvas_right_click = Signal(QPointF, QPointF)  # scene_pos, screen_pos

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(
            self.renderHints()
            | self.renderHints().__class__.Antialiasing
            | self.renderHints().__class__.SmoothPixmapTransform
        )
        # RubberBandDrag: left-drag on empty area = rubber-band selection
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._zoom: float = 1.0
        self._wire_draw_mode: bool = False
        self._pan_active: bool = False
        self._pan_start = None

    def set_wire_draw_mode(self, active: bool) -> None:
        self._wire_draw_mode = active
        if active:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setMouseTracking(True)
            self.viewport().setMouseTracking(True)
        else:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setMouseTracking(False)
            self.viewport().setMouseTracking(False)

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom *= factor
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)

    def mousePressEvent(self, event) -> None:
        if self._wire_draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self.wire_click.emit(self.mapToScene(event.pos()))
            event.accept()
            return
        # Middle-button pan (works in all modes)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_active = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            scene_pos = self.mapToScene(event.pos())
            self.canvas_right_click.emit(scene_pos, event.globalPos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_active:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            hs = self.horizontalScrollBar()
            vs = self.verticalScrollBar()
            hs.setValue(hs.value() - delta.x())
            vs.setValue(vs.value() - delta.y())
            return
        if self._wire_draw_mode:
            self.wire_move.emit(self.mapToScene(event.pos()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_active and event.button() == Qt.MouseButton.MiddleButton:
            self._pan_active = False
            if self._wire_draw_mode:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._wire_draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self.wire_double_click.emit(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


# ====================================================================
# Library browser (left dock)
# ====================================================================

class LibraryBrowserWidget(QWidget):
    """Tree-based browser for footprint and symbol libraries."""

    footprint_activated = Signal(str)  # full_name to place
    symbol_activated = Signal(str)     # full_name to link

    def __init__(self, library: LibraryBridge, parent=None) -> None:
        super().__init__(parent)
        self._lib = library

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Mode tabs
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # -- Footprint tab --
        fp_w = QWidget()
        fp_lay = QVBoxLayout(fp_w)
        fp_lay.setContentsMargins(2, 2, 2, 2)
        self._fp_filter = QComboBox()
        self._fp_filter.addItem("All libraries")
        self._fp_filter.currentIndexChanged.connect(self._on_fp_filter_changed)
        fp_lay.addWidget(self._fp_filter)
        self._fp_search = QLineEdit()
        self._fp_search.setPlaceholderText("Search footprints…")
        self._fp_search.textChanged.connect(self._filter_fp_tree)
        fp_lay.addWidget(self._fp_search)
        self._fp_tree = QTreeWidget()
        self._fp_tree.setHeaderLabels(["Footprint Libraries"])
        self._fp_tree.itemDoubleClicked.connect(self._on_fp_double_click)
        fp_lay.addWidget(self._fp_tree)
        self._btn_place_fp = QPushButton("Place on PCB")
        self._btn_place_fp.clicked.connect(self._on_place_fp)
        fp_lay.addWidget(self._btn_place_fp)
        self._tabs.addTab(fp_w, "Footprints")

        # -- Symbol tab --
        sym_w = QWidget()
        sym_lay = QVBoxLayout(sym_w)
        sym_lay.setContentsMargins(2, 2, 2, 2)
        self._sym_filter = QComboBox()
        self._sym_filter.addItem("All libraries")
        self._sym_filter.currentIndexChanged.connect(self._on_sym_filter_changed)
        sym_lay.addWidget(self._sym_filter)
        self._sym_search = QLineEdit()
        self._sym_search.setPlaceholderText("Search symbols…")
        self._sym_search.textChanged.connect(self._filter_sym_tree)
        sym_lay.addWidget(self._sym_search)
        self._sym_tree = QTreeWidget()
        self._sym_tree.setHeaderLabels(["Symbol Libraries"])
        self._sym_tree.itemDoubleClicked.connect(self._on_sym_double_click)
        sym_lay.addWidget(self._sym_tree)
        self._tabs.addTab(sym_w, "Symbols")

    # ---- Population ----

    def populate(self) -> None:
        self._populate_fp_tree()
        self._populate_sym_tree()
        self._update_filter_combos()

    @staticmethod
    def _detect_prefixes(lib_names: list[str]) -> list[str]:
        """Extract unique library prefixes (text before first '_') from names."""
        prefixes: dict[str, int] = {}
        for name in lib_names:
            if "_" in name:
                prefix = name.split("_")[0] + "_"
            else:
                prefix = name
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
        # Return prefixes that group at least 2 libs, sorted
        return sorted(p for p, cnt in prefixes.items() if cnt >= 2)

    def _update_filter_combos(self) -> None:
        """Populate filter combo boxes based on discovered library prefixes."""
        fp_libs = list(self._lib.all_footprint_libraries())
        sym_libs = list(self._lib.all_symbol_libraries())

        for combo, libs in [(self._fp_filter, fp_libs),
                            (self._sym_filter, sym_libs)]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("All libraries")
            for prefix in self._detect_prefixes(libs):
                combo.addItem(f"{prefix}*")
            combo.blockSignals(False)

    def _populate_fp_tree(self) -> None:
        self._fp_tree.clear()
        for lib in self._lib.all_footprint_libraries():
            parent = QTreeWidgetItem([lib])
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            for fp in self._lib.footprints_in(lib):
                child = QTreeWidgetItem([fp.name])
                child.setData(0, Qt.ItemDataRole.UserRole, fp.full_name)
                parent.addChild(child)
            self._fp_tree.addTopLevelItem(parent)

    def _populate_sym_tree(self) -> None:
        self._sym_tree.clear()
        for lib in self._lib.all_symbol_libraries():
            parent = QTreeWidgetItem([lib])
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            for sym in self._lib.symbols_in(lib):
                child = QTreeWidgetItem([sym.name])
                child.setData(0, Qt.ItemDataRole.UserRole, sym.full_name)
                parent.addChild(child)
            self._sym_tree.addTopLevelItem(parent)

    # ---- Filtering ----

    def _on_fp_filter_changed(self) -> None:
        self._apply_lib_filter(self._fp_tree, self._fp_filter.currentText())
        self._filter_fp_tree(self._fp_search.text())

    def _on_sym_filter_changed(self) -> None:
        self._apply_lib_filter(self._sym_tree, self._sym_filter.currentText())
        self._filter_sym_tree(self._sym_search.text())

    @staticmethod
    def _apply_lib_filter(tree: QTreeWidget, filter_text: str) -> None:
        """Show/hide top-level library items based on prefix filter."""
        show_all = filter_text == "All libraries"
        prefix = filter_text.rstrip("*") if not show_all else ""
        for i in range(tree.topLevelItemCount()):
            lib_item = tree.topLevelItem(i)
            if lib_item is None:
                continue
            if show_all:
                lib_item.setHidden(False)
                for j in range(lib_item.childCount()):
                    child = lib_item.child(j)
                    if child:
                        child.setHidden(False)
            else:
                matches = lib_item.text(0).startswith(prefix)
                lib_item.setHidden(not matches)
                if matches:
                    for j in range(lib_item.childCount()):
                        child = lib_item.child(j)
                        if child:
                            child.setHidden(False)

    def _filter_fp_tree(self, text: str) -> None:
        self._filter_tree(self._fp_tree, text, self._fp_filter.currentText())

    def _filter_sym_tree(self, text: str) -> None:
        self._filter_tree(self._sym_tree, text, self._sym_filter.currentText())

    @staticmethod
    def _filter_tree(tree: QTreeWidget, text: str, lib_filter: str = "All libraries") -> None:
        text = text.strip().lower()
        show_all_libs = lib_filter == "All libraries"
        prefix = lib_filter.rstrip("*") if not show_all_libs else ""
        for i in range(tree.topLevelItemCount()):
            lib_item = tree.topLevelItem(i)
            if lib_item is None:
                continue
            # Check library prefix filter first
            if not show_all_libs and not lib_item.text(0).startswith(prefix):
                lib_item.setHidden(True)
                continue
            any_visible = False
            for j in range(lib_item.childCount()):
                child = lib_item.child(j)
                if child is None:
                    continue
                match = not text or text in child.text(0).lower()
                child.setHidden(not match)
                if match:
                    any_visible = True
            lib_item.setHidden(not any_visible)
            if any_visible and text:
                lib_item.setExpanded(True)

    # ---- Signals ----

    def _selected_fp_name(self) -> Optional[str]:
        items = self._fp_tree.selectedItems()
        if items:
            return items[0].data(0, Qt.ItemDataRole.UserRole)
        return None

    def _on_fp_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name:
            self.footprint_activated.emit(name)

    def _on_place_fp(self) -> None:
        name = self._selected_fp_name()
        if name:
            self.footprint_activated.emit(name)

    def _on_sym_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name:
            self.symbol_activated.emit(name)


# ====================================================================
# Properties panel (right dock)
# ====================================================================

class PropertiesPanel(QWidget):
    """Edit properties of the selected component."""

    property_changed = Signal(dict, dict)
    link_symbol_requested = Signal()
    pin_pad_mapping_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current: Optional[FootprintItem] = None
        self._updating_ui = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        grp = QGroupBox("Component Properties")
        form = QFormLayout(grp)

        self._ref = QLineEdit()
        self._ref.editingFinished.connect(self._apply)
        form.addRow("Reference:", self._ref)

        self._val = QLineEdit()
        self._val.editingFinished.connect(self._apply)
        form.addRow("Value:", self._val)

        self._fp_lbl = QLabel("—")
        self._fp_lbl.setWordWrap(True)
        form.addRow("Footprint:", self._fp_lbl)

        self._sym_lbl = QLabel("—")
        self._sym_lbl.setWordWrap(True)
        form.addRow("Symbol:", self._sym_lbl)

        self._btn_link = QPushButton("Link Symbol…")
        self._btn_link.clicked.connect(self.link_symbol_requested.emit)
        form.addRow("", self._btn_link)

        self._btn_pinmap = QPushButton("Pin ↔ Pad Mapping…")
        self._btn_pinmap.clicked.connect(self.pin_pad_mapping_requested.emit)
        form.addRow("", self._btn_pinmap)

        self._layer = QComboBox()
        self._layer.addItems(["F.Cu", "B.Cu"])
        self._layer.currentTextChanged.connect(self._apply)
        form.addRow("Layer:", self._layer)

        self._rot = QDoubleSpinBox()
        self._rot.setRange(0, 359.99)
        self._rot.setSuffix("°")
        self._rot.setDecimals(2)
        self._rot.editingFinished.connect(self._apply)
        form.addRow("Rotation:", self._rot)

        layout.addWidget(grp)

        # Image controls
        img_grp = QGroupBox("Image Layers")
        img_lay = QVBoxLayout(img_grp)

        self._chk_top = QCheckBox("Top layer visible")
        self._chk_top.setChecked(True)
        img_lay.addWidget(self._chk_top)

        self._chk_bot = QCheckBox("Bottom layer visible")
        self._chk_bot.setChecked(True)
        img_lay.addWidget(self._chk_bot)

        img_lay.addWidget(QLabel("Bottom opacity:"))
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(0, 100)
        self._opacity.setValue(50)
        img_lay.addWidget(self._opacity)

        self._chk_mirror = QCheckBox("Mirror bottom")
        img_lay.addWidget(self._chk_mirror)

        layout.addWidget(img_grp)
        layout.addStretch()

    # Public helpers exposed for MainWindow
    @property
    def chk_top(self) -> QCheckBox:
        return self._chk_top

    @property
    def chk_bot(self) -> QCheckBox:
        return self._chk_bot

    @property
    def opacity_slider(self) -> QSlider:
        return self._opacity

    @property
    def chk_mirror(self) -> QCheckBox:
        return self._chk_mirror

    def set_component(self, fp: Optional[FootprintItem]) -> None:
        self._updating_ui = True
        self._current = fp
        enabled = fp is not None
        self._ref.setEnabled(enabled)
        self._val.setEnabled(enabled)
        self._layer.setEnabled(enabled)
        self._rot.setEnabled(enabled)
        self._btn_link.setEnabled(enabled)
        self._btn_pinmap.setEnabled(
            enabled and bool(fp and (fp.symbol_full_name or fp.footprint_full_name)))

        if fp:
            self._ref.setText(fp.reference)
            self._val.setText(fp.value)
            self._fp_lbl.setText(fp.footprint_full_name)
            self._sym_lbl.setText(fp.symbol_full_name or "— not linked —")
            self._layer.setCurrentText(fp.layer)
            self._rot.setValue(fp.rotation_deg)
        else:
            self._ref.clear()
            self._val.clear()
            self._fp_lbl.setText("—")
            self._sym_lbl.setText("—")
            self._rot.setValue(0)
        self._updating_ui = False

    def _apply(self) -> None:
        fp = self._current
        if not fp or self._updating_ui:
            return
        before = fp.to_dict()
        fp.set_reference(self._ref.text())
        fp.set_value(self._val.text())
        fp.layer = self._layer.currentText()
        fp.set_rotation(self._rot.value())
        after = fp.to_dict()
        if before != after:
            self.property_changed.emit(before, after)


# ====================================================================
# Component list widget
# ====================================================================

class ComponentListWidget(QWidget):
    component_selected = Signal(str)   # uid
    delete_requested = Signal(str)     # uid

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>Placed Components</b>"))
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_select)
        layout.addWidget(self._list)
        self._btn_del = QPushButton("Remove Selected")
        self._btn_del.clicked.connect(self._on_delete)
        layout.addWidget(self._btn_del)

    def refresh(self, items: list[FootprintItem]) -> None:
        self._list.clear()
        for fp in items:
            sym = f" ↔ {fp.symbol_full_name}" if fp.symbol_full_name else ""
            text = f"{fp.reference}  ({fp.footprint_name}){sym}"
            li = QListWidgetItem(text)
            li.setData(Qt.ItemDataRole.UserRole, fp.uid)
            self._list.addItem(li)

    def _on_select(self, current: QListWidgetItem | None, _prev) -> None:
        if current:
            self.component_selected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _on_delete(self) -> None:
        cur = self._list.currentItem()
        if cur:
            self.delete_requested.emit(cur.data(Qt.ItemDataRole.UserRole))


# ====================================================================
# Path settings dialog
# ====================================================================

class PathSettingsDialog(QDialog):
    def __init__(self, library: LibraryBridge, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KiCad Library Paths")
        self.resize(600, 400)
        self._lib = library

        layout = QVBoxLayout(self)

        # KiCad base
        base = library.kicad_base
        layout.addWidget(QLabel(f"<b>KiCad 9 detected:</b> {base or 'NOT FOUND'}"))

        # Footprint paths
        layout.addWidget(QLabel("<b>Footprint library paths:</b>"))
        self._fp_edit = QTextEdit()
        self._fp_edit.setPlainText("\n".join(str(p) for p in library.footprint_paths))
        layout.addWidget(self._fp_edit)
        btn_fp = QPushButton("Add Footprint Path…")
        btn_fp.clicked.connect(lambda: self._add_path(self._fp_edit))
        layout.addWidget(btn_fp)

        # Symbol paths
        layout.addWidget(QLabel("<b>Symbol library paths:</b>"))
        self._sym_edit = QTextEdit()
        self._sym_edit.setPlainText("\n".join(str(p) for p in library.symbol_paths))
        layout.addWidget(self._sym_edit)
        btn_sym = QPushButton("Add Symbol Path…")
        btn_sym.clicked.connect(lambda: self._add_path(self._sym_edit))
        layout.addWidget(btn_sym)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _add_path(self, edit: QTextEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Library Directory")
        if d:
            cur = edit.toPlainText().strip()
            edit.setPlainText(f"{cur}\n{d}" if cur else d)

    def footprint_paths(self) -> list[str]:
        return [l.strip() for l in self._fp_edit.toPlainText().splitlines() if l.strip()]

    def symbol_paths(self) -> list[str]:
        return [l.strip() for l in self._sym_edit.toPlainText().splitlines() if l.strip()]


# ====================================================================
# Symbol browser dialog
# ====================================================================

class SymbolBrowserDialog(QDialog):
    def __init__(self, library: LibraryBridge, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Link Symbol")
        self.resize(500, 500)
        self._lib = library
        self._selected: Optional[str] = None

        layout = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search symbols…")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Symbol Libraries"])
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._confirm)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._populate()

    def _populate(self) -> None:
        for lib in self._lib.all_symbol_libraries():
            parent = QTreeWidgetItem([lib])
            for sym in self._lib.symbols_in(lib):
                child = QTreeWidgetItem([sym.name])
                child.setData(0, Qt.ItemDataRole.UserRole, sym.full_name)
                parent.addChild(child)
            self._tree.addTopLevelItem(parent)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            lib_item = self._tree.topLevelItem(i)
            if lib_item is None:
                continue
            any_vis = False
            for j in range(lib_item.childCount()):
                child = lib_item.child(j)
                if child is None:
                    continue
                match = not text or text in child.text(0).lower()
                child.setHidden(not match)
                if match:
                    any_vis = True
            lib_item.setHidden(not any_vis)
            if any_vis and text:
                lib_item.setExpanded(True)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name:
            self._selected = name
            self.accept()

    def _confirm(self) -> None:
        items = self._tree.selectedItems()
        if items:
            name = items[0].data(0, Qt.ItemDataRole.UserRole)
            if name:
                self._selected = name
        self.accept()

    def selected_symbol(self) -> Optional[str]:
        return self._selected


# ====================================================================
# Pin-to-Pad mapping dialog  (visual)
# ====================================================================

_ELEC_TYPE_ABBR: dict[str, str] = {
    "input": "IN", "output": "OUT", "bidirectional": "BI",
    "passive": "PAS", "power_in": "PWR", "power_out": "PWR",
    "tri_state": "TRI", "open_collector": "OC", "open_emitter": "OE",
    "unspecified": "?", "no_connect": "NC", "free": "FREE",
}

# Colours for the preview scenes
_CLR_SYM_BODY  = QColor(0, 100, 180)
_CLR_SYM_PIN   = QColor(0, 160, 0)
_CLR_SYM_LABEL = QColor(220, 60, 20)
_CLR_FP_SILK   = QColor(200, 200, 60)
_CLR_FP_PAD    = QColor(255, 50, 50)
_CLR_FP_LABEL  = QColor(255, 255, 255)
_CLR_BG_DARK   = QColor(30, 30, 30)


_CLR_HIGHLIGHT = QColor(0, 255, 255)


class _PreviewView(QGraphicsView):
    """Zoomable preview with deep zoom support for large pin-count parts."""

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(_CLR_BG_DARK))
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._zoom_level = 0  # track zoom depth

    def fit(self) -> None:
        r = self.scene().itemsBoundingRect().adjusted(-5, -5, 5, 5)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._zoom_level == 0:
            self.fit()

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            factor = 1.30
            self._zoom_level += 1
        else:
            factor = 1 / 1.30
            self._zoom_level = max(self._zoom_level - 1, 0)
        self.scale(factor, factor)


def _build_symbol_scene(sym):
    """Draw a schematic symbol from SymbolData into a QGraphicsScene.

    Returns (scene, pin_items_dict) where pin_items_dict maps
    pin_number -> list of QGraphicsItem for highlighting.

    Multi-unit symbols (e.g. 74HC00 with separate gate units) are laid
    out vertically (top to bottom) so all pins are visible without overlap.
    """
    sc = QGraphicsScene()
    pin_items: dict[str, list] = {}
    if sym is None:
        txt = sc.addSimpleText("No symbol data")
        txt.setBrush(QBrush(QColor(160, 160, 160)))
        return sc, pin_items

    pen_body = QPen(_CLR_SYM_BODY, 0.15)
    pen_pin = QPen(_CLR_SYM_PIN, 0.1)

    S = 1.0

    # ── Detect multi-unit symbol ──
    pin_units = {pin.unit for pin in sym.pins if pin.unit > 0}
    is_multi = len(pin_units) > 1

    # Compute per-unit VERTICAL offsets for multi-unit symbols
    unit_offsets: dict[int, float] = {}  # unit -> y offset (in scene coords, Y-down)
    if is_multi:
        sorted_units = sorted(pin_units)
        shared_rects = [r for r in sym.rectangles if r.unit == 0]
        shared_polys = [p for p in sym.polylines if p.unit == 0]
        shared_circs = [c for c in sym.circles if c.unit == 0]
        gap = 4.0
        current_y = 0.0
        for u in sorted_units:
            ys: list[float] = []
            for r in shared_rects + [r for r in sym.rectangles if r.unit == u]:
                ys.extend([-r.y1, -r.y2])  # scene Y is flipped
            for pl in shared_polys + [p for p in sym.polylines if p.unit == u]:
                for _px, py in pl.points:
                    ys.append(-py)
            for c in shared_circs + [c for c in sym.circles if c.unit == u]:
                ys.extend([-(c.cy + c.radius), -(c.cy - c.radius)])
            for pin in sym.pins:
                if pin.unit == u:
                    ys.append(-pin.y)
                    rad_a = math.radians(pin.direction)
                    ys.append(-pin.y - pin.length * math.sin(rad_a))
            if ys:
                unit_offsets[u] = current_y - min(ys)
                current_y += (max(ys) - min(ys)) + gap
            else:
                unit_offsets[u] = current_y

    def _oy(unit: int) -> float:
        """Y offset in scene coordinates for a unit."""
        return unit_offsets.get(unit, 0.0)

    # ── Draw body graphics ──
    if is_multi:
        sorted_units = sorted(pin_units)
        for u in sorted_units:
            oy = _oy(u)
            for r in sym.rectangles:
                if r.unit in (0, u):
                    sc.addRect(r.x1 * S, (-r.y1 * S) + oy,
                               (r.x2 - r.x1) * S, -(r.y2 - r.y1) * S,
                               pen_body, QBrush(QColor(0, 100, 180, 30)))
            for pl in sym.polylines:
                if pl.unit in (0, u):
                    if len(pl.points) < 2:
                        continue
                    path = QPainterPath()
                    path.moveTo(pl.points[0][0] * S,
                                (-pl.points[0][1] * S) + oy)
                    for px, py in pl.points[1:]:
                        path.lineTo(px * S, (-py * S) + oy)
                    sc.addPath(path, pen_body)
            for c in sym.circles:
                if c.unit in (0, u):
                    r = c.radius
                    sc.addEllipse((c.cx - r) * S, (-(c.cy + r) * S) + oy,
                                  2 * r * S, 2 * r * S,
                                  pen_body, QBrush(Qt.BrushStyle.NoBrush))
    else:
        for r in sym.rectangles:
            sc.addRect(r.x1 * S, -r.y1 * S, (r.x2 - r.x1) * S, -(r.y2 - r.y1) * S,
                       pen_body, QBrush(QColor(0, 100, 180, 30)))
        for pl in sym.polylines:
            if len(pl.points) < 2:
                continue
            path = QPainterPath()
            path.moveTo(pl.points[0][0] * S, -pl.points[0][1] * S)
            for px, py in pl.points[1:]:
                path.lineTo(px * S, -py * S)
            sc.addPath(path, pen_body)
        for c in sym.circles:
            r = c.radius
            sc.addEllipse((c.cx - r) * S, -(c.cy + r) * S, 2 * r * S, 2 * r * S,
                          pen_body, QBrush(Qt.BrushStyle.NoBrush))

    # ── Label scale from overall scene extent ──
    bounds = sc.itemsBoundingRect()
    extent = max(bounds.width(), bounds.height(), 1.0)
    target_h = extent * 0.06 if is_multi else extent * 0.10

    # ── Draw pins ──
    ref_font = QFont("Monospace", 8)
    for pin in sym.pins:
        items_for_pin: list = []
        oy = _oy(pin.unit)
        px, py = pin.x * S, (-pin.y * S) + oy
        length = pin.length * S
        rad = math.radians(pin.direction)
        ex = px + length * math.cos(rad)
        ey = py - length * math.sin(rad)

        line = sc.addLine(px, py, ex, ey, pen_pin)
        line.setZValue(1)
        items_for_pin.append(line)

        dot_r = extent * 0.012
        dot = sc.addEllipse(px - dot_r, py - dot_r, 2 * dot_r, 2 * dot_r,
                            QPen(Qt.PenStyle.NoPen), QBrush(_CLR_SYM_PIN))
        dot.setZValue(2)
        items_for_pin.append(dot)

        lbl_text = pin.number
        if pin.name and pin.name != "~" and pin.name != pin.number:
            lbl_text = f"{pin.number} ({pin.name})"
        lbl = sc.addSimpleText(lbl_text, ref_font)
        lbl.setBrush(QBrush(_CLR_SYM_LABEL))
        lbl.setZValue(3)
        lbl_h = lbl.boundingRect().height()
        scale = target_h / max(lbl_h, 1.0)
        lbl.setScale(scale)
        lbl.setPos(px + target_h * 0.2, py - target_h * 1.1)
        items_for_pin.append(lbl)

        # Highlight ring (hidden by default)
        hl_r = extent * 0.03 if is_multi else extent * 0.06
        hl = sc.addEllipse(px - hl_r, py - hl_r, 2 * hl_r, 2 * hl_r,
                           QPen(_CLR_HIGHLIGHT, extent * 0.010),
                           QBrush(QColor(0, 255, 255, 50)))
        hl.setZValue(10)
        hl.setVisible(False)
        items_for_pin.append(hl)

        pin_items[pin.number] = items_for_pin

    # ── Unit labels for multi-unit symbols ──
    if is_multi:
        lbl_font = QFont("Monospace", 8)
        for u in sorted(pin_units):
            unit_pins = [p for p in sym.pins if p.unit == u]
            if not unit_pins:
                continue
            oy = _oy(u)
            min_y_scene = min((-p.y * S) + oy for p in unit_pins)
            avg_x = sum(p.x * S for p in unit_pins) / len(unit_pins)
            lbl = sc.addSimpleText(f"Unit {u}", lbl_font)
            lbl.setBrush(QBrush(QColor(180, 180, 180)))
            lbl.setZValue(4)
            lbl_br = lbl.boundingRect()
            lbl_scale = target_h * 1.2 / max(lbl_br.height(), 1.0)
            lbl.setScale(lbl_scale)
            lbl.setPos(avg_x - (lbl_br.width() * lbl_scale) / 2,
                       min_y_scene - target_h * 2.5)

    return sc, pin_items


def _build_generic_symbol_scene(pad_numbers: list[str]):
    """Build a rectangular generic-IC symbol from pad numbers.

    Used when no KiCad symbol is linked (generic IC).
    Pins are arranged evenly: left side gets the first half, right side
    the second half, similar to a DIP package layout.

    Returns (scene, pin_items_dict).
    """
    sc = QGraphicsScene()
    pin_items: dict[str, list] = {}
    if not pad_numbers:
        txt = sc.addSimpleText("No pads")
        txt.setBrush(QBrush(QColor(160, 160, 160)))
        return sc, pin_items

    n = len(pad_numbers)
    half = (n + 1) // 2  # left side gets ceil(n/2)
    left_pads = pad_numbers[:half]
    right_pads = pad_numbers[half:]

    pin_spacing = 2.54
    pin_length = 3.0
    body_w = 10.0
    body_h = max(len(left_pads), len(right_pads), 1) * pin_spacing + pin_spacing

    pen_body = QPen(_CLR_SYM_BODY, 0.15)
    pen_pin = QPen(_CLR_SYM_PIN, 0.1)

    # Body rectangle
    sc.addRect(0, 0, body_w, body_h, pen_body, QBrush(QColor(0, 100, 180, 30)))

    # Title
    ref_font = QFont("Monospace", 8)
    title = sc.addSimpleText("Generic IC", ref_font)
    title.setBrush(QBrush(QColor(180, 180, 180)))
    title.setZValue(3)
    tb = title.boundingRect()
    t_scale = (body_w * 0.8) / max(tb.width(), 1.0)
    t_scale = min(t_scale, pin_spacing * 0.6 / max(tb.height(), 1.0))
    title.setScale(t_scale)
    title.setPos(body_w / 2 - (tb.width() * t_scale) / 2,
                 -pin_spacing * 0.8)

    extent = max(body_w + 2 * pin_length, body_h)
    target_h = extent * 0.08
    dot_r = extent * 0.015

    def _draw_pin(pad_num: str, px: float, py: float,
                  ex: float, ey: float) -> None:
        items: list = []
        line = sc.addLine(px, py, ex, ey, pen_pin)
        line.setZValue(1)
        items.append(line)

        d = sc.addEllipse(px - dot_r, py - dot_r, 2 * dot_r, 2 * dot_r,
                          QPen(Qt.PenStyle.NoPen), QBrush(_CLR_SYM_PIN))
        d.setZValue(2)
        items.append(d)

        lbl = sc.addSimpleText(pad_num, ref_font)
        lbl.setBrush(QBrush(_CLR_SYM_LABEL))
        lbl.setZValue(3)
        lbl_h = lbl.boundingRect().height()
        scale = target_h / max(lbl_h, 1.0)
        lbl.setScale(scale)
        lbl.setPos(px + target_h * 0.2, py - target_h * 1.1)
        items.append(lbl)

        hl_r = extent * 0.05
        hl = sc.addEllipse(px - hl_r, py - hl_r, 2 * hl_r, 2 * hl_r,
                           QPen(_CLR_HIGHLIGHT, extent * 0.012),
                           QBrush(QColor(0, 255, 255, 50)))
        hl.setZValue(10)
        hl.setVisible(False)
        items.append(hl)

        pin_items[pad_num] = items

    # Left side pins (pointing left from body)
    for i, pn in enumerate(left_pads):
        y = pin_spacing + i * pin_spacing
        _draw_pin(pn, -pin_length, y, 0, y)

    # Right side pins (pointing right from body)
    for i, pn in enumerate(right_pads):
        y = pin_spacing + i * pin_spacing
        _draw_pin(pn, body_w + pin_length, y, body_w, y)

    return sc, pin_items


def _build_footprint_scene(fp_data):
    """Draw a footprint from FootprintData into a QGraphicsScene.

    Returns (scene, pad_items_dict) where pad_items_dict maps
    pad_number -> list of QGraphicsItem for highlighting.
    """
    sc = QGraphicsScene()
    pad_items: dict[str, list] = {}
    if fp_data is None:
        txt = sc.addSimpleText("No footprint data")
        txt.setBrush(QBrush(QColor(160, 160, 160)))
        return sc, pad_items

    pen_silk = QPen(_CLR_FP_SILK, 0.08)

    for line in fp_data.lines:
        sc.addLine(line.x1, line.y1, line.x2, line.y2, pen_silk)
    for r in fp_data.rects:
        sc.addRect(min(r.x1, r.x2), min(r.y1, r.y2),
                   abs(r.x2 - r.x1), abs(r.y2 - r.y1), pen_silk)
    for c in fp_data.circles:
        r = c.radius
        sc.addEllipse(c.cx - r, c.cy - r, 2 * r, 2 * r, pen_silk)
    for poly in fp_data.polys:
        if len(poly.points) < 2:
            continue
        path = QPainterPath()
        path.moveTo(poly.points[0][0], poly.points[0][1])
        for px, py in poly.points[1:]:
            path.lineTo(px, py)
        path.closeSubpath()
        sc.addPath(path, pen_silk, QBrush(QColor(200, 200, 60, 40)))

    # Label scaling from average pad size
    avg_pad = 0.0
    if fp_data.pads:
        avg_pad = sum(max(p.width, p.height) for p in fp_data.pads) / len(fp_data.pads)
    if avg_pad < 0.1:
        avg_pad = 1.0
    target_h = avg_pad * 0.55

    # Compute overall extent for highlight rings
    bounds = sc.itemsBoundingRect()
    fp_extent = max(bounds.width(), bounds.height(), 1.0)

    ref_font = QFont("Monospace", 8)

    for pad in fp_data.pads:
        items_for_pad: list = []
        w, h = pad.width, pad.height
        x, y = pad.x - w / 2, pad.y - h / 2
        if pad.shape in ("circle", "oval"):
            it = sc.addEllipse(x, y, w, h,
                               QPen(_CLR_FP_PAD, 0.04),
                               QBrush(QColor(255, 50, 50, 120)))
        else:
            it = sc.addRect(x, y, w, h,
                            QPen(_CLR_FP_PAD, 0.04),
                            QBrush(QColor(255, 50, 50, 120)))
        it.setZValue(1)
        items_for_pad.append(it)

        lbl = sc.addSimpleText(pad.number, ref_font)
        lbl.setBrush(QBrush(_CLR_FP_LABEL))
        lbl.setZValue(2)
        lbl_br = lbl.boundingRect()
        scale = target_h / max(lbl_br.height(), 1.0)
        if lbl_br.width() * scale > w * 1.5 and w > 0:
            scale = min(scale, (w * 1.5) / max(lbl_br.width(), 1.0))
        lbl.setScale(scale)
        lbl.setPos(pad.x - (lbl_br.width() * scale) / 2,
                   pad.y - (lbl_br.height() * scale) / 2)
        items_for_pad.append(lbl)

        # Highlight ring (hidden by default)
        hl_r = max(w, h) * 0.75
        hl = sc.addEllipse(pad.x - hl_r, pad.y - hl_r, 2 * hl_r, 2 * hl_r,
                           QPen(_CLR_HIGHLIGHT, fp_extent * 0.012),
                           QBrush(QColor(0, 255, 255, 50)))
        hl.setZValue(10)
        hl.setVisible(False)
        items_for_pad.append(hl)

        pad_items.setdefault(pad.number, []).extend(items_for_pad)

    return sc, pad_items


class PinPadMappingDialog(QDialog):
    """Visual dialog for assigning symbol pins to footprint pads.

    Top: two side-by-side previews (symbol + footprint) with deep zoom.
    Bottom: mapping table with horizontal scroll.
    Selecting a row highlights the pin on the symbol and the mapped pad
    on the footprint.
    """

    def __init__(
        self,
        footprint_item: FootprintItem,
        library: LibraryBridge,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._fp = footprint_item
        self._lib = library
        self._result_map: dict[str, str] = {}

        self.setWindowTitle(
            f"Pin ↔ Pad Mapping — {footprint_item.reference}"
        )
        self.setMinimumSize(820, 620)
        self.resize(1060, 780)

        # Fetch library data
        self._sym_data = None
        self._fp_data = None
        if footprint_item.symbol_lib and footprint_item.symbol_name:
            self._sym_data = library.parse_symbol(footprint_item.symbol_full_name)
        if footprint_item.footprint_lib and footprint_item.footprint_name:
            self._fp_data = library.parse_footprint(footprint_item.footprint_full_name)

        # Pad numbers (compute early so _pins can use them for generic mode)
        self._pad_numbers_early: list[str] = []
        if self._fp_data:
            self._pad_numbers_early = sorted(
                {p.number for p in self._fp_data.pads},
                key=lambda n: (n.isdigit(), int(n) if n.isdigit() else 0, n),
            )
        elif footprint_item.pad_numbers():
            self._pad_numbers_early = sorted(
                footprint_item.pad_numbers(),
                key=lambda n: (n.isdigit(), int(n) if n.isdigit() else 0, n),
            )
        self._pad_numbers = self._pad_numbers_early

        # Pin list
        self._pins: list[tuple[str, str, str]] = []
        self._generic_mode = False
        if self._sym_data:
            for p in self._sym_data.pins:
                self._pins.append((p.number, p.name, p.electrical_type))
        elif self._pad_numbers_early:
            # Generic IC: no symbol, generate pins from pad numbers
            self._generic_mode = True
            for pn in self._pad_numbers_early:
                self._pins.append((pn, f"Pad {pn}", "passive"))
        self._pins.sort(
            key=lambda t: (t[0].isdigit(), int(t[0]) if t[0].isdigit() else 0, t[0])
        )

        # Scene item maps for highlighting
        self._sym_pin_items: dict[str, list] = {}
        self._fp_pad_items: dict[str, list] = {}

        self._build_ui()
        self._load_current_mapping()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # Header
        sym_label = self._fp.symbol_full_name or ("Generic IC (footprint pads)" if self._generic_mode else "—")
        hdr = QLabel(
            f"<span style='font-size:11pt'>"
            f"<b>Symbol:</b> {sym_label} &nbsp;│&nbsp; "
            f"<b>Footprint:</b> {self._fp.footprint_full_name} &nbsp;│&nbsp; "
            f"Pins: {len(self._pins)}  Pads: {len(self._pad_numbers)}</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        # Main vertical splitter: previews (top) / table (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Two preview panes side by side ──
        preview_widget = QWidget()
        preview_lay = QHBoxLayout(preview_widget)
        preview_lay.setContentsMargins(0, 0, 0, 0)
        preview_lay.setSpacing(4)

        # Symbol preview
        sym_box = QVBoxLayout()
        sym_box.setSpacing(2)
        if self._generic_mode:
            sym_box.addWidget(QLabel("<b>Generic IC</b> — pad numbers as pins"))
            self._sym_scene, self._sym_pin_items = _build_generic_symbol_scene(
                self._pad_numbers)
        else:
            sym_box.addWidget(QLabel("<b>Symbol</b> — pin number (name)"))
            self._sym_scene, self._sym_pin_items = _build_symbol_scene(self._sym_data)
        self._sym_view = _PreviewView(self._sym_scene)
        self._sym_view.setMinimumHeight(220)
        sym_box.addWidget(self._sym_view, 1)
        preview_lay.addLayout(sym_box, 1)

        # Footprint preview
        fp_box = QVBoxLayout()
        fp_box.setSpacing(2)
        fp_box.addWidget(QLabel("<b>Footprint</b> — pad number"))
        self._fp_scene, self._fp_pad_items = _build_footprint_scene(self._fp_data)
        self._fp_view = _PreviewView(self._fp_scene)
        self._fp_view.setMinimumHeight(220)
        fp_box.addWidget(self._fp_view, 1)
        preview_lay.addLayout(fp_box, 1)

        splitter.addWidget(preview_widget)

        # ── Mapping table ──
        table_widget = QWidget()
        table_lay = QVBoxLayout(table_widget)
        table_lay.setContentsMargins(0, 4, 0, 0)

        self._table = QTableWidget(len(self._pins), 5)
        self._table.setHorizontalHeaderLabels(
            ["Pin #", "Pin Name", "Type", "→  Pad #", "Net on Pad"]
        )
        header = self._table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(60)
        self._table.setMinimumWidth(400)
        self._table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        # Connect row selection → highlight
        self._table.currentCellChanged.connect(self._on_row_selected)

        self._combos: list[QComboBox] = []
        for row, (pin_num, pin_name, elec_type) in enumerate(self._pins):
            it_num = QTableWidgetItem(pin_num)
            it_num.setFlags(it_num.flags() & ~Qt.ItemFlag.ItemIsEditable)
            bold = QFont()
            bold.setBold(True)
            it_num.setFont(bold)
            self._table.setItem(row, 0, it_num)

            it_name = QTableWidgetItem(pin_name)
            it_name.setFlags(it_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, it_name)

            abbr = _ELEC_TYPE_ABBR.get(elec_type, elec_type or "?")
            it_type = QTableWidgetItem(abbr)
            it_type.setFlags(it_type.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it_type.setToolTip(elec_type)
            self._table.setItem(row, 2, it_type)

            combo = QComboBox()
            combo.addItem("— none —", "")
            for pn in self._pad_numbers:
                combo.addItem(f"Pad {pn}", pn)
            combo.currentIndexChanged.connect(self._on_combo_changed)
            self._table.setCellWidget(row, 3, combo)
            self._combos.append(combo)

            it_net = QTableWidgetItem("")
            it_net.setFlags(it_net.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 4, it_net)

        table_lay.addWidget(self._table)
        splitter.addWidget(table_widget)

        # Splitter: previews get 60%, table 40%
        splitter.setStretchFactor(0, 60)
        splitter.setStretchFactor(1, 40)
        root.addWidget(splitter, 1)

        # Button row
        btn_row = QHBoxLayout()
        btn_auto = QPushButton("Auto-match")
        btn_auto.setToolTip("Match pins to pads by number")
        btn_auto.clicked.connect(self._auto_match)
        btn_row.addWidget(btn_auto)

        btn_reset = QPushButton("Reset (1:1)")
        btn_reset.setToolTip("Pin N → pad N identity mapping")
        btn_reset.clicked.connect(self._reset_identity)
        btn_row.addWidget(btn_reset)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # Warning label
        self._warn = QLabel("")
        self._warn.setStyleSheet("color: #cc6600; font-weight: bold;")
        self._warn.setWordWrap(True)
        root.addWidget(self._warn)

        # OK / Cancel
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._confirm)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Row selection → highlight pin on symbol + pad on footprint ──
    def _on_row_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        # Hide all highlights first
        for items in self._sym_pin_items.values():
            for it in items:
                if it.zValue() == 10:  # highlight ring
                    it.setVisible(False)
        for items in self._fp_pad_items.values():
            for it in items:
                if it.zValue() == 10:
                    it.setVisible(False)

        if row < 0 or row >= len(self._pins):
            return

        pin_num = self._pins[row][0]

        # Highlight pin on symbol
        if pin_num in self._sym_pin_items:
            for it in self._sym_pin_items[pin_num]:
                if it.zValue() == 10:
                    it.setVisible(True)

        # Highlight the mapped pad on footprint
        combo = self._combos[row]
        pad_num = combo.currentData()
        if pad_num and pad_num in self._fp_pad_items:
            for it in self._fp_pad_items[pad_num]:
                if it.zValue() == 10:
                    it.setVisible(True)

    # ── Data logic ──
    def _load_current_mapping(self) -> None:
        existing = dict(self._fp.pin_map)
        if not existing:
            existing = {p[0]: p[0] for p in self._pins if p[0] in self._pad_numbers}
        for row, (pin_num, _, _) in enumerate(self._pins):
            pad = existing.get(pin_num, "")
            combo = self._combos[row]
            idx = combo.findData(pad)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._refresh_nets()
        self._check_warnings()

    def _on_combo_changed(self) -> None:
        self._refresh_nets()
        self._check_warnings()
        # Re-apply highlight for current row
        row = self._table.currentRow()
        if row >= 0:
            self._on_row_selected(row, 0, -1, -1)

    def _refresh_nets(self) -> None:
        for row in range(len(self._pins)):
            combo = self._combos[row]
            pad = combo.currentData()
            net = self._fp.pad_nets.get(pad, "") if pad else ""
            net_item = self._table.item(row, 4)
            if net_item:
                net_item.setText(net or "—")

    def _check_warnings(self) -> None:
        used_pads: dict[str, list[str]] = {}
        unmapped = []
        for row, (pin_num, _, _) in enumerate(self._pins):
            pad = self._combos[row].currentData()
            if not pad:
                unmapped.append(pin_num)
            else:
                used_pads.setdefault(pad, []).append(pin_num)
        warnings = []
        dupes = {pad: pins for pad, pins in used_pads.items() if len(pins) > 1}
        if dupes:
            for pad, pins in dupes.items():
                warnings.append(f"Pad {pad} assigned to multiple pins: {', '.join(pins)}")
        if unmapped:
            warnings.append(f"Unmapped pins: {', '.join(unmapped)}")
        self._warn.setText("\n".join(warnings))

    def _auto_match(self) -> None:
        for row, (pin_num, _, _) in enumerate(self._pins):
            combo = self._combos[row]
            idx = combo.findData(pin_num)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._refresh_nets()
        self._check_warnings()

    def _reset_identity(self) -> None:
        self._auto_match()

    def _confirm(self) -> None:
        self._result_map = {}
        for row, (pin_num, _, _) in enumerate(self._pins):
            pad = self._combos[row].currentData()
            if pad:
                self._result_map[pin_num] = pad
        self.accept()

    def result_pin_map(self) -> dict[str, str]:
        return dict(self._result_map)


# ====================================================================
# Background library scanner
# ====================================================================

class _ScanWorker(QObject):
    """Runs LibraryBridge.scan() in a background thread."""
    finished = Signal(int, int)  # fp_count, sym_count

    def __init__(self, library: "LibraryBridge") -> None:
        super().__init__()
        self._library = library

    def run(self) -> None:
        fp_count, sym_count = self._library.scan()
        self.finished.emit(fp_count, sym_count)


class _ProjectSnapshotCommand(QUndoCommand):
    """Undo/redo command based on full project state snapshots."""

    def __init__(self, window: "MainWindow", text: str,
                 before_state: dict[str, Any], after_state: dict[str, Any]) -> None:
        super().__init__(text)
        self._window = window
        self._before_state = deepcopy(before_state)
        self._after_state = deepcopy(after_state)
        self._first_redo = True

    def undo(self) -> None:
        self._window._restore_project_state(self._before_state)

    def redo(self) -> None:
        if self._first_redo:
            self._first_redo = False
            return
        self._window._restore_project_state(self._after_state)


# ====================================================================
# Main Window
# ====================================================================

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PCB → KiCad  –  Reverse Engineering Tool")
        self.resize(1500, 950)

        # Core objects
        self._coord = CoordinateSystem(pixels_per_mm=10.0)
        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QBrush(cm.background()))
        self._image_engine = ImageEngine(self._scene)
        self._library = LibraryBridge()
        self._undo_stack = QUndoStack(self)
        self._history_restoring: bool = False
        self._footprints: list[FootprintItem] = []
        self._project_path: Optional[str] = None

        # Connect-nets state
        self._pending_pad: Optional[tuple[str, str]] = None
        self._net_counter: int = 0
        self._ratsnest_lines: list[QGraphicsLineItem] = []

        # Wire drawing state
        self._wires: list[WireSegmentItem] = []
        self._junctions: list[JunctionItem] = []
        self._wire_drawing: bool = False
        self._wire_anchor: Optional[QPointF] = None
        self._wire_preview: Optional[WirePreviewItem] = None

        # Central canvas
        self._view = PcbGraphicsView(self._scene)
        self.setCentralWidget(self._view)

        # Left dock – library browser
        self._lib_browser = LibraryBrowserWidget(self._library)
        lib_dock = QDockWidget("Library Browser", self)
        lib_dock.setWidget(self._lib_browser)
        lib_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, lib_dock)

        # Right dock – properties + component list + layers
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)

        self._props = PropertiesPanel()
        right_lay.addWidget(self._props)

        self._comp_list = ComponentListWidget()
        right_lay.addWidget(self._comp_list)

        # Layer visibility panel
        self._layer_panel = self._create_layer_panel()
        right_lay.addWidget(self._layer_panel)

        right_dock = QDockWidget("Properties", self)
        right_dock.setWidget(right_w)
        right_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right_dock)

        # Menu bar + toolbar
        self._create_menus()
        self._create_toolbar()

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Initialising…")

        # Wire signals
        self._wire_signals()

        # Apply theme (stylesheet + canvas background)
        self._apply_theme()

        # Initial library scan
        self._scan_thread = QThread(self)
        self._scan_worker = _ScanWorker(self._library)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        QTimer.singleShot(100, self._scan_thread.start)

    # ------------------------------------------------------------------
    # Layer visibility panel
    # ------------------------------------------------------------------

    def _create_layer_panel(self) -> QGroupBox:
        grp = QGroupBox("Layers && Theme")
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(4, 4, 4, 4)

        # ---- Theme selector ----
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(cm.scheme_names())
        self._theme_combo.setCurrentText(cm.current_scheme_name())
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self._theme_combo, 1)
        lay.addLayout(theme_row)

        # ---- Layer visibility + colour buttons ----
        self._layer_checks: dict[str, QCheckBox] = {}
        self._layer_color_btns: dict[str, QPushButton] = {}

        # Map layer-panel keys to the colour-manager key used for that layer
        self._layer_cm_keys: dict[str, str] = {
            "F.Cu": "pad_smd",
            "B.Cu": "pad_tht",
            "F.SilkS": "silkscreen",
            "B.SilkS": "silkscreen",
            "F.Fab": "fab",
            "B.Fab": "fab",
            "Wires": "wire",
        }
        layer_names = [
            ("F.Cu", "Front Copper"),
            ("B.Cu", "Back Copper"),
            ("F.SilkS", "Front Silkscreen"),
            ("B.SilkS", "Back Silkscreen"),
            ("F.Fab", "Front Fabrication"),
            ("B.Fab", "Back Fabrication"),
            ("Wires", "Wires / Nets"),
        ]
        for key, label in layer_names:
            row = QHBoxLayout()
            chk = QCheckBox(f"{label}  ({key})")
            chk.setChecked(True)
            chk.toggled.connect(lambda checked, k=key: self._on_layer_toggled(k, checked))
            row.addWidget(chk, 1)

            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setToolTip(f"Change colour for {label}")
            btn.clicked.connect(lambda _, k=key: self._on_pick_layer_color(k))
            row.addWidget(btn)

            lay.addLayout(row)
            self._layer_checks[key] = chk
            self._layer_color_btns[key] = btn

        self._refresh_layer_color_buttons()
        return grp

    def _on_layer_toggled(self, layer_key: str, visible: bool) -> None:
        if layer_key == "Wires":
            for w in self._wires:
                w.setVisible(visible)
            for j in self._junctions:
                j.setVisible(visible)
            for line in self._ratsnest_lines:
                line.setVisible(visible)
        else:
            for fp in self._footprints:
                if layer_key in ("F.Cu", "B.Cu"):
                    if fp.layer == layer_key:
                        fp.setVisible(visible)

    # ------------------------------------------------------------------
    # Theme / colour helpers
    # ------------------------------------------------------------------

    def _refresh_layer_color_buttons(self) -> None:
        """Update the small colour-swatch buttons to match the current scheme."""
        for key, btn in self._layer_color_btns.items():
            cm_key = self._layer_cm_keys.get(key, "wire")
            c = cm.raw(cm_key)
            btn.setStyleSheet(
                f"background-color: rgba({c[0]},{c[1]},{c[2]},{c[3]});"
                " border: 1px solid #888; border-radius: 3px;"
            )

    def _on_pick_layer_color(self, layer_key: str) -> None:
        cm_key = self._layer_cm_keys.get(layer_key, "wire")
        old = cm.raw(cm_key)
        initial = QColor(*old)
        colour = QColorDialog.getColor(
            initial, self, f"Colour for {layer_key}",
            QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if colour.isValid():
            cm.set_color(cm_key, [colour.red(), colour.green(),
                                   colour.blue(), colour.alpha()])
            cm.save_default(cm.current_scheme_name())
            self._refresh_layer_color_buttons()
            self._apply_theme()

    def _on_theme_changed(self, name: str) -> None:
        cm.set_scheme(name)
        cm.save_default(name)
        self._refresh_layer_color_buttons()
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Re-apply colours from the current scheme to all visible items."""
        # Window stylesheet (menus, panels, buttons, inputs, …)
        self.setStyleSheet(cm.stylesheet())
        # Canvas background
        self._scene.setBackgroundBrush(QBrush(cm.background()))
        # Wires, junctions
        for w in self._wires:
            w.update()
        for j in self._junctions:
            j.setBrush(QBrush(cm.junction()))
            j.setPen(QPen(cm.junction().darker(120), 1))
        if self._wire_preview:
            self._wire_preview.setPen(
                QPen(cm.wire_preview(), 1.5, Qt.PenStyle.DashLine))
        # Footprints
        for fp in self._footprints:
            fp.update()
            fp._label.setDefaultTextColor(cm.text_label())
        # Ratsnest
        self._rebuild_ratsnest()

    def _build_scheme_menu(self) -> None:
        """Populate the View ▸ Color Scheme sub-menu with radio-like actions."""
        self._scheme_menu.clear()
        current = cm.current_scheme_name()
        for name in cm.scheme_names():
            act = self._scheme_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _checked, n=name: self._on_scheme_action(n))

    def _on_scheme_action(self, name: str) -> None:
        self._theme_combo.setCurrentText(name)  # triggers _on_theme_changed
        self._build_scheme_menu()  # update radio checks

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _create_menus(self) -> None:
        mb = self.menuBar()

        # ---- File ----
        file_menu = mb.addMenu("&File")
        file_menu.addAction("&New Project", self._on_new, QKeySequence("Ctrl+N"))
        file_menu.addAction("&Open Project…", self._on_open_project, QKeySequence("Ctrl+O"))
        file_menu.addAction("&Save Project", self._on_save_project, QKeySequence("Ctrl+S"))
        self._act_save_as = file_menu.addAction(
            "Save Project &As…", self._on_save_project_as, QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        file_menu.addAction("&Export KiCad 9 Project…", self._on_export_project,
                            QKeySequence("Ctrl+E"))
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence("Alt+F4"))

        # ---- Edit ----
        edit_menu = mb.addMenu("&Edit")
        self._act_undo = self._undo_stack.createUndoAction(self, "&Undo")
        self._act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(self._act_undo)
        self._act_redo = self._undo_stack.createRedoAction(self, "&Redo")
        self._act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(self._act_redo)
        edit_menu.addSeparator()
        self._act_delete = edit_menu.addAction(
            "&Delete Selected", self._on_delete_selected, QKeySequence.StandardKey.Delete)
        edit_menu.addSeparator()
        edit_menu.addAction("Select &All", self._on_select_all, QKeySequence("Ctrl+A"))

        # ---- View ----
        view_menu = mb.addMenu("&View")
        view_menu.addAction("Zoom &In", self._on_zoom_in, QKeySequence("Ctrl+="))
        view_menu.addAction("Zoom &Out", self._on_zoom_out, QKeySequence("Ctrl+-"))
        view_menu.addAction("&Fit to Screen", self._on_fit_view, QKeySequence("Ctrl+0"))
        view_menu.addSeparator()
        view_menu.addAction("Load &Top Photo…", self._on_load_top)
        view_menu.addAction("Load &Bottom Photo…", self._on_load_bottom)
        view_menu.addSeparator()

        # Colour scheme submenu
        self._scheme_menu = view_menu.addMenu("Color &Scheme")
        self._build_scheme_menu()

        # ---- Place ----
        place_menu = mb.addMenu("&Place")
        place_menu.addAction("Place &Footprint", self._on_place_from_browser,
                             QKeySequence("P"))
        place_menu.addAction("&Link Symbol…", self._on_link_symbol)
        place_menu.addSeparator()
        self._act_draw_wire = QAction("Draw &Wire", self)
        self._act_draw_wire.setCheckable(True)
        self._act_draw_wire.setShortcut(QKeySequence("W"))
        self._act_draw_wire.toggled.connect(self._on_toggle_wire_draw)
        place_menu.addAction(self._act_draw_wire)
        place_menu.addAction("Add &Junction", self._on_add_junction, QKeySequence("J"))

        # ---- Tools ----
        tools_menu = mb.addMenu("&Tools")
        self._act_connect_nets = tools_menu.addAction("Connect &Nets (pad mode)")
        self._act_connect_nets.setCheckable(True)
        self._act_connect_nets.toggled.connect(self._on_toggle_connect_mode)
        tools_menu.addSeparator()
        tools_menu.addAction("Library &Paths…", self._on_settings)

        # ---- Help ----
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&About…", self._on_about)

    # ------------------------------------------------------------------
    # Toolbar (quick access)
    # ------------------------------------------------------------------

    def _create_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addAction("New", self._on_new)
        tb.addAction("Open", self._on_open_project)
        tb.addAction("Save", self._on_save_project)
        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()
        tb.addAction("Place Footprint", self._on_place_from_browser)
        tb.addSeparator()

        act_wire_tb = tb.addAction("Draw Wire")
        act_wire_tb.setCheckable(True)
        act_wire_tb.toggled.connect(self._act_draw_wire.setChecked)
        self._act_draw_wire.toggled.connect(act_wire_tb.setChecked)
        tb.addSeparator()

        tb.addAction("Export", self._on_export_project)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._lib_browser.footprint_activated.connect(self._on_add_footprint)
        self._comp_list.component_selected.connect(self._on_select_component)
        self._comp_list.delete_requested.connect(self._on_delete_component)
        self._props.property_changed.connect(self._on_property_changed)
        self._props.link_symbol_requested.connect(self._on_link_symbol)
        self._props.pin_pad_mapping_requested.connect(self._on_pin_pad_mapping)
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._connect_mode: bool = False

        # Image layer controls
        self._props.chk_top.toggled.connect(
            lambda v: self._image_engine.top().set_visible(v))
        self._props.chk_bot.toggled.connect(
            lambda v: self._image_engine.bottom().set_visible(v))
        self._props.opacity_slider.valueChanged.connect(
            lambda v: self._image_engine.bottom().set_opacity(v / 100.0))
        self._props.chk_mirror.toggled.connect(
            lambda v: self._image_engine.bottom().set_mirrored(v))

        # Wire drawing signals from the view
        self._view.wire_click.connect(self._on_wire_click)
        self._view.wire_move.connect(self._on_wire_move)
        self._view.wire_double_click.connect(self._on_wire_finish)
        self._view.canvas_right_click.connect(self._on_canvas_right_click)

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        # R = Rotate selected component 90° CW
        if key == Qt.Key.Key_R and not event.modifiers():
            self._rotate_selected(90)
            return
        # / or Tab = toggle wire routing direction (straight-first ↔ diagonal-first)
        if key in (Qt.Key.Key_Slash, Qt.Key.Key_Tab) and self._wire_preview:
            self._wire_preview.toggle_direction()
            return
        # Escape = cancel current wire chain; if no chain, exit wire mode; if not in wire mode, deselect
        if key == Qt.Key.Key_Escape:
            if self._wire_anchor is not None:
                # Cancel current in-progress wire chain only
                self._cancel_wire_draw()
                self._status.showMessage("Wire chain cancelled — still in wire draw mode")
            elif self._wire_drawing:
                self._act_draw_wire.setChecked(False)
            else:
                self._scene.clearSelection()
            return
        super().keyPressEvent(event)

    def _rotate_selected(self, degrees: float) -> None:
        before_state = self._capture_project_state()
        changed = False
        for fp in self._footprints:
            if fp.isSelected():
                new_rot = (fp.rotation_deg + degrees) % 360
                fp.set_rotation(new_rot)
                self._props.set_component(fp)
                self._rebuild_ratsnest()
                self._status.showMessage(f"Rotated {fp.reference} → {new_rot:.0f}°")
                changed = True
        if changed:
            self._push_history_state("Rotate component", before_state)

    # ------------------------------------------------------------------
    # Right-click context menu
    # ------------------------------------------------------------------

    def _on_canvas_right_click(self, scene_pos: QPointF, screen_pos: QPointF) -> None:
        # If drawing a wire chain, right-click cancels it
        if self._wire_anchor is not None:
            self._cancel_wire_draw()
            self._status.showMessage("Wire chain cancelled")
            return

        # Check what was clicked
        item_at = self._scene.itemAt(scene_pos, self._view.transform())

        # Find if it's a footprint or wire
        clicked_fp: Optional[FootprintItem] = None
        clicked_wire: Optional[WireSegmentItem] = None
        clicked_junction: Optional[JunctionItem] = None

        if isinstance(item_at, FootprintItem):
            clicked_fp = item_at
        elif isinstance(item_at, WireSegmentItem):
            clicked_wire = item_at
        elif isinstance(item_at, JunctionItem):
            clicked_junction = item_at
        elif item_at is not None:
            # May be a child of a FootprintItem
            parent = item_at.parentItem()
            if isinstance(parent, FootprintItem):
                clicked_fp = parent

        menu = QMenu(self)
        menu.addAction(self._act_undo)
        menu.addAction(self._act_redo)
        menu.addSeparator()

        if clicked_fp:
            # Component context menu
            menu.addAction(f"Rotate {clicked_fp.reference} CW  (R)",
                           lambda: self._ctx_rotate_fp(clicked_fp, 90))
            menu.addAction(f"Rotate {clicked_fp.reference} CCW",
                           lambda: self._ctx_rotate_fp(clicked_fp, -90))
            menu.addSeparator()
            menu.addAction(f"Flip to {'B.Cu' if clicked_fp.layer == 'F.Cu' else 'F.Cu'}",
                           lambda: self._ctx_flip_fp(clicked_fp))
            menu.addAction("Link Symbol…",
                           lambda: self._ctx_link_symbol(clicked_fp))
            menu.addAction("Pin ↔ Pad Mapping…",
                           lambda: self._ctx_pin_pad_mapping(clicked_fp))
            menu.addSeparator()
            menu.addAction(f"Delete {clicked_fp.reference}",
                           lambda: self._ctx_delete_fp(clicked_fp))
        elif clicked_wire:
            menu.addAction("Set Net Name…",
                           lambda: self._ctx_set_wire_net(clicked_wire))
            menu.addAction("Add Junction Here",
                           lambda: self._add_junction_at(scene_pos))
            menu.addSeparator()
            menu.addAction("Delete Wire",
                           lambda: self._ctx_delete_wire(clicked_wire))
        elif clicked_junction:
            menu.addAction("Delete Junction",
                           lambda: self._ctx_delete_junction(clicked_junction))
        else:
            # Canvas context menu
            menu.addAction("Place Footprint", self._on_place_from_browser)
            menu.addAction("Draw Wire", lambda: self._act_draw_wire.setChecked(True))
            menu.addAction("Add Junction", lambda: self._add_junction_at(scene_pos))
            menu.addSeparator()
            menu.addAction("Fit to Screen", self._on_fit_view)

        menu.exec(screen_pos.toPoint())

    # Context menu helpers
    def _ctx_rotate_fp(self, fp: FootprintItem, degrees: float) -> None:
        before_state = self._capture_project_state()
        new_rot = (fp.rotation_deg + degrees) % 360
        fp.set_rotation(new_rot)
        self._props.set_component(fp)
        self._rebuild_ratsnest()
        self._push_history_state("Rotate component", before_state)

    def _ctx_flip_fp(self, fp: FootprintItem) -> None:
        before_state = self._capture_project_state()
        fp.layer = "B.Cu" if fp.layer == "F.Cu" else "F.Cu"
        self._sync_footprint_visibility(fp)
        self._props.set_component(fp)
        self._push_history_state("Flip component layer", before_state)

    def _ctx_link_symbol(self, fp: FootprintItem) -> None:
        fp.setSelected(True)
        self._props.set_component(fp)
        self._on_link_symbol()

    def _ctx_pin_pad_mapping(self, fp: FootprintItem) -> None:
        fp.setSelected(True)
        self._props.set_component(fp)
        self._on_pin_pad_mapping()

    def _on_pin_pad_mapping(self) -> None:
        """Open the Pin ↔ Pad Mapping dialog for the selected component."""
        fp = self._selected_footprint()
        if not fp:
            self._status.showMessage("Select a component first.")
            return
        before_state = self._capture_project_state()
        dlg = PinPadMappingDialog(fp, self._library, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_map = dlg.result_pin_map()
            if new_map != fp.pin_map:
                fp.pin_map = new_map
                self._push_history_state("Edit pin↔pad mapping", before_state)
                self._status.showMessage(
                    f"Pin mapping updated for {fp.reference} "
                    f"({len(new_map)} assignments)")

    def _ctx_delete_fp(self, fp: FootprintItem) -> None:
        self._on_delete_component(fp.uid)

    def _ctx_set_wire_net(self, wire: WireSegmentItem) -> None:
        before_state = self._capture_project_state()
        name, ok = QInputDialog.getText(
            self, "Wire Net Name", "Net name:", text=wire.net_name)
        if ok and wire.net_name != name.strip():
            wire.net_name = name.strip()
            self._push_history_state("Change wire net", before_state)

    def _ctx_delete_wire(self, wire: WireSegmentItem) -> None:
        before_state = self._capture_project_state()
        if self._remove_wire(wire):
            self._push_history_state("Delete wire", before_state)

    def _ctx_delete_junction(self, junc: JunctionItem) -> None:
        before_state = self._capture_project_state()
        if self._remove_junction(junc):
            self._push_history_state("Delete junction", before_state)

    # ------------------------------------------------------------------
    # Wire drawing
    # ------------------------------------------------------------------

    def _on_toggle_wire_draw(self, checked: bool) -> None:
        self._wire_drawing = checked
        self._view.set_wire_draw_mode(checked)
        if checked:
            # Turn off connect-nets mode if active
            self._act_connect_nets.setChecked(False)
            self._status.showMessage(
                "Draw Wire ON — CLICK to place points, DOUBLE-CLICK or ESC to finish  |  / = toggle bend direction")
        else:
            self._cancel_wire_draw()
            self._status.showMessage("Draw Wire mode OFF")

    def _on_wire_click(self, pos: QPointF) -> None:
        """Called when user clicks on canvas in wire draw mode."""
        # Snap to nearest pad if close enough
        snap_pos = self._snap_to_pad(pos, threshold=15.0) or pos

        if self._wire_anchor is None:
            # Start new wire chain
            self._wire_anchor = snap_pos
            self._wire_preview = WirePreviewItem()
            self._wire_preview.set_anchor(snap_pos)
            self._scene.addItem(self._wire_preview)
        else:
            # Create 45°-constrained route from anchor to click position
            anchor = self._wire_anchor
            straight_first = self._wire_preview._straight_first if self._wire_preview else True
            pts = compute_45_route(anchor, snap_pos, straight_first)
            before_state = self._capture_project_state()
            created_any = False

            # Create segments between consecutive points
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                if (p1 - p2).manhattanLength() > 0.5:
                    seg = WireSegmentItem(p1.x(), p1.y(), p2.x(), p2.y())
                    self._scene.addItem(seg)
                    self._wires.append(seg)
                    self._sync_wire_visibility(seg)
                    created_any = True

            # Move anchor for next segment
            self._wire_anchor = snap_pos
            if self._wire_preview:
                self._wire_preview.set_anchor(snap_pos)
            if created_any:
                self._push_history_state("Draw wire", before_state)

    def _on_wire_move(self, pos: QPointF) -> None:
        """Update the wire preview as mouse moves."""
        if self._wire_preview:
            snap = self._snap_to_pad(pos, threshold=15.0) or pos
            self._wire_preview.update_preview(snap)

    def _on_wire_finish(self, pos: QPointF) -> None:
        """Double-click finishes the wire chain."""
        if self._wire_anchor is not None:
            self._on_wire_click(pos)
        self._cancel_wire_draw()

    def _cancel_wire_draw(self) -> None:
        """Stop wire drawing, remove preview."""
        self._wire_anchor = None
        if self._wire_preview:
            self._scene.removeItem(self._wire_preview)
            self._wire_preview = None

    def _snap_to_pad(self, pos: QPointF, threshold: float = 15.0) -> Optional[QPointF]:
        """If *pos* is within *threshold* pixels of a pad centre, return that centre."""
        best_dist = threshold
        best_pos: Optional[QPointF] = None
        for fp in self._footprints:
            for pad_num in fp.pad_numbers():
                pad_pos = fp.pad_scene_pos(pad_num)
                if pad_pos:
                    dist = (pos - pad_pos).manhattanLength()
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = pad_pos
        return best_pos

    def _on_add_junction(self) -> None:
        """Add junction at the centre of the viewport."""
        centre = self._view.mapToScene(self._view.viewport().rect().center())
        self._add_junction_at(centre)

    def _add_junction_at(self, pos: QPointF) -> None:
        before_state = self._capture_project_state()
        junc = JunctionItem(pos.x(), pos.y())
        self._scene.addItem(junc)
        self._junctions.append(junc)
        self._sync_wire_visibility(junc)
        self._status.showMessage(f"Junction added at ({pos.x():.1f}, {pos.y():.1f})")
        self._push_history_state("Add junction", before_state)

    # ------------------------------------------------------------------
    # View actions
    # ------------------------------------------------------------------

    def _on_zoom_in(self) -> None:
        self._view.scale(1.25, 1.25)

    def _on_zoom_out(self) -> None:
        self._view.scale(0.8, 0.8)

    def _on_fit_view(self) -> None:
        self._view.fitInView(
            self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _on_select_all(self) -> None:
        for item in self._scene.items():
            if item.flags() & item.GraphicsItemFlag.ItemIsSelectable:
                item.setSelected(True)

    def _on_delete_selected(self) -> None:
        """Delete all selected items (footprints, wires, junctions)."""
        before_state = self._capture_project_state()
        changed = False
        # Collect items to delete (iterate copy to avoid mutation)
        for item in list(self._scene.selectedItems()):
            if isinstance(item, FootprintItem):
                changed = self._remove_footprint_by_uid(item.uid) or changed
            elif isinstance(item, WireSegmentItem):
                changed = self._remove_wire(item) or changed
            elif isinstance(item, JunctionItem):
                changed = self._remove_junction(item) or changed
        if changed:
            self._push_history_state("Delete selection", before_state)

    def _on_about(self) -> None:
        QMessageBox.about(
            self, "About PCB → KiCad",
            "<h3>PCB → KiCad – Reverse Engineering Tool</h3>"
            "<p>Converts scanned PCB images to KiCad 9 projects.</p>"
            "<p>Place footprints on top/bottom photos, draw wires, "
            "link symbols, and export to KiCad.</p>"
            "<p><b>Shortcuts:</b></p>"
            "<ul>"
            "<li><b>R</b> – Rotate selected component 90°</li>"
            "<li><b>W</b> – Toggle wire drawing mode</li>"
            "<li><b>J</b> – Add junction</li>"
            "<li><b>P</b> – Place footprint</li>"
            "<li><b>Del</b> – Delete selected</li>"
            "<li><b>Ctrl+Z / Ctrl+Y</b> – Undo / Redo</li>"
            "<li><b>Esc</b> – Cancel / deselect</li>"
            "<li><b>Ctrl+E</b> – Export KiCad project</li>"
            "</ul>")

    def _on_save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", "project.p2k", "PCB-to-KiCad Project (*.p2k)")
        if path:
            self._project_path = path
            self._on_save_project()

    # ------------------------------------------------------------------
    # Library scan
    # ------------------------------------------------------------------

    def _on_scan_finished(self, fp_count: int, sym_count: int) -> None:
        self._lib_browser.populate()
        if fp_count or sym_count:
            self._status.showMessage(
                f"Libraries: {fp_count} footprints, {sym_count} symbols  |  "
                f"KiCad base: {self._library.kicad_base or 'not found'}")
        else:
            self._status.showMessage(
                "No KiCad libraries found. Use 'Library Paths' to configure.")

    def _capture_project_state(self) -> dict[str, Any]:
        return {
            "settings": {
                "pixels_per_mm": self._coord.pixels_per_mm,
                "origin_offset_mm": list(self._coord.origin_offset_mm),
                "net_counter": self._net_counter,
            },
            "images": {
                "top": self._image_engine.top().source_path,
                "bottom": self._image_engine.bottom().source_path,
                "top_visible": self._props.chk_top.isChecked(),
                "bottom_visible": self._props.chk_bot.isChecked(),
                "bottom_opacity": self._props.opacity_slider.value(),
                "bottom_mirrored": self._props.chk_mirror.isChecked(),
            },
            "components": [fp.to_dict() for fp in self._footprints],
            "wires": [w.to_dict() for w in self._wires],
            "junctions": [j.to_dict() for j in self._junctions],
        }

    def _push_history_state(self, text: str, before_state: dict[str, Any]) -> None:
        if self._history_restoring:
            return
        after_state = self._capture_project_state()
        if before_state == after_state:
            return
        self._undo_stack.push(_ProjectSnapshotCommand(self, text, before_state, after_state))

    @staticmethod
    def _replace_component_state(state: dict[str, Any], component: dict[str, Any]) -> bool:
        uid = component.get("uid")
        for i, comp in enumerate(state.get("components", [])):
            if comp.get("uid") == uid:
                state["components"][i] = deepcopy(component)
                return True
        return False

    def _clear_pending_pad(self) -> None:
        if not self._pending_pad:
            return
        src_uid, src_pad = self._pending_pad
        src_fp = next((f for f in self._footprints if f.uid == src_uid), None)
        if src_fp:
            src_fp.highlight_pad(src_pad, False)
        self._pending_pad = None

    def _clear_project_scene(self) -> None:
        self._cancel_wire_draw()
        self._clear_pending_pad()
        for fp in self._footprints:
            self._scene.removeItem(fp)
        self._footprints.clear()
        for w in self._wires:
            self._scene.removeItem(w)
        self._wires.clear()
        for j in self._junctions:
            self._scene.removeItem(j)
        self._junctions.clear()
        for line in self._ratsnest_lines:
            self._scene.removeItem(line)
        self._ratsnest_lines.clear()
        self._image_engine.clear()
        self._comp_list.refresh(self._footprints)
        self._props.set_component(None)

    def _sync_footprint_visibility(self, fp: FootprintItem) -> None:
        if fp.layer in self._layer_checks:
            fp.setVisible(self._layer_checks[fp.layer].isChecked())

    def _sync_wire_visibility(self, item: QGraphicsItem) -> None:
        item.setVisible(self._layer_checks["Wires"].isChecked())

    def _attach_footprint_signals(self, item: FootprintItem) -> None:
        item.signals.pad_clicked.connect(self._on_pad_clicked)
        item.signals.pad_right_clicked.connect(self._on_pad_right_clicked)
        item.signals.position_changed.connect(lambda *_: self._rebuild_ratsnest())
        item.signals.move_finished.connect(self._on_footprint_move_finished)
        item.connect_mode = self._connect_mode

    def _create_footprint_from_data(self, cd: dict[str, Any]) -> FootprintItem:
        fp_name = f"{cd['footprint_lib']}:{cd['footprint_name']}"
        fp_data = self._library.parse_footprint(fp_name)
        item = FootprintItem(
            footprint_data=fp_data,
            uid=cd.get("uid", ""),
            footprint_lib=cd["footprint_lib"],
            footprint_name=cd["footprint_name"],
            reference=cd.get("reference", "REF**"),
            value=cd.get("value", "VAL**"),
            symbol_lib=cd.get("symbol_lib", ""),
            symbol_name=cd.get("symbol_name", ""),
            pixels_per_mm=self._coord.pixels_per_mm,
        )
        item.layer = cd.get("layer", "F.Cu")
        item.set_rotation(cd.get("rotation", 0))
        item.setPos(cd.get("x_px", 0), cd.get("y_px", 0))
        for pad_num, net_name in cd.get("pad_nets", {}).items():
            item.set_pad_net(pad_num, net_name)
        item.pin_map = dict(cd.get("pin_map", {}))
        self._attach_footprint_signals(item)
        self._scene.addItem(item)
        self._footprints.append(item)
        self._sync_footprint_visibility(item)
        return item

    def _restore_project_state(self, state: dict[str, Any]) -> None:
        self._history_restoring = True
        try:
            self._clear_project_scene()

            settings = state.get("settings", {})
            self._coord.pixels_per_mm = settings.get("pixels_per_mm", 10.0)
            off = settings.get("origin_offset_mm", [0, 0])
            self._coord.origin_offset_mm = (off[0], off[1])
            self._net_counter = settings.get("net_counter", 0)

            images = state.get("images", {})
            top_path = images.get("top", "")
            bottom_path = images.get("bottom", "")
            if top_path:
                self._image_engine.load_top(top_path)
            if bottom_path:
                self._image_engine.load_bottom(bottom_path)
            self._props.chk_top.setChecked(images.get("top_visible", True))
            self._props.chk_bot.setChecked(images.get("bottom_visible", True))
            self._props.opacity_slider.setValue(images.get("bottom_opacity", 50))
            self._props.chk_mirror.setChecked(images.get("bottom_mirrored", False))

            for cd in state.get("components", []):
                self._create_footprint_from_data(cd)

            for wd in state.get("wires", []):
                wire = WireSegmentItem.from_dict(wd)
                self._scene.addItem(wire)
                self._wires.append(wire)
                self._sync_wire_visibility(wire)

            for jd in state.get("junctions", []):
                junc = JunctionItem.from_dict(jd)
                self._scene.addItem(junc)
                self._junctions.append(junc)
                self._sync_wire_visibility(junc)

            self._comp_list.refresh(self._footprints)
            self._props.set_component(None)
            self._rebuild_ratsnest()
        finally:
            self._history_restoring = False

    # ------------------------------------------------------------------
    # File slots
    # ------------------------------------------------------------------

    def _on_new(self) -> None:
        if self._footprints or self._wires or self._junctions:
            reply = QMessageBox.question(
                self, "New Project",
                "Discard current work?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._clear_project_scene()
        self._net_counter = 0
        self._project_path = None
        self._undo_stack.clear()
        self._status.showMessage("New project created.")

    def _on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "PCB-to-KiCad Project (*.p2k)")
        if not path:
            return
        try:
            data = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", str(exc))
            return

        # DEBUG: při ladění nenačítáme cesty z projektu ani nepřeskenováváme
        # knihovny – používáme pouze uživatelské knihovny načtené při startu.
        # fp_paths = settings.get("footprint_paths", [])
        # sym_paths = settings.get("symbol_paths", [])
        # if fp_paths:
        #     self._library.set_footprint_paths(fp_paths)
        # if sym_paths:
        #     self._library.set_symbol_paths(sym_paths)
        # self._library.scan()
        # self._lib_browser.populate()

        self._restore_project_state(data)

        self._project_path = path
        self._undo_stack.clear()
        self._status.showMessage(f"Opened: {path}")

    def _on_save_project(self) -> None:
        if not self._project_path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Project", "project.p2k", "PCB-to-KiCad Project (*.p2k)")
            if not path:
                return
            self._project_path = path

        comps = [fp.to_dict() for fp in self._footprints]
        wire_data = [w.to_dict() for w in self._wires]
        junction_data = [j.to_dict() for j in self._junctions]
        top_img = ""
        bot_img = ""
        try:
            top_img = str(getattr(self._image_engine.top(), '_source_path', '')) or ""
            bot_img = str(getattr(self._image_engine.bottom(), '_source_path', '')) or ""
        except Exception:
            pass

        try:
            save_project(
                self._project_path,
                footprint_paths=[str(p) for p in self._library.footprint_paths],
                symbol_paths=[str(p) for p in self._library.symbol_paths],
                pixels_per_mm=self._coord.pixels_per_mm,
                origin_offset=self._coord.origin_offset_mm,
                top_image=top_img,
                bottom_image=bot_img,
                components=comps,
                wires=wire_data,
                junctions=junction_data,
            )
            self._status.showMessage(f"Saved: {self._project_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    # ------------------------------------------------------------------
    # Photo slots
    # ------------------------------------------------------------------

    def _image_filter(self) -> str:
        return "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.tif)"

    def _on_load_top(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Top Photo", "", self._image_filter())
        if path:
            before_state = self._capture_project_state()
            if self._image_engine.load_top(path):
                self._status.showMessage(f"Top layer: {Path(path).name}")
                self._view.fitInView(
                    self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
                self._push_history_state("Load top photo", before_state)
            else:
                QMessageBox.warning(self, "Error", f"Failed to load: {path}")

    def _on_load_bottom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Bottom Photo", "", self._image_filter())
        if path:
            before_state = self._capture_project_state()
            if self._image_engine.load_bottom(path):
                self._status.showMessage(f"Bottom layer: {Path(path).name}")
                self._push_history_state("Load bottom photo", before_state)
            else:
                QMessageBox.warning(self, "Error", f"Failed to load: {path}")

    # ------------------------------------------------------------------
    # Component placement
    # ------------------------------------------------------------------

    def _on_add_footprint(self, full_name: str) -> None:
        """Place a real footprint on the canvas at the viewport centre."""
        before_state = self._capture_project_state()
        fp_data = self._library.parse_footprint(full_name)
        info = self._library.get_footprint(full_name)
        if not info:
            QMessageBox.warning(self, "Error", f"Footprint not found: {full_name}")
            return

        centre = self._view.mapToScene(self._view.viewport().rect().center())

        # Auto-increment reference
        prefix = self._guess_prefix(info.library)
        num = sum(1 for f in self._footprints
                  if f.reference.startswith(prefix)) + 1

        item = FootprintItem(
            footprint_data=fp_data,
            footprint_lib=info.library,
            footprint_name=info.name,
            reference=f"{prefix}{num}",
            value=info.name,
            pixels_per_mm=self._coord.pixels_per_mm,
        )
        item.setPos(centre)
        self._attach_footprint_signals(item)
        self._scene.addItem(item)
        self._footprints.append(item)
        self._sync_footprint_visibility(item)
        self._comp_list.refresh(self._footprints)
        self._props.set_component(item)
        self._status.showMessage(f"Placed {item.reference} ({info.full_name})")
        self._push_history_state("Place footprint", before_state)

    def _on_place_from_browser(self) -> None:
        """Toolbar action: place whatever is selected in the footprint browser."""
        name = self._lib_browser._selected_fp_name()
        if name:
            self._on_add_footprint(name)
        else:
            self._status.showMessage("Select a footprint in the library browser first.")

    @staticmethod
    def _guess_prefix(library: str) -> str:
        ll = library.lower()
        if "resistor" in ll:
            return "R"
        if "capacitor" in ll:
            return "C"
        if "inductor" in ll:
            return "L"
        if "diode" in ll or "led" in ll:
            return "D"
        if "connector" in ll:
            return "J"
        if "crystal" in ll or "oscillator" in ll:
            return "Y"
        if "transistor" in ll:
            return "Q"
        return "U"

    # ------------------------------------------------------------------
    # Symbol linking
    # ------------------------------------------------------------------

    def _on_link_symbol(self) -> None:
        fp = self._selected_footprint()
        if not fp:
            self._status.showMessage("Select a component first.")
            return
        before_state = self._capture_project_state()
        dlg = SymbolBrowserDialog(self._library, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            sym_name = dlg.selected_symbol()
            if sym_name:
                parts = sym_name.split(":", 1)
                fp.symbol_lib = parts[0]
                fp.symbol_name = parts[1] if len(parts) > 1 else parts[0]
                # Reset pin_map when symbol changes
                fp.pin_map = {}
                self._props.set_component(fp)
                self._comp_list.refresh(self._footprints)
                self._status.showMessage(
                    f"Linked {fp.reference} → {sym_name}")
                self._push_history_state("Link symbol", before_state)
                # Auto-open pin mapping dialog so user can assign pads
                self._on_pin_pad_mapping()

    # ------------------------------------------------------------------
    # Component selection / deletion
    # ------------------------------------------------------------------

    def _on_select_component(self, uid: str) -> None:
        for fp in self._footprints:
            if fp.uid == uid:
                fp.setSelected(True)
                self._props.set_component(fp)
                self._view.centerOn(fp)
                return

    def _on_delete_component(self, uid: str) -> None:
        before_state = self._capture_project_state()
        if self._remove_footprint_by_uid(uid):
            self._push_history_state("Delete component", before_state)

    def _on_property_changed(self, before_component: dict[str, Any], after_component: dict[str, Any]) -> None:
        self._comp_list.refresh(self._footprints)
        self._rebuild_ratsnest()
        after_state = self._capture_project_state()
        before_state = deepcopy(after_state)
        if self._replace_component_state(before_state, before_component):
            label = before_component.get("reference") or after_component.get("reference") or "component"
            self._undo_stack.push(_ProjectSnapshotCommand(
                self,
                f"Edit {label}",
                before_state,
                after_state,
            ))

    def _on_scene_selection_changed(self) -> None:
        selected = [fp for fp in self._footprints if fp.isSelected()]
        if selected:
            self._props.set_component(selected[0])
        elif not self._scene.selectedItems():
            self._props.set_component(None)

    def _selected_footprint(self) -> Optional[FootprintItem]:
        for fp in self._footprints:
            if fp.isSelected():
                return fp
        return self._props._current

    def _remove_footprint_by_uid(self, uid: str) -> bool:
        for i, fp in enumerate(self._footprints):
            if fp.uid == uid:
                self._scene.removeItem(fp)
                self._footprints.pop(i)
                self._comp_list.refresh(self._footprints)
                if self._props._current is fp:
                    self._props.set_component(None)
                self._rebuild_ratsnest()
                self._status.showMessage(f"Removed {fp.reference}")
                return True
        return False

    def _remove_wire(self, wire: WireSegmentItem) -> bool:
        if wire in self._wires:
            self._wires.remove(wire)
            self._scene.removeItem(wire)
            return True
        return False

    def _remove_junction(self, junc: JunctionItem) -> bool:
        if junc in self._junctions:
            self._junctions.remove(junc)
            self._scene.removeItem(junc)
            return True
        return False

    def _on_footprint_move_finished(self, uid: str,
                                    old_x: float, old_y: float,
                                    _new_x: float, _new_y: float) -> None:
        if self._history_restoring:
            return
        after_state = self._capture_project_state()
        before_state = deepcopy(after_state)
        for comp in before_state.get("components", []):
            if comp.get("uid") == uid:
                comp["x_px"] = old_x
                comp["y_px"] = old_y
                self._undo_stack.push(_ProjectSnapshotCommand(
                    self,
                    f"Move {comp.get('reference', 'component')}",
                    before_state,
                    after_state,
                ))
                return

    # ------------------------------------------------------------------
    # Connect Nets mode
    # ------------------------------------------------------------------

    def _on_toggle_connect_mode(self, checked: bool) -> None:
        self._connect_mode = checked
        for fp in self._footprints:
            fp.connect_mode = checked
        if not checked and self._pending_pad:
            # Cancel any pending source pad
            src_uid, src_pad = self._pending_pad
            src_fp = next((f for f in self._footprints if f.uid == src_uid), None)
            if src_fp:
                src_fp.highlight_pad(src_pad, False)
            self._pending_pad = None
        if checked:
            self._status.showMessage(
                "Connect Nets ON — LEFT CLICK pad 1, then pad 2 to connect  |  RIGHT CLICK pad to set name manually")
        else:
            self._status.showMessage("Connect Nets mode OFF")

    def _on_pad_clicked(self, fp_uid: str, pad_number: str) -> None:
        """Two-click connect: first click selects source, second click connects."""
        fp = next((f for f in self._footprints if f.uid == fp_uid), None)
        if not fp:
            return

        if self._pending_pad is None:
            # ---- First click: select source pad ----
            self._pending_pad = (fp_uid, pad_number)
            fp.highlight_pad(pad_number, True)
            net = fp.pad_nets.get(pad_number, "")
            self._status.showMessage(
                f"Source: {fp.reference}[{pad_number}]"
                + (f" (net: {net})" if net else "")
                + "  — click target pad to connect  |  click same pad to cancel")
        else:
            src_uid, src_pad = self._pending_pad
            src_fp = next((f for f in self._footprints if f.uid == src_uid), None)

            # Clear highlight on source pad
            if src_fp:
                src_fp.highlight_pad(src_pad, False)
            self._pending_pad = None

            # Same pad clicked again → cancel
            if src_uid == fp_uid and src_pad == pad_number:
                self._status.showMessage("Connection cancelled.")
                return

            # Determine net name: prefer existing net, else auto-generate
            before_state = self._capture_project_state()
            src_net = src_fp.pad_nets.get(src_pad, "") if src_fp else ""
            dst_net = fp.pad_nets.get(pad_number, "")
            if src_net:
                net = src_net
            elif dst_net:
                net = dst_net
            else:
                self._net_counter += 1
                net = f"Net_{self._net_counter}"

            if src_fp:
                src_fp.set_pad_net(src_pad, net)
            fp.set_pad_net(pad_number, net)
            self._rebuild_ratsnest()
            src_ref = src_fp.reference if src_fp else "?"
            self._status.showMessage(
                f"Connected {src_ref}[{src_pad}] ↔ {fp.reference}[{pad_number}]  net: '{net}'")
            self._push_history_state("Connect pads", before_state)

    def _on_pad_right_clicked(self, fp_uid: str, pad_number: str) -> None:
        """Right-click: manually enter/clear net name for a single pad."""
        fp = next((f for f in self._footprints if f.uid == fp_uid), None)
        if not fp:
            return
        current_net = fp.pad_nets.get(pad_number, "")
        before_state = self._capture_project_state()
        net_name, ok = QInputDialog.getText(
            self,
            f"Set Net — {fp.reference} pad {pad_number}",
            "Net name (leave empty to clear):",
            text=current_net,
        )
        if ok:
            fp.set_pad_net(pad_number, net_name.strip())
            self._rebuild_ratsnest()
            self._status.showMessage(
                f"{fp.reference}[{pad_number}] → '{net_name.strip()}'"
                if net_name.strip() else
                f"{fp.reference}[{pad_number}] net cleared")
            self._push_history_state("Edit pad net", before_state)

    def _rebuild_ratsnest(self) -> None:
        """Redraw ratsnest lines connecting pads that share the same net."""
        for line in self._ratsnest_lines:
            self._scene.removeItem(line)
        self._ratsnest_lines.clear()

        # Build net -> [(fp, pad_number), ...] map
        net_map: dict[str, list[tuple[FootprintItem, str]]] = {}
        for fp in self._footprints:
            for pad_num, net_name in fp.pad_nets.items():
                if net_name:
                    net_map.setdefault(net_name, []).append((fp, pad_num))

        pen = QPen(cm.ratsnest(), 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        for net_name, pads in net_map.items():
            if len(pads) < 2:
                continue
            for i in range(len(pads) - 1):
                fp_a, pad_a = pads[i]
                fp_b, pad_b = pads[i + 1]
                pos_a = fp_a.pad_scene_pos(pad_a)
                pos_b = fp_b.pad_scene_pos(pad_b)
                if pos_a and pos_b:
                    line = QGraphicsLineItem(pos_a.x(), pos_a.y(), pos_b.x(), pos_b.y())
                    line.setPen(pen)
                    line.setZValue(-1)
                    self._scene.addItem(line)
                    self._ratsnest_lines.append(line)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _on_export_project(self) -> None:
        if not self._footprints:
            QMessageBox.information(
                self, "Export", "No components placed. Add at least one footprint.")
            return

        directory = QFileDialog.getExistingDirectory(self, "Choose Export Folder")
        if not directory:
            return

        project_name = Path(directory).name or "pcb_project"

        try:
            mgr = KiCadProjectManager(self._library, self._coord)
            wire_data = [w.to_dict() for w in self._wires]
            out = mgr.export(self._footprints, directory, project_name,
                             wire_data=wire_data)
            self._status.showMessage(f"Exported KiCad 9 project → {out}")
            QMessageBox.information(
                self, "Export Complete",
                f"KiCad 9 project exported:\n\n"
                f"  {out / (project_name + '.kicad_pro')}\n"
                f"  {out / (project_name + '.kicad_pcb')}\n"
                f"  {out / (project_name + '.kicad_sch')}\n\n"
                f"Open the .kicad_pro in KiCad 9 to continue.")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_settings(self) -> None:
        dlg = PathSettingsDialog(self._library, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._library.set_footprint_paths(dlg.footprint_paths())
            self._library.set_symbol_paths(dlg.symbol_paths())
            self._status.showMessage("Scanning libraries…")
            self._lib_browser._fp_tree.clear()
            self._lib_browser._sym_tree.clear()
            # Restart background scan
            if self._scan_thread.isRunning():
                self._scan_thread.quit()
                self._scan_thread.wait(2000)
            self._scan_worker = _ScanWorker(self._library)
            self._scan_worker.moveToThread(self._scan_thread)
            self._scan_thread.started.connect(self._scan_worker.run)
            self._scan_worker.finished.connect(self._on_scan_finished)
            self._scan_worker.finished.connect(self._scan_thread.quit)
            self._scan_thread.start()
