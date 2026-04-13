"""
DWSIM_ry_test/tasks/flash_engine.py
=====================================
Group 2 — Flash Flowsheet Engine.

Flowsheet topology (matches professor's manually validated DWSIM example exactly):

    FEED ──► [Cool-1] ──► FFEED ──► [Flash1] ──► V (Vapor)
                │                       │      └──► L (Liquid)
               E1                      E2

NO VALVE. The cooler provides the temperature drop; the flash drum
operates at a slightly lower pressure than the feed (set via flash_pressure_Pa,
defaults to feed pressure_Pa × 0.971 to match the professor's
3447370 → 3350000 Pa example). All pressure drop is attributed to the cooler
(PressureDrop on Cool-1).

Input JSON schema (professor's format from feedback PDF):
{
  "components":       {"Hydrogen": 0.3, "Methane": 0.6, ...},   ← dict, not list
  "pressure_Pa":      3447370.0,      ← feed pressure
  "temperature_K":    410.0,          ← feed temperature
  "flash_type":       "PT",           ← always "PT" for now
  "property_package": "Peng-Robinson" ← normalized to "PR" internally
}

Optional keys:
  "cooler_temp_K":    320.0           ← cooler outlet T (default: temperature_K - 90)
  "flash_pressure_Pa": 3350000.0      ← flash drum P   (default: pressure_Pa * 0.971)
  "feed_flow_molh":   5596990.0       ← feed molar flow [mol/h] (default: 100.0)

Validated reference (professor's manual DWSIM run):
  Feed:   H2(0.3)/CH4(0.6)/Benzene(0.075)/Toluene(0.025) @ 410 K, 3447370 Pa
  Cooler: outlet 320 K (46.85°C), removes 10345.38 kW, V_frac = 0.906286
  Flash:  @ 320 K / 3350000 Pa
  V:      5074.37 kmol/h, fully vapor
  L:      522.62 kmol/h,  fully liquid

CLI:
    python DWSIM_ry_test/tasks/flash_engine.py \\
        DWSIM_ry_test/tasks/examples/flash_h2_ch4_benzene_toluene.json

Group 1 orchestrator call:
    from DWSIM_ry_test.tasks.flash_engine import run_flash_task
    results = run_flash_task(task_dict, output_dir="output")
"""

import json
import os
import sys
import argparse

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
#  PATH SETUP
#  _HERE = dwsim_ai/DWSIM_ry_test/tasks/
#  _ROOT = dwsim_ai/
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_PATH = r"C:\Users\terrbear\AppData\Local\DWSIM"
OUTPUT_DIR  = os.path.join(_ROOT, "output")

POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL",
}

VALID_PACKAGES = ("NRTL", "PR", "SRK", "UNIQUAC", "IDEAL", "STEAM")

# Normalize any common alias → internal DWSIM tag
PACKAGE_ALIASES = {
    "PENG-ROBINSON":  "PR",
    "PENGROBINSON":   "PR",
    "PENG ROBINSON":  "PR",
    "PR":             "PR",
    "NRTL":           "NRTL",
    "SRK":            "SRK",
    "SOAVE-REDLICH-KWONG": "SRK",
    "UNIQUAC":        "UNIQUAC",
    "IDEAL":          "IDEAL",
    "RAOULT":         "IDEAL",
    "STEAM":          "STEAM",
    "STEAM TABLES":   "STEAM",
}

# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM AVAILABILITY GUARD
# ─────────────────────────────────────────────────────────────────────────────
DWSIM_AVAILABLE = False
try:
    import pythoncom  # noqa
    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim,
        create_flowsheet,
        add_component,
        select_property_package,
        solve_flowsheet,
        save_flowsheet,
    )
    DWSIM_AVAILABLE = True
    print("[flash_engine] DWSIM library ready.")
except Exception as _e:
    print(f"[flash_engine] DWSIM not available ({_e}) — running in mock mode.")


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_package(pkg_str: str) -> str:
    """'Peng-Robinson' → 'PR', 'nrtl' → 'NRTL', etc."""
    key = pkg_str.upper().replace("-", "").replace(" ", "")
    # Try removing hyphens/spaces first, then bare lookup
    for alias_key, val in PACKAGE_ALIASES.items():
        if alias_key.replace("-", "").replace(" ", "") == key:
            return val
    return pkg_str   # pass through; validation will catch unknowns


def _auto_select_package(components: list) -> str:
    """NRTL if any component is polar; PR otherwise."""
    if any(c.upper() in POLAR_COMPONENTS for c in components):
        return "NRTL"
    return "PR"


def _parse_components(task: dict):
    """
    Accept EITHER the professor's dict format OR the old list+composition format.

    Professor's format (new, preferred):
        "components": {"Hydrogen": 0.3, "Methane": 0.6, ...}

    Old format (still accepted for backward compatibility):
        "components": ["Hydrogen", "Methane", ...],
        "feed_composition": {"Hydrogen": 0.3, ...}

    Returns (comp_list, comp_dict) where:
        comp_list : ["Hydrogen", "Methane", ...]
        comp_dict : {"Hydrogen": 0.3, "Methane": 0.6, ...}
    """
    raw = task["components"]

    if isinstance(raw, dict):
        comp_list = list(raw.keys())
        comp_dict = {k: float(v) for k, v in raw.items()}
    elif isinstance(raw, list):
        comp_list = raw
        comp_dict = {k: float(v) for k, v in task.get("feed_composition", {}).items()}
    else:
        raise ValueError("'components' must be a dict {name: mole_frac} or a list of names.")

    return comp_list, comp_dict


