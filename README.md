# PCB → KiCad Reverse Engineering Tool

PySide6 desktop application for reverse-engineering PCBs from photographs.
Place KiCad footprints on top of a board photo, assign schematic symbols and net names,
then export a complete **KiCad 9** project (`.kicad_pro` + `.kicad_pcb` + `.kicad_sch`).

## Status

> **Work in progress — implemented but not yet fully tested.**

---

## Implemented features

### Core canvas
- Load top/bottom PCB photographs as background layers
- Adjust bottom-layer opacity and mirror (for two-sided boards)
- Zoom/pan canvas (`QGraphicsView`)

### Footprint placement
- Library browser (left dock) — scans KiCad footprint `.kicad_mod` files
- Drag footprint from browser onto canvas
- Move, rotate placed footprints
- Auto-increment reference (R1, C2, U3 …) based on library name

### Symbol linking
- Link any placed footprint to a KiCad schematic symbol
- Symbol browser dialog; symbol S-expression is read from `.kicad_sym` files

### Net / pad connections *(implemented, untested)*
- **"Connect Nets" toolbar button** — toggle connect mode
- Click any pad → `QInputDialog` prompts for net name
- Net names stored per pad (`pad_nets: dict[str, str]`)
- Pads display colour-coded overlay: teal = unassigned, green = assigned, yellow = hovered
- Net assignments survive project save/load

### KiCad 9 export *(implemented, untested)*
- Generates `.kicad_pro`, `.kicad_pcb`, `.kicad_sch` in a chosen directory
- PCB file: real footprint S-expressions (from `.kicad_mod`) with corrected position / layer / reference / value
- PCB file: net table `(net N "name")` fully populated from pad assignments
- PCB file: net clauses injected into each pad node
- Schematic file: linked symbol instances; placeholder symbols for unlinked components
- Fallback placeholder footprint (2-pad SMD rectangle) when no `.kicad_mod` is available

### Project save/load
- `.p2k` JSON format — saves component list, image paths, library paths, pixels/mm calibration, pad nets

### Coordinate system
- Configurable pixels-per-mm scale
- Origin offset (mm) for aligning canvas coordinates to real PCB coordinates

---

## Requirements

```
Python >= 3.11
PySide6
KiCad 9 installed (for library files; optional but recommended)
```

Install dependencies:
```bash
pip install PySide6
```

## Run

```bash
python main.py
```

## Project structure

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `ui_main.py` | `MainWindow` — toolbar, docks, all UI slots |
| `footprint_item.py` | `FootprintItem` + `PadGraphicsItem` — canvas items |
| `kicad_generator.py` | `KicadPcbWriter`, `KicadSchWriter`, `KicadProjectWriter` |
| `kicad_project.py` | Orchestrates export — builds `ComponentPlacement` list |
| `kicad_parser.py` | S-expression parser/serialiser for `.kicad_mod` / `.kicad_sym` |
| `library_bridge.py` | Scans KiCad library directories, reads footprint/symbol S-expressions |
| `image_engine.py` | Loads top/bottom photos into the scene |
| `coordinate_system.py` | Pixel ↔ mm conversion |
| `project_manager.py` | `.p2k` JSON save/load |

---

## Known gaps / TODO

- [ ] Net visual ratsnest lines on canvas (wires between connected pads)
- [ ] Schematic net labels (`net_label` nodes in `.kicad_sch`)
- [ ] Full end-to-end export test with a real board
- [ ] Undo/redo
- [ ] Multi-selection and bulk property edit
- [ ] DRC-style net consistency check before export
