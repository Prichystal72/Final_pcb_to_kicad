"""Detailed analysis of routed schematic – checks wire-body crossings,
pin connectivity, and visual layout quality.

Run:  python -m test.test_analysis
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

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
    extract_symbol_body_rect,
    GRID_MM,
    BODY_PADDING_GRIDS,
)


def load_p2k(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_placements(data: dict):
    """Same as test_router – recreate export pipeline."""
    ppm = data["settings"]["pixels_per_mm"]
    comps = []
    for c in data["components"]:
        x_mm = c.get("x_px", 0.0) / ppm
        y_mm = c.get("y_px", 0.0) / ppm
        comps.append(ComponentPlacement(
            reference=c.get("reference", ""),
            value=c.get("value", ""),
            footprint_lib=c.get("footprint_lib", ""),
            footprint_name=c.get("footprint_name", ""),
            symbol_lib=c.get("symbol_lib", ""),
            symbol_name=c.get("symbol_name", ""),
            x_mm=x_mm, y_mm=y_mm,
            rotation=c.get("rotation", 0.0),
            layer=c.get("layer", "F.Cu"),
            pad_nets=c.get("pad_nets", {}),
        ))

    if comps:
        min_x = min(c.x_mm for c in comps)
        max_x = max(c.x_mm for c in comps)
        min_y = min(c.y_mm for c in comps)
        max_y = max(c.y_mm for c in comps)
        cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
        dx, dy = 297.0 / 2 - cx, 210.0 / 2 - cy
        for c in comps:
            c.x_mm += dx
            c.y_mm += dy
    else:
        dx = dy = 0

    for c in comps:
        c.rotation = (360 - c.rotation) % 360

    pitch_map = {
        "CP_Radial_D25.0mm_P10.00mm_SnapIn": 10.0,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal": 10.16,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal": 7.62,
    }
    pad_lookup = []
    for c_data, cp in zip(data["components"], comps):
        fp_name = c_data.get("footprint_name", "")
        pitch = pitch_map.get(fp_name, 10.0)
        rot_rad = math.radians(c_data.get("rotation", 0.0))
        px_cx, px_cy = c_data["x_px"], c_data["y_px"]
        pad_lookup.append((px_cx, px_cy, cp.reference, "1"))
        p2x = px_cx + pitch * ppm * math.cos(rot_rad)
        p2y = px_cy - pitch * ppm * math.sin(rot_rad)
        pad_lookup.append((p2x, p2y, cp.reference, "2"))

    def find_pad(x_px, y_px):
        best_d = 15.0 * ppm
        ref, pad = "", ""
        for px, py, r, p in pad_lookup:
            d = math.hypot(px - x_px, py - y_px)
            if d < best_d:
                best_d = d
                ref, pad = r, p
        return ref, pad

    wires = []
    for wd in data.get("wires", []):
        x1, y1 = wd.get("x1", 0), wd.get("y1", 0)
        x2, y2 = wd.get("x2", 0), wd.get("y2", 0)
        s_ref, s_pad = find_pad(x1, y1)
        e_ref, e_pad = find_pad(x2, y2)
        wires.append(WirePlacement(
            x1_mm=x1 / ppm + dx, y1_mm=y1 / ppm + dy,
            x2_mm=x2 / ppm + dx, y2_mm=y2 / ppm + dy,
            start_ref=s_ref, start_pad=s_pad,
            end_ref=e_ref, end_pad=e_pad,
        ))
    return comps, wires


def compute_body_rect_mm(comp: ComponentPlacement) -> tuple[float, float, float, float]:
    """Get body AABB in schematic mm."""
    sch_rot = compute_sch_rotation(comp)
    local_rect = (-2.54, -1.27, 2.54, 1.27)  # placeholder default
    corners = [
        (local_rect[0], local_rect[1]),
        (local_rect[2], local_rect[1]),
        (local_rect[0], local_rect[3]),
        (local_rect[2], local_rect[3]),
    ]
    rad = math.radians(sch_rot)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    xs, ys = [], []
    for lx, ly in corners:
        ly = -ly
        rx = lx * cos_r - ly * sin_r + comp.x_mm
        ry = lx * sin_r + ly * cos_r + comp.y_mm
        xs.append(rx)
        ys.append(ry)
    return (min(xs), min(ys), max(xs), max(ys))


def compute_pin_pos(comp: ComponentPlacement, pad_num: str) -> tuple[float, float]:
    """Compute schematic pin position (same as router/writer)."""
    sch_rot = compute_sch_rotation(comp)
    pins = {"1": (-5.08, 0.0), "2": (5.08, 0.0)}
    px, py = pins.get(pad_num, (0, 0))
    py = -py
    rad = math.radians(sch_rot)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    rpx = px * cos_r - py * sin_r
    rpy = px * sin_r + py * cos_r
    return (comp.x_mm + rpx, comp.y_mm + rpy)


def segment_crosses_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx1: float, ry1: float, rx2: float, ry2: float,
) -> bool:
    """Check if a horizontal or vertical segment crosses a rectangle."""
    if abs(x2 - x1) < 0.01:  # vertical
        x = x1
        seg_y1, seg_y2 = min(y1, y2), max(y1, y2)
        if x < rx1 or x > rx2:
            return False
        return seg_y1 < ry2 and seg_y2 > ry1
    elif abs(y2 - y1) < 0.01:  # horizontal
        y = y1
        seg_x1, seg_x2 = min(x1, x2), max(x1, x2)
        if y < ry1 or y > ry2:
            return False
        return seg_x1 < rx2 and seg_x2 > rx1
    return False


def main() -> None:
    data = load_p2k(ROOT / "project.p2k")
    comps, wires = build_placements(data)

    router = SchematicRouter()
    routed_comps, routed_wires = router.route(comps, wires)

    # Build component info
    comp_info = {}
    for comp in routed_comps:
        body = compute_body_rect_mm(comp)
        pin1 = compute_pin_pos(comp, "1")
        pin2 = compute_pin_pos(comp, "2")
        comp_info[comp.reference] = {
            "center": (comp.x_mm, comp.y_mm),
            "body": body,
            "pin1": pin1,
            "pin2": pin2,
            "sch_rot": compute_sch_rotation(comp),
        }

    print("=" * 65)
    print("  DETAILED ROUTING ANALYSIS")
    print("=" * 65)

    print("\n--- Component Layout ---")
    for ref, info in comp_info.items():
        b = info["body"]
        print(f"  {ref}:")
        print(f"    Center: ({info['center'][0]:.2f}, {info['center'][1]:.2f}) "
              f"  Rot: {info['sch_rot']:.0f}°")
        print(f"    Body:   ({b[0]:.2f},{b[1]:.2f}) → ({b[2]:.2f},{b[3]:.2f})")
        print(f"    Pin 1:  ({info['pin1'][0]:.2f}, {info['pin1'][1]:.2f})")
        print(f"    Pin 2:  ({info['pin2'][0]:.2f}, {info['pin2'][1]:.2f})")

    # Check wire-body crossings
    print("\n--- Wire-Body Crossing Check ---")
    crossing_issues = 0
    for i, w in enumerate(routed_wires):
        for ref, info in comp_info.items():
            b = info["body"]
            # Only check if wire doesn't start/end at this component's pin
            wire_refs = set()
            if w.start_ref:
                wire_refs.add(w.start_ref)
            if w.end_ref:
                wire_refs.add(w.end_ref)

            if ref not in wire_refs:
                if segment_crosses_rect(w.x1_mm, w.y1_mm, w.x2_mm, w.y2_mm,
                                        b[0], b[1], b[2], b[3]):
                    crossing_issues += 1
                    print(f"  ✗ Wire {i}: ({w.x1_mm:.2f},{w.y1_mm:.2f})→"
                          f"({w.x2_mm:.2f},{w.y2_mm:.2f}) crosses {ref} body")

    if crossing_issues == 0:
        print(f"  ✓ No wires cross foreign symbol bodies")

    # Verify pin connectivity
    print("\n--- Pin Connectivity ---")
    pin_connections: dict[str, list[str]] = {}
    for w in routed_wires:
        pts = []
        if w.start_ref and w.start_pad:
            pts.append(f"{w.start_ref}.{w.start_pad}")
        if w.end_ref and w.end_pad:
            pts.append(f"{w.end_ref}.{w.end_pad}")
        for p in pts:
            pin_connections.setdefault(p, [])

    # Trace wire chains
    def endpoint_key(x, y):
        return (round(x, 2), round(y, 2))

    endpoint_map: dict[tuple, list[int]] = {}
    for i, w in enumerate(routed_wires):
        k1 = endpoint_key(w.x1_mm, w.y1_mm)
        k2 = endpoint_key(w.x2_mm, w.y2_mm)
        endpoint_map.setdefault(k1, []).append(i)
        endpoint_map.setdefault(k2, []).append(i)

    # Find connected wire groups
    visited = set()
    nets = []

    def flood_fill(start_idx):
        stack = [start_idx]
        group_wires = set()
        group_pins = set()
        while stack:
            idx = stack.pop()
            if idx in visited:
                continue
            visited.add(idx)
            group_wires.add(idx)
            w = routed_wires[idx]
            if w.start_ref and w.start_pad:
                group_pins.add(f"{w.start_ref}.{w.start_pad}")
            if w.end_ref and w.end_pad:
                group_pins.add(f"{w.end_ref}.{w.end_pad}")
            for k in (endpoint_key(w.x1_mm, w.y1_mm),
                       endpoint_key(w.x2_mm, w.y2_mm)):
                for j in endpoint_map.get(k, []):
                    if j not in visited:
                        stack.append(j)
        return group_wires, group_pins

    for i in range(len(routed_wires)):
        if i not in visited:
            group_wires, group_pins = flood_fill(i)
            nets.append((group_wires, group_pins))

    for ni, (gwires, gpins) in enumerate(nets):
        print(f"  Net {ni+1}: {' — '.join(sorted(gpins))} "
              f"({len(gwires)} segment{'s' if len(gwires)>1 else ''})")

    # Grid alignment check
    print("\n--- Grid Alignment ---")
    all_on_grid = True
    for w in routed_wires:
        for x, y, label in [(w.x1_mm, w.y1_mm, "start"), (w.x2_mm, w.y2_mm, "end")]:
            gx_err = abs(x / GRID_MM - round(x / GRID_MM))
            gy_err = abs(y / GRID_MM - round(y / GRID_MM))
            if gx_err > 0.01 or gy_err > 0.01:
                all_on_grid = False
                print(f"  ✗ Wire {label} ({x:.4f},{y:.4f}) off-grid")
    if all_on_grid:
        print(f"  ✓ All wire endpoints are on 50-mil grid")

    # Summary metrics
    total_wire_len = sum(
        math.hypot(w.x2_mm - w.x1_mm, w.y2_mm - w.y1_mm) for w in routed_wires
    )
    turns = 0
    for ni, (gwires, _) in enumerate(nets):
        sorted_wires = sorted(gwires)
        for i in range(len(sorted_wires) - 1):
            w1 = routed_wires[sorted_wires[i]]
            w2 = routed_wires[sorted_wires[i + 1]]
            d1 = (abs(w1.x2_mm - w1.x1_mm) > 0.01, abs(w1.y2_mm - w1.y1_mm) > 0.01)
            d2 = (abs(w2.x2_mm - w2.x1_mm) > 0.01, abs(w2.y2_mm - w2.y1_mm) > 0.01)
            if d1 != d2:
                turns += 1

    print(f"\n--- Summary ---")
    print(f"  Components:        {len(routed_comps)}")
    print(f"  Nets:              {len(nets)}")
    print(f"  Wire segments:     {len(routed_wires)}")
    print(f"  Direction changes: {turns}")
    print(f"  Total wire length: {total_wire_len:.2f} mm")
    print(f"  Crossing issues:   {crossing_issues}")
    print(f"  Grid-aligned:      {'Yes' if all_on_grid else 'No'}")
    print("=" * 65)


if __name__ == "__main__":
    main()