# ─────────────────────────────────────────────────────────────────────────────
#  INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_flash_task(task: dict) -> None:
    """
    Validate a flash task dict. Raises ValueError with a clear message.

    Required keys (professor's format):
        components       : dict {name: mole_fraction}  OR list (old format)
        pressure_Pa      : float > 0   (feed pressure)
        temperature_K    : float > 0   (feed temperature)
        property_package : string

    Optional keys:
        flash_type       : "PT" (default, only supported type for now)
        cooler_temp_K    : float > 0   (default: temperature_K - 90)
        flash_pressure_Pa: float > 0   (default: pressure_Pa * 0.971)
        feed_flow_molh   : float > 0   (default: 100.0)
    """
    # Accept either "pressure_Pa"/"temperature_K" (new) or old field names
    required = {"components", "pressure_Pa", "temperature_K"}
    missing = required - task.keys()
    if missing:
        raise ValueError(f"Task JSON missing required keys: {missing}")

    comp_list, comp_dict = _parse_components(task)

    if len(comp_list) < 1:
        raise ValueError("'components' must contain at least one component.")

    if set(comp_dict.keys()) != set(comp_list):
        raise ValueError(
            f"Component names in composition {set(comp_dict.keys())} "
            f"must match components list {set(comp_list)}."
        )

    total = sum(comp_dict.values())
    if abs(total - 1.0) > 0.02:
        raise ValueError(
            f"Component mole fractions must sum to 1.0. Got: {total:.4f}"
        )

    p = task["pressure_Pa"]
    t = task["temperature_K"]
    if not isinstance(p, (int, float)) or p <= 0:
        raise ValueError(f"'pressure_Pa' must be a positive number. Got: {p}")
    if not isinstance(t, (int, float)) or t <= 0:
        raise ValueError(f"'temperature_K' must be a positive number. Got: {t}")

    pkg = task.get("property_package")
    if pkg is not None:
        if _normalize_package(pkg) not in VALID_PACKAGES:
            raise ValueError(
                f"Unknown property_package: '{pkg}'. Valid: {VALID_PACKAGES}"
            )

    flash_p = task.get("flash_pressure_Pa", p * 0.971)
    if flash_p >= p:
        raise ValueError(
            f"flash_pressure_Pa ({flash_p} Pa) must be < pressure_Pa ({p} Pa)."
        )

    print("[flash_engine] Task validation passed.")


# ─────────────────────────────────────────────────────────────────────────────
#  STREAM RESULT EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────

def _extract_stream(stream_obj, stream_name: str, comp_names: list) -> dict:
    """
    Pull T, P, molar flow, vapor fraction, and per-component mole fractions
    from a solved DWSIM MaterialStream.

    DWSIM Phase indexing:
        Phases[0] = Overall mixture  → temperature, pressure, molarflow
        Phases[2] = Vapor phase      → molarfraction = vapor fraction of mixture

    pythonnet may return properties as plain float or Nullable[Double] depending
    on version. We use a safe helper that handles both.
    """
    def _get(val):
        """Safely extract float from either a plain float or Nullable[Double]."""
        if val is None:
            return 0.0
        try:
            # Plain Python float or int
            return float(val)
        except TypeError:
            try:
                # .NET Nullable[Double]
                return float(val.GetValueOrDefault())
            except Exception:
                return 0.0

    p0 = stream_obj.Phases[0].Properties
    p2 = stream_obj.Phases[2].Properties  # vapor phase

    result = {
        "stream":          stream_name,
        "T_K":             round(_get(p0.temperature),   4),
        "T_C":             round(_get(p0.temperature) - 273.15, 4),
        "P_Pa":            round(_get(p0.pressure),      2),
        "P_bar":           round(_get(p0.pressure) / 1e5, 4),
        "molar_flow_molh": round(_get(p0.molarflow) * 3600, 4),
        "vapor_fraction":  round(_get(p2.molarfraction), 6),
    }

    for comp_name in comp_names:
        try:
            xval = _get(stream_obj.Phases[0].Compounds[comp_name].MoleFraction)
        except Exception:
            xval = 0.0
        result[f"x_{comp_name}"] = round(xval, 6)

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM FLOWSHEET: Feed → Cool-1 → Flash1 → V + L
#
#  NO VALVE. Topology:
#
#    FEED ──(mat)──► [Cool-1] ──(mat)──► FFEED ──(mat)──► [Flash1] ──► V
#                       │                                     │      └──► L
#                      E1                                    E2
#
#  Pressure drop across the cooler = feed_pressure - flash_pressure.
#  Flash drum inherits T and P from FFEED stream (set by cooler).
# ─────────────────────────────────────────────────────────────────────────────

