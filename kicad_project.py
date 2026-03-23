"""High-level export orchestrator.

Resolves footprint + symbol S-expression data from LibraryBridge,
builds ComponentPlacement objects, and delegates to KicadProjectWriter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from library_bridge import LibraryBridge
from kicad_generator import ComponentPlacement, KicadProjectWriter

if TYPE_CHECKING:
    from coordinate_system import CoordinateSystem
    from footprint_item import FootprintItem


class KiCadProjectManager:
    """Coordinates the full export pipeline."""

    def __init__(self, bridge: LibraryBridge, coord: "CoordinateSystem") -> None:
        self.bridge = bridge
        self.coord = coord

    def export(
        self,
        footprints: list["FootprintItem"],
        output_dir: str | Path,
        project_name: str,
        board_w_mm: float = 100.0,
        board_h_mm: float = 100.0,
    ) -> Path:
        """Export a full KiCad 9 project.

        Parameters
        ----------
        footprints : list of FootprintItem
            Placed footprint items from the canvas.
        output_dir : path
            Destination directory.
        project_name : str
            Base name for the .kicad_pro / .kicad_pcb / .kicad_sch files.

        Returns
        -------
        Path
            The output directory (same as *output_dir*).
        """
        ppm = self.coord.pixels_per_mm
        placements: list[ComponentPlacement] = []

        for fp in footprints:
            data = fp.to_dict()
            fp_lib = data.get("footprint_lib", "")
            fp_name = data.get("footprint_name", "")
            sym_lib = data.get("symbol_lib", "")
            sym_name = data.get("symbol_name", "")

            fp_sexpr = ""
            if fp_lib and fp_name:
                fp_sexpr = self.bridge.read_footprint_sexpr(fp_lib, fp_name)

            sym_sexpr = ""
            if sym_lib and sym_name:
                sym_sexpr = self.bridge.read_symbol_sexpr(sym_lib, sym_name)

            x_mm = data.get("x_px", 0.0) / ppm
            y_mm = data.get("y_px", 0.0) / ppm

            cp = ComponentPlacement(
                reference=data.get("reference", "REF**"),
                value=data.get("value", "VAL**"),
                footprint_lib=fp_lib,
                footprint_name=fp_name,
                symbol_lib=sym_lib,
                symbol_name=sym_name,
                x_mm=x_mm,
                y_mm=y_mm,
                rotation=data.get("rotation", 0.0),
                layer=data.get("layer", "F.Cu"),
                footprint_sexpr=fp_sexpr,
                symbol_sexpr=sym_sexpr,
                uid=data.get("uid", ""),
                pad_nets=data.get("pad_nets", {}),
            )
            placements.append(cp)

        out_dir = Path(output_dir)
        writer = KicadProjectWriter()
        writer.generate(
            project_name=project_name,
            output_dir=out_dir,
            components=placements,
            board_w=board_w_mm,
            board_h=board_h_mm,
        )
        return out_dir
