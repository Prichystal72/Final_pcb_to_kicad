"""Utility script: search KiCad standard symbol libraries for symbol names.

Used during development to locate correct symbol identifiers when
mapping footprints to schematic symbols.  Not part of the main application.
"""
import re
from pathlib import Path

KICAD_STD_SYM = Path(r"C:\Program Files\KiCad\9.0\share\kicad\symbols")

# Search patterns for missing symbols
searches = {
    "Device": [
        "L_Core", "D_TVS_x2", "LED_Dual", "Photo", "EMI_Filter", 
        "Jumper", "TestPoint",
        "Q_NPN_B", "Q_PNP_B", "Q_NMOS", "Q_PMOS", "Q_NJFET", "Q_PJFET",
        "Darlington", "TRIAC", "DIAC", "IGBT", "Optocoupler",
    ],
    "Amplifier_Operational": ["MCP6004"],
    "Regulator_Linear": ["LM337", "LM1117"],
    "Timer": ["555", "ICM7555"],
    "74xx": ["7408", "7432", "74125", "74126", "74112", "74541", "74573", "74574",
             "74161", "74163", "74139", "74151", "74153", "74157", "74052", "74053"],
    "Logic_LevelTranslator": ["TXS"],
    "Analog_DAC": ["MCP4725", "DAC"],
    "Memory_EEPROM": ["AT24C256", "24C256"],
    "Connector": ["USB_C"],
    "power": ["VREF", "Vref"],
}

for lib_name, patterns in searches.items():
    lib_file = KICAD_STD_SYM / f"{lib_name}.kicad_sym"
    if not lib_file.is_file():
        print(f"LIB NOT FOUND: {lib_name}")
        continue
    content = lib_file.read_text(encoding="utf-8")
    syms = re.findall(r'\n\t\(symbol "([^"]+)"', content)
    for pat in patterns:
        matches = [s for s in syms if pat.lower() in s.lower()]
        if matches:
            print(f"{lib_name} / {pat}: {matches[:5]}")
        else:
            print(f"{lib_name} / {pat}: NOT FOUND")
