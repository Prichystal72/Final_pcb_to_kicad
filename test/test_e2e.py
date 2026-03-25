"""End-to-end integration test – uses the real export pipeline
(KiCadProjectManager) with the router enabled.

Loads project.p2k, reads real .kicad_sym / .kicad_mod libraries,
and exports routed schematic.

Run:  python test/test_e2e.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordinate_system import CoordinateSystem
from kicad_generator import KicadProjectWriter, ComponentPlacement, WirePlacement
from kicad_parser import parse_sexpr, find_node, find_all
from library_bridge import LibraryBridge
from project_manager import load_project
from schematic_router import GRID_MM


def main() -> None:
    p2k_path = ROOT / "project.p2k"
    out_dir = ROOT / "test" / "e2e_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading project: {p2k_path}")
    data = load_project(p2k_path)
    ppm = data["settings"]["pixels_per_mm"]

    # Setup library bridge
    lib = LibraryBridge()
    fp_paths = [Path(p) for p in data["settings"].get("footprint_paths", [])]
    sym_paths = [Path(p) for p in data["settings"].get("symbol_paths", [])]
    lib.set_footprint_paths(fp_paths)
    lib.set_symbol_paths(sym_paths)
    print("Scanning libraries...")
    lib.scan()
    print(f"  Footprint libraries: {len(lib.all_footprint_libraries())}")
    print(f"  Symbol libraries:    {len(lib.all_symbol_libraries())}")

    # Build ComponentPlacement objects (mimicking kicad_project.py)
    coord = CoordinateSystem(pixels_per_mm=ppm)
    placements: list[ComponentPlacement] = []

    for comp_data in data.get("components", []):
        fp_lib = comp_data.get("footprint_lib", "")
        fp_name = comp_data.get("footprint_name", "")
        sym_lib = comp_data.get("symbol_lib", "")
        sym_name = comp_data.get("symbol_name", "")

        fp_sexpr = ""
        if fp_lib and fp_name:
            fp_sexpr = lib.read_footprint_sexpr(fp_lib, fp_name)

        sym_sexpr = ""
        if sym_lib and sym_name:
            sym_sexpr = lib.read_symbol_sexpr(sym_lib, sym_name)

        x_mm = comp_data.get("x_px", 0.0) / ppm
        y_mm = comp_data.get("y_px", 0.0) / ppm

        cp = ComponentPlacement(
            reference=comp_data.get("reference", ""),
            value=comp_data.get("value", ""),
            footprint_lib=fp_lib,
            footprint_name=fp_name,
            symbol_lib=sym_lib,
            symbol_name=sym_name,
            x_mm=x_mm,
            y_mm=y_mm,
            rotation=comp_data.get("rotation", 0.0),
            layer=comp_data.get("layer", "F.Cu"),
            footprint_sexpr=fp_sexpr,
            symbol_sexpr=sym_sexpr,
            pad_nets=comp_data.get("pad_nets", {}),
        )
        placements.append(cp)

    print(f"\nComponents ({len(placements)}):")
    for cp in placements:
        has_sym = "✓" if cp.symbol_sexpr.strip() else "✗"
        has_fp = "✓" if cp.footprint_sexpr.strip() else "✗"
        print(f"  {cp.reference} [{cp.symbol_lib}:{cp.symbol_name}]  "
              f"sym={has_sym}  fp={has_fp}")

    # Build wire data for pad matching (simplified – uses pitch)
    pitch_map = {
        "CP_Radial_D25.0mm_P10.00mm_SnapIn": 10.0,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal": 10.16,
        "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal": 7.62,
    }
    pad_lookup = []
    for cd, cp in zip(data["components"], placements):
        fp_name = cd.get("footprint_name", "")
        pitch = pitch_map.get(fp_name, 10.0)
        rot_rad = math.radians(cd.get("rotation", 0.0))
        px_cx, px_cy = cd["x_px"], cd["y_px"]
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

    wire_placements: list[WirePlacement] = []
    for wd in data.get("wires", []):
        x1, y1 = wd.get("x1", 0), wd.get("y1", 0)
        x2, y2 = wd.get("x2", 0), wd.get("y2", 0)
        s_ref, s_pad = find_pad(x1, y1)
        e_ref, e_pad = find_pad(x2, y2)
        wire_placements.append(WirePlacement(
            x1_mm=x1 / ppm, y1_mm=y1 / ppm,
            x2_mm=x2 / ppm, y2_mm=y2 / ppm,
            start_ref=s_ref, start_pad=s_pad,
            end_ref=e_ref, end_pad=e_pad,
        ))

    print(f"\nWire placements: {len(wire_placements)}")

    # Export using KicadProjectWriter with auto_route=True
    print("\n--- Exporting with auto-routing ---")
    writer = KicadProjectWriter()
    result = writer.generate(
        project_name="test_e2e",
        output_dir=out_dir,
        components=placements,
        wires=wire_placements,
        auto_route=True,
    )
    print(f"  Output: {out_dir}")

    # Validate generated schematic
    sch_path = out_dir / "test_e2e.kicad_sch"
    print(f"\n--- Validating {sch_path.name} ---")
    text = sch_path.read_text(encoding="utf-8")
    tree = parse_sexpr(text)

    wire_nodes = find_all(tree, "wire")
    total_wires = len(wire_nodes)
    non_manhattan = 0
    for wn in wire_nodes:
        pts = find_node(wn, "pts")
        if pts:
            xys = find_all(pts, "xy")
            if len(xys) >= 2:
                x1, y1 = float(xys[0][1]), float(xys[0][2])
                x2, y2 = float(xys[1][1]), float(xys[1][2])
                if abs(x2 - x1) > 0.01 and abs(y2 - y1) > 0.01:
                    non_manhattan += 1

    sym_nodes = [n for n in tree[1:]
                 if isinstance(n, list) and n and n[0] == "symbol"
                 and find_node(n, "lib_id")]
    off_grid = 0
    for sn in sym_nodes:
        at = find_node(sn, "at")
        if at and len(at) >= 3:
            sx, sy = float(at[1]), float(at[2])
            if (abs(sx / GRID_MM - round(sx / GRID_MM)) > 0.01 or
                    abs(sy / GRID_MM - round(sy / GRID_MM)) > 0.01):
                off_grid += 1

    print(f"  Wire segments:     {total_wires}")
    print(f"  Manhattan (H/V):   {total_wires - non_manhattan}")
    print(f"  Non-Manhattan:     {non_manhattan}")
    print(f"  Symbols:           {len(sym_nodes)}")
    print(f"  On-grid:           {len(sym_nodes) - off_grid}")
    print(f"  Off-grid:          {off_grid}")

    # Show wire details
    print(f"\n  Wire segments in output:")
    for wn in wire_nodes:
        pts = find_node(wn, "pts")
        if pts:
            xys = find_all(pts, "xy")
            if len(xys) >= 2:
                x1, y1 = float(xys[0][1]), float(xys[0][2])
                x2, y2 = float(xys[1][1]), float(xys[1][2])
                orient = "H" if abs(y2 - y1) < 0.01 else ("V" if abs(x2 - x1) < 0.01 else "D")
                print(f"    [{orient}] ({x1:.2f},{y1:.2f}) → ({x2:.2f},{y2:.2f})")

    # Show symbol positions
    print(f"\n  Symbol positions:")
    for sn in sym_nodes:
        at = find_node(sn, "at")
        lib_id = find_node(sn, "lib_id")
        if at and lib_id and len(at) >= 4:
            print(f"    {lib_id[1]} at ({float(at[1]):.2f}, {float(at[2]):.2f}) rot={float(at[3]):.0f}°")

    if non_manhattan == 0 and off_grid == 0:
        print(f"\n  ✓ PASS – Clean Manhattan routing, all on-grid")
    else:
        print(f"\n  ✗ ISSUES found")

    # Also export WITHOUT routing for comparison
    comps2 = []
    for cd in data.get("components", []):
        fp_lib2 = cd.get("footprint_lib", "")
        fp_name2 = cd.get("footprint_name", "")
        sym_lib2 = cd.get("symbol_lib", "")
        sym_name2 = cd.get("symbol_name", "")
        fp_sexpr2 = lib.read_footprint_sexpr(fp_lib2, fp_name2) if fp_lib2 and fp_name2 else ""
        sym_sexpr2 = lib.read_symbol_sexpr(sym_lib2, sym_name2) if sym_lib2 and sym_name2 else ""
        comps2.append(ComponentPlacement(
            reference=cd.get("reference", ""),
            value=cd.get("value", ""),
            footprint_lib=fp_lib2, footprint_name=fp_name2,
            symbol_lib=sym_lib2, symbol_name=sym_name2,
            x_mm=cd.get("x_px", 0.0) / ppm,
            y_mm=cd.get("y_px", 0.0) / ppm,
            rotation=cd.get("rotation", 0.0),
            layer=cd.get("layer", "F.Cu"),
            footprint_sexpr=fp_sexpr2, symbol_sexpr=sym_sexpr2,
            pad_nets=cd.get("pad_nets", {}),
        ))

    pad_lookup2 = []
    for cd, cp2 in zip(data["components"], comps2):
        fn = cd.get("footprint_name", "")
        pitch2 = pitch_map.get(fn, 10.0)
        rot2 = math.radians(cd.get("rotation", 0.0))
        pcx, pcy = cd["x_px"], cd["y_px"]
        pad_lookup2.append((pcx, pcy, cp2.reference, "1"))
        pad_lookup2.append((pcx + pitch2 * ppm * math.cos(rot2),
                            pcy - pitch2 * ppm * math.sin(rot2),
                            cp2.reference, "2"))

    def find_pad2(xp, yp):
        best2 = 15.0 * ppm
        r2, p2 = "", ""
        for ppx, ppy, rr, pp in pad_lookup2:
            dd = math.hypot(ppx - xp, ppy - yp)
            if dd < best2:
                best2 = dd
                r2, p2 = rr, pp
        return r2, p2

    wires2 = []
    for wd in data.get("wires", []):
        wx1, wy1 = wd.get("x1", 0), wd.get("y1", 0)
        wx2, wy2 = wd.get("x2", 0), wd.get("y2", 0)
        sr, sp = find_pad2(wx1, wy1)
        er, ep = find_pad2(wx2, wy2)
        wires2.append(WirePlacement(
            x1_mm=wx1 / ppm, y1_mm=wy1 / ppm,
            x2_mm=wx2 / ppm, y2_mm=wy2 / ppm,
            start_ref=sr, start_pad=sp,
            end_ref=er, end_pad=ep,
        ))

    orig_dir = out_dir / "original"
    orig_dir.mkdir(exist_ok=True)
    writer2 = KicadProjectWriter()
    writer2.generate("test_original", orig_dir, comps2, wires=wires2, auto_route=False)
    print(f"\n  Original (unrouted) → {orig_dir}")


if __name__ == "__main__":
    main()
