"""
DWSIM_ry_test/tasks/txy_engine.py
===================================
Group 2 — Txy (Temperature-Composition) phase equilibrium engine.

Pipeline:
  1. Receive validated JSON task spec from Group 1 orchestrator.
  2. Initialize DWSIM headless via Automation3.
  3. Add components and property package (NRTL or PR; auto-detected if omitted).
  4. Sweep x1 = 0 -> 1, performing a bubble-point flash at each step.
  5. Extract T [K/C], x1 [liquid], y1 [vapor] per point.
  6. Save CSV + .dwxmz to output/.

Input JSON schema  ->  DWSIM_ry_test/tasks/schemas/txy_input_schema.json
Output CSV columns ->  x1 | y1 | T_K | T_C

CLI usage:
    python DWSIM_ry_test/tasks/txy_engine.py DWSIM_ry_test/tasks/examples/ethanol_water.json
"""

import json
import os
import sys
import argparse

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
#  PATH SETUP
# ─────────────────────────────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_PATH = r"C:\Users\rickyyu\AppData\Local\DWSIM"

POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL",
}

OUTPUT_DIR = os.path.join(_ROOT, "output")

# ─────────────────────────────────────────────────────────────────────────────
#  CHECK DWSIM AVAILABILITY
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
        get_recommended_package,
    )
    DWSIM_AVAILABLE = True
    print("[txy_engine] DWSIM library ready.")
except Exception as _e:
    print(f"[txy_engine] DWSIM not available ({_e}) — running in mock mode.")


# ─────────────────────────────────────────────────────────────────────────────
#  INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_task(task: dict) -> None:
    required = {"task_type", "component_1", "component_2"}
    missing = required - task.keys()
    if missing:
        raise ValueError(f"Task JSON missing required keys: {missing}")
    if task["task_type"] != "txy":
        raise ValueError(f"txy_engine only handles task_type='txy'. Got: '{task['task_type']}'")
    n = task.get("n_points", 20)
    if not isinstance(n, int) or n < 5:
        raise ValueError(f"n_points must be an integer >= 5. Got: {n}")
    p = task.get("pressure_Pa", 101325.0)
    if not isinstance(p, (int, float)) or p <= 0:
        raise ValueError(f"pressure_Pa must be a positive number. Got: {p}")
    pkg = task.get("property_package")
    if pkg is not None and pkg not in ("NRTL", "UNIQUAC", "PR", "SRK", "IDEAL"):
        raise ValueError(f"Unknown property_package: '{pkg}'. Valid: NRTL, UNIQUAC, PR, SRK, IDEAL")
    print("[txy_engine] Task validation passed.")


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO PROPERTY PACKAGE SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def _auto_select_package(comp1: str, comp2: str) -> str:
    if {comp1.upper(), comp2.upper()} & POLAR_COMPONENTS:
        return "NRTL"
    return "PR"


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER: get first PP from sim.PropertyPackages (Dictionary, not list)
# ─────────────────────────────────────────────────────────────────────────────

def _get_first_pp(sim):
    for kvp in sim.PropertyPackages:
        return kvp.Value
    raise RuntimeError("No property package found on flowsheet.")


# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM TXY CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def _run_dwsim_txy(
    comp1: str,
    comp2: str,
    pressure_Pa: float,
    n_points: int,
    package_tag: str,
    dwsim_path: str,
    output_dir: str,
) -> pd.DataFrame:
    """
    Run the Txy sweep using DWSIM headless (Automation3 API).

    Key implementation notes:
      1. init_dwsim() MUST run before any 'from DWSIM.X import Y' statements.
      2. sim.PropertyPackages is a Dictionary — use _get_first_pp().
      3. SetOverallComposition needs a .NET Double[] array (not Python list).
      4. pythonnet 3.0: StreamSpec enum must be cast explicitly via StreamSpec(2).
      5. No SetVaporFraction() method exists — set ms.MolarVaporFraction directly.
      6. y1 from Phases[2] (vapor); T from GetTemperature().
    """
    import pythoncom
    pythoncom.CoInitialize()

    # ── Step 1: load DLLs first ──────────────────────────────────────────────
    interf = init_dwsim(dwsim_path)
    sim    = create_flowsheet(interf)

    add_component(sim, comp1)
    add_component(sim, comp2)
    select_property_package(sim, package_tag)

    # ── Step 2: safe to import DWSIM namespaces now ──────────────────────────
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.Interfaces.Enums import StreamSpec
    from DWSIM.GlobalSettings import Settings
    from System import Array, Double

    Settings.SolverMode = 0

    pp = _get_first_pp(sim)
    print(f"[txy_engine] Using property package: {pp}")

    # pythonnet 3.0: must explicitly cast int to enum type
    SPEC_P_VF = StreamSpec(2)   # Pressure_and_VaporFraction = 2

    x1_vals = np.linspace(0.0, 1.0, n_points)
    records = []

    for i, x1 in enumerate(x1_vals):
        x2 = 1.0 - x1
        try:
            ms_obj = sim.AddObject(ObjectType.MaterialStream, 0, i * 10, f"flash_{i:03d}")
            ms = ms_obj.GetAsObject()

            ms.SetPressure(pressure_Pa)

            # SetOverallComposition requires .NET Double[] — not Python list
            composition = Array[Double]([float(x1), float(x2)])
            ms.SetOverallComposition(composition)

            # Bubble point spec: P fixed, Q = 0
            ms.SpecType = SPEC_P_VF
            ms.MolarVaporFraction = 0.0   # Q = 0 -> bubble point (no SetVaporFraction method)

            ms.PropertyPackage = pp
            ms.Calculate(True, True)

            T_K = float(ms.GetTemperature())

            try:
                y1 = float(ms.Phases[2].Compounds[comp1].MoleFraction)
            except Exception:
                y1 = float(list(ms.Phases[2].Compounds.Values)[0].MoleFraction)

            records.append({
                "x1":  round(float(x1), 6),
                "y1":  round(float(y1), 6),
                "T_K": round(T_K, 4),
                "T_C": round(T_K - 273.15, 4),
            })

            print(f"[txy_engine] x1={x1:.3f} -> T={T_K - 273.15:.2f}C  y1={y1:.4f}")

        except Exception as e:
            print(f"[txy_engine] Flash failed at x1={x1:.4f}: {e}")
            records.append({
                "x1":  round(float(x1), 6),
                "y1":  None,
                "T_K": None,
                "T_C": None,
            })

    df = pd.DataFrame(records)

    os.makedirs(output_dir, exist_ok=True)
    dwxmz_path = os.path.join(output_dir, f"txy_{comp1}_{comp2}.dwxmz")
    save_flowsheet(interf, sim, dwxmz_path)

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  MOCK CALCULATION (no DWSIM — Antoine + Raoult's Law)
# ─────────────────────────────────────────────────────────────────────────────

def _mock_txy(comp1: str, comp2: str, pressure_Pa: float, n_points: int) -> pd.DataFrame:
    """
    Approximate Txy using Antoine equation + Raoult's Law (ideal liquid).

    FOR PIPELINE TESTING ONLY. Does NOT capture:
      - NRTL activity coefficient corrections
      - Azeotropes (e.g. Ethanol-Water azeotrope at x1 ~ 0.894)
      - Non-ideal vapor phase behavior
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

    P_mmHg = pressure_Pa / 133.322
    c1 = comp1.upper()
    c2 = comp2.upper()

    if c1 not in ANTOINE or c2 not in ANTOINE:
        print(f"[txy_engine] Antoine data missing for {comp1}/{comp2} — using linear fallback.")
        x1_vals = np.linspace(0.0, 1.0, n_points)
        T_vals  = np.linspace(110.0, 60.0, n_points)
        return pd.DataFrame({
            "x1":  np.round(x1_vals, 6),
            "y1":  np.round(x1_vals, 6),
            "T_K": np.round(T_vals + 273.15, 4),
            "T_C": np.round(T_vals, 4),
        })

    def T_bp(A, B, C, P):
        return B / (A - math.log10(P)) - C

    def Psat(A, B, C, T_C):
        return 10 ** (A - B / (T_C + C))

    A1, B1, C1 = ANTOINE[c1]
    A2, B2, C2 = ANTOINE[c2]
    T1_C = T_bp(A1, B1, C1, P_mmHg)
    T2_C = T_bp(A2, B2, C2, P_mmHg)

    records = []
    for x1 in np.linspace(0.0, 1.0, n_points):
        T_C = T2_C + (T1_C - T2_C) * x1
        T_K = T_C + 273.15
        y1  = min(max(float(x1) * Psat(A1, B1, C1, T_C) / P_mmHg, 0.0), 1.0)
        records.append({
            "x1":  round(float(x1), 6),
            "y1":  round(float(y1), 6),
            "T_K": round(T_K, 4),
            "T_C": round(T_C, 4),
        })

    print(f"[txy_engine] MOCK Txy: {comp1}/{comp2} (Raoult's Law — no NRTL, no azeotrope)")
    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN PUBLIC FUNCTION  (called by Group 1 orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

def run_txy_task(
    task: dict,
    dwsim_path: str = DWSIM_PATH,
    output_dir: str = None,
) -> pd.DataFrame:
    if output_dir is None:
        output_dir = OUTPUT_DIR

    validate_task(task)

    comp1    = task["component_1"]
    comp2    = task["component_2"]
    pressure = float(task.get("pressure_Pa", 101325.0))
    n_points = int(task.get("n_points", 20))
    pkg_tag  = task.get("property_package") or _auto_select_package(comp1, comp2)

    print(f"[txy_engine] Task: {comp1}/{comp2} | P={pressure:.0f} Pa | pkg={pkg_tag} | n={n_points}")

    dwsim_ready = DWSIM_AVAILABLE and os.path.isdir(dwsim_path)

    if dwsim_ready:
        df = _run_dwsim_txy(comp1, comp2, pressure, n_points, pkg_tag, dwsim_path, output_dir)
    else:
        if DWSIM_AVAILABLE and not os.path.isdir(dwsim_path):
            print(f"[txy_engine] DWSIM path not found: {dwsim_path} — falling back to mock.")
        df = _mock_txy(comp1, comp2, pressure, n_points)

    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, f"txy_{comp1}_{comp2}.csv")
    df.to_csv(csv_path, index=False)
    print(f"[txy_engine] CSV saved: {csv_path}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a Txy DWSIM task from a JSON spec file."
    )
    parser.add_argument(
        "json_file",
        help="Path to task JSON (e.g. DWSIM_ry_test/tasks/examples/ethanol_water.json)"
    )
    parser.add_argument("--dwsim-path", default=DWSIM_PATH)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    with open(args.json_file) as f:
        task = json.load(f)

    df = run_txy_task(task, dwsim_path=args.dwsim_path, output_dir=args.output_dir)

    print("\n" + "-" * 50)
    print(f"  Txy Results: {task['component_1']} / {task['component_2']}")
    print("-" * 50)
    print(df.to_string(index=False))