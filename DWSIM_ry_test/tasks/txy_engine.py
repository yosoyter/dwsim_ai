"""
DWSIM/tasks/txy_engine.py
==========================
Group 2 — Back-end Txy (Temperature-Composition) engine.

Pipeline:
  1.  Receive validated JSON task spec from Group 1 orchestrator.
  2.  Initialize DWSIM headless.
  3.  Select property package (NRTL or PR; auto-detected if not specified).
  4.  Sweep x1 = 0 → 1 with bubble-point (PQ) flashes at fixed pressure.
  5.  Extract T [K/°C], x1 [liquid fraction], y1 [vapor fraction] per point.
  6.  Return pandas DataFrame; save CSV + .dwxmz to output/.

Input JSON schema  →  DWSIM/tasks/schemas/txy_input_schema.json
Output CSV columns →  x1 | y1 | T_K | T_C

CLI usage:
    python DWSIM/tasks/txy_engine.py DWSIM/tasks/examples/ethanol_water.json
"""

import json
import os
import sys
import argparse
import traceback

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
#  IMPORT DWSIM LIBRARY (falls back to mock/dry-run if not installed)
# ─────────────────────────────────────────────────────────────────────────────

# Add project root to path so imports work from any working directory
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_AVAILABLE = False
try:
    import clr as _clr_check  # noqa — confirms pythonnet is installed
    from DWSIM.lib.dwsim_core import (
        init_dwsim,
        create_flowsheet,
        select_property_package,
        add_component,
        save_flowsheet,
        get_recommended_package,
    )
    DWSIM_AVAILABLE = True
    print("[txy_engine] DWSIM library loaded. Full simulation mode active.")
except Exception:
    print("[txy_engine] WARNING: DWSIM/pythonnet not found — running in MOCK mode.")
    print("[txy_engine] On workstation: activate conda env and pip install pythonnet==2.5.2")

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL",
}

OUTPUT_DIR = os.path.join(_ROOT, "output")


# ─────────────────────────────────────────────────────────────────────────────
#  INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_task(task: dict) -> None:
    """
    Validate the incoming JSON task dict.
    Raises ValueError with a descriptive message on any failure.
    """
    required = {"task_type", "component_1", "component_2"}
    missing = required - task.keys()
    if missing:
        raise ValueError(f"Task JSON is missing required keys: {missing}")

    if task["task_type"] != "txy":
        raise ValueError(
            f"txy_engine only handles task_type='txy'. Got: '{task['task_type']}'"
        )

    n = task.get("n_points", 20)
    if not isinstance(n, int) or n < 5:
        raise ValueError(f"n_points must be an integer >= 5. Got: {n}")

    p = task.get("pressure_Pa", 101325.0)
    if not isinstance(p, (int, float)) or p <= 0:
        raise ValueError(f"pressure_Pa must be a positive number. Got: {p}")

    pkg = task.get("property_package")
    if pkg is not None and pkg not in ("NRTL", "UNIQUAC", "PR", "SRK", "IDEAL"):
        raise ValueError(
            f"Unknown property_package: '{pkg}'. "
            "Valid: NRTL, UNIQUAC, PR, SRK, IDEAL"
        )

    print("[txy_engine] Task validation passed.")


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO PROPERTY PACKAGE SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def _auto_select_package(comp1: str, comp2: str) -> str:
    """Select NRTL for any polar component pair, PR otherwise."""
    if {comp1.upper(), comp2.upper()} & POLAR_COMPONENTS:
        return "NRTL"
    return "PR"


# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM BUBBLE-POINT FLASH SWEEP
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
    Execute the full DWSIM Txy sweep (requires DWSIM on workstation).

    Uses PQ flash (Q=0 → bubble point) at each x1 composition step.
    Extracts T_bubble and y1 vapor composition at each point.
    """
    interf = init_dwsim(dwsim_path)
    flowsheet = create_flowsheet(interf)
    add_component(flowsheet, comp1)
    add_component(flowsheet, comp2)
    pp = select_property_package(flowsheet, package_tag)

    import DWSIM.Thermodynamics.PropertyPackages as dwpp  # noqa

    x1_vals = np.linspace(0.0, 1.0, n_points)
    records = []

    for x1 in x1_vals:
        x2 = 1.0 - x1
        z = [float(x1), float(x2)]   # overall composition = liquid at bubble pt

        try:
            # PQ flash: P fixed, vapor quality Q = 0 (bubble point)
            result = pp.CalculateEquilibrium(
                dwpp.FlashSpec.P,
                dwpp.FlashSpec.VAP,
                pressure_Pa,
                0.0,   # Q = 0 → bubble point
                z,
                0.0,
            )
            T_K = float(result.CalculatedTemperature)
            # Vapor phase composition of component 1
            y1 = float(result.VaporPhaseMoleComposition[0]) \
                if result.VaporPhaseMoleComposition is not None else float(x1)

            records.append({
                "x1":  round(x1, 6),
                "y1":  round(y1, 6),
                "T_K": round(T_K, 4),
                "T_C": round(T_K - 273.15, 4),
            })
        except Exception as e:
            print(f"[txy_engine] Flash failed at x1={x1:.4f}: {e}")
            records.append({"x1": round(x1, 6), "y1": None, "T_K": None, "T_C": None})

    df = pd.DataFrame(records)

    # Save .dwxmz artifact
    os.makedirs(output_dir, exist_ok=True)
    dwxmz_name = f"txy_{comp1}_{comp2}.dwxmz"
    save_flowsheet(flowsheet, os.path.join(output_dir, dwxmz_name))

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  MOCK CALCULATION (no DWSIM — Antoine equation + Raoult's Law)
# ─────────────────────────────────────────────────────────────────────────────

def _mock_txy(comp1: str, comp2: str, pressure_Pa: float, n_points: int) -> pd.DataFrame:
    """
    Approximate Txy using Antoine equation + Raoult's Law (ideal liquid).

    This is for PIPELINE TESTING ONLY.
    It does NOT capture:
      - NRTL activity coefficient corrections
      - Azeotropes (e.g. Ethanol-Water azeotrope at x1 ≈ 0.894)
      - Non-ideal vapor phase behavior

    Antoine coefficients (log10, P in mmHg, T in °C) for common pairs:
      Ethanol:  A=8.04494, B=1554.30,  C=222.65
      Water:    A=8.07131, B=1730.63,  C=233.426
      Benzene:  A=6.89272, B=1203.531, C=219.888
      Toluene:  A=6.95805, B=1346.773, C=219.693
      Methanol: A=7.87863, B=1473.11,  C=230.00
      Acetone:  A=7.02447, B=1161.0,   C=224.0
    """
    ANTOINE = {
        "ETHANOL":  (8.04494, 1554.30,  222.65),
        "WATER":    (8.07131, 1730.63,  233.426),
        "BENZENE":  (6.89272, 1203.531, 219.888),
        "TOLUENE":  (6.95805, 1346.773, 219.693),
        "METHANOL": (7.87863, 1473.11,  230.00),
        "ACETONE":  (7.02447, 1161.0,   224.0),
    }

    import math
    P_mmHg = pressure_Pa / 133.322

    def T_bp(A, B, C, P):
        return B / (A - math.log10(P)) - C   # bubble-point T [°C] from Antoine

    def Psat(A, B, C, T_C):
        return 10 ** (A - B / (T_C + C))     # saturation pressure [mmHg]

    c1_key = comp1.upper()
    c2_key = comp2.upper()

    if c1_key not in ANTOINE or c2_key not in ANTOINE:
        # Fallback: linear T sweep between two guessed boiling points
        print(f"[txy_engine] Antoine data unavailable for {comp1}/{comp2}. Using linear fallback.")
        T_range = np.linspace(60.0, 110.0, n_points)
        x1_vals = np.linspace(0.0, 1.0, n_points)
        records = [
            {"x1": round(x, 6), "y1": round(x, 6), "T_K": round(T + 273.15, 4), "T_C": round(T, 4)}
            for x, T in zip(x1_vals, T_range[::-1])
        ]
        return pd.DataFrame(records)

    A1, B1, C1 = ANTOINE[c1_key]
    A2, B2, C2 = ANTOINE[c2_key]
    T1_C = T_bp(A1, B1, C1, P_mmHg)   # pure comp1 bp
    T2_C = T_bp(A2, B2, C2, P_mmHg)   # pure comp2 bp

    x1_vals = np.linspace(0.0, 1.0, n_points)
    records = []

    for x1 in x1_vals:
        x2 = 1.0 - x1
        # Bubble T: linear mix of pure boiling points (ideal approx)
        T_C = T2_C + (T1_C - T2_C) * x1
        T_K = T_C + 273.15
        # Raoult's Law: y1 = x1 * P1sat / P
        P1sat = Psat(A1, B1, C1, T_C)
        y1 = x1 * P1sat / P_mmHg if P_mmHg > 0 else x1
        y1 = float(min(max(y1, 0.0), 1.0))

        records.append({
            "x1":  round(float(x1), 6),
            "y1":  round(y1, 6),
            "T_K": round(T_K, 4),
            "T_C": round(T_C, 4),
        })

    print(f"[txy_engine] MOCK Txy: {comp1}/{comp2} @ {pressure_Pa:.0f} Pa")
    print("[txy_engine] NOTE: Raoult's Law only — no NRTL correction, no azeotrope.")
    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN PUBLIC FUNCTION  (called by Group 1 orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

def run_txy_task(
    task: dict,
    dwsim_path: str = r"C:\Users\Public\DWSIM",
    output_dir: str = None,
) -> pd.DataFrame:
    """
    Execute a Txy phase-equilibrium task from a JSON spec dict.

    Parameters
    ----------
    task       : dict   validated task JSON (see schemas/txy_input_schema.json)
    dwsim_path : str    path to DWSIM installation (Windows workstation)
    output_dir : str    directory for CSV + .dwxmz output (default: output/)

    Returns
    -------
    df : pd.DataFrame   columns: x1, y1, T_K, T_C
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    # Validate
    validate_task(task)

    comp1     = task["component_1"]
    comp2     = task["component_2"]
    pressure  = float(task.get("pressure_Pa", 101325.0))
    n_points  = int(task.get("n_points", 20))
    pkg_tag   = task.get("property_package") or _auto_select_package(comp1, comp2)

    print(f"[txy_engine] Task: {comp1} / {comp2} | P={pressure:.0f} Pa | pkg={pkg_tag} | n={n_points}")

    # Run DWSIM or mock
    if DWSIM_AVAILABLE:
        df = _run_dwsim_txy(comp1, comp2, pressure, n_points, pkg_tag, dwsim_path, output_dir)
    else:
        df = _mock_txy(comp1, comp2, pressure, n_points)

    # Save CSV
    os.makedirs(output_dir, exist_ok=True)
    csv_name = f"txy_{comp1}_{comp2}.csv"
    csv_path = os.path.join(output_dir, csv_name)
    df.to_csv(csv_path, index=False)
    print(f"[txy_engine] CSV saved: {csv_path}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  CLI  — run directly with a JSON file
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a Txy DWSIM task from a JSON spec file."
    )
    parser.add_argument("json_file", help="Path to task JSON (e.g. DWSIM/tasks/examples/ethanol_water.json)")
    parser.add_argument("--dwsim-path", default=r"C:\Users\Public\DWSIM", help="Path to DWSIM install")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: output/)")
    args = parser.parse_args()

    with open(args.json_file) as f:
        task = json.load(f)

    df = run_txy_task(task, dwsim_path=args.dwsim_path, output_dir=args.output_dir)

    print("\n" + "─" * 50)
    print(f"  Txy Results: {task['component_1']} / {task['component_2']}")
    print("─" * 50)
    print(df.to_string(index=False))
