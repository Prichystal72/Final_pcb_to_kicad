"""JSON project save / load for the PCB-to-KiCad workspace.

The internal project format stores:
- Library path settings
- Calibration / coordinate system data
- Image file references (top / bottom photos)
- All placed components with their footprint, symbol binding, and position
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_project(
    path: str | Path,
    *,
    footprint_paths: list[str],
    symbol_paths: list[str],
    pixels_per_mm: float,
    origin_offset: tuple[float, float],
    top_image: str,
    bottom_image: str,
    components: list[dict[str, Any]],
) -> Path:
    """Serialize the workspace to a JSON file."""
    data = {
        "version": "0.2.0",
        "settings": {
            "footprint_paths": footprint_paths,
            "symbol_paths": symbol_paths,
            "pixels_per_mm": pixels_per_mm,
            "origin_offset_mm": list(origin_offset),
        },
        "images": {
            "top": top_image,
            "bottom": bottom_image,
        },
        "components": components,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_project(path: str | Path) -> dict[str, Any]:
    """Read a JSON project file and return the data dict."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))
