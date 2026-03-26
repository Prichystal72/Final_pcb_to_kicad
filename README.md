# PCB → KiCad Reverse Engineering Tool

**Version 1.0 beta** · Author: Jaroslav Přichystal · Organisation: Prichy

PySide6 desktop application for reverse-engineering PCBs from photographs.
Load top and bottom photos of a real circuit board, preprocess them with a
built-in image editor, place KiCad footprints on top of the overlay, draw
wires, link schematic symbols, and export a complete **KiCad 9** project
(`.kicad_pro` + `.kicad_pcb` + `.kicad_sch`).

---

## Quick start

### Requirements

| Dependency | Version |
|---|---|
| Python | >= 3.11 |
| PySide6 | >= 6.6 |
| OpenCV (`opencv-python`) | >= 4.8 |
| Pillow | >= 10.0 |
| NumPy | >= 1.24 |
| KiCad 9 | installed — library files (`.kicad_mod`, `.kicad_sym`) are needed |

### Installation

```bash
pip install PySide6 opencv-python Pillow numpy
```

### Run

```bash
python main.py
```

---

## Typical workflow

Below is a step-by-step guide to reverse-engineering a PCB from photographs.

### 1. Create or open a project

Launch the application. Use **File → New Project** (`Ctrl+N`) or
**File → Open Project** (`Ctrl+O`) to load an existing `.p2k` file.
Configure footprint and symbol library paths via **Tools → Path Settings**.

### 2. Load and edit PCB photographs

Use **View → Load Top Photo** and **View → Load Bottom Photo** to import
images (`.png`, `.jpg`, `.bmp`).

> **Tip:** Load the top photo first. If you try to load the bottom layer
> without a top layer, the application issues a warning.

Open the built-in **Image Editor** via **View → Edit Top Photo** (or
bottom). The editor provides a 10-step processing pipeline:

| # | Step | Description |
|---|---|---|
| 0 | Distortion correction | Barrel / pincushion compensation (`cv2.undistort`) |
| 1 | Crop | Interactive rubber-band crop |
| 2 | Rotation | Free-angle rotation -180 ° .. +180 ° |
| 3 | Scale | Resize 5 % .. 500 % |
| 4 | Mirror | Horizontal / vertical flip |
| 5 | Brightness & contrast | Adjust brightness (-100..100) and contrast (50..300) |
| 6 | Gamma | Gamma correction (20..500, 100 = 1.0) |
| 7 | Sharpen | Unsharp-mask sharpening |
| 8 | Denoise | Non-local-means denoising |
| 9 | Greyscale | Convert to greyscale |
| 10 | Invert | Colour inversion |

The editor supports **template overlay** (the other layer shown on top for
alignment), EXIF auto-orientation for mobile photos, and live preview.

**Editor keyboard shortcuts:**
- `F` – Zoom to fit
- `1` – Zoom 1:1
- `+` / `-` – Zoom in / out
- Right-click or middle-click drag – Pan

### 3. Align the layers

After importing both photos, use the **right-side Properties panel** to
adjust per-layer opacity, brightness, mirror, and scale. Use the **Joint
Scale** slider to resize both layers simultaneously so the footprints match
the real board dimensions. Set **Pixels/mm** and **Origin Offset** for
accurate coordinate mapping.

### 4. Place footprints

Browse KiCad footprint libraries in the **left dock** panel. Search for a
footprint and click **Place Footprint** (`P`) or use **Place → Place
Footprint** from the menu bar. Click on the canvas to place the component.

- **Move** — drag with the mouse
- **Rotate** — press `R` (90 ° increments)
- **Delete** — press `Del`

The application auto-increments references (R1, R2, C1, C2, U1 …) based on
the library prefix.

### 5. Link schematic symbols

Right-click a placed footprint and assign a KiCad schematic symbol
(`.kicad_sym`) via the **Symbol Browser** dialog. Pin-to-pad mapping is
created automatically.

### 6. Assign nets

Use the **Draw Wire** tool (`W`) to draw copper connections between pads.
Assign net names to pads:

- Pads are colour-coded: **teal** = unassigned, **green** = assigned,
  **yellow** = hovered.
- Add **junctions** (`J`) where wires cross.

### 7. Export

Use **File → Export KiCad 9 Project** (`Ctrl+E`) to generate the output
files in a chosen directory:

