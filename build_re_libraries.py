#!/usr/bin/env python3
"""Build curated KiCad RE (Reverse Engineering) libraries.

Stand-alone script — run once (or re-run) to populate:
    ~/Documents/KiCad/9.0/footprints/RE_*.pretty
    ~/Documents/KiCad/9.0/symbols/RE_*.kicad_sym

Strategy
--------
1. Copy footprints from user's 00_basic (HandSolder pads) first.
2. Fill missing footprints from KiCad standard libraries.
3. Extract selected symbols from KiCad standard .kicad_sym files.
4. Generate generic symbols (IC_Nx, Q_NPN_generic, etc.) procedurally.

Library layout (Variant B — by type):
    RE_passive.kicad_sym   / RE_passive_smd.pretty  / RE_passive_tht.pretty
    RE_active.kicad_sym    / RE_active.pretty
    RE_ic.kicad_sym        / RE_ic.pretty
    RE_connector.kicad_sym / RE_connector.pretty
    RE_power.kicad_sym     (symbols only — no footprints)
    RE_generic.kicad_sym   (generic IC/transistor outlines)
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ======================================================================
# Configuration
# ======================================================================

KICAD_BASE = Path(r"C:\Program Files\KiCad\9.0")
KICAD_STD_FP = KICAD_BASE / "share" / "kicad" / "footprints"
KICAD_STD_SYM = KICAD_BASE / "share" / "kicad" / "symbols"

USER_KICAD = Path(os.path.expandvars(r"%USERPROFILE%\Documents\KiCad\9.0"))
USER_FP = USER_KICAD / "footprints"
USER_SYM = USER_KICAD / "symbols"
USER_BASIC_FP = USER_FP / "00_basic.pretty"

# Target directories
TARGET_FP = USER_FP
TARGET_SYM = USER_SYM

# ======================================================================
# Footprint copy lists  { target_library: [ (source, filename), ...] }
# source = "user" means 00_basic.pretty, otherwise "Lib_Name.pretty"
# ======================================================================

FP_PASSIVE_SMD = {
    "lib": "RE_passive_smd.pretty",
    "items": [
        # Resistors
        ("user", "R_0805_2012Metric_Pad1.20x1.40mm_HandSolder.kicad_mod"),
        ("Resistor_SMD.pretty", "R_0402_1005Metric.kicad_mod"),
        ("Resistor_SMD.pretty", "R_0603_1608Metric.kicad_mod"),
        ("Resistor_SMD.pretty", "R_1206_3216Metric.kicad_mod"),
        ("Resistor_SMD.pretty", "R_1210_3225Metric.kicad_mod"),
        ("Resistor_SMD.pretty", "R_2010_5025Metric.kicad_mod"),
        ("Resistor_SMD.pretty", "R_2512_6332Metric.kicad_mod"),
        # Capacitors
        ("user", "C_0805_2012Metric_Pad1.18x1.45mm_HandSolder.kicad_mod"),
        ("Capacitor_SMD.pretty", "C_0402_1005Metric.kicad_mod"),
        ("Capacitor_SMD.pretty", "C_0603_1608Metric.kicad_mod"),
        ("Capacitor_SMD.pretty", "C_1206_3216Metric.kicad_mod"),
        ("Capacitor_SMD.pretty", "C_1210_3225Metric.kicad_mod"),
        ("Capacitor_SMD.pretty", "C_1812_4532Metric.kicad_mod"),
        # Inductors
        ("Inductor_SMD.pretty", "L_0805_2012Metric.kicad_mod"),
        ("Inductor_SMD.pretty", "L_1008_2520Metric.kicad_mod"),
        ("Inductor_SMD.pretty", "L_1210_3225Metric.kicad_mod"),
        ("Inductor_SMD.pretty", "L_1812_4532Metric.kicad_mod"),
        # Diodes
        ("user", "D_0805_2012Metric_Pad1.15x1.40mm_HandSolder.kicad_mod"),
        ("Diode_SMD.pretty", "D_SOD-123.kicad_mod"),
        ("Diode_SMD.pretty", "D_SOD-323.kicad_mod"),
        ("Diode_SMD.pretty", "D_SMA.kicad_mod"),
        ("Diode_SMD.pretty", "D_SMB.kicad_mod"),
        ("Diode_SMD.pretty", "D_SMC.kicad_mod"),
        # LEDs
        ("user", "LED_0805_2012Metric_Pad1.15x1.40mm_HandSolder.kicad_mod"),
        ("LED_SMD.pretty", "LED_0603_1608Metric.kicad_mod"),
        ("LED_SMD.pretty", "LED_1206_3216Metric.kicad_mod"),
        # Fuses
        ("Fuse.pretty", "Fuse_0805_2012Metric.kicad_mod"),
        ("Fuse.pretty", "Fuse_1206_3216Metric.kicad_mod"),
        # Ferrite bead (same as inductor footprint)
        ("Inductor_SMD.pretty", "L_0603_1608Metric.kicad_mod"),
    ],
}

FP_PASSIVE_THT = {
    "lib": "RE_passive_tht.pretty",
    "items": [
        # Resistors axial
        ("user", "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal.kicad_mod"),
        ("user", "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal.kicad_mod"),
        ("Resistor_THT.pretty", "R_Axial_DIN0309_L9.0mm_D3.6mm_P12.70mm_Horizontal.kicad_mod"),
        ("Resistor_THT.pretty", "R_Axial_DIN0617_L17.0mm_D6.0mm_P20.32mm_Horizontal.kicad_mod"),
        # Capacitors axial
        ("user", "C_Axial_L3.8mm_D2.6mm_P7.50mm_Horizontal.kicad_mod"),
        ("user", "C_Axial_L5.1mm_D3.1mm_P7.50mm_Horizontal.kicad_mod"),
        # Electrolytic radial (user's full set)
        ("user", "CP_Radial_D4.0mm_P2.00mm.kicad_mod"),
        ("user", "CP_Radial_D5.0mm_P2.50mm.kicad_mod"),
        ("user", "CP_Radial_D6.3mm_P2.50mm.kicad_mod"),
        ("user", "CP_Radial_D8.0mm_P3.50mm.kicad_mod"),
        ("user", "CP_Radial_D10.0mm_P5.00mm.kicad_mod"),
        ("user", "CP_Radial_D12.5mm_P5.00mm.kicad_mod"),
        ("user", "CP_Radial_D16.0mm_P7.50mm.kicad_mod"),
        ("user", "CP_Radial_D22.0mm_P10.00mm_SnapIn.kicad_mod"),
        ("user", "CP_Radial_D25.0mm_P10.00mm_SnapIn.kicad_mod"),
        ("user", "CP_Radial_D30.0mm_P10.00mm_SnapIn.kicad_mod"),
        ("user", "CP_Radial_D40.0mm_P10.00mm_SnapIn.kicad_mod"),
        # Diodes THT
        ("Diode_THT.pretty", "D_DO-35_SOD27_P7.62mm_Horizontal.kicad_mod"),
        ("Diode_THT.pretty", "D_DO-41_SOD81_P10.16mm_Horizontal.kicad_mod"),
        # LED THT
        ("LED_THT.pretty", "LED_D3.0mm.kicad_mod"),
        ("LED_THT.pretty", "LED_D5.0mm.kicad_mod"),
        # Crystal
        ("Crystal.pretty", "Crystal_HC49-4H_Vertical.kicad_mod"),
        ("Crystal.pretty", "Crystal_HC49-U_Vertical.kicad_mod"),
        # Fuse THT
        ("Fuse.pretty", "Fuse_Bourns_MF-RG300.kicad_mod"),
        # Inductor / choke THT
        ("Inductor_THT.pretty", "L_Axial_L11.0mm_D4.5mm_P15.24mm_Horizontal_Fastron_MECC.kicad_mod"),
        ("Inductor_THT.pretty", "L_Radial_D10.0mm_P5.00mm_Fastron_07P.kicad_mod"),
        # Varistor
        ("Varistor.pretty", "RV_Disc_D9mm_W4.4mm_P5mm.kicad_mod"),
        ("Varistor.pretty", "RV_Disc_D12mm_W4.6mm_P7.5mm.kicad_mod"),
    ],
}

FP_ACTIVE = {
    "lib": "RE_active.pretty",
    "items": [
        # SOT-23 variants (transistors, MOSFETs, small regulators)
        ("Package_TO_SOT_SMD.pretty", "SOT-23.kicad_mod"),
        ("Package_TO_SOT_SMD.pretty", "SOT-23-5.kicad_mod"),
        ("Package_TO_SOT_SMD.pretty", "SOT-23-6.kicad_mod"),
        ("Package_TO_SOT_SMD.pretty", "SOT-23W.kicad_mod"),
        # SOT-223 (regulators, medium transistors)
        ("Package_TO_SOT_SMD.pretty", "SOT-223-3_TabPin2.kicad_mod"),
        # SOT-89 (regulators)
        ("Package_TO_SOT_SMD.pretty", "SOT-89-3.kicad_mod"),
        # SOT-363 (dual FET, small logic)
        ("Package_TO_SOT_SMD.pretty", "SOT-363_SC-70-6.kicad_mod"),
        # DPAK / D2PAK (power MOSFETs, regulators)
        ("Package_TO_SOT_SMD.pretty", "TO-252-2.kicad_mod"),
        ("Package_TO_SOT_SMD.pretty", "TO-263-2.kicad_mod"),
        # TO-92 (transistors THT)
        ("Package_TO_SOT_THT.pretty", "TO-92_Inline.kicad_mod"),
        ("Package_TO_SOT_THT.pretty", "TO-92_Wide.kicad_mod"),
        # TO-220 (power transistors, regulators)
        ("Package_TO_SOT_THT.pretty", "TO-220-3_Vertical.kicad_mod"),
        ("Package_TO_SOT_THT.pretty", "TO-220-3_Horizontal_TabDown.kicad_mod"),
        # TO-247 (high power)
        ("Package_TO_SOT_THT.pretty", "TO-247-3_Vertical.kicad_mod"),
        # TO-3P
        ("Package_TO_SOT_THT.pretty", "TO-3P-3_Vertical.kicad_mod"),
        # Diode bridge
        ("Diode_THT.pretty", "Diode_Bridge_Round_D8.9mm.kicad_mod"),
        ("Package_TO_SOT_SMD.pretty", "TO-269AA.kicad_mod"),
        # PowerPAK SO-8 (power MOSFETs)
        ("Package_SO.pretty", "PowerPAK_SO-8_Single.kicad_mod"),
        # SC-59 (small transistors)
        ("Package_TO_SOT_SMD.pretty", "SC-59.kicad_mod"),
    ],
}

FP_IC = {
    "lib": "RE_ic.pretty",
    "items": [
        # DIP packages
        ("user", "DIP-16_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-4_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-6_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-8_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-14_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-18_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-20_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-24_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-28_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-32_W7.62mm.kicad_mod"),
        ("Package_DIP.pretty", "DIP-40_W15.24mm.kicad_mod"),
        # SOIC packages
        ("Package_SO.pretty", "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-14_3.9x8.7mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-16_3.9x9.9mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-16W_7.5x10.3mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-18W_7.5x11.6mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-20W_7.5x12.8mm_P1.27mm.kicad_mod"),
        ("Package_SO.pretty", "SOIC-28W_7.5x17.9mm_P1.27mm.kicad_mod"),
        # MSOP / SSOP
        ("Package_SO.pretty", "MSOP-8_3x3mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "SSOP-8_3.95x5.21mm_P1.27mm.kicad_mod"),
        # TSSOP packages
        ("Package_SO.pretty", "TSSOP-8_4.4x3mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "TSSOP-14_4.4x5mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "TSSOP-16_4.4x5mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "TSSOP-20_4.4x6.5mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "TSSOP-24_4.4x7.8mm_P0.65mm.kicad_mod"),
        ("Package_SO.pretty", "TSSOP-28_4.4x9.7mm_P0.65mm.kicad_mod"),
        # QFP packages
        ("Package_QFP.pretty", "LQFP-32_7x7mm_P0.8mm.kicad_mod"),
        ("Package_QFP.pretty", "LQFP-44_10x10mm_P0.8mm.kicad_mod"),
        ("Package_QFP.pretty", "LQFP-48_7x7mm_P0.5mm.kicad_mod"),
        ("Package_QFP.pretty", "LQFP-64_10x10mm_P0.5mm.kicad_mod"),
        ("Package_QFP.pretty", "TQFP-32_7x7mm_P0.8mm.kicad_mod"),
        ("Package_QFP.pretty", "TQFP-44_10x10mm_P0.8mm.kicad_mod"),
        ("Package_QFP.pretty", "LQFP-100_14x14mm_P0.5mm.kicad_mod"),
        ("Package_QFP.pretty", "LQFP-144_20x20mm_P0.5mm.kicad_mod"),
        # QFN / DFN
        ("Package_DFN_QFN.pretty", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm.kicad_mod"),
        ("Package_DFN_QFN.pretty", "QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm.kicad_mod"),
        ("Package_DFN_QFN.pretty", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm.kicad_mod"),
        ("Package_DFN_QFN.pretty", "QFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm.kicad_mod"),
        ("Package_DFN_QFN.pretty", "QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm.kicad_mod"),
        # SOP-4 (optocoupler)
        ("Package_SO.pretty", "SOP-4_3.8x4.1mm_P2.54mm.kicad_mod"),
    ],
}

FP_CONNECTOR = {
    "lib": "RE_connector.pretty",
    "items": [
        # Pin headers 2.54mm
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x02_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x03_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x04_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x05_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x06_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x08_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x10_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x16_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_1x20_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x02_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x03_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x04_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x05_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x08_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x10_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinHeader_2.54mm.pretty", "PinHeader_2x20_P2.54mm_Vertical.kicad_mod"),
        # Pin sockets
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x02_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x04_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x06_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x08_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x10_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x16_P2.54mm_Vertical.kicad_mod"),
        ("Connector_PinSocket_2.54mm.pretty", "PinSocket_1x20_P2.54mm_Vertical.kicad_mod"),
        # JST EH (user's HandSolder — copy from 00_basic)
        ("user", "JST_EH_B2B-EH-A_1x02_P2.50mm_Vertical.kicad_mod"),
        ("user", "JST_EH_B3B-EH-A_1x03_P2.50mm_Vertical.kicad_mod"),
        ("user", "JST_EH_B4B-EH-A_1x04_P2.50mm_Vertical.kicad_mod"),
        ("user", "JST_EH_B5B-EH-A_1x05_P2.50mm_Vertical.kicad_mod"),
        ("user", "JST_EH_B6B-EH-A_1x06_P2.50mm_Vertical.kicad_mod"),
        ("user", "JST_EH_S2B-EH_1x02_P2.50mm_Horizontal.kicad_mod"),
        ("user", "JST_EH_S3B-EH_1x03_P2.50mm_Horizontal.kicad_mod"),
        ("user", "JST_EH_S4B-EH_1x04_P2.50mm_Horizontal.kicad_mod"),
        ("user", "JST_EH_S5B-EH_1x05_P2.50mm_Horizontal.kicad_mod"),
        ("user", "JST_EH_S6B-EH_1x06_P2.50mm_Horizontal.kicad_mod"),
        # Screw terminals
        ("TerminalBlock_Phoenix.pretty", "TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal.kicad_mod"),
        ("TerminalBlock_Phoenix.pretty", "TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal.kicad_mod"),
        # Barrel jack
        ("Connector_BarrelJack.pretty", "BarrelJack_Horizontal.kicad_mod"),
        # USB connectors
        ("Connector_USB.pretty", "USB_B_OST_USB-B1HSxx_Vertical.kicad_mod"),
        ("Connector_USB.pretty", "USB_Micro-B_Molex-105017-0001.kicad_mod"),
        ("Connector_USB.pretty", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.kicad_mod"),
    ],
}

ALL_FP_GROUPS = [FP_PASSIVE_SMD, FP_PASSIVE_THT, FP_ACTIVE, FP_IC, FP_CONNECTOR]

# ======================================================================
# Symbol extraction lists  { target_lib: [ (source_lib, symbol_name), ...] }
# ======================================================================

SYM_PASSIVE = {
    "lib": "RE_passive",
    "items": [
        ("Device", "R"),
        ("Device", "R_Small"),
        ("Device", "R_Pack04"),
        ("Device", "R_Network08"),
        ("Device", "R_Potentiometer"),
        ("Device", "C"),
        ("Device", "C_Small"),
        ("Device", "C_Polarized"),
        ("Device", "C_Polarized_Small"),
        ("Device", "L"),
        ("Device", "L_Small"),
        ("Device", "L_Iron"),
        ("Device", "FerriteBead"),
        ("Device", "FerriteBead_Small"),
        ("Device", "D"),
        ("Device", "D_Small"),
        ("Device", "D_Schottky"),
        ("Device", "D_Schottky_Small"),
        ("Device", "D_Zener"),
        ("Device", "D_Zener_Small"),
        ("Device", "D_TVS"),
        ("Device", "D_TVS_Dual_AAC"),
        ("Device", "D_Bridge_+-AA"),
        ("Device", "LED"),
        ("Device", "LED_Small"),
        ("Device", "LED_Dual_AAK"),
        ("Device", "LED_RGB"),
        ("Device", "Fuse"),
        ("Device", "Fuse_Small"),
        ("Device", "Polyfuse"),
        ("Device", "Polyfuse_Small"),
        ("Device", "Varistor"),
        ("Device", "Crystal"),
        ("Device", "Crystal_GND24"),
        ("Device", "Resonator"),
        ("Device", "Battery"),
        ("Device", "Battery_Cell"),
        ("Device", "Buzzer"),
        ("Device", "Transformer_1P_1S"),
        ("Device", "Thermistor_NTC"),
        ("Device", "Thermistor_PTC"),
        ("Device", "Thermistor"),
        ("Device", "D_Photo"),
        ("Device", "Heater"),
        ("Device", "Filter_EMI_LCL"),
        ("Device", "Filter_EMI_CLC"),
        ("Device", "Filter_EMI_LL"),
        ("Jumper", "Jumper_2_Open"),
        ("Jumper", "Jumper_3_Bridged12"),
        ("Connector", "TestPoint"),
    ],
}

SYM_ACTIVE = {
    "lib": "RE_active",
    "items": [
        # BJT transistors
        ("Device", "Q_NPN"),
        ("Device", "Q_NPN_BRT"),
        ("Device", "Q_PNP"),
        ("Device", "Q_PNP_BRT"),
        # MOSFETs
        ("Device", "Q_NMOS"),
        ("Device", "Q_NMOS_Depletion"),
        ("Device", "Q_PMOS"),
        ("Device", "Q_PMOS_Depletion"),
        # JFET
        ("Device", "Q_NJFET_DGS"),
        ("Device", "Q_NJFET_GDS"),
        ("Device", "Q_PJFET_DGS"),
        ("Device", "Q_PJFET_GDS"),
        # Darlington
        ("Device", "Q_NPN_Darlington"),
        ("Device", "Q_PNP_Darlington"),
        # Thyristors / TRIAC
        ("Device", "Q_SCR_AGK"),
        ("Device", "Q_SCR_KGA"),
        ("Device", "Q_Triac"),
        ("Device", "DIAC"),
        # IGBT
        ("Device", "Q_NIGBT_GCE"),
        ("Device", "Q_NIGBT_CEG"),
        # Optocouplers
        ("Isolator", "Optocoupler_DC_PhotoNPN_AKEC"),
        # Op-amps
        ("Amplifier_Operational", "LM358"),
        ("Amplifier_Operational", "LM324"),
        ("Amplifier_Operational", "NE5532"),
        ("Amplifier_Operational", "TL072"),
        ("Amplifier_Operational", "TL074"),
        ("Amplifier_Operational", "LM741"),
        ("Amplifier_Operational", "MCP6002-xMS"),
        ("Amplifier_Operational", "MCP6004"),
        # Comparators
        ("Comparator", "LM339"),
        ("Comparator", "LM393"),
        ("Comparator", "LM311"),
        # Voltage regulators
        ("Regulator_Linear", "LM7805_TO220"),
        ("Regulator_Linear", "LM7812_TO220"),
        ("Regulator_Linear", "LM7905_TO220"),
        ("Regulator_Linear", "LM317_TO-220"),
        ("Regulator_Linear", "LM337_TO220"),
        ("Regulator_Linear", "AMS1117-3.3"),
        ("Regulator_Linear", "AMS1117-5.0"),
        ("Regulator_Linear", "LM1117DT-3.3"),
        ("Regulator_Linear", "AP2112K-3.3"),
        # Switching regulators
        ("Regulator_Switching", "LM2596S-5"),
        ("Regulator_Switching", "LM2596S-ADJ"),
        ("Regulator_Switching", "LM2576S-5"),
        ("Regulator_Switching", "MC34063AD"),
        # Power protection
        ("Power_Protection", "SP0502BAHT"),
        ("Power_Protection", "PRTR5V0U2X"),
        # 555 timer
        ("Timer", "NE555D"),
        ("Timer", "NE556"),
        ("Timer", "ICM7555xP"),
        # Driver
        ("Driver_Motor", "L293D"),
        ("Driver_Motor", "L298N"),
    ],
}

SYM_IC = {
    "lib": "RE_ic",
    "items": [
        # 74xx logic  — gates
        ("74xx", "74HC00"),     # quad NAND
        ("74xx", "74HC02"),     # quad NOR
        ("74xx", "74HC04"),     # hex NOT
        ("74xx", "74HC86"),     # quad XOR
        # 74xx — buffers & drivers
        ("74xx", "74HC245"),    # octal bus transceiver
        ("74xx", "74AHC244"),   # octal buffer tristate
        ("74xx", "74AHC541"),   # octal buffer
        ("74xx", "74AHCT125"), # quad buffer tristate
        # 74xx — flip-flops
        ("74xx", "74HC74"),     # dual D FF
        ("74xx", "74HC273"),    # octal D FF with clear
        ("74xx", "74HC373"),    # octal D latch
        ("74xx", "74HC374"),    # octal D FF
        # 74xx — counters
        ("74xx", "74HC4060"),   # 14-bit binary counter + osc
        ("74xx", "74HC590"),    # 8-bit binary counter
        ("74xx", "74HC4024"),   # 7-stage binary counter
        # 74xx — shift registers
        ("74xx", "74HC164"),    # 8-bit SISO/SIPO shift
        ("74xx", "74HC165"),    # 8-bit PISO shift
        ("74xx", "74HC595"),    # 8-bit shift register + output latch
        ("74xx", "74HC594"),    # 8-bit shift register
        # 74xx — decoders / mux
        ("74xx", "74HC138"),    # 3-to-8 decoder
        ("74xx", "74HC237"),    # 3-to-8 decoder (latch)
        ("74xx", "74HC4051"),   # 8-ch analog mux
        ("74xx", "74HC688"),    # 8-bit comparator
        # Level translators
        ("Logic_LevelTranslator", "TXS0102DCT"),
        ("Logic_LevelTranslator", "TXS0108EPW"),
        # Schmitt trigger
        ("74xx", "74HC14"),     # hex Schmitt NOT
        # Interface
        ("Interface_UART", "MAX232"),
        ("Interface_UART", "MAX3232"),
        ("Interface_CAN_LIN", "MCP2551-I-SN"),
        ("Interface_CAN_LIN", "SN65HVD230"),
        # ADC / DAC
        ("Analog_ADC", "MCP3008"),
        ("Analog_ADC", "ADS1115IDGS"),
        ("Analog_DAC", "MCP4725xxx-xCH"),
        # EEPROM / memory
        ("Memory_EEPROM", "24LC256"),
        ("Memory_EEPROM", "CAT24C256"),
        ("Memory_Flash", "W25Q32JVSS"),
        # RTC
        ("Timer_RTC", "DS1307Z+"),
        ("Timer_RTC", "DS3231M"),
    ],
}

SYM_CONNECTOR = {
    "lib": "RE_connector",
    "items": [
        # Generic connectors 1-row
        ("Connector_Generic", "Conn_01x01"),
        ("Connector_Generic", "Conn_01x02"),
        ("Connector_Generic", "Conn_01x03"),
        ("Connector_Generic", "Conn_01x04"),
        ("Connector_Generic", "Conn_01x05"),
        ("Connector_Generic", "Conn_01x06"),
        ("Connector_Generic", "Conn_01x08"),
        ("Connector_Generic", "Conn_01x10"),
        ("Connector_Generic", "Conn_01x12"),
        ("Connector_Generic", "Conn_01x16"),
        ("Connector_Generic", "Conn_01x20"),
        # Generic connectors 2-row
        ("Connector_Generic", "Conn_02x02_Odd_Even"),
        ("Connector_Generic", "Conn_02x03_Odd_Even"),
        ("Connector_Generic", "Conn_02x04_Odd_Even"),
        ("Connector_Generic", "Conn_02x05_Odd_Even"),
        ("Connector_Generic", "Conn_02x08_Odd_Even"),
        ("Connector_Generic", "Conn_02x10_Odd_Even"),
        ("Connector_Generic", "Conn_02x20_Odd_Even"),
        # Specific connectors
        ("Connector", "Barrel_Jack"),
        ("Connector", "USB_B"),
        ("Connector", "USB_B_Micro"),
        ("Connector", "USB_C_Receptacle"),
        ("Connector", "TestPoint"),
        ("Connector", "Screw_Terminal_01x02"),
        ("Connector", "Screw_Terminal_01x03"),
    ],
}

SYM_POWER = {
    "lib": "RE_power",
    "items": [
        ("power", "+3V3"),
        ("power", "+5V"),
        ("power", "+9V"),
        ("power", "+12V"),
        ("power", "+24V"),
        ("power", "+3.3V"),
        ("power", "GND"),
        ("power", "GNDA"),
        ("power", "GNDPWR"),
        ("power", "VCC"),
        ("power", "VDD"),
        ("power", "VSS"),
        ("power", "VBUS"),
        ("power", "+48V"),
        ("power", "-5V"),
        ("power", "-12V"),
        ("power", "VPP"),
        ("power", "GND1"),
        ("power", "GND2"),
    ],
}

ALL_SYM_GROUPS = [SYM_PASSIVE, SYM_ACTIVE, SYM_IC, SYM_CONNECTOR, SYM_POWER]

# ======================================================================
# Generic symbols – generated procedurally
# ======================================================================

@dataclass
class GenericPin:
    name: str
    number: str
    x: float
    y: float
    direction: float  # 0=right, 90=up, 180=left, 270=down
    etype: str = "passive"  # passive, input, output, bidirectional, power_in


def _build_kicad_sym_header() -> str:
    return (
        '(kicad_symbol_lib\n'
        '\t(version 20241209)\n'
        '\t(generator "re_library_builder")\n'
        '\t(generator_version "1.0")\n'
    )


def _build_symbol_block(
    name: str,
    reference: str,
    description: str,
    pins: list[GenericPin],
    rectangles: list[tuple[float, float, float, float]] | None = None,
    polylines: list[list[tuple[float, float]]] | None = None,
    circles: list[tuple[float, float, float]] | None = None,
    pin_names_visible: bool = True,
    pin_numbers_visible: bool = True,
    in_bom: bool = True,
    on_board: bool = True,
    power: bool = False,
) -> str:
    """Build a full (symbol ...) S-expression block."""
    lines = []
    lines.append(f'\t(symbol "{name}"')
    if not pin_numbers_visible:
        lines.append('\t\t(pin_numbers (hide yes))')
    if not pin_names_visible:
        lines.append('\t\t(pin_names (hide yes))')
    lines.append(f'\t\t(exclude_from_sim no)')
    lines.append(f'\t\t(in_bom {"yes" if in_bom else "no"})')
    lines.append(f'\t\t(on_board {"yes" if on_board else "no"})')
    if power:
        lines.append(f'\t\t(power)')

    # Properties
    lines.append(f'\t\t(property "Reference" "{reference}"')
    lines.append('\t\t\t(at 0 1.27 0)')
    lines.append('\t\t\t(effects (font (size 1.27 1.27)))')
    lines.append('\t\t)')
    lines.append(f'\t\t(property "Value" "{name}"')
    lines.append('\t\t\t(at 0 -1.27 0)')
    lines.append('\t\t\t(effects (font (size 1.27 1.27)))')
    lines.append('\t\t)')
    lines.append(f'\t\t(property "Footprint" ""')
    lines.append('\t\t\t(at 0 0 0)')
    lines.append('\t\t\t(effects (font (size 1.27 1.27)) (hide yes))')
    lines.append('\t\t)')
    lines.append(f'\t\t(property "Datasheet" ""')
    lines.append('\t\t\t(at 0 0 0)')
    lines.append('\t\t\t(effects (font (size 1.27 1.27)) (hide yes))')
    lines.append('\t\t)')
    lines.append(f'\t\t(property "Description" "{description}"')
    lines.append('\t\t\t(at 0 0 0)')
    lines.append('\t\t\t(effects (font (size 1.27 1.27)) (hide yes))')
    lines.append('\t\t)')

    # Sub-symbol with graphics
    lines.append(f'\t\t(symbol "{name}_0_1"')
    if rectangles:
        for rx1, ry1, rx2, ry2 in rectangles:
            lines.append(f'\t\t\t(rectangle')
            lines.append(f'\t\t\t\t(start {rx1} {ry1})')
            lines.append(f'\t\t\t\t(end {rx2} {ry2})')
            lines.append(f'\t\t\t\t(stroke (width 0.254) (type default))')
            lines.append(f'\t\t\t\t(fill (type background))')
            lines.append(f'\t\t\t)')
    if polylines:
        for pts in polylines:
            lines.append(f'\t\t\t(polyline')
            lines.append(f'\t\t\t\t(pts')
            for px, py in pts:
                lines.append(f'\t\t\t\t\t(xy {px} {py})')
            lines.append(f'\t\t\t\t)')
            lines.append(f'\t\t\t\t(stroke (width 0.254) (type default))')
            lines.append(f'\t\t\t\t(fill (type none))')
            lines.append(f'\t\t\t)')
    if circles:
        for cx, cy, r in circles:
            lines.append(f'\t\t\t(circle')
            lines.append(f'\t\t\t\t(center {cx} {cy})')
            lines.append(f'\t\t\t\t(radius {r})')
            lines.append(f'\t\t\t\t(stroke (width 0.254) (type default))')
            lines.append(f'\t\t\t\t(fill (type none))')
            lines.append(f'\t\t\t)')
    lines.append(f'\t\t)')

    # Sub-symbol with pins
    lines.append(f'\t\t(symbol "{name}_1_1"')
    for p in pins:
        lines.append(f'\t\t\t(pin {p.etype} line')
        lines.append(f'\t\t\t\t(at {p.x} {p.y} {int(p.direction)})')
        lines.append(f'\t\t\t\t(length 2.54)')
        lines.append(f'\t\t\t\t(name "{p.name}"')
        lines.append(f'\t\t\t\t\t(effects (font (size 1.27 1.27)))')
        lines.append(f'\t\t\t\t)')
        lines.append(f'\t\t\t\t(number "{p.number}"')
        lines.append(f'\t\t\t\t\t(effects (font (size 1.27 1.27)))')
        lines.append(f'\t\t\t\t)')
        lines.append(f'\t\t\t)')
    lines.append(f'\t\t)')

    lines.append(f'\t)')
    return '\n'.join(lines)


def generate_generic_ic(pin_count: int) -> str:
    """Generate a generic IC symbol with N pins (DIP-style: left+right)."""
    name = f"Generic_IC_{pin_count}pin"
    half = pin_count // 2
    # Pin spacing 2.54 mm, box sized to fit
    body_h = (half + 1) * 2.54
    body_w = 7.62
    top = body_h / 2
    bot = -body_h / 2

    pins = []
    for i in range(half):
        y = top - (i + 1) * 2.54
        # Left side pins: 1..half
        pins.append(GenericPin(
            name=str(i + 1), number=str(i + 1),
            x=-body_w / 2 - 2.54, y=y, direction=0, etype="bidirectional"))
        # Right side pins: pin_count down to half+1
        rn = pin_count - i
        pins.append(GenericPin(
            name=str(rn), number=str(rn),
            x=body_w / 2 + 2.54, y=y, direction=180, etype="bidirectional"))

    return _build_symbol_block(
        name=name, reference="U", description=f"Generic {pin_count}-pin IC",
        pins=pins,
        rectangles=[(-body_w / 2, top, body_w / 2, bot)],
    )


def generate_generic_transistor_bjt(ptype: str) -> str:
    """Generate generic NPN or PNP transistor symbol."""
    name = f"Generic_Q_{ptype}"
    is_pnp = ptype == "PNP"

    # Collector top, emitter bottom, base left
    # Arrow direction differs NPN vs PNP
    pins = [
        GenericPin("B", "1", x=-5.08, y=0, direction=0, etype="input"),
        GenericPin("C", "2", x=2.54, y=5.08, direction=270, etype="passive"),
        GenericPin("E", "3", x=2.54, y=-5.08, direction=90, etype="passive"),
    ]

    # Body line from base
    polylines = [
        [(-2.54, 1.905), (-2.54, -1.905)],  # vertical line at base
        [(-2.54, 1.27), (2.54, 2.54)],  # collector line
        [(-2.54, -1.27), (2.54, -2.54)],  # emitter line
    ]

    # Arrow on emitter (NPN: outward, PNP: inward)
    if not is_pnp:
        polylines.append([(0.508, -1.778), (2.54, -2.54), (1.27, -1.016)])
    else:
        polylines.append([(-2.032, -0.762), (-2.54, -1.27), (-1.524, -1.524)])

    return _build_symbol_block(
        name=name, reference="Q",
        description=f"Generic {ptype} transistor (B/C/E)",
        pins=pins, polylines=polylines,
        circles=[(0, 0, 3.81)],
    )


def generate_generic_mosfet(ptype: str) -> str:
    """Generate generic N-ch or P-ch MOSFET symbol."""
    name = f"Generic_Q_{ptype}MOS"
    is_p = ptype == "P"

    pins = [
        GenericPin("G", "1", x=-5.08, y=0, direction=0, etype="input"),
        GenericPin("D", "2", x=2.54, y=5.08, direction=270, etype="passive"),
        GenericPin("S", "3", x=2.54, y=-5.08, direction=90, etype="passive"),
    ]

    polylines = [
        [(-2.54, 1.905), (-2.54, -1.905)],  # gate line
        [(-1.27, 1.905), (-1.27, -1.905)],  # channel (broken)
        [(-1.27, 1.27), (2.54, 1.27), (2.54, 2.54)],  # drain
        [(-1.27, -1.27), (2.54, -1.27), (2.54, -2.54)],  # source
        [(2.54, 1.27), (2.54, -1.27)],  # body connection
    ]

    # Arrow on source (NMOS: inward, PMOS: outward)
    if not is_p:
        polylines.append([(0.508, -1.27), (-1.27, -1.27)])  # arrow into body
    else:
        polylines.append([(0.508, 1.27), (-1.27, 1.27)])  # arrow outward

    return _build_symbol_block(
        name=name, reference="Q",
        description=f"Generic {ptype}-channel MOSFET (G/D/S)",
        pins=pins, polylines=polylines,
        circles=[(0, 0, 3.81)],
    )


def generate_generic_opamp() -> str:
    """Generate a generic single opamp symbol."""
    name = "Generic_OpAmp"
    pins = [
        GenericPin("+", "1", x=-7.62, y=2.54, direction=0, etype="input"),
        GenericPin("-", "2", x=-7.62, y=-2.54, direction=0, etype="input"),
        GenericPin("OUT", "3", x=7.62, y=0, direction=180, etype="output"),
        GenericPin("V+", "4", x=0, y=5.08, direction=270, etype="power_in"),
        GenericPin("V-", "5", x=0, y=-5.08, direction=90, etype="power_in"),
    ]

    polylines = [
        [(-5.08, 5.08), (-5.08, -5.08), (5.08, 0), (-5.08, 5.08)],
    ]

    return _build_symbol_block(
        name=name, reference="U",
        description="Generic single op-amp",
        pins=pins, polylines=polylines,
    )


def generate_generic_comparator() -> str:
    """Generate a generic comparator symbol."""
    name = "Generic_Comparator"
    pins = [
        GenericPin("+", "1", x=-7.62, y=2.54, direction=0, etype="input"),
        GenericPin("-", "2", x=-7.62, y=-2.54, direction=0, etype="input"),
        GenericPin("OUT", "3", x=7.62, y=0, direction=180, etype="output"),
        GenericPin("V+", "4", x=0, y=5.08, direction=270, etype="power_in"),
        GenericPin("V-", "5", x=0, y=-5.08, direction=90, etype="power_in"),
    ]
    polylines = [
        [(-5.08, 5.08), (-5.08, -5.08), (5.08, 0), (-5.08, 5.08)],
    ]
    return _build_symbol_block(
        name=name, reference="U",
        description="Generic single comparator",
        pins=pins, polylines=polylines,
    )


def generate_generic_regulator(pins_count: int) -> str:
    """Generate generic voltage regulator (3 or 4 pin)."""
    name = f"Generic_Regulator_{pins_count}pin"
    body_w = 7.62
    body_h = 5.08

    pins = [
        GenericPin("IN", "1", x=-body_w / 2 - 2.54, y=0, direction=0, etype="power_in"),
        GenericPin("GND", "2" if pins_count == 3 else "3",
                   x=0, y=-body_h / 2 - 2.54, direction=90, etype="power_in"),
        GenericPin("OUT", "3" if pins_count == 3 else "2",
                   x=body_w / 2 + 2.54, y=0, direction=180, etype="power_out"),
    ]
    if pins_count >= 4:
        pins.append(GenericPin(
            "EN", "4", x=0, y=body_h / 2 + 2.54, direction=270, etype="input"))

    return _build_symbol_block(
        name=name, reference="U",
        description=f"Generic {pins_count}-pin voltage regulator",
        pins=pins,
        rectangles=[(-body_w / 2, body_h / 2, body_w / 2, -body_h / 2)],
    )


def generate_generic_diode_bridge() -> str:
    """Generate generic diode bridge rectifier."""
    name = "Generic_DiodeBridge"
    pins = [
        GenericPin("AC1", "1", x=-5.08, y=0, direction=0, etype="passive"),
        GenericPin("AC2", "2", x=5.08, y=0, direction=180, etype="passive"),
        GenericPin("+", "3", x=0, y=5.08, direction=270, etype="passive"),
        GenericPin("-", "4", x=0, y=-5.08, direction=90, etype="passive"),
    ]
    polylines = [
        [(-2.54, 2.54), (2.54, 2.54), (2.54, -2.54), (-2.54, -2.54), (-2.54, 2.54)],
    ]
    return _build_symbol_block(
        name=name, reference="D",
        description="Generic full diode bridge rectifier",
        pins=pins, polylines=polylines,
    )


def generate_generic_relay() -> str:
    """Generic relay with coil + SPDT contact."""
    name = "Generic_Relay_SPDT"
    pins = [
        GenericPin("COIL+", "1", x=-7.62, y=2.54, direction=0, etype="passive"),
        GenericPin("COIL-", "2", x=-7.62, y=-2.54, direction=0, etype="passive"),
        GenericPin("COM", "3", x=7.62, y=0, direction=180, etype="passive"),
        GenericPin("NO", "4", x=7.62, y=2.54, direction=180, etype="passive"),
        GenericPin("NC", "5", x=7.62, y=-2.54, direction=180, etype="passive"),
    ]
    return _build_symbol_block(
        name=name, reference="K",
        description="Generic SPDT relay",
        pins=pins,
        rectangles=[(-5.08, 5.08, 5.08, -5.08)],
    )


def generate_generic_optocoupler() -> str:
    """Generic 4-pin optocoupler."""
    name = "Generic_Optocoupler"
    pins = [
        GenericPin("A", "1", x=-7.62, y=2.54, direction=0, etype="passive"),
        GenericPin("K", "2", x=-7.62, y=-2.54, direction=0, etype="passive"),
        GenericPin("C", "3", x=7.62, y=2.54, direction=180, etype="passive"),
        GenericPin("E", "4", x=7.62, y=-2.54, direction=180, etype="passive"),
    ]
    return _build_symbol_block(
        name=name, reference="U",
        description="Generic 4-pin optocoupler",
        pins=pins,
        rectangles=[(-5.08, 5.08, 5.08, -5.08)],
    )


def generate_generic_switch(poles: int) -> str:
    """Generic switch (SPST, SPDT...)."""
    if poles == 1:
        name = "Generic_SW_SPST"
        pins = [
            GenericPin("1", "1", x=-5.08, y=0, direction=0, etype="passive"),
            GenericPin("2", "2", x=5.08, y=0, direction=180, etype="passive"),
        ]
        polylines = [[(-2.54, 0), (2.54, 1.27)]]
        return _build_symbol_block(
            name=name, reference="SW",
            description="Generic SPST switch",
            pins=pins, polylines=polylines,
            circles=[(-2.54, 0, 0.381), (2.54, 0, 0.381)],
        )
    else:
        name = "Generic_SW_SPDT"
        pins = [
            GenericPin("COM", "1", x=-5.08, y=0, direction=0, etype="passive"),
            GenericPin("A", "2", x=5.08, y=1.27, direction=180, etype="passive"),
            GenericPin("B", "3", x=5.08, y=-1.27, direction=180, etype="passive"),
        ]
        polylines = [[(-2.54, 0), (2.54, 1.27)]]
        return _build_symbol_block(
            name=name, reference="SW",
            description="Generic SPDT switch",
            pins=pins, polylines=polylines,
            circles=[(-2.54, 0, 0.381), (2.54, 1.27, 0.381), (2.54, -1.27, 0.381)],
        )


def build_generic_symbols_lib() -> str:
    """Build the complete RE_generic.kicad_sym content."""
    parts = [_build_kicad_sym_header()]

    # Generic ICs: 4,6,8,10,14,16,18,20,24,28,32,40,44,48,64,100 pins
    for n in [4, 6, 8, 10, 14, 16, 18, 20, 24, 28, 32, 40, 44, 48, 64, 100]:
        parts.append(generate_generic_ic(n))

    # Generic transistors
    parts.append(generate_generic_transistor_bjt("NPN"))
    parts.append(generate_generic_transistor_bjt("PNP"))
    parts.append(generate_generic_mosfet("N"))
    parts.append(generate_generic_mosfet("P"))

    # Generic opamp
    parts.append(generate_generic_opamp())
    parts.append(generate_generic_comparator())

    # Generic regulators
    parts.append(generate_generic_regulator(3))
    parts.append(generate_generic_regulator(4))

    # Generic diode bridge
    parts.append(generate_generic_diode_bridge())

    # Generic relay
    parts.append(generate_generic_relay())

    # Generic optocoupler
    parts.append(generate_generic_optocoupler())

    # Generic switches
    parts.append(generate_generic_switch(1))
    parts.append(generate_generic_switch(2))

    parts.append(')')
    return '\n'.join(parts)


# ======================================================================
# Symbol extraction from KiCad standard .kicad_sym files
# ======================================================================

def extract_symbol_block(lib_file: Path, symbol_name: str) -> Optional[str]:
    """Extract a single top-level (symbol "NAME" ...) block from a .kicad_sym.

    This preserves the exact formatting from KiCad standard libraries.
    """
    if not lib_file.is_file():
        return None

    content = lib_file.read_text(encoding="utf-8")

    # Find the top-level symbol block: \n\t(symbol "NAME"\n
    pattern = re.compile(
        r'\n(\t\(symbol "' + re.escape(symbol_name) + r'"[\s\S]*?\n\t\))',
        re.DOTALL,
    )
    m = pattern.search(content)
    if m:
        return m.group(1)

    return None


def extract_symbol_block_robust(lib_file: Path, symbol_name: str) -> Optional[str]:
    """Extract symbol block using bracket counting for robustness."""
    if not lib_file.is_file():
        return None

    content = lib_file.read_text(encoding="utf-8")
    marker = f'\t(symbol "{symbol_name}"'
    idx = content.find(marker)
    if idx < 0:
        return None

    # Count parentheses from the opening (
    start = idx + 1  # skip the tab
    depth = 0
    i = start
    while i < len(content):
        ch = content[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return content[idx:i + 1]
        elif ch == '"':
            # Skip string content
            i += 1
            while i < len(content) and content[i] != '"':
                if content[i] == '\\':
                    i += 1
                i += 1
        i += 1

    return None


# ======================================================================
# Main build logic
# ======================================================================

def resolve_footprint_source(source: str, filename: str) -> Optional[Path]:
    """Resolve a footprint file path from source specification."""
    if source == "user":
        path = USER_BASIC_FP / filename
    else:
        path = KICAD_STD_FP / source / filename
    if path.is_file():
        return path
    return None


def copy_footprints(group: dict, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """Copy footprints for one library group.

    Returns (copied, skipped, errors).
    """
    lib_name = group["lib"]
    target_dir = TARGET_FP / lib_name
    copied = 0
    skipped = 0
    errors = []

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    for source, filename in group["items"]:
        src = resolve_footprint_source(source, filename)
        if src is None:
            errors.append(f"  NOT FOUND: {source}/{filename}")
            skipped += 1
            continue

        dst = target_dir / filename
        if dst.exists():
            skipped += 1
            continue

        if not dry_run:
            shutil.copy2(src, dst)
        copied += 1

    return copied, skipped, errors


def build_symbol_library(group: dict, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """Extract symbols and build a .kicad_sym file.

    Returns (extracted, skipped, errors).
    """
    lib_name = group["lib"]
    target_file = TARGET_SYM / f"{lib_name}.kicad_sym"
    extracted = 0
    skipped = 0
    errors = []
    blocks = []

    for src_lib, sym_name in group["items"]:
        lib_file = KICAD_STD_SYM / f"{src_lib}.kicad_sym"
        block = extract_symbol_block_robust(lib_file, sym_name)
        if block is None:
            errors.append(f"  NOT FOUND: {src_lib}:{sym_name}")
            skipped += 1
            continue
        blocks.append(block)
        extracted += 1

    if not dry_run and blocks:
        content = _build_kicad_sym_header()
        content += '\n'.join(blocks)
        content += '\n)\n'
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

    return extracted, skipped, errors


def main() -> None:
    print("=" * 60)
    print("RE Library Builder – KiCad Reverse Engineering Libraries")
    print("=" * 60)

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY RUN — no files will be written]\n")

    # Validate paths
    if not KICAD_STD_FP.is_dir():
        print(f"ERROR: KiCad standard footprints not found at {KICAD_STD_FP}")
        sys.exit(1)
    if not KICAD_STD_SYM.is_dir():
        print(f"ERROR: KiCad standard symbols not found at {KICAD_STD_SYM}")
        sys.exit(1)
    if not USER_BASIC_FP.is_dir():
        print(f"WARNING: User 00_basic.pretty not found at {USER_BASIC_FP}")
        print("  (user footprints will be skipped)\n")

    # 1. Footprints
    print("\n--- Footprint Libraries ---")
    total_fp_copied = 0
    total_fp_skip = 0
    all_fp_errors = []
    for grp in ALL_FP_GROUPS:
        c, s, e = copy_footprints(grp, dry_run)
        total_fp_copied += c
        total_fp_skip += s
        all_fp_errors.extend(e)
        status = "OK" if not e else f"WARN ({len(e)} missing)"
        print(f"  {grp['lib']}: {c} copied, {s} skipped — {status}")

    # 2. Standard symbol libraries
    print("\n--- Symbol Libraries ---")
    total_sym_ext = 0
    total_sym_skip = 0
    all_sym_errors = []
    for grp in ALL_SYM_GROUPS:
        c, s, e = build_symbol_library(grp, dry_run)
        total_sym_ext += c
        total_sym_skip += s
        all_sym_errors.extend(e)
        status = "OK" if not e else f"WARN ({len(e)} missing)"
        print(f"  {grp['lib']}.kicad_sym: {c} extracted, {s} skipped — {status}")

    # 3. Generic symbols
    print("\n--- Generic Symbol Library ---")
    generic_content = build_generic_symbols_lib()
    generic_file = TARGET_SYM / "RE_generic.kicad_sym"
    if not dry_run:
        generic_file.parent.mkdir(parents=True, exist_ok=True)
        generic_file.write_text(generic_content, encoding="utf-8")
    # Count symbols in generic
    gen_count = generic_content.count('\n\t(symbol "')
    print(f"  RE_generic.kicad_sym: {gen_count} generated symbols")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Footprints:  {total_fp_copied} copied, {total_fp_skip} skipped")
    print(f"  Symbols:     {total_sym_ext} extracted from standard libs")
    print(f"  Generic:     {gen_count} procedurally generated")

    if all_fp_errors or all_sym_errors:
        print(f"\n  WARNINGS ({len(all_fp_errors) + len(all_sym_errors)} items not found):")
        for e in all_fp_errors + all_sym_errors:
            print(e)

    print("\nTarget directories:")
    print(f"  Footprints: {TARGET_FP}")
    print(f"  Symbols:    {TARGET_SYM}")
    print("=" * 60)


if __name__ == "__main__":
    main()
