"""Centralised colour-scheme manager.

Loads ``color_schemes.json`` from the application directory and exposes the
current palette to all rendering code via module-level functions.

Typical usage::

    from color_manager import cm

    pen = QPen(cm.wire(), 2)          # current wire colour
    scene.setBackgroundBrush(cm.background())
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor

_DIR = Path(__file__).resolve().parent
_JSON_PATH = _DIR / "color_schemes.json"


def _rgba(arr: list[int]) -> QColor:
    """Convert [R, G, B, A] list to QColor."""
    return QColor(arr[0], arr[1], arr[2], arr[3] if len(arr) > 3 else 255)


class ColorManager:
    """Manages colour schemes loaded from JSON."""

    def __init__(self) -> None:
        self._schemes: dict[str, dict[str, list[int]]] = {}
        self._current_name: str = ""
        self._current: dict[str, list[int]] = {}
        self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        self._schemes = data.get("schemes", {})
        default = data.get("default", "")

        if not self._schemes:
            # Absolute fallback – dark scheme hard-coded
            self._schemes["Dark (KiCad)"] = {
                "background": [34, 34, 34, 255],
                "wire": [0, 200, 0, 230],
                "wire_selected": [255, 120, 0, 255],
                "wire_preview": [100, 255, 100, 140],
                "junction": [0, 255, 0, 230],
                "ratsnest": [255, 220, 0, 170],
                "pad_smd": [200, 50, 50, 180],
                "pad_tht": [50, 80, 200, 180],
                "pad_drill": [40, 40, 40, 210],
                "pad_net": [50, 220, 80, 220],
                "pad_hover": [255, 200, 0, 200],
                "pad_pending": [255, 80, 220, 230],
                "silkscreen": [0, 200, 200, 200],
                "fab": [200, 200, 50, 140],
                "courtyard": [200, 0, 200, 120],
                "border_normal": [0, 200, 0, 200],
                "border_selected": [255, 120, 0, 230],
                "text_label": [230, 230, 230, 240],
                "grid": [60, 60, 60, 100],
            }
            default = "Dark (KiCad)"

        self.set_scheme(default if default in self._schemes
                        else next(iter(self._schemes)))

    def save_default(self, name: str) -> None:
        """Persist *name* as the default scheme in the JSON file."""
        try:
            with open(_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data["default"] = name
        data.setdefault("schemes", {}).update(self._schemes)
        with open(_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # scheme selection
    # ------------------------------------------------------------------

    def scheme_names(self) -> list[str]:
        return list(self._schemes.keys())

    def current_scheme_name(self) -> str:
        return self._current_name

    def set_scheme(self, name: str) -> None:
        if name not in self._schemes:
            return
        self._current_name = name
        self._current = self._schemes[name]

    def scheme_data(self, name: str) -> dict[str, list[int]]:
        return dict(self._schemes.get(name, {}))

    def set_color(self, key: str, rgba: list[int]) -> None:
        """Override a single colour key in the current scheme."""
        self._current[key] = rgba
        self._schemes[self._current_name] = dict(self._current)

    # ------------------------------------------------------------------
    # colour getters  (return QColor)
    # ------------------------------------------------------------------

    def _c(self, key: str, fallback: list[int] | None = None) -> QColor:
        arr = self._current.get(key, fallback or [200, 200, 200, 255])
        return _rgba(arr)

    def background(self) -> QColor:   return self._c("background", [34, 34, 34, 255])
    def grid(self) -> QColor:         return self._c("grid", [60, 60, 60, 100])
    def wire(self) -> QColor:         return self._c("wire", [0, 200, 0, 230])
    def wire_selected(self) -> QColor:return self._c("wire_selected", [255, 120, 0, 255])
    def wire_preview(self) -> QColor: return self._c("wire_preview", [100, 255, 100, 140])
    def junction(self) -> QColor:     return self._c("junction", [0, 255, 0, 230])
    def ratsnest(self) -> QColor:     return self._c("ratsnest", [255, 220, 0, 170])
    def pad_smd(self) -> QColor:      return self._c("pad_smd", [200, 50, 50, 180])
    def pad_tht(self) -> QColor:      return self._c("pad_tht", [50, 80, 200, 180])
    def pad_drill(self) -> QColor:    return self._c("pad_drill", [40, 40, 40, 210])
    def pad_net(self) -> QColor:      return self._c("pad_net", [50, 220, 80, 220])
    def pad_hover(self) -> QColor:    return self._c("pad_hover", [255, 200, 0, 200])
    def pad_pending(self) -> QColor:  return self._c("pad_pending", [255, 80, 220, 230])
    def silkscreen(self) -> QColor:   return self._c("silkscreen", [0, 200, 200, 200])
    def fab(self) -> QColor:          return self._c("fab", [200, 200, 50, 140])
    def courtyard(self) -> QColor:    return self._c("courtyard", [200, 0, 200, 120])
    def border_normal(self) -> QColor:return self._c("border_normal", [0, 200, 0, 200])
    def border_selected(self) -> QColor:return self._c("border_selected", [255, 120, 0, 230])
    def text_label(self) -> QColor:   return self._c("text_label", [230, 230, 230, 240])

    # UI window colours
    def ui_bg(self) -> QColor:        return self._c("ui_bg", [45, 45, 45, 255])
    def ui_text(self) -> QColor:      return self._c("ui_text", [220, 220, 220, 255])
    def ui_accent(self) -> QColor:    return self._c("ui_accent", [60, 120, 200, 255])
    def ui_border(self) -> QColor:    return self._c("ui_border", [70, 70, 70, 255])
    def ui_input(self) -> QColor:     return self._c("ui_input", [55, 55, 55, 255])
    def ui_hover(self) -> QColor:     return self._c("ui_hover", [65, 65, 65, 255])

    # raw RGBA list for a key
    def raw(self, key: str) -> list[int]:
        return list(self._current.get(key, [200, 200, 200, 255]))

    # ------------------------------------------------------------------
    # Qt stylesheet for the whole application window
    # ------------------------------------------------------------------

    def stylesheet(self) -> str:
        bg = self.raw("ui_bg") if "ui_bg" in self._current else [45, 45, 45, 255]
        tx = self.raw("ui_text") if "ui_text" in self._current else [220, 220, 220, 255]
        ac = self.raw("ui_accent") if "ui_accent" in self._current else [60, 120, 200, 255]
        bd = self.raw("ui_border") if "ui_border" in self._current else [70, 70, 70, 255]
        inp = self.raw("ui_input") if "ui_input" in self._current else [55, 55, 55, 255]
        hv = self.raw("ui_hover") if "ui_hover" in self._current else [65, 65, 65, 255]

        def rgb(c): return f"rgb({c[0]},{c[1]},{c[2]})"
        def rgba(c): return f"rgba({c[0]},{c[1]},{c[2]},{c[3]})"

        return f"""
