"""Generate KiCad 9 project files (.kicad_pro, .kicad_pcb, .kicad_sch).

Produces real S-expression output with embedded footprint geometry and
symbol definitions so that projects open cleanly in KiCad 9.
"""

from __future__ import annotations

import json
import uuid
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from kicad_parser import parse_sexpr, serialize_sexpr, find_node, find_all, find_value


@dataclass
class ComponentPlacement:
    """One component destined for PCB and schematic."""
    reference: str
    value: str
    footprint_lib: str
    footprint_name: str
    symbol_lib: str = ""
    symbol_name: str = ""
    x_mm: float = 0.0
    y_mm: float = 0.0
    rotation: float = 0.0
    layer: str = "F.Cu"
    footprint_sexpr: str = ""
    symbol_sexpr: str = ""
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    # pad_number -> net_name
    pad_nets: dict[str, str] = field(default_factory=dict)


@dataclass
class WirePlacement:
    """A single wire segment for export."""
    x1_mm: float = 0.0
    y1_mm: float = 0.0
    x2_mm: float = 0.0
    y2_mm: float = 0.0
    net_name: str = ""
    start_ref: str = ""   # component reference at start endpoint
    start_pad: str = ""   # pad number at start endpoint
    end_ref: str = ""     # component reference at end endpoint
    end_pad: str = ""     # pad number at end endpoint


def _uuid() -> str:
    return str(uuid.uuid4())


