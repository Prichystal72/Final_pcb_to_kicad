"""Test script – exercises the schematic router on project.p2k data.

Loads the project file, simulates the export pipeline with routing,
generates a test schematic, and validates the output.

Run from the project root:
    python -m test.test_router
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kicad_generator import ComponentPlacement, WirePlacement, KicadSchWriter
from kicad_parser import parse_sexpr, find_node, find_all
from schematic_router import (
    SchematicRouter,
    snap_to_grid,
    mm_to_grid,
    grid_to_mm,
    compute_sch_rotation,
    extract_pins_from_sexpr,
    GRID_MM,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_p2k(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_placements(data: dict) -> tuple[list[ComponentPlacement], list[WirePlacement]]:
    """Replicate the export pipeline from kicad_project.py.

    Since we don't have the full UI / library, we construct
    ComponentPlacement objects with placeholder symbol data and
    WirePlacement objects with pad reference assignments.
    """
    ppm = data["settings"]["pixels_per_mm"]
    comps: list[ComponentPlacement] = []

    for c in data["components"]:
        x_mm = c.get("x_px", 0.0) / ppm
        y_mm = c.get("y_px", 0.0) / ppm
        cp = ComponentPlacement(
            reference=c.get("reference", ""),
            value=c.get("value", ""),
            footprint_lib=c.get("footprint_lib", ""),
            footprint_name=c.get("footprint_name", ""),
            symbol_lib=c.get("symbol_lib", ""),
            symbol_name=c.get("symbol_name", ""),
            x_mm=x_mm,
            y_mm=y_mm,
            rotation=c.get("rotation", 0.0),
            layer=c.get("layer", "F.Cu"),
            pad_nets=c.get("pad_nets", {}),
        )
        comps.append(cp)

    # --- Simulate centering (same logic as KicadPcbWriter.generate) ---
    if comps:
        min_x = min(c.x_mm for c in comps)
        max_x = max(c.x_mm for c in comps)
        min_y = min(c.y_mm for c in comps)
        max_y = max(c.y_mm for c in comps)
        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        dx = 297.0 / 2 - cx
        dy = 210.0 / 2 - cy
        for c in comps:
            c.x_mm += dx
            c.y_mm += dy
    else:
        dx = dy = 0

    # --- Simulate PCB rotation negation ---
    for c in comps:
        c.rotation = (360 - c.rotation) % 360

    # --- Build pad lookup for wire endpoint matching ---
    # For THT footprints with pitch P, pads are at local:
    #   pad 1 at (0, 0), pad 2 at (pitch_mm, 0)
    pad_lookup: list[tuple[float, float, str, str]] = []
    pitch_map = {
        "CP_Radial_D25.0mm_P10.00mm_SnapIn": 10.0,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal": 10.16,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal": 7.62,
    }
    for c_data, cp in zip(data["components"], comps):
        fp_name = c_data.get("footprint_name", "")
        pitch = pitch_map.get(fp_name, 10.0)
        rot_rad = math.radians(c_data.get("rotation", 0.0))
        px_cx = c_data["x_px"]
        px_cy = c_data["y_px"]
        ppm_val = ppm
        # Pad 1 at footprint center
        pad_lookup.append((px_cx, px_cy, cp.reference, "1"))
        # Pad 2 offset by pitch
        p2x = px_cx + pitch * ppm_val * math.cos(rot_rad)
        p2y = px_cy - pitch * ppm_val * math.sin(rot_rad)
        pad_lookup.append((p2x, p2y, cp.reference, "2"))

    def find_pad(x_px: float, y_px: float) -> tuple[str, str]:
        best_d = 15.0 * ppm
        ref, pad = "", ""
        for px, py, r, p in pad_lookup:
            d = math.hypot(px - x_px, py - y_px)
            if d < best_d:
                best_d = d
                ref, pad = r, p
        return ref, pad

    # --- Build centered wire placements ---
    wires: list[WirePlacement] = []
    for wd in data.get("wires", []):
        x1_px = wd.get("x1", 0)
        y1_px = wd.get("y1", 0)
        x2_px = wd.get("x2", 0)
        y2_px = wd.get("y2", 0)
        s_ref, s_pad = find_pad(x1_px, y1_px)
        e_ref, e_pad = find_pad(x2_px, y2_px)
        wires.append(WirePlacement(
            x1_mm=x1_px / ppm + dx,
            y1_mm=y1_px / ppm + dy,
            x2_mm=x2_px / ppm + dx,
            y2_mm=y2_px / ppm + dy,
            net_name=wd.get("net_name", ""),
            start_ref=s_ref,
            start_pad=s_pad,
            end_ref=e_ref,
            end_pad=e_pad,
        ))

    return comps, wires


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_schematic(sch_path: Path) -> list[str]:
    """Parse the generated .kicad_sch and validate routing quality."""
    issues: list[str] = []
    text = sch_path.read_text(encoding="utf-8")
    tree = parse_sexpr(text)
    if not tree:
        issues.append("ERROR: Failed to parse schematic S-expression")
        return issues

    # Collect wires
    wire_nodes = find_all(tree, "wire")
    total_wires = len(wire_nodes)
    non_manhattan = 0

    for wn in wire_nodes:
        pts = find_node(wn, "pts")
        if not pts:
            continue
        xys = find_all(pts, "xy")
        if len(xys) < 2:
            continue
        x1, y1 = float(xys[0][1]), float(xys[0][2])
        x2, y2 = float(xys[1][1]), float(xys[1][2])
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx > 0.01 and dy > 0.01:
            non_manhattan += 1
            issues.append(
                f"  Non-Manhattan wire: ({x1:.2f},{y1:.2f}) → ({x2:.2f},{y2:.2f})"
            )

    # Collect symbols
    symbol_nodes = [
        n for n in tree[1:]
        if isinstance(n, list) and n and n[0] == "symbol" and find_node(n, "lib_id")
    ]

    # Check grid alignment
    off_grid = 0
    for sym in symbol_nodes:
        at = find_node(sym, "at")
        if at and len(at) >= 3:
            sx, sy = float(at[1]), float(at[2])
            gx_remainder = abs(sx / GRID_MM - round(sx / GRID_MM))
            gy_remainder = abs(sy / GRID_MM - round(sy / GRID_MM))
            if gx_remainder > 0.01 or gy_remainder > 0.01:
                off_grid += 1
                ref_prop = find_node(sym, "property")
                ref = ref_prop[2] if ref_prop and len(ref_prop) >= 3 else "?"
                issues.append(
                    f"  Off-grid symbol {ref}: ({sx:.4f},{sy:.4f})"
                )

    print(f"\n{'='*60}")
    print(f"  Schematic Validation Report")
    print(f"{'='*60}")
    print(f"  Total wire segments: {total_wires}")
    print(f"  Manhattan (H/V):     {total_wires - non_manhattan}")
    print(f"  Non-Manhattan:       {non_manhattan}")
    print(f"  Total symbols:       {len(symbol_nodes)}")
    print(f"  On-grid symbols:     {len(symbol_nodes) - off_grid}")
    print(f"  Off-grid symbols:    {off_grid}")

    if non_manhattan == 0 and off_grid == 0:
        print(f"\n  ✓ PASS – All wires are Manhattan, all symbols on grid")
    else:
        if non_manhattan:
            print(f"\n  ✗ FAIL – {non_manhattan} non-Manhattan wire(s)")
        if off_grid:
            print(f"\n  ✗ FAIL – {off_grid} off-grid symbol(s)")

    print(f"{'='*60}\n")
    return issues


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def main() -> None:
    p2k_path = ROOT / "project.p2k"
    out_dir = ROOT / "test" / "routed_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading project: {p2k_path}")
    data = load_p2k(p2k_path)

    # Build placements
    comps, wires = build_placements(data)

    print(f"\nComponents ({len(comps)}):")
    for cp in comps:
        rot = compute_sch_rotation(cp)
        print(f"  {cp.reference}: ({cp.x_mm:.2f}, {cp.y_mm:.2f}) mm, "
              f"PCB rot={cp.rotation:.0f}°, SCH rot={rot:.0f}°")

    print(f"\nOriginal wires ({len(wires)}):")
    for w in wires:
        print(f"  ({w.x1_mm:.2f},{w.y1_mm:.2f}) → ({w.x2_mm:.2f},{w.y2_mm:.2f})  "
              f"[{w.start_ref}.{w.start_pad} → {w.end_ref}.{w.end_pad}]")

    # --- Run router ---
    print("\n--- Running SchematicRouter ---")
    router = SchematicRouter()
    routed_comps, routed_wires = router.route(comps, wires)

    print(f"\nSnapped components ({len(routed_comps)}):")
    for cp in routed_comps:
        rot = compute_sch_rotation(cp)
        print(f"  {cp.reference}: ({cp.x_mm:.2f}, {cp.y_mm:.2f}) mm, SCH rot={rot:.0f}°")

    print(f"\nRouted wires ({len(routed_wires)}):")
    for w in routed_wires:
        dx = abs(w.x2_mm - w.x1_mm)
        dy = abs(w.y2_mm - w.y1_mm)
        is_h = dy < 0.01
        is_v = dx < 0.01
        orient = "H" if is_h else ("V" if is_v else "D")
        tags = []
        if w.start_ref:
            tags.append(f"start={w.start_ref}.{w.start_pad}")
        if w.end_ref:
            tags.append(f"end={w.end_ref}.{w.end_pad}")
        tag_str = "  " + ", ".join(tags) if tags else ""
        print(f"  [{orient}] ({w.x1_mm:.2f},{w.y1_mm:.2f}) → "
              f"({w.x2_mm:.2f},{w.y2_mm:.2f}){tag_str}")

    # --- Generate test schematic ---
    sch_path = out_dir / "test_routed.kicad_sch"
    print(f"\nGenerating schematic: {sch_path}")
    writer = KicadSchWriter()
    writer.generate(routed_comps, sch_path, wires=routed_wires)

    # --- Validate ---
    issues = validate_schematic(sch_path)
    if issues:
        print("Details:")
        for issue in issues:
            print(issue)

    # --- Also generate WITHOUT routing for comparison ---
    comps_orig, wires_orig = build_placements(data)
    sch_orig = out_dir / "test_original.kicad_sch"
    writer2 = KicadSchWriter()
    writer2.generate(comps_orig, sch_orig, wires=wires_orig)
    print(f"Original (unrouted) schematic: {sch_orig}")
    print("\nComparing original:")
    validate_schematic(sch_orig)


if __name__ == "__main__":
    main()