/* ---- Global ---- */
QMainWindow, QDialog {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
}}
QWidget {{
    color: {rgb(tx)};
}}
QDockWidget {{
    color: {rgb(tx)};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background-color: {rgb([bg[0]+10, bg[1]+10, bg[2]+10])};
    padding: 4px;
    border: 1px solid {rgb(bd)};
}}
/* ---- Menu bar ---- */
QMenuBar {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
    border-bottom: 1px solid {rgb(bd)};
}}
QMenuBar::item:selected {{
    background-color: {rgb(hv)};
}}
QMenu {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
}}
QMenu::item:selected {{
    background-color: {rgb(ac)};
}}
QMenu::separator {{
    height: 1px;
    background: {rgb(bd)};
    margin: 4px 8px;
}}
/* ---- Toolbar ---- */
QToolBar {{
    background-color: {rgb(bg)};
    border-bottom: 1px solid {rgb(bd)};
    spacing: 4px;
}}
QToolButton {{
    background-color: transparent;
    color: {rgb(tx)};
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 3px 6px;
}}
QToolButton:hover {{
    background-color: {rgb(hv)};
    border: 1px solid {rgb(bd)};
}}
QToolButton:checked {{
    background-color: {rgb(ac)};
}}
/* ---- Panels / groups ---- */
QGroupBox {{
    border: 1px solid {rgb(bd)};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 12px;
    color: {rgb(tx)};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
/* ---- Inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {{
    background-color: {rgb(inp)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
    border-radius: 3px;
    padding: 2px 4px;
}}
QComboBox::drop-down {{
    border-left: 1px solid {rgb(bd)};
}}
QComboBox QAbstractItemView {{
    background-color: {rgb(inp)};
    color: {rgb(tx)};
    selection-background-color: {rgb(ac)};
}}
/* ---- Lists / trees ---- */
QListWidget, QTreeWidget {{
    background-color: {rgb(inp)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {rgb(ac)};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {rgb(hv)};
}}
QHeaderView::section {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
    padding: 3px;
}}
/* ---- Tabs ---- */
QTabWidget::pane {{
    border: 1px solid {rgb(bd)};
}}
QTabBar::tab {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
    padding: 4px 10px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
}}
QTabBar::tab:selected {{
    background-color: {rgb(inp)};
}}
QTabBar::tab:hover {{
    background-color: {rgb(hv)};
}}
/* ---- Buttons ---- */
QPushButton {{
    background-color: {rgb(inp)};
    color: {rgb(tx)};
    border: 1px solid {rgb(bd)};
    border-radius: 3px;
    padding: 4px 12px;
}}
QPushButton:hover {{
    background-color: {rgb(hv)};
}}
QPushButton:pressed {{
    background-color: {rgb(ac)};
}}
/* ---- Checkboxes / Labels ---- */
QCheckBox {{
    color: {rgb(tx)};
}}
QLabel {{
    color: {rgb(tx)};
}}
/* ---- Scrollbars ---- */
QScrollBar:vertical {{
    background: {rgb(bg)};
    width: 12px;
}}
QScrollBar::handle:vertical {{
    background: {rgb(bd)};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {rgb(bg)};
    height: 12px;
}}
QScrollBar::handle:horizontal {{
    background: {rgb(bd)};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
/* ---- Status bar ---- */
QStatusBar {{
    background-color: {rgb(bg)};
    color: {rgb(tx)};
    border-top: 1px solid {rgb(bd)};
}}
/* ---- Slider ---- */
QSlider::groove:horizontal {{
    background: {rgb(bd)};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {rgb(ac)};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
"""


# Module-level singleton
cm = ColorManager()
