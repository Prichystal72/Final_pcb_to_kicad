"""Schematic auto-router with snap-to-grid and A* Manhattan routing.

Creates clean schematic layouts by:
1. Snapping all symbol positions to 50-mil (1.27 mm) KiCad grid
2. Resolving overlapping symbols (200 mil = 5.08 mm minimum spacing)
3. A* pathfinding with Manhattan (90°-only) routing
4. Priority: shortest connections routed first

Cost function for A*:
    * Empty cell traversal:  1
    * Direction change (turn): 10  (keeps wires straight)
    * Symbol body cell:     infinity (blocked)
    * Overlap with existing route: 50  (crossing OK, co-routing not)
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Optional, Any

from kicad_generator import ComponentPlacement, WirePlacement
from kicad_parser import parse_sexpr, find_node, find_all

# ---------------------------------------------------------------------------
# Grid constants – 50 mil = 1.27 mm (KiCad standard)
# ---------------------------------------------------------------------------
GRID_MM = 1.27
SAFE_SPACING_GRIDS = 4        # 200 mil = 4 × 50 mil minimum gap
MARGIN_GRIDS = 15             # extra grid cells around bounding box
BODY_PADDING_GRIDS = 1        # padding around symbol bodies for routing clearance

# ---------------------------------------------------------------------------
# A* cost constants
# ---------------------------------------------------------------------------
COST_MOVE = 1
COST_TURN = 10
COST_BLOCKED = float("inf")
COST_OVERLAP = 50


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def snap_to_grid(value: float) -> float:
    """Snap a value to the nearest 50-mil grid point."""
    return round(value / GRID_MM) * GRID_MM


def mm_to_grid(value: float) -> int:
    """Convert mm to grid coordinate."""
    return round(value / GRID_MM)


def grid_to_mm(value: int) -> float:
    """Convert grid coordinate to mm."""
    return value * GRID_MM


def compute_sch_rotation(comp: ComponentPlacement) -> float:
    """Compute the schematic rotation (degrees) for a component.

    Must match the logic in ``KicadSchWriter.generate()``:
        rot = (round(((360 - canvas_rot) % 360) / 90) * 90) % 360
    """
    original_rot = (360 - comp.rotation) % 360
    return (round(original_rot / 90.0) * 90) % 360


def extract_pins_from_sexpr(sexpr_text: str) -> dict[str, tuple[float, float]]:
    """Parse pin number → (x, y) from symbol S-expression.

    Same logic as ``KicadSchWriter._extract_pins_from_sexpr()``.
    """
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
                pins[str(num_node[1])] = (float(at[1]), float(at[2]))
    for pin_node in find_all(tree, "pin"):
        at = find_node(pin_node, "at")
        num_node = find_node(pin_node, "number")
        if at and num_node and len(at) >= 3 and len(num_node) >= 2:
            pins[str(num_node[1])] = (float(at[1]), float(at[2]))
    return pins


def extract_symbol_body_rect(
    sexpr_text: str,
) -> Optional[tuple[float, float, float, float]]:
    """Return local (min_x, min_y, max_x, max_y) of the symbol body."""
    try:
        tree = parse_sexpr(sexpr_text.strip())
    except Exception:
        return None
    if not tree or not isinstance(tree, list):
        return None

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    found = False

    for sub_sym in find_all(tree, "symbol"):
        for rect in find_all(sub_sym, "rectangle"):
            start = find_node(rect, "start")
            end = find_node(rect, "end")
            if start and end and len(start) >= 3 and len(end) >= 3:
                x1, y1 = float(start[1]), float(start[2])
                x2, y2 = float(end[1]), float(end[2])
                min_x, max_x = min(min_x, x1, x2), max(max_x, x1, x2)
                min_y, max_y = min(min_y, y1, y2), max(max_y, y1, y2)
                found = True
        for poly in find_all(sub_sym, "polyline"):
            pts = find_node(poly, "pts")
            if pts:
                for pt in find_all(pts, "xy"):
                    if len(pt) >= 3:
                        min_x = min(min_x, float(pt[1]))
                        min_y = min(min_y, float(pt[2]))
                        max_x = max(max_x, float(pt[1]))
                        max_y = max(max_y, float(pt[2]))
                        found = True
        for arc in find_all(sub_sym, "arc"):
            for tag in ("start", "mid", "end"):
                node = find_node(arc, tag)
                if node and len(node) >= 3:
                    min_x = min(min_x, float(node[1]))
                    min_y = min(min_y, float(node[2]))
                    max_x = max(max_x, float(node[1]))
                    max_y = max(max_y, float(node[2]))
                    found = True

    return (min_x, min_y, max_x, max_y) if found else None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PinInfo:
    """A component pin in schematic coordinates."""
    reference: str
    pad_number: str
    x: float          # schematic mm
    y: float          # schematic mm
    gx: int = 0       # grid x
    gy: int = 0       # grid y


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------

class SchematicRouter:
    """Smart schematic router with snap-to-grid and A* Manhattan routing.

    Usage::

        router = SchematicRouter()
        components, wires = router.route(components, wires)
    """

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def route(
        self,
        components: list[ComponentPlacement],
        wires: list[WirePlacement],
    ) -> tuple[list[ComponentPlacement], list[WirePlacement]]:
        """Run the full routing pipeline.

        * Modifies component positions in-place (snap to grid).
        * Returns ``(components, new_wires)`` with Manhattan-routed wires.
        """
        if not components:
            return components, wires

        # 1. Snap to grid
        self._snap_components(components)

        # 2. Resolve symbol overlaps
        self._resolve_overlaps(components)

        # 3. Compute pin positions in schematic space
        pin_map = self._build_pin_map(components)

        # Build (ref, pad) → PinInfo lookup
        ref_pad_lookup: dict[tuple[str, str], PinInfo] = {}
        for pin in pin_map:
            ref_pad_lookup[(pin.reference, pin.pad_number)] = pin

        # 4. Extract net topology from wire data
        nets = self._extract_nets(wires, ref_pad_lookup)

        if not nets:
            return components, []

        # 5. Sort nets by estimated wire length (shortest first)
        nets.sort(key=lambda n: self._net_mst_cost(n))

        # 6. Build obstacle grid (symbol bodies)
        grid_bounds = self._compute_grid_bounds(pin_map, components)
        obstacles = self._build_obstacle_grid(components, grid_bounds, pin_map)

        # 7. Route each net with A*
        used_cells: set[tuple[int, int]] = set()
        new_wires: list[WirePlacement] = []

        for net_pins in nets:
            segments = self._route_net(net_pins, obstacles, used_cells, grid_bounds)
            new_wires.extend(segments)

        return components, new_wires

    # ------------------------------------------------------------------ #
    # Step 1 – Snap to grid
    # ------------------------------------------------------------------ #

    @staticmethod
    def _snap_components(components: list[ComponentPlacement]) -> None:
        for comp in components:
            comp.x_mm = snap_to_grid(comp.x_mm)
            comp.y_mm = snap_to_grid(comp.y_mm)

    # ------------------------------------------------------------------ #
    # Step 2 – Overlap resolution
    # ------------------------------------------------------------------ #

    def _resolve_overlaps(self, components: list[ComponentPlacement]) -> None:
        """Push apart symbols whose bounding boxes overlap."""
        for _iteration in range(100):
            moved = False
            for i in range(len(components)):
                for j in range(i + 1, len(components)):
                    ci, cj = components[i], components[j]
                    bi = self._get_body_extent_mm(ci)
                    bj = self._get_body_extent_mm(cj)
                    safe = SAFE_SPACING_GRIDS * GRID_MM

                    # AABB overlap test with safe spacing
                    overlap_x = min(bi[2], bj[2]) - max(bi[0], bj[0]) + safe
                    overlap_y = min(bi[3], bj[3]) - max(bi[1], bj[1]) + safe

                    if overlap_x > 0 and overlap_y > 0:
                        # Push apart along shorter overlap axis
                        cx_i = (bi[0] + bi[2]) / 2
                        cx_j = (bj[0] + bj[2]) / 2
                        cy_i = (bi[1] + bi[3]) / 2
                        cy_j = (bj[1] + bj[3]) / 2

                        dx = cx_i - cx_j
                        dy = cy_i - cy_j

                        if abs(dx) < 0.01 and abs(dy) < 0.01:
                            dx = 1.0  # arbitrary direction

                        # Push mainly along the axis with smaller overlap
                        if overlap_x < overlap_y:
                            push = overlap_x / 2 + GRID_MM
                            sign = 1.0 if dx >= 0 else -1.0
                            ci.x_mm = snap_to_grid(ci.x_mm + sign * push)
                            cj.x_mm = snap_to_grid(cj.x_mm - sign * push)
                        else:
                            push = overlap_y / 2 + GRID_MM
                            sign = 1.0 if dy >= 0 else -1.0
                            ci.y_mm = snap_to_grid(ci.y_mm + sign * push)
                            cj.y_mm = snap_to_grid(cj.y_mm - sign * push)
                        moved = True

            if not moved:
                break

    def _get_body_extent_mm(
        self, comp: ComponentPlacement
    ) -> tuple[float, float, float, float]:
        """Body bounding box in schematic mm (min_x, min_y, max_x, max_y)."""
        sch_rot = compute_sch_rotation(comp)

        local_rect = None
        if comp.symbol_sexpr and comp.symbol_sexpr.strip():
            local_rect = extract_symbol_body_rect(comp.symbol_sexpr)
        if local_rect is None:
            local_rect = (-2.54, -1.27, 2.54, 1.27)

        # Transform all four corners through Y-flip + rotation
        corners_local = [
            (local_rect[0], local_rect[1]),
            (local_rect[2], local_rect[1]),
            (local_rect[0], local_rect[3]),
            (local_rect[2], local_rect[3]),
        ]

        rad = math.radians(sch_rot)
        cos_r, sin_r = math.cos(rad), math.sin(rad)

        xs, ys = [], []
        for lx, ly in corners_local:
            ly = -ly  # Y-flip (symbol Y-up → schematic Y-down)
            rx = lx * cos_r - ly * sin_r + comp.x_mm
            ry = lx * sin_r + ly * cos_r + comp.y_mm
            xs.append(rx)
            ys.append(ry)

        return (min(xs), min(ys), max(xs), max(ys))

    # ------------------------------------------------------------------ #
    # Step 3 – Pin map
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_pin_map(components: list[ComponentPlacement]) -> list[PinInfo]:
        """Compute all pin positions in schematic space.

        Uses ``comp.pin_map`` (pin_number → pad_number) so that the
        lookup key ``(reference, pad_number)`` matches WirePlacement
        fields ``start_pad`` / ``end_pad`` which carry **pad** numbers.
        """
        pins: list[PinInfo] = []
        for comp in components:
            sch_rot = compute_sch_rotation(comp)
            local_pins = (
                extract_pins_from_sexpr(comp.symbol_sexpr)
                if comp.symbol_sexpr and comp.symbol_sexpr.strip()
                else {}
            )
            if not local_pins:
                local_pins = {"1": (-5.08, 0.0), "2": (5.08, 0.0)}

            # pin_map: symbol_pin_number → footprint_pad_number
            pin_to_pad: dict[str, str] = dict(comp.pin_map) if comp.pin_map else {}

            rad = math.radians(sch_rot)
            cos_r, sin_r = math.cos(rad), math.sin(rad)

            for pin_num, (px, py) in local_pins.items():
                pad_num = pin_to_pad.get(pin_num, pin_num)
                py_flip = -py  # symbol Y-up → schematic Y-down
                rpx = px * cos_r - py_flip * sin_r
                rpy = px * sin_r + py_flip * cos_r
                sx = comp.x_mm + rpx
                sy = comp.y_mm + rpy
                pins.append(
                    PinInfo(
                        reference=comp.reference,
                        pad_number=pad_num,
                        x=sx,
                        y=sy,
                        gx=mm_to_grid(sx),
                        gy=mm_to_grid(sy),
                    )
                )
        return pins

    # ------------------------------------------------------------------ #
    # Step 4 – Net extraction from wire topology
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_nets(
        wires: list[WirePlacement],
        ref_pad_lookup: dict[tuple[str, str], PinInfo],
    ) -> list[list[PinInfo]]:
        """Determine nets by grouping transitively-connected pads.

        Uses Union-Find on (reference, pad_number) keys.
        Also groups via shared wire endpoint positions for chains that
        pass through junctions.
        """
        if not wires:
            return []

        # --- Phase 1: collect unique position → pad mappings ---
        # Quantize positions to 0.1 mm for matching
        def _qpos(x: float, y: float) -> tuple[int, int]:
            return (round(x * 10), round(y * 10))

        # position → set of (ref, pad)
        pos_pads: dict[tuple[int, int], set[tuple[str, str]]] = {}
        # wire endpoint positions → edges between positions
        pos_edges: list[tuple[tuple[int, int], tuple[int, int]]] = []

        for w in wires:
            p1 = _qpos(w.x1_mm, w.y1_mm)
            p2 = _qpos(w.x2_mm, w.y2_mm)
            pos_pads.setdefault(p1, set())
            pos_pads.setdefault(p2, set())

            if w.start_ref and w.start_pad:
                pos_pads[p1].add((w.start_ref, w.start_pad))
            if w.end_ref and w.end_pad:
                pos_pads[p2].add((w.end_ref, w.end_pad))

            pos_edges.append((p1, p2))

        # --- Phase 2: Union-Find on positions ---
        parent: dict[tuple[int, int], tuple[int, int]] = {
            p: p for p in pos_pads
        }

        def find(x: tuple[int, int]) -> tuple[int, int]:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: tuple[int, int], b: tuple[int, int]) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for p1, p2 in pos_edges:
            union(p1, p2)

        # --- Phase 3: group pads by root ---
        groups: dict[tuple[int, int], set[tuple[str, str]]] = {}
        for pos, pads in pos_pads.items():
            root = find(pos)
            groups.setdefault(root, set()).update(pads)

        # --- Phase 4: convert to PinInfo lists ---
        result: list[list[PinInfo]] = []
        for pad_set in groups.values():
            pin_list = []
            for key in pad_set:
                if key in ref_pad_lookup:
                    pin_list.append(ref_pad_lookup[key])
            if len(pin_list) >= 2:
                result.append(pin_list)

        return result

    # ------------------------------------------------------------------ #
    # Step 5 – Net cost estimation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _net_mst_cost(pins: list[PinInfo]) -> float:
        """Greedy MST cost (Manhattan) for net priority sorting."""
        if len(pins) < 2:
            return 0.0
        total = 0.0
        remaining = list(pins[1:])
        current = pins[0]
        while remaining:
            best_d = float("inf")
            best_i = 0
            for i, p in enumerate(remaining):
                d = abs(p.gx - current.gx) + abs(p.gy - current.gy)
                if d < best_d:
                    best_d = d
                    best_i = i
            total += best_d
            current = remaining.pop(best_i)
        return total

    # ------------------------------------------------------------------ #
    # Step 6 – Obstacle grid
    # ------------------------------------------------------------------ #

    def _compute_grid_bounds(
        self,
        pins: list[PinInfo],
        components: list[ComponentPlacement],
    ) -> tuple[int, int, int, int]:
        """(min_gx, min_gy, max_gx, max_gy) with margin."""
        all_gx = [p.gx for p in pins]
        all_gy = [p.gy for p in pins]
        for comp in components:
            ext = self._get_body_extent_mm(comp)
            all_gx += [mm_to_grid(ext[0]), mm_to_grid(ext[2])]
            all_gy += [mm_to_grid(ext[1]), mm_to_grid(ext[3])]
        if not all_gx:
            return (0, 0, 0, 0)
        return (
            min(all_gx) - MARGIN_GRIDS,
            min(all_gy) - MARGIN_GRIDS,
            max(all_gx) + MARGIN_GRIDS,
            max(all_gy) + MARGIN_GRIDS,
        )

    def _build_obstacle_grid(
        self,
        components: list[ComponentPlacement],
        grid_bounds: tuple[int, int, int, int],
        pin_map: list[PinInfo],
    ) -> set[tuple[int, int]]:
        """Mark grid cells occupied by symbol bodies as obstacles.

        Pin cells are excluded from obstacles so routes can reach them.
        """
        obstacles: set[tuple[int, int]] = set()
        pin_cells = {(p.gx, p.gy) for p in pin_map}

        for comp in components:
            ext = self._get_body_extent_mm(comp)
            gx1 = mm_to_grid(ext[0]) - BODY_PADDING_GRIDS
            gy1 = mm_to_grid(ext[1]) - BODY_PADDING_GRIDS
            gx2 = mm_to_grid(ext[2]) + BODY_PADDING_GRIDS
            gy2 = mm_to_grid(ext[3]) + BODY_PADDING_GRIDS

            for gx in range(gx1, gx2 + 1):
                for gy in range(gy1, gy2 + 1):
                    if (gx, gy) not in pin_cells:
                        obstacles.add((gx, gy))

        return obstacles

    # ------------------------------------------------------------------ #
    # Step 7 – Net routing (MST + A*)
    # ------------------------------------------------------------------ #

    def _route_net(
        self,
        pins: list[PinInfo],
        obstacles: set[tuple[int, int]],
        used_cells: set[tuple[int, int]],
        grid_bounds: tuple[int, int, int, int],
    ) -> list[WirePlacement]:
        """Route a multi-pin net using greedy MST + A*."""
        if len(pins) < 2:
            return []

        # Build MST edges by Prim-like greedy nearest-neighbour
        segments: list[WirePlacement] = []
        connected = [pins[0]]
        remaining = list(pins[1:])

        while remaining:
            best_d = float("inf")
            best_c: Optional[PinInfo] = None
            best_r: Optional[PinInfo] = None
            for c in connected:
                for r in remaining:
                    d = abs(c.gx - r.gx) + abs(c.gy - r.gy)
                    if d < best_d:
                        best_d = d
                        best_c = c
                        best_r = r

            if best_r is None or best_c is None:
                break

            remaining.remove(best_r)
            connected.append(best_r)

            # A* route for this MST edge
            path = self._a_star(
                (best_c.gx, best_c.gy),
                (best_r.gx, best_r.gy),
                obstacles,
                used_cells,
                grid_bounds,
            )

            if path and len(path) >= 2:
                wire_segs = self._path_to_wire_placements(
                    path, best_c, best_r
                )
                segments.extend(wire_segs)
                for cell in path:
                    used_cells.add(cell)
            else:
                # Fallback: simple L-shaped route
                wire_segs = self._l_route(best_c, best_r)
                segments.extend(wire_segs)
                # Mark L-route cells as used
                for seg in wire_segs:
                    for pt in (
                        (mm_to_grid(seg.x1_mm), mm_to_grid(seg.y1_mm)),
                        (mm_to_grid(seg.x2_mm), mm_to_grid(seg.y2_mm)),
                    ):
                        used_cells.add(pt)

        return segments

    # ------------------------------------------------------------------ #
    # A* pathfinding
    # ------------------------------------------------------------------ #

    @staticmethod
    def _a_star(
        start: tuple[int, int],
        goal: tuple[int, int],
        obstacles: set[tuple[int, int]],
        used_cells: set[tuple[int, int]],
        grid_bounds: tuple[int, int, int, int],
    ) -> Optional[list[tuple[int, int]]]:
        """A* with Manhattan routing, direction-aware costs.

        State: ``(gx, gy, direction)`` where direction ∈ {0,1,2,3,4}
            0 = start (no direction), 1 = right, 2 = left, 3 = down, 4 = up
        """
        if start == goal:
            return [start]

        min_gx, min_gy, max_gx, max_gy = grid_bounds
        DIRS = {1: (1, 0), 2: (-1, 0), 3: (0, 1), 4: (0, -1)}

        def heuristic(pos: tuple[int, int]) -> int:
            return abs(pos[0] - goal[0]) + abs(pos[1] - goal[1])

        # Priority queue: (f, g, gx, gy, direction, counter)
        counter = 0
        open_set: list[tuple[float, float, int, int, int, int]] = []
        heapq.heappush(
            open_set, (heuristic(start), 0.0, start[0], start[1], 0, counter)
        )

        g_scores: dict[tuple[int, int, int], float] = {
            (start[0], start[1], 0): 0.0
        }
        came_from: dict[tuple[int, int, int], tuple[int, int, int]] = {}

        while open_set:
            _f, g, cx, cy, c_dir, _ = heapq.heappop(open_set)

            if (cx, cy) == goal:
                # Reconstruct path
                path: list[tuple[int, int]] = [(cx, cy)]
                state = (cx, cy, c_dir)
                while state in came_from:
                    state = came_from[state]
                    pos = (state[0], state[1])
                    if not path or path[-1] != pos:
                        path.append(pos)
                path.reverse()
                return path

            state_key = (cx, cy, c_dir)
            if g > g_scores.get(state_key, float("inf")):
                continue

            for d, (dx, dy) in DIRS.items():
                nx, ny = cx + dx, cy + dy

                # Bounds check
                if nx < min_gx or nx > max_gx or ny < min_gy or ny > max_gy:
                    continue

                # Obstacle check (always allow start and goal cells)
                if (nx, ny) in obstacles and (nx, ny) != goal:
                    continue

                # Cost
                move_cost = COST_MOVE
                if c_dir != 0 and d != c_dir:
                    move_cost += COST_TURN
                if (nx, ny) in used_cells:
                    move_cost += COST_OVERLAP

                new_g = g + move_cost
                new_state = (nx, ny, d)

                if new_g < g_scores.get(new_state, float("inf")):
                    g_scores[new_state] = new_g
                    counter += 1
                    heapq.heappush(
                        open_set,
                        (new_g + heuristic((nx, ny)), new_g, nx, ny, d, counter),
                    )
                    came_from[new_state] = state_key

        return None  # no path found

    # ------------------------------------------------------------------ #
    # Path → WirePlacement conversion
    # ------------------------------------------------------------------ #

    @staticmethod
    def _path_to_wire_placements(
        path: list[tuple[int, int]],
        start_pin: PinInfo,
        end_pin: PinInfo,
    ) -> list[WirePlacement]:
        """Compress a grid path into direction-change waypoints, then
        emit one ``WirePlacement`` per straight segment.

        First segment is tagged with *start_pin*, last with *end_pin*.
        """
        if len(path) < 2:
            return []

        # Compress into waypoints (keep only direction-change points)
        waypoints = [path[0]]
        for i in range(1, len(path) - 1):
            prev_d = (
                path[i][0] - path[i - 1][0],
                path[i][1] - path[i - 1][1],
            )
            next_d = (
                path[i + 1][0] - path[i][0],
                path[i + 1][1] - path[i][1],
            )
            if prev_d != next_d:
                waypoints.append(path[i])
        waypoints.append(path[-1])

        segments: list[WirePlacement] = []
        n = len(waypoints)
        for i in range(n - 1):
            wp = WirePlacement(
                x1_mm=grid_to_mm(waypoints[i][0]),
                y1_mm=grid_to_mm(waypoints[i][1]),
                x2_mm=grid_to_mm(waypoints[i + 1][0]),
                y2_mm=grid_to_mm(waypoints[i + 1][1]),
            )
            if i == 0:
                wp.start_ref = start_pin.reference
                wp.start_pad = start_pin.pad_number
            if i == n - 2:
                wp.end_ref = end_pin.reference
                wp.end_pad = end_pin.pad_number
            segments.append(wp)

        return segments

    # ------------------------------------------------------------------ #
    # Fallback L-route
    # ------------------------------------------------------------------ #

    @staticmethod
    def _l_route(pin_a: PinInfo, pin_b: PinInfo) -> list[WirePlacement]:
        """Simple two-segment Manhattan route (horizontal-first)."""
        ax = grid_to_mm(pin_a.gx)
        ay = grid_to_mm(pin_a.gy)
        bx = grid_to_mm(pin_b.gx)
        by = grid_to_mm(pin_b.gy)

        mid_x, mid_y = bx, ay  # horizontal first, then vertical

        segs: list[WirePlacement] = []
        if abs(ax - mid_x) > 0.01:
            segs.append(
                WirePlacement(
                    x1_mm=ax, y1_mm=ay, x2_mm=mid_x, y2_mm=mid_y,
                    start_ref=pin_a.reference, start_pad=pin_a.pad_number,
                )
            )
        if abs(mid_y - by) > 0.01:
            wp = WirePlacement(x1_mm=mid_x, y1_mm=mid_y, x2_mm=bx, y2_mm=by,
                               end_ref=pin_b.reference, end_pad=pin_b.pad_number)
            if not segs:
                wp.start_ref = pin_a.reference
                wp.start_pad = pin_a.pad_number
            segs.append(wp)

        if not segs:
            segs.append(
                WirePlacement(
                    x1_mm=ax, y1_mm=ay, x2_mm=bx, y2_mm=by,
                    start_ref=pin_a.reference, start_pad=pin_a.pad_number,
                    end_ref=pin_b.reference, end_pad=pin_b.pad_number,
                )
            )
        return segs
