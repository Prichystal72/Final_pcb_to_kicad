"""High-level export orchestrator for KiCad project generation.

This module bridges the UI world (FootprintItems on a canvas) and the
export world (KicadProjectWriter).  It:

1. Collects all placed footprint items and their schematic symbol bindings
2. Resolves footprint .kicad_mod and symbol .kicad_sym S-expression data
   from the ``LibraryBridge``
3. Builds ``ComponentPlacement`` objects with mm-coordinates and rotation
4. Collects wire and junction data for the schematic
5. Delegates to ``KicadProjectWriter`` which writes the final .kicad_pro,
   .kicad_pcb, and .kicad_sch files
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from library_bridge import LibraryBridge
from kicad_generator import ComponentPlacement, KicadProjectWriter, WirePlacement

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
        wire_data: list[dict[str, Any]] | None = None,
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
                pin_map=data.get("pin_map", {}),
            )
            placements.append(cp)

        # Build pad pixel-position lookup: (x_px, y_px, reference, pad_num, uid)
        pad_lookup: list[tuple[float, float, str, str, str]] = []
        for fp in footprints:
            ref = fp.reference
            uid = fp.uid
            for pn in fp.pad_numbers():
                spos = fp.pad_scene_pos(pn)
                if spos is not None:
                    pad_lookup.append((spos.x(), spos.y(), ref, pn, uid))

        def _find_pad(x_px: float, y_px: float) -> tuple[str, str, str]:
            """Return (reference, pad_number, uid) of nearest pad, or ('','','')."""
            best_d = 3.0 * ppm  # 3 mm tolerance in pixels
            ref, pad, uid = "", "", ""
            for px, py, r, p, u in pad_lookup:
                d = ((px - x_px) ** 2 + (py - y_px) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    ref, pad, uid = r, p, u
            return ref, pad, uid

        # Convert wire data from pixel coords to mm, tagging pad connections.
        # Only tag TERMINAL wire endpoints (degree != 2 in wire graph)
        # to avoid false pad matches at intermediate points that pass
        # near components.
        from collections import Counter
        endpoint_degree: Counter[tuple[float, float]] = Counter()
        for wd in (wire_data or []):
            k1 = (round(wd.get("x1", 0.0), 1), round(wd.get("y1", 0.0), 1))
            k2 = (round(wd.get("x2", 0.0), 1), round(wd.get("y2", 0.0), 1))
            endpoint_degree[k1] += 1
            endpoint_degree[k2] += 1

        wire_placements: list[WirePlacement] = []
        for wd in (wire_data or []):
            x1_px = wd.get("x1", 0.0)
            y1_px = wd.get("y1", 0.0)
            x2_px = wd.get("x2", 0.0)
            y2_px = wd.get("y2", 0.0)

            k1 = (round(x1_px, 1), round(y1_px, 1))
            k2 = (round(x2_px, 1), round(y2_px, 1))

            # Degree-2 points are intermediate chain joints — skip pad tagging
            if endpoint_degree[k1] != 2:
                s_ref, s_pad, s_uid = _find_pad(x1_px, y1_px)
            else:
                s_ref, s_pad, s_uid = "", "", ""
            if endpoint_degree[k2] != 2:
                e_ref, e_pad, e_uid = _find_pad(x2_px, y2_px)
            else:
                e_ref, e_pad, e_uid = "", "", ""

            wp = WirePlacement(
                x1_mm=x1_px / ppm,
                y1_mm=y1_px / ppm,
                x2_mm=x2_px / ppm,
                y2_mm=y2_px / ppm,
                net_name=wd.get("net_name", ""),
                start_ref=s_ref,
                start_pad=s_pad,
                end_ref=e_ref,
                end_pad=e_pad,
                start_uid=s_uid,
                end_uid=e_uid,
            )
            wire_placements.append(wp)

        out_dir = Path(output_dir)
        writer = KicadProjectWriter()
        writer.generate(
            project_name=project_name,
            output_dir=out_dir,
            components=placements,
            board_w=board_w_mm,
            board_h=board_h_mm,
            wires=wire_placements,
        )
        return out_dir
