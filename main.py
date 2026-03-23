"""PCB-to-KiCad – entry point.

Reverse-engineering tool: place KiCad footprints on PCB photographs,
link schematic symbols, and export a complete KiCad 9.0 project
(.kicad_pro + .kicad_pcb + .kicad_sch).

Usage:
    python main.py
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui_main import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PCB-to-KiCad")
    app.setOrganizationName("ept")
    app.setApplicationVersion("0.2.0")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