def _validate_brackets(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


class KicadPcbWriter:
    """Produces a .kicad_pcb file with real footprint geometry."""

    def __init__(self, board_w_mm: float = 100.0, board_h_mm: float = 100.0):
        self.board_w = board_w_mm
        self.board_h = board_h_mm

    def generate(self, components: list[ComponentPlacement],
                 output_path: Path) -> tuple[float, float]:
        """Generate PCB file. Returns (dx, dy) centering offset applied."""
        lines: list[str] = []
        lines.append('(kicad_pcb\n')
        lines.append('  (version 20241229)\n')
        lines.append('  (generator "pcb_to_kicad")\n')
        lines.append('  (generator_version "9.0")\n')
        lines.append(f'  (general (thickness 1.6) (legacy_teardrops no))\n')
        lines.append('  (paper "A4")\n')
        self._write_layers(lines)
        self._write_setup(lines)
        self._write_nets(lines, components)

        # Center components on the A4 sheet (297 x 210 mm landscape)
        dx, dy = 0.0, 0.0
        if components:
            min_x = min(c.x_mm for c in components)
            max_x = max(c.x_mm for c in components)
            min_y = min(c.y_mm for c in components)
            max_y = max(c.y_mm for c in components)
            cx = (min_x + max_x) / 2.0
            cy = (min_y + max_y) / 2.0
            sheet_cx, sheet_cy = 297.0 / 2.0, 210.0 / 2.0
            dx = sheet_cx - cx
            dy = sheet_cy - cy
            for c in components:
                c.x_mm += dx
                c.y_mm += dy

        # Qt canvas uses clockwise-positive rotation,
        # KiCad uses counter-clockwise-positive → negate.
        for c in components:
            c.rotation = (360 - c.rotation) % 360

        for comp in components:
            self._write_footprint(lines, comp, components)

        lines.append(')\n')

        text = "".join(lines)
        if not _validate_brackets(text):
            raise ValueError("Generated .kicad_pcb has mismatched brackets")
        output_path.write_text(text, encoding="utf-8")
        return (dx, dy)

    def _write_layers(self, lines: list[str]) -> None:
        lines.append('  (layers\n')
        layer_defs = [
            (0, "F.Cu", "signal"),
            (31, "B.Cu", "signal"),
            (32, "B.Adhes", "user", "B.Adhesive"),
            (33, "F.Adhes", "user", "F.Adhesive"),
            (34, "B.Paste", "user"),
            (35, "F.Paste", "user"),
            (36, "B.SilkS", "user", "B.Silkscreen"),
            (37, "F.SilkS", "user", "F.Silkscreen"),
            (38, "B.Mask", "user"),
            (39, "F.Mask", "user"),
            (40, "Dwgs.User", "user", "User.Drawings"),
            (41, "Cmts.User", "user", "User.Comments"),
            (42, "Eco1.User", "user", "User.Eco1"),
            (43, "Eco2.User", "user", "User.Eco2"),
            (44, "Edge.Cuts", "user"),
            (45, "Margin", "user"),
            (46, "B.CrtYd", "user", "B.Courtyard"),
            (47, "F.CrtYd", "user", "F.Courtyard"),
            (48, "B.Fab", "user"),
            (49, "F.Fab", "user"),
        ]
        for ld in layer_defs:
            idx, name, ltype = ld[0], ld[1], ld[2]
            alias = ld[3] if len(ld) > 3 else ""
            if alias:
                lines.append(f'    ({idx} "{name}" {ltype} "{alias}")\n')
            else:
                lines.append(f'    ({idx} "{name}" {ltype})\n')
        lines.append('  )\n')

    def _write_setup(self, lines: list[str]) -> None:
        lines.append('  (setup\n')
        lines.append('    (pad_to_mask_clearance 0)\n')
        lines.append('    (allow_soldermask_bridges_in_footprints no)\n')
        lines.append('    (pcbplotparams\n')
        lines.append('      (layerselection 0x00010fc_ffffffff)\n')
        lines.append('      (plot_on_all_layers_selection 0x0000000_00000000)\n')
        lines.append('    )\n')
        lines.append('  )\n')

    def _write_nets(self, lines: list[str], components: list[ComponentPlacement]) -> None:
        net_names = sorted({net for comp in components for net in comp.pad_nets.values() if net})
        lines.append('  (net 0 "")\n')
        for idx, name in enumerate(net_names, start=1):
            lines.append(f'  (net {idx} "{name}")\n')

    @staticmethod
    def _build_net_index(components: list["ComponentPlacement"]) -> dict[str, int]:
        names = sorted({net for comp in components for net in comp.pad_nets.values() if net})
        return {name: idx for idx, name in enumerate(names, start=1)}

    def _write_footprint(self, lines: list[str], comp: ComponentPlacement,
                         components: list[ComponentPlacement]) -> None:
        if comp.footprint_sexpr.strip():
            self._write_footprint_from_sexpr(lines, comp, components)
        else:
            self._write_footprint_placeholder(lines, comp, components)

    def _write_footprint_from_sexpr(self, lines: list[str], comp: ComponentPlacement,
                                      components: list[ComponentPlacement]) -> None:
        """Embed a real .kicad_mod footprint with adjusted position/reference/value."""
        text = comp.footprint_sexpr.strip()
        tree = parse_sexpr(text)
        if not tree or not isinstance(tree, list) or tree[0] != "footprint":
            self._write_footprint_placeholder(lines, comp, components)
            return

        full_name = f"{comp.footprint_lib}:{comp.footprint_name}"
        tree[1] = full_name

        at_node = find_node(tree, "at")
        if at_node:
            while len(at_node) > 1:
                at_node.pop()
            at_node.extend([comp.x_mm, comp.y_mm])
            if abs(comp.rotation) > 0.01:
                at_node.append(comp.rotation)
        else:
            at_data = ["at", comp.x_mm, comp.y_mm]
            if abs(comp.rotation) > 0.01:
                at_data.append(comp.rotation)
            tree.insert(2, at_data)

        layer_node = find_node(tree, "layer")
        if layer_node:
            while len(layer_node) > 1:
                layer_node.pop()
            layer_node.append(comp.layer)

        for prop in find_all(tree, "property"):
            if len(prop) >= 3 and prop[1] == "Reference":
                prop[2] = comp.reference
            elif len(prop) >= 3 and prop[1] == "Value":
                prop[2] = comp.value

        for fp_text in find_all(tree, "fp_text"):
            if len(fp_text) >= 3:
                if fp_text[1] == "reference":
                    fp_text[2] = comp.reference
                elif fp_text[1] == "value":
                    fp_text[2] = comp.value

        if comp.pad_nets:
            net_idx = self._build_net_index(components)
            for pad_node in find_all(tree, "pad"):
                if len(pad_node) < 2:
                    continue
                pad_num = str(pad_node[1])
                net_name = comp.pad_nets.get(pad_num, "")
                if net_name:
                    for i, child in enumerate(pad_node):
                        if isinstance(child, list) and child and child[0] == "net":
                            pad_node.pop(i)
                            break
                    pad_node.append(["net", net_idx.get(net_name, 0), net_name])

        old_uuid = find_node(tree, "tstamp")
        if old_uuid:
            old_uuid.clear()
            old_uuid.extend(["tstamp", _uuid()])
        else:
            new_uuid = find_node(tree, "uuid")
            if new_uuid:
                while len(new_uuid) > 1:
                    new_uuid.pop()
                new_uuid.append(_uuid())

        serialized = serialize_sexpr(tree, indent=4)
        lines.append(f"  {serialized}\n")

    def _write_footprint_placeholder(self, lines: list[str], comp: ComponentPlacement,
                                     components: list[ComponentPlacement]) -> None:
        full_name = f"{comp.footprint_lib}:{comp.footprint_name}"
        u = _uuid()
        lines.append(f'  (footprint "{full_name}"\n')
        lines.append(f'    (layer "{comp.layer}")\n')
        lines.append(f'    (uuid "{u}")\n')
        lines.append(f'    (at {comp.x_mm:.4f} {comp.y_mm:.4f}')
        if abs(comp.rotation) > 0.01:
            lines.append(f' {comp.rotation:.1f}')
        lines.append(')\n')
        lines.append(f'    (property "Reference" "{comp.reference}" (at 0 -2 0) (layer "{comp.layer.replace("Cu","Fab")}") (uuid "{_uuid()}")\n')
        lines.append(f'      (effects (font (size 1 1) (thickness 0.15)))\n')
        lines.append(f'    )\n')
        lines.append(f'    (property "Value" "{comp.value}" (at 0 2 0) (layer "{comp.layer.replace("Cu","Fab")}") (uuid "{_uuid()}")\n')
        lines.append(f'      (effects (font (size 1 1) (thickness 0.15)))\n')
        lines.append(f'    )\n')
        lines.append(f'    (fp_line (start -1.5 -1) (end 1.5 -1) (stroke (width 0.12) (type solid)) (layer "{comp.layer.replace("Cu","SilkS")}") (uuid "{_uuid()}"))\n')
        lines.append(f'    (fp_line (start 1.5 -1) (end 1.5 1) (stroke (width 0.12) (type solid)) (layer "{comp.layer.replace("Cu","SilkS")}") (uuid "{_uuid()}"))\n')
        lines.append(f'    (fp_line (start 1.5 1) (end -1.5 1) (stroke (width 0.12) (type solid)) (layer "{comp.layer.replace("Cu","SilkS")}") (uuid "{_uuid()}"))\n')
        lines.append(f'    (fp_line (start -1.5 1) (end -1.5 -1) (stroke (width 0.12) (type solid)) (layer "{comp.layer.replace("Cu","SilkS")}") (uuid "{_uuid()}"))\n')
        net_idx = self._build_net_index(components)
        def _pad_net_clause(pad_num: str) -> str:
            net_name = comp.pad_nets.get(pad_num, "")
            if not net_name:
                return ""
            return f' (net {net_idx.get(net_name, 0)} "{net_name}")'
        lines.append(f'    (pad "1" smd rect (at -0.5 0) (size 0.6 0.8) (layers "{comp.layer}" "{comp.layer.replace("Cu","Paste")}" "{comp.layer.replace("Cu","Mask")}"){_pad_net_clause("1")} (uuid "{_uuid()}"))\n')
        lines.append(f'    (pad "2" smd rect (at 0.5 0) (size 0.6 0.8) (layers "{comp.layer}" "{comp.layer.replace("Cu","Paste")}" "{comp.layer.replace("Cu","Mask")}"){_pad_net_clause("2")} (uuid "{_uuid()}"))\n')
        lines.append( '  )\n')


class KicadSchWriter:
    """Produces a .kicad_sch with linked symbols and net labels."""

    def generate(self, components: list[ComponentPlacement],
                 output_path: Path,
                 wires: list[WirePlacement] | None = None) -> None:
        lines: list[str] = []
        lines.append('(kicad_sch\n')
        lines.append('  (version 20250114)\n')
        lines.append('  (generator "pcb_to_kicad")\n')
        lines.append('  (generator_version "9.0")\n')
        lines.append(f'  (uuid "{_uuid()}")\n')
        lines.append('  (paper "A4")\n')

        self._write_lib_symbols(lines, components)

        # Track pin scene positions for net label generation
        # net_name -> list of (scene_x, scene_y)
        pin_positions: dict[str, list[tuple[float, float]]] = {}
        # (reference, pad_number) -> (sch_x, sch_y) for wire endpoint lookup
        ref_pad_to_sch: dict[tuple[str, str], tuple[float, float]] = {}

        for comp in components:
            sx, sy = comp.x_mm, comp.y_mm
            # comp.rotation was negated for KiCad PCB by the PCB writer
            # ((360 - R) % 360).  Undo that to recover the original canvas
            # rotation, then add the schematic +90° offset.
            original_rot = (360 - comp.rotation) % 360
            rot = (round(original_rot / 90.0) * 90 + 90) % 360
            self._write_symbol_instance(lines, comp, sx, sy, rot)
            # Collect pin connection points for net labels
            self._collect_pin_positions(comp, sx, sy, rot, pin_positions)
            # Build (ref, pad) -> SCH pin position lookup
            self._build_ref_pad_lookup(comp, sx, sy, rot, ref_pad_to_sch)

        # Write net labels at each pin that has a net
        for net_name, positions in pin_positions.items():
            for px, py in positions:
                self._write_net_label(lines, net_name, px, py)

        # Write wires: for each segment, snap pad-connected endpoints
        # to the exact SCH pin position; leave free junction points as-is.
        for w in (wires or []):
            x1, y1 = w.x1_mm, w.y1_mm
            x2, y2 = w.x2_mm, w.y2_mm
            if w.start_ref and w.start_pad:
                pin = ref_pad_to_sch.get((w.start_ref, w.start_pad))
                if pin:
                    x1, y1 = pin
            if w.end_ref and w.end_pad:
                pin = ref_pad_to_sch.get((w.end_ref, w.end_pad))
                if pin:
                    x2, y2 = pin
            if math.hypot(x2 - x1, y2 - y1) > 0.01:
                self._write_wire(lines, WirePlacement(
                    x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2))

        lines.append(')\n')

        text = "".join(lines)
        if not _validate_brackets(text):
            raise ValueError("Generated .kicad_sch has mismatched brackets")
        output_path.write_text(text, encoding="utf-8")

    def _write_lib_symbols(self, lines: list[str], components: list[ComponentPlacement]) -> None:
        lines.append('  (lib_symbols\n')
        seen: set[str] = set()
        for comp in components:
            if not comp.symbol_lib or not comp.symbol_name:
                key = f"_placeholder:{comp.reference}"
                if key not in seen:
                    seen.add(key)
                    self._write_placeholder_lib_symbol(lines, comp)
                continue

            lib_key = f"{comp.symbol_lib}:{comp.symbol_name}"
            if lib_key in seen:
                continue
            seen.add(lib_key)

            if comp.symbol_sexpr.strip():
                self._embed_symbol_sexpr(lines, comp)
            else:
                self._write_placeholder_lib_symbol(lines, comp)
        lines.append('  )\n')

    def _embed_symbol_sexpr(self, lines: list[str], comp: ComponentPlacement) -> None:
        text = comp.symbol_sexpr.strip()
        tree = parse_sexpr(text)
        if tree and isinstance(tree, list) and tree[0] == "symbol":
            full_name = f"{comp.symbol_lib}:{comp.symbol_name}"
            tree[1] = full_name
            serialized = serialize_sexpr(tree, indent=4)
            lines.append(f"    {serialized}\n")
        else:
            self._write_placeholder_lib_symbol(lines, comp)

    def _write_placeholder_lib_symbol(self, lines: list[str], comp: ComponentPlacement) -> None:
        if comp.symbol_lib and comp.symbol_name:
            sym_name = f"{comp.symbol_lib}:{comp.symbol_name}"
            short_name = comp.symbol_name
        else:
            sym_name = f"_placeholder:{comp.reference}"
            short_name = comp.reference

        lines.append(f'    (symbol "{sym_name}"\n')
        lines.append(f'      (pin_names (offset 1.016))\n')
        lines.append(f'      (exclude_from_sim no)\n')
        lines.append(f'      (in_bom yes)\n')
        lines.append(f'      (on_board yes)\n')
        lines.append(f'      (property "Reference" "{comp.reference}" (at 0 2.54 0)\n')
        lines.append(f'        (effects (font (size 1.27 1.27)))\n')
        lines.append(f'      )\n')
        lines.append(f'      (property "Value" "{comp.value}" (at 0 -2.54 0)\n')
        lines.append(f'        (effects (font (size 1.27 1.27)))\n')
        lines.append(f'      )\n')
        lines.append(f'      (property "Footprint" "{comp.footprint_lib}:{comp.footprint_name}" (at 0 -5.08 0)\n')
        lines.append(f'        (effects (font (size 1.27 1.27)) hide)\n')
        lines.append(f'      )\n')
        lines.append(f'      (symbol "{short_name}_0_1"\n')
        lines.append(f'        (rectangle (start -2.54 1.27) (end 2.54 -1.27)\n')
        lines.append(f'          (stroke (width 0.254) (type default))\n')
        lines.append(f'          (fill (type background))\n')
        lines.append(f'        )\n')
        lines.append(f'      )\n')
        lines.append(f'      (symbol "{short_name}_1_1"\n')
        lines.append(f'        (pin passive line (at -5.08 0 0) (length 2.54)\n')
        lines.append(f'          (name "1" (effects (font (size 1.27 1.27))))\n')
        lines.append(f'          (number "1" (effects (font (size 1.27 1.27))))\n')
        lines.append(f'        )\n')
        lines.append(f'        (pin passive line (at 5.08 0 180) (length 2.54)\n')
        lines.append(f'          (name "2" (effects (font (size 1.27 1.27))))\n')
        lines.append(f'          (number "2" (effects (font (size 1.27 1.27))))\n')
        lines.append(f'        )\n')
        lines.append(f'      )\n')
        lines.append(f'    )\n')

    def _write_symbol_instance(self, lines: list[str], comp: ComponentPlacement,
                               x: float, y: float, rotation: float = 0.0) -> None:
        if comp.symbol_lib and comp.symbol_name:
            lib_id = f"{comp.symbol_lib}:{comp.symbol_name}"
        else:
            lib_id = f"_placeholder:{comp.reference}"

        # Property labels always horizontal with fixed offset above/below
        ref_x, ref_y = x, y - 3.0
        val_x, val_y = x, y + 3.0
        fp_x, fp_y = x, y + 5.0

        inst_uuid = _uuid()
        lines.append(f'  (symbol\n')
        lines.append(f'    (lib_id "{lib_id}")\n')
        lines.append(f'    (at {x:.2f} {y:.2f} {rotation:.0f})\n')
        lines.append(f'    (unit 1)\n')
        lines.append(f'    (exclude_from_sim no)\n')
        lines.append(f'    (in_bom yes)\n')
        lines.append(f'    (on_board yes)\n')
        lines.append(f'    (dnp no)\n')
        lines.append(f'    (uuid "{inst_uuid}")\n')
        lines.append(f'    (property "Reference" "{comp.reference}" (at {ref_x:.2f} {ref_y:.2f} 0)\n')
        lines.append(f'      (effects (font (size 1.27 1.27)))\n')
        lines.append(f'    )\n')
        lines.append(f'    (property "Value" "{comp.value}" (at {val_x:.2f} {val_y:.2f} 0)\n')
        lines.append(f'      (effects (font (size 1.27 1.27)))\n')
        lines.append(f'    )\n')
        fp_full = f"{comp.footprint_lib}:{comp.footprint_name}" if comp.footprint_lib else ""
        lines.append(f'    (property "Footprint" "{fp_full}" (at {fp_x:.2f} {fp_y:.2f} 0)\n')
        lines.append(f'      (effects (font (size 1.27 1.27)) hide)\n')
        lines.append(f'    )\n')
        lines.append(f'    (instances\n')
        lines.append(f'      (project ""\n')
        lines.append(f'        (path "/"\n')
        lines.append(f'          (reference "{comp.reference}")\n')
        lines.append(f'          (unit 1)\n')
        lines.append(f'        )\n')
        lines.append(f'      )\n')
        lines.append(f'    )\n')
        lines.append(f'  )\n')

    def _collect_pin_positions(self, comp: ComponentPlacement,
                               sym_x: float, sym_y: float, rotation: float,
                               pin_positions: dict[str, list[tuple[float, float]]]) -> None:
        """Determine where each pin's connection point lands in schematic coords."""
        if not comp.pad_nets:
            return

        # Try to extract pin layout from the real symbol sexpr
        pins = self._extract_pins_from_sexpr(comp.symbol_sexpr) if comp.symbol_sexpr.strip() else {}
        if not pins:
            # Placeholder symbol: pin 1 at left (-5.08, 0), pin 2 at right (5.08, 0)
            pins = {"1": (-5.08, 0.0), "2": (5.08, 0.0)}

        rad = math.radians(rotation)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        for pad_num, net_name in comp.pad_nets.items():
            if not net_name:
                continue
            if pad_num in pins:
                px, py = pins[pad_num]
                # Symbol pin coords use Y-up; schematic uses Y-down → flip py
                py = -py
                # Rotate pin offset by symbol rotation
                rpx = px * cos_r - py * sin_r
                rpy = px * sin_r + py * cos_r
                scene_x = sym_x + rpx
                scene_y = sym_y + rpy
                pin_positions.setdefault(net_name, []).append((scene_x, scene_y))

    def _build_ref_pad_lookup(self, comp: ComponentPlacement,
                              sym_x: float, sym_y: float, rotation: float,
                              lookup: dict[tuple[str, str], tuple[float, float]]) -> None:
        """Populate (reference, pad_number) → (sch_x, sch_y) for every pin."""
        pins = self._extract_pins_from_sexpr(comp.symbol_sexpr) if comp.symbol_sexpr.strip() else {}
        if not pins:
            pins = {"1": (-5.08, 0.0), "2": (5.08, 0.0)}

        rad = math.radians(rotation)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        for pad_num, (px, py) in pins.items():
            py = -py  # Y-up → Y-down
            rpx = px * cos_r - py * sin_r
            rpy = px * sin_r + py * cos_r
            lookup[(comp.reference, pad_num)] = (sym_x + rpx, sym_y + rpy)

    @staticmethod
    def _extract_pins_from_sexpr(sexpr_text: str) -> dict[str, tuple[float, float]]:
        """Parse pin number -> (x, y) from symbol S-expression."""
        pins: dict[str, tuple[float, float]] = {}
        try:
            tree = parse_sexpr(sexpr_text.strip())
        except Exception:
            return pins
        if not tree or not isinstance(tree, list):
            return pins
        for sub_sym in find_all(tree, "symbol"):
            for pin_node in find_all(sub_sym, "pin"):
                at = find_node(pin_node, "at")
                num_node = find_node(pin_node, "number")
                if at and num_node and len(at) >= 3 and len(num_node) >= 2:
                    num = str(num_node[1])
                    px = float(at[1])
                    py = float(at[2])
                    pins[num] = (px, py)
        # Also check top-level pins
        for pin_node in find_all(tree, "pin"):
            at = find_node(pin_node, "at")
            num_node = find_node(pin_node, "number")
            if at and num_node and len(at) >= 3 and len(num_node) >= 2:
                num = str(num_node[1])
                px = float(at[1])
                py = float(at[2])
                pins[num] = (px, py)
        return pins

    @staticmethod
    def _write_net_label(lines: list[str], net_name: str, x: float, y: float) -> None:
        """Write a KiCad net label at the given schematic position."""
        lines.append(f'  (label "{net_name}" (at {x:.2f} {y:.2f} 0)\n')
        lines.append(f'    (effects (font (size 1.27 1.27)))\n')
        lines.append(f'    (uuid "{_uuid()}")\n')
        lines.append(f'  )\n')

    @staticmethod
    def _write_wire(lines: list[str], wire: WirePlacement) -> None:
        """Write a wire segment as a KiCad schematic wire."""
        lines.append(f'  (wire (pts (xy {wire.x1_mm:.2f} {wire.y1_mm:.2f})'
                     f' (xy {wire.x2_mm:.2f} {wire.y2_mm:.2f}))\n')
        lines.append(f'    (stroke (width 0) (type default))\n')
        lines.append(f'    (uuid "{_uuid()}")\n')
        lines.append(f'  )\n')


class KicadProjectWriter:
    """Writes .kicad_pro JSON file."""

    def generate(self, project_name: str, output_dir: Path,
                 components: list[ComponentPlacement],
                 board_w: float = 100.0, board_h: float = 100.0,
                 wires: list[WirePlacement] | None = None) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        pro_path = output_dir / f"{project_name}.kicad_pro"
        pcb_path = output_dir / f"{project_name}.kicad_pcb"
        sch_path = output_dir / f"{project_name}.kicad_sch"

        pcb_writer = KicadPcbWriter(board_w, board_h)
        dx, dy = pcb_writer.generate(components, pcb_path)

        # Apply same centering offset to wires
        centered_wires: list[WirePlacement] = []
        for w in (wires or []):
            centered_wires.append(WirePlacement(
                x1_mm=w.x1_mm + dx,
                y1_mm=w.y1_mm + dy,
                x2_mm=w.x2_mm + dx,
                y2_mm=w.y2_mm + dy,
                net_name=w.net_name,
                start_ref=w.start_ref,
                start_pad=w.start_pad,
                end_ref=w.end_ref,
                end_pad=w.end_pad,
            ))

        sch_writer = KicadSchWriter()
        sch_writer.generate(components, sch_path, wires=centered_wires)

        pro_data = {
            "meta": {
                "filename": pro_path.name,
                "version": 3
            },
            "board": {
                "design_settings": {"defaults": {}},
                "layer_presets": [],
                "layer_pairs": [],
            },
            "boards": [],
            "cvpcb": {"equivalence_files": []},
            "libraries": {
                "pinned_footprint_libs": [],
                "pinned_symbol_libs": [],
            },
            "net_settings": {
                "classes": [
                    {
                        "bus_width": 12,
                        "clearance": 0.2,
                        "diff_pair_gap": 0.25,
                        "diff_pair_via_gap": 0.25,
                        "diff_pair_width": 0.2,
                        "line_style": 0,
                        "microvia_diameter": 0.3,
                        "microvia_drill": 0.1,
                        "name": "Default",
                        "pcb_color": "rgba(0, 0, 0, 0.000)",
                        "schematic_color": "rgba(0, 0, 0, 0.000)",
                        "track_width": 0.2,
                        "via_diameter": 0.6,
                        "via_drill": 0.3,
                        "wire_width": 6,
                    }
                ],
                "meta": {"version": 4},
                "net_colors": {},
            },
            "pcbnew": {"last_paths": {"gencad": "", "idf": "", "netlist": "", "plot": "", "pos_files": "", "specctra_dsn": "", "step": "", "vrml": ""}},
            "schematic": {"drawing": {"default_line_thickness": 6}, "legacy_lib_dir": "", "legacy_lib_list": []},
            "sheets": [
                [_uuid(), "Root"]
            ],
            "text_variables": {},
        }
        pro_path.write_text(json.dumps(pro_data, indent=2), encoding="utf-8")

        return {
            "pro": pro_path,
            "pcb": pcb_path,
            "sch": sch_path,
        }