| File | Content |
|---|---|
| `.kicad_pro` | Project metadata |
| `.kicad_pcb` | PCB layout — real footprint S-expressions with position, layer, reference, value, pads, net assignments |
| `.kicad_sch` | Schematic — symbol instances, wires, net labels. An A* Manhattan auto-router lays out the schematic wires. |

Open the generated project in KiCad 9 for further editing.

---

## Keyboard shortcuts (main window)

| Key | Action |
|---|---|
| `Ctrl+N` | New project |
| `Ctrl+O` | Open project |
| `Ctrl+S` | Save project |
| `Ctrl+Shift+S` | Save project as… |
| `Ctrl+E` | Export KiCad 9 project |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+A` | Select all |
| `Ctrl+=` / `Ctrl+-` | Zoom in / out |
| `Ctrl+0` | Fit canvas to screen |
| `R` | Rotate selected component 90 ° |
| `W` | Toggle wire drawing mode |
| `J` | Add junction |
| `P` | Place footprint |
| `Del` | Delete selected |
| `Esc` | Cancel / deselect |

---

## Project file format (`.p2k`)

Projects are stored as plain JSON:

```json
{
  "version": "0.4.0",
  "settings": {
    "footprint_paths": ["..."],
    "symbol_paths": ["..."],
    "pixels_per_mm": 10.0,
    "origin_offset_mm": [0.0, 0.0]
  },
  "images": {
    "top": "path/to/top.png",
    "bottom": "path/to/bottom.png"
  },
  "components": [
    {
      "uid": "FP_1",
      "footprint_lib": "00_basic",
      "footprint_name": "R_0805_2012Metric",
      "symbol_lib": "RE_passive",
      "symbol_name": "R",
      "reference": "R1",
      "value": "10k",
      "x_px": 320.0,
      "y_px": 180.0,
      "rotation": 90.0,
      "layer": "F.Cu",
      "pad_nets": {"1": "VCC", "2": "GND"},
      "pin_map": {"1": "1", "2": "2"}
    }
  ]
}
```

Image-editor parameters and layer alignment settings (opacity, brightness,
mirror, scale, offset) are also stored in the project file to ensure
reproducible results.

---

## Project structure

| File | Purpose |
|---|---|
| `main.py` | Application entry point |
| `ui_main.py` | Main window — menu bar, toolbar, docks, all UI logic |
| `image_editor.py` | Modal image-preprocessing dialog (10-step pipeline) |
| `image_engine.py` | Top/bottom photo layer manager (opacity, brightness, mirror, scale) |
| `footprint_item.py` | `FootprintItem` + `PadGraphicsItem` — canvas graphics items |
| `wire_item.py` | `WireItem` + `JunctionItem` — wire/junction graphics items |
| `kicad_generator.py` | KiCad 9 file writers (`.kicad_pro`, `.kicad_pcb`, `.kicad_sch`) |
| `kicad_project.py` | Export orchestrator — builds component-placement list |
| `kicad_parser.py` | S-expression parser/serialiser for `.kicad_mod` / `.kicad_sym` |
| `schematic_router.py` | A* Manhattan auto-router for schematic wires |
| `library_bridge.py` | Scans KiCad library directories, reads footprint/symbol data |
| `coordinate_system.py` | Pixel ↔ mm conversion and origin offset |
| `project_manager.py` | `.p2k` JSON project save/load |
| `config_manager.py` | Application-level settings persistence (library paths, last project) |
| `color_manager.py` | Colour scheme management (light/dark themes) |
| `build_re_libraries.py` | Utility — build custom RE footprint/symbol libraries |
| `_find_syms.py` | Utility — scan and list available KiCad symbols |

---

## TODO / Known limitations

- [ ] **PCB board export** — currently only footprint placement and
      silkscreen geometry are exported; copper fills, zones, and board
      stackup are not yet generated
- [ ] **Net visual ratsnest** — lines between connected pads on the canvas
- [ ] **Schematic net labels** — `net_label` nodes in `.kicad_sch`
- [ ] **Full end-to-end test** — export verified with a real board in KiCad
- [ ] **Multi-selection** — bulk property edit for multiple components
- [ ] **DRC-style check** — net consistency validation before export
- [ ] **Board outline** — draw and export `Edge.Cuts` board outline

---

## Licence

This project is provided as-is for personal and educational use.