def _run_dwsim_flash(task: dict, dwsim_path: str, output_dir: str) -> dict:
    """Build and solve Feed → Cooler → Flash in DWSIM headless.

    Follows official DanWBR automation gist patterns exactly:
    - sim.AddCompound() instead of AvailableCompounds dict
    - m1.SetTemperature / SetPressure / SetMolarFlow for feed conditions
    - m1.SetOverallCompoundMolarFlow for per-component flows
    - sim.CreateAndAddPropertyPackage() for property package
    - sim.ConnectObjects(obj1.GraphicObject, obj2.GraphicObject, -1, -1)
    - interf.CalculateFlowsheet4(sim) — latest solver API
    """
    import pythoncom
    pythoncom.CoInitialize()

    comp_list, comp_dict = _parse_components(task)
    feed_temp   = float(task["temperature_K"])
    feed_press  = float(task["pressure_Pa"])
    feed_flow   = float(task.get("feed_flow_molh", 100.0))
    cooler_temp = float(task.get("cooler_temp_K",  feed_temp - 90.0))
    flash_press = float(task.get("flash_pressure_Pa", feed_press * 0.971))

    raw_pkg = task.get("property_package")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg else _auto_select_package(comp_list)

    # ── 1. Init ───────────────────────────────────────────────────────────────
    interf = init_dwsim(dwsim_path)
    sim    = create_flowsheet(interf)

    # ── 2. Components — official API: sim.AddCompound(name) ──────────────────
    for comp in comp_list:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    # ── 3. Property package — use dwsim_core.select_property_package ─────────
    # CreateAndAddPropertyPackage requires exact DWSIM internal display name keys
    # which vary by version. select_property_package instantiates the class
    # directly and is confirmed working for this DWSIM installation.
    select_property_package(sim, pkg_tag)
    print(f"[flash_engine] Property package: {pkg_tag}")

    # ── 4. Enums (safe after DLL load) ────────────────────────────────────────
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    # ── 5. Material streams ───────────────────────────────────────────────────
    feed_obj   = sim.AddObject(ObjectType.MaterialStream,  50, 300, "FEED" ).GetAsObject()
    ffeed_obj  = sim.AddObject(ObjectType.MaterialStream, 350, 300, "FFEED").GetAsObject()
    vapor_obj  = sim.AddObject(ObjectType.MaterialStream, 650, 100, "V"    ).GetAsObject()
    liquid_obj = sim.AddObject(ObjectType.MaterialStream, 650, 500, "L"    ).GetAsObject()

    # ── 6. Energy streams ─────────────────────────────────────────────────────
    e1 = sim.AddObject(ObjectType.EnergyStream, 200, 500, "E1").GetAsObject()
    e2 = sim.AddObject(ObjectType.EnergyStream, 500, 500, "E2").GetAsObject()

    # ── 7. Unit operations ────────────────────────────────────────────────────
    cooler_uo = sim.AddObject(ObjectType.Cooler, 200, 300, "Cool-1").GetAsObject()
    flash_uo  = sim.AddObject(ObjectType.Vessel, 500, 300, "Flash1").GetAsObject()

    # ── 8. Configure feed — official API: SetTemperature/SetPressure/SetMolarFlow
    # (from DanWBR heater gist: m1.SetTemperature(300.0), m1.SetMassFlow(100.0))
    # (from DanWBR PFR gist:   m1.SetMolarFlow(0.0), m1.SetOverallCompoundMolarFlow(...))
    feed_obj.SetTemperature(feed_temp)   # K
    feed_obj.SetPressure(feed_press)     # Pa
    feed_obj.SetMolarFlow(feed_flow / 3600.0)   # mol/s

    # Set per-component molar flows (mol/s) — official PFR gist pattern
    for comp_name in comp_names:
        mole_frac = float(comp_dict.get(comp_name, 0.0))
        comp_flow = mole_frac * feed_flow / 3600.0  # mol/s
        feed_obj.SetOverallCompoundMolarFlow(comp_name, comp_flow)

    print(f"[flash_engine] Feed: {feed_temp} K, {feed_press:.0f} Pa, {feed_flow:.1f} mol/h")
    print(f"[flash_engine] Composition: {comp_dict}")

    # ── 9. Configure Cooler ───────────────────────────────────────────────────
    cooler_uo.CalcMode          = DWSIMUnitOps.Cooler.CalculationMode.OutletTemperature
    cooler_uo.OutletTemperature = cooler_temp
    cooler_uo.PressureDrop      = feed_press - flash_press
    print(f"[flash_engine] Cooler: T_out={cooler_temp} K, ΔP={feed_press - flash_press:.0f} Pa")

    # ── 10. Flash drum — no config needed ─────────────────────────────────────
    # Inherits T and P from FFEED (cooler outlet). DWSIM solves VLE automatically.

    # ── 11. Wire flowsheet ────────────────────────────────────────────────────
    sim.ConnectObjects(feed_obj.GraphicObject,    cooler_uo.GraphicObject, -1, -1)
    sim.ConnectObjects(cooler_uo.GraphicObject,   ffeed_obj.GraphicObject, -1, -1)
    sim.ConnectObjects(e1.GraphicObject,          cooler_uo.GraphicObject, -1, -1)
    sim.ConnectObjects(ffeed_obj.GraphicObject,   flash_uo.GraphicObject,  -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,    vapor_obj.GraphicObject, -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,    liquid_obj.GraphicObject,-1, -1)
    sim.ConnectObjects(e2.GraphicObject,          flash_uo.GraphicObject,  -1, -1)

    sim.AutoLayout()
    print("[flash_engine] Wired: FEED → Cool-1 → FFEED → Flash1 → V / L")

    # ── 12. Solve — use CalculateFlowsheet4 (latest API from PFR gist) ────────
    from DWSIM.GlobalSettings import Settings
    Settings.SolverMode = 0  # synchronous
    errors = interf.CalculateFlowsheet4(sim)

    if errors is not None and len(errors) > 0:
        err_list = [str(e) for e in errors]
        print(f"[flash_engine] Solver errors: {err_list}")
        raise RuntimeError("[flash_engine] Solver errors:\n" + "\n".join(err_list))

    print("[flash_engine] Flowsheet solved successfully.")

    # ── 13. Extract results ───────────────────────────────────────────────────
    results = {
        "feed":       _extract_stream(feed_obj,   "FEED",   comp_names),
        "cooler_out": _extract_stream(ffeed_obj,  "FFEED",  comp_names),
        "vapor":      _extract_stream(vapor_obj,  "V",      comp_names),
        "liquid":     _extract_stream(liquid_obj, "L",      comp_names),
        "property_package": pkg_tag,
    }

    # ── 14. Save .dwxmz ───────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    save_flowsheet(interf, sim, os.path.join(output_dir, f"flash_{tag}.dwxmz"))

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  MOCK FLASH  (Rachford-Rice, no DWSIM)
# ─────────────────────────────────────────────────────────────────────────────

