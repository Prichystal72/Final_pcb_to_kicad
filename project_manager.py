"""JSON project save / load for the PCB-to-KiCad workspace.

Project file format (.p2k):
    A plain JSON file containing:
    - version          – format version string (currently "0.4.0")
    - settings         – library paths, calibration px/mm, board origin offset
    - images           – top/bottom photo paths plus display settings
                         (visibility, opacity, brightness, mirror, offsets, scales)
    - image_params     – per-layer import editor pipeline parameters
                         (rotation, distortion, crop, filters, …)
    - components       – list of placed footprints with position, rotation, symbol
    - wires            – copper trace segments drawn by the user
    - junctions        – wire junction dots
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
    wires: list[dict[str, Any]] | None = None,
    junctions: list[dict[str, Any]] | None = None,
    image_params: dict[str, Any] | None = None,
    image_display: dict[str, Any] | None = None,
) -> Path:
    """Serialize the full workspace state to a JSON .p2k project file.

    Args:
        path:            Destination file path.
        footprint_paths: Directories containing .kicad_mod footprint libraries.
        symbol_paths:    Directories containing .kicad_sym symbol libraries.
        pixels_per_mm:   Calibration ratio – how many image pixels equal 1 mm.
        origin_offset:   Board origin offset in mm (x, y).
        top_image:       File path of the top layer photo.
        bottom_image:    File path of the bottom layer photo.
        components:      Serialised footprint placement dicts.
        wires:           Serialised wire segment dicts.
        junctions:       Serialised junction dicts.
        image_params:    Per-layer import editor parameters (rotation, crop, …).
        image_display:   Per-layer display settings (opacity, brightness, offsets, …).
    """
    images_dict: dict[str, Any] = {
        "top": top_image,
        "bottom": bottom_image,
    }
    if image_display:
        images_dict.update(image_display)
    data = {
        "version": "0.4.0",
        "settings": {
            "footprint_paths": footprint_paths,
            "symbol_paths": symbol_paths,
            "pixels_per_mm": pixels_per_mm,
            "origin_offset_mm": list(origin_offset),
        },
        "images": images_dict,
        "image_params": image_params or {},
        "components": components,
        "wires": wires or [],
        "junctions": junctions or [],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_project(path: str | Path) -> dict[str, Any]:
    """Read a JSON project file and return the data dict."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))
