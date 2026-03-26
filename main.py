"""PCB-to-KiCad — application entry point.

Author:  Jaroslav Přichystal (Prichy)
Version: 1.0 beta

Reverse-engineering tool that lets the user place KiCad footprints on
photographed / scanned PCB images, draw copper traces as wires, link
schematic symbols, and export a complete KiCad 9.0 project consisting
of .kicad_pro, .kicad_pcb, and .kicad_sch files.

Usage:
    python main.py
"""


from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui_main import MainWindow


def main() -> None:
    """Create the Qt application, show the main window, and enter the event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("PCB-to-KiCad")
    app.setOrganizationName("Prichy")
    app.setApplicationVersion("1.0-beta")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