def _mock_flash(task: dict) -> dict:
    """
    Approximate flash using Rachford-Rice + Antoine / calibrated K-values.
    Simulates FEED, FFEED (cooler out), V, L — no valve stream.
    """
    import math

    ANTOINE = {
        "ETHANOL":  (8.04494, 1554.30,  222.65),
        "WATER":    (8.07131, 1730.63,  233.426),
        "BENZENE":  (6.89272, 1203.531, 219.888),
        "TOLUENE":  (6.95805, 1346.773, 219.693),
        "METHANOL": (7.87863, 1473.11,  230.00),
        "ACETONE":  (7.02447, 1161.0,   224.0),
    }

    # Calibrated K-values for professor's H2/CH4/Benzene/Toluene case
    # at 320 K / 3350000 Pa using Peng-Robinson
    HIGH_PRESSURE_K = {
        "HYDROGEN": 65.0,
        "METHANE":  8.0,
        "BENZENE":  0.13,
        "TOLUENE":  0.07,
        "ETHANE":   3.5,
        "PROPANE":  1.2,
        "N-BUTANE": 0.4,
    }

    comp_list, comp_dict = _parse_components(task)
    z = [comp_dict[c] for c in comp_list]

    feed_temp   = float(task["temperature_K"])
    feed_press  = float(task["pressure_Pa"])
    feed_flow   = float(task.get("feed_flow_molh", 100.0))
    cooler_temp = float(task.get("cooler_temp_K",  feed_temp - 90.0))
    flash_press = float(task.get("flash_pressure_Pa", feed_press * 0.971))
    T_flash_C   = cooler_temp - 273.15
    P_mmHg      = flash_press / 133.322

    # K-values at flash conditions
    K = []
    for comp in comp_list:
        cu = comp.upper()
        if cu in HIGH_PRESSURE_K:
            K.append(HIGH_PRESSURE_K[cu])
        elif cu in ANTOINE:
            A, B, C = ANTOINE[cu]
            Psat = 10 ** (A - B / (T_flash_C + C))
            K.append(Psat / P_mmHg)
        else:
            K.append(1.0)

    # Rachford-Rice
    def rr(V):
        return sum(z[i] * (K[i] - 1) / (1 + V * (K[i] - 1)) for i in range(len(z)))

    sum_Kz = sum(K[i] * z[i] for i in range(len(z)))
    sum_zK = sum(z[i] / K[i] for i in range(len(z)))

    if sum_Kz <= 1.0:
        V_frac = 0.0
    elif sum_zK <= 1.0:
        V_frac = 1.0
    else:
        lo, hi = 1e-8, 1 - 1e-8
        for _ in range(200):
            mid = (lo + hi) / 2
            if rr(mid) > 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-12:
                break
        V_frac = (lo + hi) / 2

    # Phase compositions
    x_raw = [z[i] / (1 + V_frac * (K[i] - 1)) if abs(1 + V_frac * (K[i] - 1)) > 1e-12
             else z[i] for i in range(len(z))]
    y_raw = [K[i] * x_raw[i] for i in range(len(z))]

    xs = sum(x_raw); ys = sum(y_raw)
    x_liq = [round(x / xs, 6) if xs > 1e-12 else round(z[i], 6)
              for i, x in enumerate(x_raw)]
    y_vap = [round(y / ys, 6) if ys > 1e-12 else round(z[i], 6)
              for i, y in enumerate(y_raw)]

    feed_mols = feed_flow / 3600.0  # mol/s

    def _s(name, T_K, P_Pa, vf, fracs, flow_mols):
        d = {
            "stream":          name,
            "T_K":             round(T_K, 4),
            "T_C":             round(T_K - 273.15, 4),
            "P_Pa":            round(P_Pa, 2),
            "P_bar":           round(P_Pa / 1e5, 4),
            "molar_flow_molh": round(flow_mols * 3600, 4),
            "vapor_fraction":  round(vf, 6),
        }
        for comp, xval in zip(comp_list, fracs):
            d[f"x_{comp}"] = round(xval, 6)
        return d

    results = {
        "feed":       _s("FEED",  feed_temp,   feed_press,  0.0,    z,     feed_mols),
        "cooler_out": _s("FFEED", cooler_temp, flash_press, V_frac, z,     feed_mols),
        "vapor":      _s("V",     cooler_temp, flash_press, 1.0,    y_vap, feed_mols * V_frac),
        "liquid":     _s("L",     cooler_temp, flash_press, 0.0,    x_liq, feed_mols * (1 - V_frac)),
        "property_package": "MOCK (Rachford-Rice)",
    }

    print(f"[flash_engine] MOCK flash: V_frac = {V_frac:.4f}")
    print(f"[flash_engine] K-values: "
          + ", ".join(f"{c}={K[i]:.3f}" for i, c in enumerate(comp_list)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  RESULTS FORMATTING
# ─────────────────────────────────────────────────────────────────────────────

def results_to_dataframe(results: dict) -> pd.DataFrame:
    """One row per stream (4 rows: FEED, FFEED, V, L)."""
    rows = []
    for key in ("feed", "cooler_out", "vapor", "liquid"):
        if key in results:
            rows.append(results[key])
    return pd.DataFrame(rows)


def print_flash_summary(results: dict) -> None:
    pkg = results.get("property_package", "—")
    print("\n" + "=" * 72)
    print(f"  FLASH FLOWSHEET RESULTS  |  Property Package: {pkg}")
    print("  Feed → Cool-1 → FFEED → Flash1 → V / L  (no valve)")
    print("=" * 72)

    labels = {
        "feed":       "  FEED        →",
        "cooler_out": "  FFEED(Cool) →",
        "vapor":      "  V (Vapor)   →",
        "liquid":     "  L (Liquid)  →",
    }
    for key, label in labels.items():
        if key not in results:
            continue
        s = results[key]
        print(f"\n{label}")
        print(f"    T = {s['T_K']:.2f} K ({s['T_C']:.2f} °C)  "
              f"P = {s['P_Pa']:.0f} Pa ({s['P_bar']:.3f} bar)  "
              f"V_frac = {s['vapor_fraction']:.4f}  "
              f"flow = {s['molar_flow_molh']:.2f} mol/h")
        for k, v in s.items():
            if k.startswith("x_"):
                comp = k[2:]
                print(f"      {k:<20} = {v:.6f}")

    v  = results.get("vapor",  {})
    l  = results.get("liquid", {})
    if v and l:
        vfh = v["molar_flow_molh"]
        lfh = l["molar_flow_molh"]
        #vf  = results["cooler_out"]["vapor_fraction"]
        print(f"\n  ── Separation summary ───────────────────────────────")
        #print(f"     Overall vapor fraction  : {vf:.4f}")
        print(f"     V stream flow           : {vfh:.2f} mol/h")
        print(f"     L stream flow           : {lfh:.2f} mol/h")
        print(f"     V/(V+L) check           : {vfh/(vfh+lfh):.4f}")
    print("=" * 72 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# ISOTHERMAL PT FLASH — DWSIM path
# Topology: FEED ──► [Flash1] ──► V (Vapor)
#                              └──► L (Liquid)
# ─────────────────────────────────────────────────────────────────────────────
def _run_dwsim_isothermal_pt_flash(
    comp_list, comp_dict,
    feed_temp_K, feed_press, feed_flow,
    drum_temp_K, drum_press,
    pkg_tag, dwsim_path, output_dir
) -> dict:
    import pythoncom
    pythoncom.CoInitialize()

    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet,
    )

    interf = init_dwsim(dwsim_path)
    sim    = create_flowsheet(interf)

    for comp in comp_list:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    select_property_package(sim, pkg_tag)

    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.GlobalSettings import Settings

    # ── Material streams ────────────────────────────────────────────────────
    feed_obj   = sim.AddObject(ObjectType.MaterialStream, 50,  300, "FEED").GetAsObject()
    vapor_obj  = sim.AddObject(ObjectType.MaterialStream, 400, 100, "V"   ).GetAsObject()
    liquid_obj = sim.AddObject(ObjectType.MaterialStream, 400, 500, "L"   ).GetAsObject()

    # ── Energy stream ───────────────────────────────────────────────────────
    e1 = sim.AddObject(ObjectType.EnergyStream, 250, 500, "E1").GetAsObject()

    # ── Flash drum ──────────────────────────────────────────────────────────
    flash_uo = sim.AddObject(ObjectType.Vessel, 250, 300, "Flash1").GetAsObject()

    # ── Set feed to DRUM conditions (T and P define the flash equilibrium) ──
    # For isothermal PT flash, we want VLE at drum_T and drum_P.
    # The Vessel inherits T and P from the inlet stream.
    feed_obj.SetTemperature(drum_temp_K)
    feed_obj.SetPressure(drum_press)
    feed_obj.SetMolarFlow(feed_flow)
    for comp_name in comp_names:
        mole_frac = float(comp_dict.get(comp_name, 0.0))
        feed_obj.SetOverallCompoundMolarFlow(comp_name, mole_frac * feed_flow)

    print(f"[flash_engine] Flash conditions: {drum_temp_K-273.15:.1f} C, {drum_press/1e5:.2f} bar")

    # ── Wire: FEED → Flash1 → V / L ─────────────────────────────────────────
    sim.ConnectObjects(feed_obj.GraphicObject,  flash_uo.GraphicObject,   -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,  vapor_obj.GraphicObject,  -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,  liquid_obj.GraphicObject, -1, -1)
    sim.ConnectObjects(e1.GraphicObject,        flash_uo.GraphicObject,   -1, -1)
    sim.AutoLayout()

    # ── Solve ────────────────────────────────────────────────────────────────
    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors is not None and len(errors) > 0:
        raise RuntimeError("[flash_engine] Solver errors:\n" + "\n".join(str(e) for e in errors))

    print("[flash_engine] isothermal_PT_flash solved successfully.")

    # ── Extract — report FEED as the overall feed info, V and L as products ─
    results = {
        "feed":             _extract_stream(feed_obj,   "FEED", comp_names),
        "vapor":            _extract_stream(vapor_obj,  "V",    comp_names),
        "liquid":           _extract_stream(liquid_obj, "L",    comp_names),
        "property_package": pkg_tag,
    }

    # Manually patch FEED stream to show original feed conditions for clarity
    results["feed"]["T_C"]    = round(feed_temp_K - 273.15, 4)
    results["feed"]["T_K"]    = round(feed_temp_K, 4)
    results["feed"]["P_Pa"]   = round(feed_press, 2)
    results["feed"]["P_bar"]  = round(feed_press / 1e5, 4)

    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    save_flowsheet(interf, sim, os.path.join(output_dir, f"isothermal_pt_flash_{tag}.dwxmz"))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# ISOTHERMAL PT FLASH — Mock path (Rachford-Rice, no DWSIM)
# ─────────────────────────────────────────────────────────────────────────────
def _mock_isothermal_pt_flash(
    comp_list, comp_dict,
    feed_temp_K, feed_press,
    drum_temp_K, drum_press,
    feed_flow_mol_s
) -> dict:
    """Approximate isothermal PT flash using Rachford-Rice + Antoine/calibrated K-values."""
    import math

    ANTOINE = {
        "ETHANOL":  (8.04494, 1554.30,  222.65),
        "WATER":    (8.07131, 1730.63,  233.426),
        "BENZENE":  (6.89272, 1203.531, 219.888),
        "TOLUENE":  (6.95805, 1346.773, 219.693),
        "METHANOL": (7.87863, 1473.11,  230.00),
        "ACETONE":  (7.02447, 1161.0,   224.0),
    }
    HIGH_PRESSURE_K = {
        "HYDROGEN": 65.0, "METHANE": 8.0,  "BENZENE":  0.13,
        "TOLUENE":  0.07, "ETHANE":  3.5,  "PROPANE":  2.0,
        "N-BUTANE": 0.6,
    }

    z         = [comp_dict[c] for c in comp_list]
    T_flash_C = drum_temp_K - 273.15
    P_mmHg    = drum_press / 133.322

    K = []
    for comp in comp_list:
        cu = comp.upper()
        if cu in HIGH_PRESSURE_K:
            K.append(HIGH_PRESSURE_K[cu])
        elif cu in ANTOINE:
            A, B, C = ANTOINE[cu]
            Psat = 10 ** (A - B / (T_flash_C + C))
            K.append(Psat / P_mmHg)
        else:
            K.append(1.0)

    # Rachford-Rice
    def rr(V):
        return sum(z[i] * (K[i] - 1) / (1 + V * (K[i] - 1)) for i in range(len(z)))

    sum_Kz  = sum(K[i] * z[i] for i in range(len(z)))
    sum_zK  = sum(z[i] / K[i] for i in range(len(z)))

    if sum_Kz <= 1.0:
        V_frac = 0.0
    elif sum_zK <= 1.0:
        V_frac = 1.0
    else:
        lo, hi = 1e-8, 1 - 1e-8
        for _ in range(200):
            mid = (lo + hi) / 2
            if rr(mid) > 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-12:
                break
        V_frac = (lo + hi) / 2

    x_raw = [z[i] / (1 + V_frac * (K[i] - 1)) for i in range(len(z))]
    y_raw = [K[i] * x_raw[i] for i in range(len(z))]
    xs = sum(x_raw); ys = sum(y_raw)
    x_liq = [round(x / xs, 6) if xs > 1e-12 else round(z[i], 6) for i, x in enumerate(x_raw)]
    y_vap = [round(y / ys, 6) if ys > 1e-12 else round(z[i], 6) for i, y in enumerate(y_raw)]

    def _s(name, T_K, P_Pa, vf, fracs, flow_mol_s):
        d = {
            "stream":           name,
            "T_K":              round(T_K, 4),
            "T_C":              round(T_K - 273.15, 4),
            "P_Pa":             round(P_Pa, 2),
            "P_bar":            round(P_Pa / 1e5, 4),
            "molar_flow_molh":  round(flow_mol_s * 3600, 4),
            "vapor_fraction":   round(vf, 6),
        }
        for comp, xval in zip(comp_list, fracs):
            d[f"x_{comp}"] = round(xval, 6)
        return d

    results = {
        "feed":             _s("FEED", feed_temp_K, feed_press,  0.0,    z,     feed_flow_mol_s),
        "vapor":            _s("V",    drum_temp_K,  drum_press, 1.0,    y_vap, feed_flow_mol_s * V_frac),
        "liquid":           _s("L",    drum_temp_K,  drum_press, 0.0,    x_liq, feed_flow_mol_s * (1 - V_frac)),
        "property_package": "MOCK (Rachford-Rice)",
    }

    print(f"[flash_engine] MOCK isothermal_PT_flash: V_frac = {V_frac:.4f}")
    print(f"[flash_engine] K-values: " + ", ".join(f"{c}={K[i]:.3f}" for i, c in enumerate(comp_list)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# ISOTHERMAL PT FLASH — Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def run_isothermal_pt_flash(
    task: dict,
    dwsim_path: str = DWSIM_PATH,
    output_dir: str = None,
) -> dict:
    """
    Run an isothermal PT flash: feed enters a drum held at fixed T and P.
    No cooler or valve — the drum conditions are set directly.

    Required JSON fields:
        components       : {name: mole_fraction}
        feed             : {flow_kmol_h, temperature_C, pressure_bar}
        flash_drum       : {temperature_C, pressure_bar}
        property_package : string
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    comp_list = list(task["components"].keys())
    comp_dict = {k: float(v) for k, v in task["components"].items()}
    feed      = task["feed"]
    drum      = task["flash_drum"]

    feed_flow_mol_s = float(feed.get("flow_kmol_h", 100.0)) * 1000 / 3600  # mol/s
    feed_temp_K     = float(feed["temperature_C"]) + 273.15
    feed_press      = float(feed["pressure_bar"]) * 1e5   # Pa
    drum_temp_K     = float(drum["temperature_C"]) + 273.15
    drum_press      = float(drum["pressure_bar"]) * 1e5   # Pa

    raw_pkg = task.get("property_package", "auto")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg != "auto" else _auto_select_package(comp_list)

    print(f"\n[flash_engine] ── isothermal_PT_flash ───────────────────────────")
    print(f"[flash_engine] Components  : {comp_list}")
    print(f"[flash_engine] Feed        : {feed['temperature_C']} C, {feed['pressure_bar']} bar")
    print(f"[flash_engine] Drum        : {drum['temperature_C']} C, {drum['pressure_bar']} bar")
    print(f"[flash_engine] Package     : {pkg_tag}")

    dwsim_ready = DWSIM_AVAILABLE and os.path.isdir(dwsim_path)

    if dwsim_ready:
        results = _run_dwsim_isothermal_pt_flash(
            comp_list, comp_dict,
            feed_temp_K, feed_press, feed_flow_mol_s,
            drum_temp_K, drum_press,
            pkg_tag, dwsim_path, output_dir
        )
    else:
        if DWSIM_AVAILABLE and not os.path.isdir(dwsim_path):
            print(f"[flash_engine] DWSIM path not found: {dwsim_path} — using mock.")
        results = _mock_isothermal_pt_flash(
            comp_list, comp_dict,
            feed_temp_K, feed_press,
            drum_temp_K, drum_press,
            feed_flow_mol_s
        )

    # Save CSV
    os.makedirs(output_dir, exist_ok=True)
    tag      = "_".join(comp_list)
    rows     = [results[k] for k in ("feed", "vapor", "liquid") if k in results]
    df       = pd.DataFrame(rows)
    csv_path = os.path.join(output_dir, f"isothermal_pt_flash_{tag}.csv")
    df.to_csv(csv_path, index=False)
    print(f"[flash_engine] CSV saved: {csv_path}")

    # Print summary
    print("\n" + "=" * 72)
    print(f" ISOTHERMAL PT FLASH RESULTS | Property Package: {results.get('property_package','—')}")
    print(f" Feed → Flash1 → V / L  (no cooler, no valve)")
    print("=" * 72)
    for key in ("feed", "cooler_out", "vapor", "liquid"):
        if key not in results:
            continue
        s = results[key]
        print(f"\n  {s['stream']:<6}  T={s['T_C']:.2f} C  P={s['P_bar']:.3f} bar  "
              f"V_frac={s['vapor_fraction']:.4f}  flow={s['molar_flow_molh']:.2f} mol/h")
        for k, v in s.items():
            if k.startswith("x_"):
                print(f"    {k:<22} = {v:.6f}")
    v_flow = results.get("vapor", {}).get("molar_flow_molh", 0)
    l_flow = results.get("liquid", {}).get("molar_flow_molh", 0)
    if v_flow + l_flow > 0:
        overall_vf = v_flow / (v_flow + l_flow)
        print(f"\n  Overall vapor fraction (DWSIM): {overall_vf:.4f}")
        print(f"  V stream flow                 : {v_flow:.2f} mol/h")
        print(f"  L stream flow                 : {l_flow:.2f} mol/h")
    print("=" * 72 + "\n")

    return results


def _run_dwsim_adiabatic_pt_flash(
    comp_list, comp_dict,
    feed_temp_K, feed_press, feed_flow,
    drum_press,
    pkg_tag, dwsim_path, output_dir
) -> dict:
    import pythoncom
    pythoncom.CoInitialize()

    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet,
    )

    interf = init_dwsim(dwsim_path)
    sim = create_flowsheet(interf)

    for comp in comp_list:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    select_property_package(sim, pkg_tag)

    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.GlobalSettings import Settings
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    feed_obj   = sim.AddObject(ObjectType.MaterialStream,  50, 300, "FEED").GetAsObject()
    valve_out  = sim.AddObject(ObjectType.MaterialStream, 250, 300, "VFEED").GetAsObject()
    vapor_obj  = sim.AddObject(ObjectType.MaterialStream, 500, 100, "V").GetAsObject()
    liquid_obj = sim.AddObject(ObjectType.MaterialStream, 500, 500, "L").GetAsObject()

    # e1 = sim.AddObject(ObjectType.EnergyStream, 150, 500, "E1").GetAsObject()
    e2 = sim.AddObject(ObjectType.EnergyStream, 375, 500, "E2").GetAsObject()

    valve_uo = sim.AddObject(ObjectType.Valve, 150, 300, "Valve1").GetAsObject()
    flash_uo = sim.AddObject(ObjectType.Vessel, 375, 300, "Flash1").GetAsObject()

    feed_obj.SetTemperature(feed_temp_K)
    feed_obj.SetPressure(feed_press)
    feed_obj.SetMolarFlow(feed_flow)
    for comp_name in comp_names:
        mole_frac = float(comp_dict.get(comp_name, 0.0))
        feed_obj.SetOverallCompoundMolarFlow(comp_name, mole_frac * feed_flow)

    valve_uo.CalcMode = DWSIMUnitOps.Valve.CalculationMode.OutletPressure
    valve_uo.OutletPressure = drum_press

    sim.ConnectObjects(feed_obj.GraphicObject,  valve_uo.GraphicObject,   -1, -1)
    # sim.ConnectObjects(e1.GraphicObject,        valve_uo.GraphicObject,   -1, -1)
    sim.ConnectObjects(valve_uo.GraphicObject,  valve_out.GraphicObject,  -1, -1)
    sim.ConnectObjects(valve_out.GraphicObject, flash_uo.GraphicObject,   -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,  vapor_obj.GraphicObject,  -1, -1)
    sim.ConnectObjects(flash_uo.GraphicObject,  liquid_obj.GraphicObject, -1, -1)
    sim.ConnectObjects(e2.GraphicObject,        flash_uo.GraphicObject,   -1, -1)

    sim.AutoLayout()

    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors is not None and len(errors) > 0:
        raise RuntimeError("[flash_engine] Solver errors:\n" + "\n".join(str(e) for e in errors))

    print("[flash_engine] adiabatic_PT_flash solved successfully.")

    results = {
        "feed":             _extract_stream(feed_obj,   "FEED",  comp_names),
        "vapor":            _extract_stream(vapor_obj,  "V",     comp_names),
        "liquid":           _extract_stream(liquid_obj, "L",     comp_names),
        "property_package": pkg_tag,
    }

    results["feed"]["T_C"]   = round(feed_temp_K - 273.15, 4)
    results["feed"]["T_K"]   = round(feed_temp_K, 4)
    results["feed"]["P_Pa"]  = round(feed_press, 2)
    results["feed"]["P_bar"] = round(feed_press / 1e5, 4)

    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    save_flowsheet(interf, sim, os.path.join(output_dir, f"adiabatic_pt_flash_{tag}.dwxmz"))
    return results

def _mock_adiabatic_pt_flash(
    comp_list, comp_dict,
    feed_temp_K, feed_press,
    drum_press,
    feed_flow_mol_s
) -> dict:
    """
    Simple placeholder mock:
    approximate adiabatic flash by assuming the flash temperature
    drops 15 C from feed, then reuse the isothermal mock.
    Replace later with enthalpy-based iteration if desired.
    """
    drum_temp_K = feed_temp_K - 15.0

    results = _mock_isothermal_pt_flash(
        comp_list, comp_dict,
        feed_temp_K, feed_press,
        drum_temp_K, drum_press,
        feed_flow_mol_s
    )
    results["property_package"] = "MOCK (adiabatic PT flash)"
    return results

# ─────────────────────────────────────────────────────────────────────────────
# ADIABATIC PT FLASH — Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def run_adiabatic_pt_flash(
    task: dict,
    dwsim_path: str = DWSIM_PATH,
    output_dir: str = None,
) -> dict:
    """
    Run an adiabatic PT flash:
    - feed enters at specified feed T and P
    - drum pressure is specified
    - drum temperature is solved by the flash from energy balance (adiabatic)
    """

    if output_dir is None:
        output_dir = OUTPUT_DIR

    comp_list = list(task["components"].keys())
    comp_dict = {k: float(v) for k, v in task["components"].items()}
    feed = task["feed"]
    drum = task["flash_drum"]

    feed_flow_mol_s = float(feed.get("flow_kmol_h", 100.0)) * 1000 / 3600
    feed_temp_K = float(feed["temperature_C"]) + 273.15
    feed_press = float(feed["pressure_bar"]) * 1e5
    drum_press = float(drum["pressure_bar"]) * 1e5

    raw_pkg = task.get("property_package", "auto")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg != "auto" else _auto_select_package(comp_list)

    print(f"\n[flash_engine] ── adiabatic_PT_flash ───────────────────────────")
    print(f"[flash_engine] Components  : {comp_list}")
    print(f"[flash_engine] Feed        : {feed['temperature_C']} C, {feed['pressure_bar']} bar")
    print(f"[flash_engine] Drum P      : {drum['pressure_bar']} bar")
    print(f"[flash_engine] Package     : {pkg_tag}")

    dwsim_ready = DWSIM_AVAILABLE and os.path.isdir(dwsim_path)

    if dwsim_ready:
        results = _run_dwsim_adiabatic_pt_flash(
            comp_list, comp_dict,
            feed_temp_K, feed_press, feed_flow_mol_s,
            drum_press,
            pkg_tag, dwsim_path, output_dir
        )
    else:
        if DWSIM_AVAILABLE and not os.path.isdir(dwsim_path):
            print(f"[flash_engine] DWSIM path not found: {dwsim_path} — using mock.")
        results = _mock_adiabatic_pt_flash(
            comp_list, comp_dict,
            feed_temp_K, feed_press,
            drum_press,
            feed_flow_mol_s
        )

    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    rows = [results[k] for k in ("feed", "vapor", "liquid") if k in results]
    df = pd.DataFrame(rows)
    csv_path = os.path.join(output_dir, f"adiabatic_pt_flash_{tag}.csv")
    df.to_csv(csv_path, index=False)
    print(f"[flash_engine] CSV saved: {csv_path}")

    print("\n" + "=" * 72)
    print(f" ADIABATIC PT FLASH RESULTS | Property Package: {results.get('property_package','—')}")
    print(f" Feed → Valve/flash to drum P → Flash1 → V / L  (Q = 0)")
    print("=" * 72)
    for key in ("feed", "vapor", "liquid"):
        if key not in results:
            continue
        s = results[key]
        print(f"\n  {s['stream']:<6}  T={s['T_C']:.2f} C  P={s['P_bar']:.3f} bar  "
              f"V_frac={s['vapor_fraction']:.4f}  flow={s['molar_flow_molh']:.2f} mol/h")
        for k, v in s.items():
            if k.startswith("x_"):
                print(f"    {k:<22} = {v:.6f}")
    v_flow = results.get("vapor", {}).get("molar_flow_molh", 0)
    l_flow = results.get("liquid", {}).get("molar_flow_molh", 0)
    if v_flow + l_flow > 0:
        overall_vf = v_flow / (v_flow + l_flow)
        print(f"\n  Overall vapor fraction (DWSIM): {overall_vf:.4f}")
        print(f"  V stream flow                 : {v_flow:.2f} mol/h")
        print(f"  L stream flow                 : {l_flow:.2f} mol/h")
    print("=" * 72 + "\n")

    return results

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def run_flash_task(
    task: dict,
    dwsim_path: str = DWSIM_PATH,
    output_dir: str = None,
) -> dict:
    """
    Run a Flash Flowsheet task: Feed → Cooler → Flash → V + L.

    Parameters
    ----------
    task       : task dict matching professor's JSON format
    dwsim_path : path to DWSIM installation
    output_dir : where to write CSV + .dwxmz  (default: dwsim_ai/output/)

    Returns
    -------
    results : dict with keys: feed, cooler_out, vapor, liquid, property_package
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    flash_mode = task.get("flash_mode", "legacy_flash")

    # Route to mode-specific handler
    if flash_mode == "isothermal_PT_flash":
        return run_isothermal_pt_flash(task, dwsim_path=dwsim_path, output_dir=output_dir)

    if flash_mode == "adiabatic_PT_flash":
        return run_adiabatic_pt_flash(task, dwsim_path=dwsim_path, output_dir=output_dir)

    # Legacy path — original flat-schema flash (Feed→Cooler→Flash)
    validate_flash_task(task)

    comp_list, _ = _parse_components(task)
    tag = "_".join(comp_list)

    raw_pkg = task.get("property_package", "auto")
    print(f"\n[flash_engine] ── Flash task ──────────────────────────────")
    print(f"[flash_engine] Components : {comp_list}")
    print(f"[flash_engine] Feed       : {task['temperature_K']} K, {task['pressure_Pa']} Pa")
    print(f"[flash_engine] Cooler out : {task.get('cooler_temp_K', task['temperature_K']-90)} K")
    print(f"[flash_engine] Flash P    : {task.get('flash_pressure_Pa', task['pressure_Pa']*0.971):.0f} Pa")
    print(f"[flash_engine] Package    : {raw_pkg}")

    dwsim_ready = DWSIM_AVAILABLE and os.path.isdir(dwsim_path)

    if dwsim_ready:
        results = _run_dwsim_flash(task, dwsim_path, output_dir)
    else:
        if DWSIM_AVAILABLE and not os.path.isdir(dwsim_path):
            print(f"[flash_engine] DWSIM path not found: {dwsim_path} — using mock.")
        results = _mock_flash(task)

    # Save CSV
    os.makedirs(output_dir, exist_ok=True)
    df       = results_to_dataframe(results)
    csv_path = os.path.join(output_dir, f"flash_{tag}.csv")
    df.to_csv(csv_path, index=False)
    print(f"[flash_engine] CSV saved: {csv_path}")

    print_flash_summary(results)
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Flash Flowsheet (Feed→Cool-1→Flash1→V+L) from JSON spec."
    )
    parser.add_argument(
        "json_file",
        help="Path to task JSON, e.g. DWSIM_ry_test/tasks/examples/flash_h2_ch4_benzene_toluene.json"
    )
    parser.add_argument("--dwsim-path", default=DWSIM_PATH)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    with open(args.json_file) as f:
        task = json.load(f)

    run_flash_task(task, dwsim_path=args.dwsim_path, output_dir=args.output_dir)