"""Library Bridge – discovers and manages KiCad footprint and symbol libraries.

Responsibilities:
- Auto-detect KiCad 9 installation and user library paths
- Scan directories for .kicad_mod footprint files and .kicad_sym symbol files
- Parse footprint geometry (pads, lines, arcs, text) for canvas rendering
- Parse symbol definitions for schematic export
- Maintain an in-memory index of available libraries for quick lookup

The bridge is initialised once at startup and shared across the application.
Footprint and symbol data is parsed on demand and cached.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from kicad_parser import parse_sexpr, find_node, find_all, find_value


# ---------------------------------------------------------------------------
# KiCad path detection
# ---------------------------------------------------------------------------

def detect_kicad_base() -> Optional[Path]:
    """Try to find the KiCad 9 installation directory."""
    if os.name == "nt":
        candidates = [
            Path(r"C:\Program Files\KiCad\9.0"),
            Path(r"C:\Program Files (x86)\KiCad\9.0"),
        ]
        prog = os.environ.get("PROGRAMFILES", "")
        if prog:
            candidates.append(Path(prog) / "KiCad" / "9.0")
    else:
        candidates = [
            Path("/usr/share/kicad"),
            Path("/usr/local/share/kicad"),
            Path.home() / ".local" / "share" / "kicad" / "9.0",
        ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def default_footprint_paths() -> list[Path]:
    """Return auto-detected footprint library directories."""
    # base = detect_kicad_base()  # DEBUG: standard libraries temporarily disabled
    paths: list[Path] = []
    # if base:
    #     for sub in ("share/kicad/footprints", "footprints"):
    #         fp_dir = base / sub
    #         if fp_dir.is_dir():
    #             paths.append(fp_dir)
    #             break
    if os.name == "nt":
        user_fp = Path(os.path.expandvars(r"%USERPROFILE%\Documents\KiCad\9.0\footprints"))
    else:
        user_fp = Path.home() / ".local" / "share" / "kicad" / "9.0" / "footprints"
    if user_fp.is_dir():
        paths.append(user_fp)
    return paths


def default_symbol_paths() -> list[Path]:
    """Return auto-detected symbol library directories."""
    # base = detect_kicad_base()  # DEBUG: standard libraries temporarily disabled
    paths: list[Path] = []
    # if base:
    #     for sub in ("share/kicad/symbols", "symbols"):
    #         sym_dir = base / sub
    #         if sym_dir.is_dir():
    #             paths.append(sym_dir)
    #             break
    if os.name == "nt":
        user_sym = Path(os.path.expandvars(r"%USERPROFILE%\Documents\KiCad\9.0\symbols"))
    else:
        user_sym = Path.home() / ".local" / "share" / "kicad" / "9.0" / "symbols"
    if user_sym.is_dir():
        paths.append(user_sym)
    return paths


# ---------------------------------------------------------------------------
# Data classes – footprint geometry
# ---------------------------------------------------------------------------

@dataclass
class PadData:
    number: str
    pad_type: str
    shape: str
    x: float
    y: float
    width: float
    height: float
    layers: list[str]
    drill: float = 0.0
    roundrect_rratio: float = 0.0


@dataclass
class LineData:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: str


@dataclass
class CircleData:
    cx: float
    cy: float
    end_x: float
    end_y: float
    width: float
    layer: str

    @property
    def radius(self) -> float:
        return ((self.end_x - self.cx) ** 2 + (self.end_y - self.cy) ** 2) ** 0.5


@dataclass
class ArcData:
    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    width: float
    layer: str


@dataclass
class RectData:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: str


@dataclass
class PolyData:
    points: list[tuple[float, float]]
    width: float
    layer: str


@dataclass
class FootprintData:
    """Fully parsed footprint geometry from a .kicad_mod file."""
    library: str
    name: str
    description: str
    pads: list[PadData]
    lines: list[LineData]
    circles: list[CircleData]
    arcs: list[ArcData]
    rects: list[RectData]
    polys: list[PolyData]
    raw_sexpr: list
    file_path: Path

    @property
    def full_name(self) -> str:
        return f"{self.library}:{self.name}"


# ---------------------------------------------------------------------------
# Data classes – symbol geometry
# ---------------------------------------------------------------------------

@dataclass
class PinData:
    name: str
    number: str
    x: float
    y: float
    length: float
    direction: float
    electrical_type: str
    unit: int = 0


@dataclass
class SymbolGraphicRect:
    x1: float
    y1: float
    x2: float
    y2: float
    unit: int = 0


@dataclass
class SymbolGraphicLine:
    points: list[tuple[float, float]]
    unit: int = 0


@dataclass
class SymbolGraphicCircle:
    cx: float
    cy: float
    radius: float
    unit: int = 0


@dataclass
class SymbolData:
    """Parsed symbol definition from a .kicad_sym file."""
    library: str
    name: str
    description: str
    pins: list[PinData]
    rectangles: list[SymbolGraphicRect]
    polylines: list[SymbolGraphicLine]
    circles: list[SymbolGraphicCircle]
    raw_sexpr: list
    properties: dict[str, str]

    @property
    def full_name(self) -> str:
        return f"{self.library}:{self.name}"


# ---------------------------------------------------------------------------
# Lightweight index entries
# ---------------------------------------------------------------------------

@dataclass
class FootprintInfo:
    library: str
    name: str
    path: Path

    @property
    def full_name(self) -> str:
        return f"{self.library}:{self.name}"


@dataclass
class SymbolInfo:
    library: str
    name: str
    lib_file_path: Path

    @property
    def full_name(self) -> str:
        return f"{self.library}:{self.name}"


# ---------------------------------------------------------------------------
# Footprint file parser
# ---------------------------------------------------------------------------

def parse_footprint_file(path: Path, library: str = "") -> FootprintData:
    text = path.read_text(encoding="utf-8")
    tree = parse_sexpr(text)
    name = str(tree[1]) if len(tree) > 1 else path.stem
    desc = str(find_value(tree, "descr", ""))

    return FootprintData(
        library=library, name=name, description=desc,
        pads=[_parse_pad(n) for n in find_all(tree, "pad")],
        lines=[_parse_fp_line(n) for n in find_all(tree, "fp_line")],
        circles=[_parse_fp_circle(n) for n in find_all(tree, "fp_circle")],
        arcs=[_parse_fp_arc(n) for n in find_all(tree, "fp_arc")],
        rects=[_parse_fp_rect(n) for n in find_all(tree, "fp_rect")],
        polys=[_parse_fp_poly(n) for n in find_all(tree, "fp_poly")],
        raw_sexpr=tree, file_path=path,
    )


def _parse_pad(node: list) -> PadData:
    number = str(node[1]) if len(node) > 1 else ""
    pad_type = str(node[2]) if len(node) > 2 else "smd"
    shape = str(node[3]) if len(node) > 3 else "rect"
    at = find_node(node, "at")
    x = float(at[1]) if at and len(at) > 1 else 0.0
    y = float(at[2]) if at and len(at) > 2 else 0.0
    sz = find_node(node, "size")
    w = float(sz[1]) if sz and len(sz) > 1 else 1.0
    h = float(sz[2]) if sz and len(sz) > 2 else 1.0
    layers_n = find_node(node, "layers")
    layers = [str(l) for l in layers_n[1:]] if layers_n else ["F.Cu"]
    drill_n = find_node(node, "drill")
    drill = float(drill_n[1]) if drill_n and len(drill_n) > 1 else 0.0
    rratio = float(find_value(node, "roundrect_rratio", 0.0))
    return PadData(number=number, pad_type=pad_type, shape=shape,
                   x=x, y=y, width=w, height=h,
                   layers=layers, drill=drill, roundrect_rratio=rratio)


def _parse_fp_line(node: list) -> LineData:
    start = find_node(node, "start")
    end = find_node(node, "end")
    stroke = find_node(node, "stroke")
    layer = str(find_value(node, "layer", "F.SilkS"))
    x1 = float(start[1]) if start and len(start) > 1 else 0.0
    y1 = float(start[2]) if start and len(start) > 2 else 0.0
    x2 = float(end[1]) if end and len(end) > 1 else 0.0
    y2 = float(end[2]) if end and len(end) > 2 else 0.0
    return LineData(x1=x1, y1=y1, x2=x2, y2=y2, width=_stroke_w(stroke), layer=layer)


def _parse_fp_circle(node: list) -> CircleData:
    center = find_node(node, "center")
    end = find_node(node, "end")
    stroke = find_node(node, "stroke")
    layer = str(find_value(node, "layer", "F.SilkS"))
    cx = float(center[1]) if center and len(center) > 1 else 0.0
    cy = float(center[2]) if center and len(center) > 2 else 0.0
    ex = float(end[1]) if end and len(end) > 1 else 0.0
    ey = float(end[2]) if end and len(end) > 2 else 0.0
    return CircleData(cx=cx, cy=cy, end_x=ex, end_y=ey, width=_stroke_w(stroke), layer=layer)


def _parse_fp_arc(node: list) -> ArcData:
    start = find_node(node, "start")
    mid = find_node(node, "mid")
    end = find_node(node, "end")
    stroke = find_node(node, "stroke")
    layer = str(find_value(node, "layer", "F.SilkS"))
    sx = float(start[1]) if start and len(start) > 1 else 0.0
    sy = float(start[2]) if start and len(start) > 2 else 0.0
    mx = float(mid[1]) if mid and len(mid) > 1 else 0.0
    my = float(mid[2]) if mid and len(mid) > 2 else 0.0
    ex = float(end[1]) if end and len(end) > 1 else 0.0
    ey = float(end[2]) if end and len(end) > 2 else 0.0
    return ArcData(start_x=sx, start_y=sy, mid_x=mx, mid_y=my,
                   end_x=ex, end_y=ey, width=_stroke_w(stroke), layer=layer)


def _parse_fp_rect(node: list) -> RectData:
    start = find_node(node, "start")
    end = find_node(node, "end")
    stroke = find_node(node, "stroke")
    layer = str(find_value(node, "layer", "F.SilkS"))
    x1 = float(start[1]) if start and len(start) > 1 else 0.0
    y1 = float(start[2]) if start and len(start) > 2 else 0.0
    x2 = float(end[1]) if end and len(end) > 1 else 0.0
    y2 = float(end[2]) if end and len(end) > 2 else 0.0
    return RectData(x1=x1, y1=y1, x2=x2, y2=y2, width=_stroke_w(stroke), layer=layer)


def _parse_fp_poly(node: list) -> PolyData:
    pts_node = find_node(node, "pts")
    stroke = find_node(node, "stroke")
    layer = str(find_value(node, "layer", "F.SilkS"))
    points: list[tuple[float, float]] = []
    if pts_node:
        for xy in find_all(pts_node, "xy"):
            points.append((float(xy[1]) if len(xy) > 1 else 0.0,
                           float(xy[2]) if len(xy) > 2 else 0.0))
    return PolyData(points=points, width=_stroke_w(stroke), layer=layer)


def _stroke_w(stroke_node: list | None) -> float:
    if stroke_node:
        w = find_value(stroke_node, "width")
        if w is not None:
            return float(w)
    return 0.12


# ---------------------------------------------------------------------------
# Symbol library parser
# ---------------------------------------------------------------------------

def parse_symbol_library(path: Path) -> list[SymbolData]:
    text = path.read_text(encoding="utf-8")
    tree = parse_sexpr(text)
    lib_name = path.stem
    symbols: list[SymbolData] = []

    for sym_node in find_all(tree, "symbol"):
        sym_name = str(sym_node[1]) if len(sym_node) > 1 else ""
        if not sym_name:
            continue
        desc = ""
        props: dict[str, str] = {}
        for prop_node in find_all(sym_node, "property"):
            if len(prop_node) >= 3:
                pn, pv = str(prop_node[1]), str(prop_node[2])
                props[pn] = pv
                if pn == "Description":
                    desc = pv
        pins, rects, polys, circs = [], [], [], []
        for sub_sym in find_all(sym_node, "symbol"):
            # Extract unit number from sub-symbol name (e.g. "74HC00_2_1" → 2)
            sub_name = str(sub_sym[1]) if len(sub_sym) > 1 else ""
            unit_num = 0
            if sub_name.startswith(sym_name + "_"):
                parts = sub_name[len(sym_name) + 1:].split("_")
                if parts and parts[0].isdigit():
                    unit_num = int(parts[0])
            for p in find_all(sub_sym, "pin"):
                pd = _parse_sym_pin(p)
                pd.unit = unit_num
                pins.append(pd)
            for r in find_all(sub_sym, "rectangle"):
                rd = _parse_sym_rect(r)
                rd.unit = unit_num
                rects.append(rd)
            for pl in find_all(sub_sym, "polyline"):
                pld = _parse_sym_polyline(pl)
                pld.unit = unit_num
                polys.append(pld)
            for c in find_all(sub_sym, "circle"):
                cd = _parse_sym_circle(c)
                cd.unit = unit_num
                circs.append(cd)
        for p in find_all(sym_node, "pin"):
            pins.append(_parse_sym_pin(p))
        for r in find_all(sym_node, "rectangle"):
            rects.append(_parse_sym_rect(r))
        symbols.append(SymbolData(
            library=lib_name, name=sym_name, description=desc,
            pins=pins, rectangles=rects, polylines=polys, circles=circs,
            raw_sexpr=sym_node, properties=props,
        ))
    return symbols


def _parse_sym_pin(node: list) -> PinData:
    etype = str(node[1]) if len(node) > 1 else "passive"
    at = find_node(node, "at")
    x = float(at[1]) if at and len(at) > 1 else 0.0
    y = float(at[2]) if at and len(at) > 2 else 0.0
    d = float(at[3]) if at and len(at) > 3 else 0.0
    ln = find_node(node, "length")
    length = float(ln[1]) if ln and len(ln) > 1 else 2.54
    nm = find_node(node, "name")
    name = str(nm[1]) if nm and len(nm) > 1 else ""
    nu = find_node(node, "number")
    number = str(nu[1]) if nu and len(nu) > 1 else ""
    return PinData(name=name, number=number, x=x, y=y,
                   length=length, direction=d, electrical_type=etype)


def _parse_sym_rect(node: list) -> SymbolGraphicRect:
    s = find_node(node, "start")
    e = find_node(node, "end")
    return SymbolGraphicRect(
        x1=float(s[1]) if s and len(s) > 1 else 0,
        y1=float(s[2]) if s and len(s) > 2 else 0,
        x2=float(e[1]) if e and len(e) > 1 else 0,
        y2=float(e[2]) if e and len(e) > 2 else 0,
    )


def _parse_sym_polyline(node: list) -> SymbolGraphicLine:
    pts = find_node(node, "pts")
    points = []
    if pts:
        for xy in find_all(pts, "xy"):
            points.append((float(xy[1]) if len(xy) > 1 else 0,
                           float(xy[2]) if len(xy) > 2 else 0))
    return SymbolGraphicLine(points=points)


def _parse_sym_circle(node: list) -> SymbolGraphicCircle:
    c = find_node(node, "center")
    r = find_value(node, "radius", 1.0)
    return SymbolGraphicCircle(
        cx=float(c[1]) if c and len(c) > 1 else 0,
        cy=float(c[2]) if c and len(c) > 2 else 0,
        radius=float(r),
    )


# ---------------------------------------------------------------------------
# Main Library Bridge class
# ---------------------------------------------------------------------------

class LibraryBridge:
    """Discovers and manages KiCad footprint and symbol libraries."""

    def __init__(self) -> None:
        self._fp_paths: list[Path] = default_footprint_paths()
        self._sym_paths: list[Path] = default_symbol_paths()
        self._footprints: dict[str, FootprintInfo] = {}
        self._symbols: dict[str, SymbolInfo] = {}
        self._kicad_base: Optional[Path] = detect_kicad_base()

    @property
    def kicad_base(self) -> Optional[Path]:
        return self._kicad_base

    def scan(self) -> tuple[int, int]:
        """Scan all paths. Returns (footprint_count, symbol_count)."""
        return self._scan_footprints(), self._scan_symbols()

    def _scan_footprints(self) -> int:
        self._footprints.clear()
        for base in self._fp_paths:
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if not d.is_dir() or d.suffix != ".pretty":
                    continue
                lib = d.stem
                for f in sorted(d.glob("*.kicad_mod")):
                    info = FootprintInfo(library=lib, name=f.stem, path=f)
                    self._footprints[info.full_name] = info
        return len(self._footprints)

    def _scan_symbols(self) -> int:
        self._symbols.clear()
        for base in self._sym_paths:
            if not base.is_dir():
                continue
            for sf in sorted(base.glob("*.kicad_sym")):
                lib = sf.stem
                try:
                    tree = parse_sexpr(sf.read_text(encoding="utf-8"))
                    for sn in find_all(tree, "symbol"):
                        name = str(sn[1]) if len(sn) > 1 else ""
                        if name:
                            self._symbols[f"{lib}:{name}"] = SymbolInfo(
                                library=lib, name=name, lib_file_path=sf)
                except Exception:
                    continue
        return len(self._symbols)

    # -- Footprint access --
    def get_footprint(self, full_name: str) -> Optional[FootprintInfo]:
        return self._footprints.get(full_name)

    def search_footprints(self, pattern: str) -> list[FootprintInfo]:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        return [f for f in self._footprints.values() if rx.search(f.full_name)]

    def all_footprint_libraries(self) -> list[str]:
        return sorted({f.library for f in self._footprints.values()})

    def footprints_in(self, library: str) -> list[FootprintInfo]:
        return sorted([f for f in self._footprints.values() if f.library == library],
                      key=lambda x: x.name)

    def parse_footprint(self, full_name: str) -> Optional[FootprintData]:
        info = self._footprints.get(full_name)
        if not info:
            return None
        try:
            return parse_footprint_file(info.path, info.library)
        except Exception:
            return None

    # -- Symbol access --
    def get_symbol(self, full_name: str) -> Optional[SymbolInfo]:
        return self._symbols.get(full_name)

    def search_symbols(self, pattern: str) -> list[SymbolInfo]:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        return [s for s in self._symbols.values() if rx.search(s.full_name)]

    def all_symbol_libraries(self) -> list[str]:
        return sorted({s.library for s in self._symbols.values()})

    def symbols_in(self, library: str) -> list[SymbolInfo]:
        return sorted([s for s in self._symbols.values() if s.library == library],
                      key=lambda x: x.name)

    def parse_symbol(self, full_name: str) -> Optional[SymbolData]:
        info = self._symbols.get(full_name)
        if not info:
            return None
        try:
            for s in parse_symbol_library(info.lib_file_path):
                if s.name == info.name:
                    return s
        except Exception:
            pass
        return None

    # -- Path config --
    @property
    def footprint_paths(self) -> list[Path]:
        return list(self._fp_paths)

    @property
    def symbol_paths(self) -> list[Path]:
        return list(self._sym_paths)

    def set_footprint_paths(self, paths: list[str] | list[Path]) -> None:
        self._fp_paths = [Path(p) for p in paths if Path(p).is_dir()]

    def set_symbol_paths(self, paths: list[str] | list[Path]) -> None:
        self._sym_paths = [Path(p) for p in paths if Path(p).is_dir()]

    def add_footprint_path(self, path: str | Path) -> bool:
        p = Path(path)
        if p.is_dir() and p not in self._fp_paths:
            self._fp_paths.append(p)
            return True
        return False

    def add_symbol_path(self, path: str | Path) -> bool:
        p = Path(path)
        if p.is_dir() and p not in self._sym_paths:
            self._sym_paths.append(p)
            return True
        return False

    def read_footprint_sexpr(self, library: str, name: str) -> str:
        """Return raw .kicad_mod text for the given footprint."""
        full = f"{library}:{name}"
        info = self._footprints.get(full)
        if not info or not info.path.is_file():
            return ""
        try:
            return info.path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def read_symbol_sexpr(self, library: str, name: str) -> str:
        """Return the raw S-expression block for one symbol from its .kicad_sym."""
        full = f"{library}:{name}"
        info = self._symbols.get(full)
        if not info or not info.lib_file_path.is_file():
            return ""
        try:
            from kicad_parser import parse_sexpr, find_all, serialize_sexpr
            text = info.lib_file_path.read_text(encoding="utf-8")
            tree = parse_sexpr(text)
            if not tree or not isinstance(tree, list):
                return ""
            for sym_node in find_all(tree, "symbol"):
                if len(sym_node) >= 2 and sym_node[1] == name:
                    return serialize_sexpr(sym_node)
            return ""
        except Exception:
            return ""

    @property
    def footprint_count(self) -> int:
        return len(self._footprints)

    @property
    def symbol_count(self) -> int:
        return len(self._symbols)
