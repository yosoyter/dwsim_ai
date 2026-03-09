"""
DWSIM_ry_test/tasks/txy_engine.py
===================================
Group 2 — Txy (Temperature-Composition) phase equilibrium engine.

Pipeline:
  1. Receive validated JSON task spec from Group 1 orchestrator.
  2. Initialize DWSIM headless via Automation3.
  3. Add components and property package (NRTL or PR; auto-detected if omitted).
  4. Call pp.DW_ReturnBinaryEnvelope(options) — the exact same method the GUI
     invokes — to get bubble + dew curves in a single call.
  5. Export tabulated CSV: x1 | T_bubble_K | T_bubble_C | T_dew_K | T_dew_C
  6. Generate Matplotlib Txy plot (bubble curve + dew curve).
  7. Save CSV + PNG + .dwxmz to output/.

Source reference (DWSIM GitHub, windows branch):
  DWSIM.Thermodynamics/Interfaces/ShortcutUtilities.vb  — CalculationType.BinaryEnvelopeTxy
  DWSIM.UI.Desktop.Editors/Utilities/BinaryEnvelope.cs  — GUI wiring

The real API call (from ShortcutUtilities.vb, lines for BinaryEnvelopeTxy):
    BinaryEnvelopeOptions = {"T-x-y", pressure_Pa, temperature_K, vle, lle, sle, critical, False}
    res = pp.DW_ReturnBinaryEnvelope(BinaryEnvelopeOptions)
    # res is Object[12]:
    #   res[0]  = ArrayList  x1 (mole fractions, shared)
    #   res[1]  = ArrayList  T_bubble [K]    <- bubble curve y-values
    #   res[2]  = ArrayList  T_dew    [K]    <- dew curve y-values
    #   res[3]  = ArrayList  px1l1  (LLE liquid 1 x1)
    #   res[4]  = ArrayList  px1l2  (LLE liquid 2 x1)
    #   res[5]  = ArrayList  T_lle  [K]  (LLE temperatures)
    #   res[6]  = ArrayList  pxs1   (SLE solid 1 x1)
    #   res[7]  = ArrayList  pys1   (SLE solid 1 T [K])
    #   res[8]  = ArrayList  pxs2   (SLE solid 2 x1)
    #   res[9]  = ArrayList  pys2   (SLE solid 2 T [K])
    #   res[10] = ArrayList  pxc    (critical line x1)
    #   res[11] = ArrayList  pyc    (critical line T [K])

Note: ShortcutUtilities.vb converts from SI (K) → selected unit system AFTER
calling DW_ReturnBinaryEnvelope. We call it directly without unit conversion,
so results are always in SI (K / Pa).

Input JSON schema  ->  DWSIM_ry_test/tasks/schemas/txy_input_schema.json
Output CSV columns ->  x1 | T_bubble_K | T_bubble_C | T_dew_K | T_dew_C

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
    """
    Return the first PropertyPackage on the flowsheet, cast to the concrete
    DWSIM.Thermodynamics.PropertyPackages.PropertyPackage type.

    sim.PropertyPackages values come back typed as IPropertyPackage (interface).
    The interface does NOT expose .ComponentName or .DW_ReturnBinaryEnvelope —
    those live on the concrete abstract base class PropertyPackage.

    pythonnet cast pattern (confirmed in FormBinEnv.vb):
        For Each pp1 As PropertyPackage In Flowsheet.PropertyPackages.Values

    In Python/pythonnet we replicate this with a Cast[T] call.

    Source: DWSIM.Thermodynamics/PropertyPackages/PropertyPackage.vb:133
        Public MustInherit Class PropertyPackage  (implements IPropertyPackage)
    """
    import clr
    from DWSIM.Thermodynamics.PropertyPackages import PropertyPackage as _PP
    for kvp in sim.PropertyPackages:
        iface = kvp.Value
        # pythonnet 3.x cast: use __cast__ or Cast[T]
        try:
            return clr.Convert(iface, _PP)
        except Exception:
            pass
        # pythonnet 2.x / alternate: direct Python cast via __new__
        try:
            return _PP.__new__(_PP, iface.__implementation__)
        except Exception:
            pass
        # If all casts fail, the runtime may still dispatch correctly
        # (pythonnet sometimes resolves methods dynamically even on interface refs)
        return iface
    raise RuntimeError("No property package found on flowsheet.")


# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM BINARY ENVELOPE CALCULATION
#
#  Source: DWSIM.Thermodynamics/Interfaces/ShortcutUtilities.vb
#          Case CalculationType.BinaryEnvelopeTxy
#
#  The GUI (BinaryEnvelope.cs) builds a MaterialStream, attaches a PP,
#  then calls:
#      calc = Calculation(ms)
#      calc.CalcType = BinaryEnvelopeTxy
#      calc.BinaryEnvelopeOptions = {"", 0, 0, vle, lle, sle, critical, False}
#      results = calc.Calculate()
#
#  Inside Calculate(), for BinaryEnvelopeTxy:
#      BinaryEnvelopeOptions[0] = "T-x-y"
#      BinaryEnvelopeOptions[1] = MixPressure   (Pa)
#      BinaryEnvelopeOptions[2] = MixTemperature (K)
#      res = pp.DW_ReturnBinaryEnvelope(BinaryEnvelopeOptions)
#
#  We replicate this directly — no wrapper class needed.
#
#  res[0] = x1 mole fractions (ArrayList)
#  res[1] = T_bubble [K]      (ArrayList)
#  res[2] = T_dew    [K]      (ArrayList)
#  res[3..11] = LLE, SLE, critical line data (we extract but don't use in CSV)
# ─────────────────────────────────────────────────────────────────────────────

def _run_dwsim_binary_envelope(
    comp1: str,
    comp2: str,
    pressure_Pa: float,
    temperature_K: float,
    package_tag: str,
    dwsim_path: str,
    output_dir: str,
) -> pd.DataFrame:
    """
    Run the Txy Binary Phase Envelope using pp.DW_ReturnBinaryEnvelope().

    This replicates exactly what DWSIM's ShortcutUtilities.vb does for
    CalculationType.BinaryEnvelopeTxy, but called directly from Python
    without going through the Calculation wrapper class.

    Parameters
    ----------
    comp1, comp2   : component names (must match DWSIM database)
    pressure_Pa    : system pressure [Pa]
    temperature_K  : reference temperature [K] (used for LLE/SLE/critical;
                     doesn't affect VLE bubble/dew results)
    package_tag    : 'NRTL', 'PR', 'SRK', 'UNIQUAC', or 'IDEAL'
    dwsim_path     : path to DWSIM installation
    output_dir     : where to save .dwxmz

    Returns
    -------
    df : pd.DataFrame with columns:
         x1 | T_bubble_K | T_bubble_C | T_dew_K | T_dew_C
    """
    import pythoncom
    pythoncom.CoInitialize()

    # ── Step 1: load DLLs (MUST precede any 'from DWSIM.X import Y') ─────────
    interf = init_dwsim(dwsim_path)
    sim    = create_flowsheet(interf)

    add_component(sim, comp1)
    add_component(sim, comp2)
    pp = select_property_package(sim, package_tag)  # returns concrete PropertyPackage subclass

    # ── Step 2: DWSIM namespaces now safe to import ───────────────────────────
    from DWSIM.Thermodynamics.Streams import MaterialStream
    from DWSIM.Thermodynamics.BaseClasses import Compound as DWSIMCompound
    from DWSIM.GlobalSettings import Settings

    Settings.SolverMode = 0

    print(f"[txy_engine] Property package: {pp.ComponentName}")

    # ── Step 3: build the MaterialStream the GUI builds ───────────────────────
    # (ShortcutUtilities.vb uses ms.Phases(0).Compounds + pp.CurrentMaterialStream)
    ms = MaterialStream("txy_stream", "")
    ms.SetFlowsheet(sim)
    ms.PropertyPackage = pp

    comp1_obj = sim.SelectedCompounds[comp1]
    comp2_obj = sim.SelectedCompounds[comp2]

    for phase in ms.Phases.Values:
        c1 = DWSIMCompound(comp1, "")
        c1.ConstantProperties = comp1_obj
        phase.Compounds.Add(comp1, c1)

        c2 = DWSIMCompound(comp2, "")
        c2.ConstantProperties = comp2_obj
        phase.Compounds.Add(comp2, c2)

    ms.Phases[0].Properties.temperature = temperature_K
    ms.Phases[0].Properties.pressure    = pressure_Pa

    # ── Step 4: set pp.CurrentMaterialStream (required by DW_ReturnBinaryEnvelope) ──
    pp.CurrentMaterialStream = ms

    # ── Step 5: build BinaryEnvelopeOptions array exactly as ShortcutUtilities does ─
    # Object[] { mode, pressure, temperature, vle, lle, sle, critical, unused }
    #   mode        = "T-x-y"         (string, set inside Calculation.Calculate())
    #   pressure    = Pa              (float)
    #   temperature = K               (float)
    #   vle         = True            (bool)
    #   lle         = False           (bool)
    #   sle         = False           (bool)
    #   critical    = False           (bool)
    #   unused      = False           (bool)
    from System import Object, String, Double, Boolean
    from System import Array

    options = Array[Object](8)
    options[0] = "T-x-y"
    options[1] = float(pressure_Pa)
    options[2] = float(temperature_K)
    options[3] = True    # VLE
    options[4] = False   # LLE
    options[5] = False   # SLE
    options[6] = False   # Critical line
    options[7] = False   # unused

    # ── Step 6: call the same method the GUI calls ────────────────────────────
    print(f"[txy_engine] Calling DW_ReturnBinaryEnvelope: {comp1}/{comp2} @ {pressure_Pa:.0f} Pa ...")
    # Signature (PropertyPackage.vb:4675):
    #   Function DW_ReturnBinaryEnvelope(parameters As Object,
    #                                    Optional bw As BackgroundWorker = Nothing) As Object
    # FormBinEnv.vb passes BackgroundWorker for progress reporting; we pass None.
    
    res = pp.DW_ReturnBinaryEnvelope(options, None)

    # ── Step 7: extract results ───────────────────────────────────────────────
    # res[0] = ArrayList of x1 mole fractions (shared by bubble and dew curves)
    # res[1] = ArrayList of T_bubble [K]
    # res[2] = ArrayList of T_dew    [K]
    x1_list  = [float(v) for v in res[0]]
    bub_list = [float(v) for v in res[1]]
    dew_list = [float(v) for v in res[2]]

    print(f"[txy_engine] Got {len(x1_list)} envelope points.")

    records = []
    for x1, T_bub, T_dew in zip(x1_list, bub_list, dew_list):
        records.append({
            "x1":         round(x1,    6),
            "T_bubble_K": round(T_bub, 4),
            "T_bubble_C": round(T_bub - 273.15, 4),
            "T_dew_K":    round(T_dew, 4),
            "T_dew_C":    round(T_dew - 273.15, 4),
        })

    df = pd.DataFrame(records).sort_values("x1").reset_index(drop=True)

    # ── Step 8: save flowsheet ────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    dwxmz_path = os.path.join(output_dir, f"txy_{comp1}_{comp2}.dwxmz")
    save_flowsheet(interf, sim, dwxmz_path)

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  MOCK CALCULATION (no DWSIM — Antoine equation + Raoult's Law)
#  Returns bubble AND dew curves to match the full DataFrame schema.
# ─────────────────────────────────────────────────────────────────────────────

def _mock_txy(comp1: str, comp2: str, pressure_Pa: float, n_points: int) -> pd.DataFrame:
    """
    Approximate Txy using Antoine equation + Raoult's Law (ideal liquid).

    FOR PIPELINE TESTING ONLY. Does NOT capture:
      - NRTL activity coefficient corrections
      - Azeotropes (e.g. Ethanol-Water azeotrope at x1 ~ 0.894)
      - Non-ideal vapor phase behavior

    Returns both bubble and dew point columns to match the full schema.
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
            "x1":         np.round(x1_vals, 6),
            "T_bubble_K": np.round(T_vals + 273.15, 4),
            "T_bubble_C": np.round(T_vals, 4),
            "T_dew_K":    np.round(T_vals + 2 + 273.15, 4),
            "T_dew_C":    np.round(T_vals + 2, 4),
        })

    def Psat(A, B, C, T_C):
        return 10 ** (A - B / (T_C + C))

    def T_bp(A, B, C, P):
        return B / (A - math.log10(P)) - C

    A1, B1, C1 = ANTOINE[c1]
    A2, B2, C2 = ANTOINE[c2]

    bubble_records = []
    dew_records    = []
    x1_vals = np.linspace(0.0, 1.0, n_points)

    # Bubble curve: sweep x1, Newton-solve T where x1*Psat1(T) + x2*Psat2(T) = P
    for x1 in x1_vals:
        x1 = float(x1)
        x2 = 1.0 - x1
        T2_C = T_bp(A2, B2, C2, P_mmHg)
        T1_C = T_bp(A1, B1, C1, P_mmHg)
        T_C  = T2_C + (T1_C - T2_C) * x1
        for _ in range(40):
            f    = x1 * Psat(A1, B1, C1, T_C) + x2 * Psat(A2, B2, C2, T_C) - P_mmHg
            dP1  = Psat(A1, B1, C1, T_C) * B1 * math.log(10) / (T_C + C1)**2
            dP2  = Psat(A2, B2, C2, T_C) * B2 * math.log(10) / (T_C + C2)**2
            dfdT = x1 * dP1 + x2 * dP2
            if abs(dfdT) < 1e-14:
                break
            step = f / dfdT
            T_C -= step
            if abs(step) < 1e-9:
                break
        bubble_records.append((x1, T_C + 273.15, T_C))

    # Dew curve: sweep y1 (=x1 axis), Newton-solve T where y1/Psat1(T) + y2/Psat2(T) = 1/P
    for y1 in x1_vals:
        y1 = float(y1)
        y2 = 1.0 - y1
        T2_C = T_bp(A2, B2, C2, P_mmHg)
        T1_C = T_bp(A1, B1, C1, P_mmHg)
        T_C  = T2_C + (T1_C - T2_C) * y1 + 3.0 * y1 * y2
        for _ in range(40):
            Ps1  = Psat(A1, B1, C1, T_C)
            Ps2  = Psat(A2, B2, C2, T_C)
            f    = y1 / Ps1 + y2 / Ps2 - 1.0 / P_mmHg
            dP1  = Ps1 * B1 * math.log(10) / (T_C + C1)**2
            dP2  = Ps2 * B2 * math.log(10) / (T_C + C2)**2
            dfdT = -y1 * dP1 / Ps1**2 - y2 * dP2 / Ps2**2
            if abs(dfdT) < 1e-14:
                break
            step = f / dfdT
            T_C -= step
            if abs(step) < 1e-9:
                break
        dew_records.append((y1, T_C + 273.15, T_C))

    records = []
    for (x1, T_bub_K, T_bub_C), (_, T_dew_K, T_dew_C) in zip(bubble_records, dew_records):
        records.append({
            "x1":         round(x1,      6),
            "T_bubble_K": round(T_bub_K, 4),
            "T_bubble_C": round(T_bub_C, 4),
            "T_dew_K":    round(T_dew_K, 4),
            "T_dew_C":    round(T_dew_C, 4),
        })

    print(f"[txy_engine] MOCK Txy: {comp1}/{comp2} (Raoult's Law — no NRTL, no azeotrope)")
    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
#  PLOT GENERATOR
#  Dark-themed Txy diagram matching DWSIM Binary Phase Envelope GUI style:
#    green  = bubble curve (Bubble Points)
#    orange = dew curve    (Dew Points)
# ─────────────────────────────────────────────────────────────────────────────

def generate_txy_plot(
    df: pd.DataFrame,
    comp1: str,
    comp2: str,
    pressure_Pa: float,
    package_tag: str,
    output_dir: str,
    temp_unit: str = "K",
) -> str:
    """
    Generate and save a Txy phase envelope plot matching the DWSIM GUI style.

    Parameters
    ----------
    df          : DataFrame with x1, T_bubble_K/C, T_dew_K/C columns
    comp1       : component 1 name (x-axis label)
    comp2       : component 2 name (subtitle)
    pressure_Pa : system pressure in Pa (title)
    package_tag : property package tag (subtitle)
    output_dir  : directory to save PNG
    temp_unit   : 'K' or 'C'

    Returns
    -------
    png_path : str
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    bub_col = f"T_bubble_{temp_unit}"
    dew_col = f"T_dew_{temp_unit}"
    ylabel  = f"Temperature ({'°C' if temp_unit == 'C' else 'K'})"

    df_plot = df.dropna(subset=[bub_col, dew_col]).sort_values("x1").reset_index(drop=True)

    BG   = "#1e1e1e"
    GRID = "#3a3a3a"

    fig, ax = plt.subplots(figsize=(8.5, 6.0))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    # Bubble curve — dark green (matches DWSIM DarkGreen)
    ax.plot(
        df_plot["x1"], df_plot[bub_col],
        color="#2e8b57", linewidth=2.5, label="Bubble Points", zorder=3,
    )
    # Dew curve — dark orange (matches DWSIM DarkOrange)
    ax.plot(
        df_plot["x1"], df_plot[dew_col],
        color="#e8a030", linewidth=2.5, label="Dew Points", zorder=3,
    )

    # Shade two-phase region between the curves
    ax.fill_between(
        df_plot["x1"],
        df_plot[bub_col],
        df_plot[dew_col],
        alpha=0.10, color="#ffffff", zorder=1,
    )

    ax.set_xlabel(f"Mole Fraction {comp1}", color="white", fontsize=12, labelpad=8)
    ax.set_ylabel(ylabel, color="white", fontsize=12, labelpad=8)
    ax.tick_params(colors="white", which="both", labelsize=10)
    for spine in ax.spines.values():
        spine.set_edgecolor("#555555")

    ax.set_xlim(0.0, 1.0)
    ax.xaxis.set_major_locator(ticker.MultipleLocator(0.2))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(0.05))
    ax.grid(True, which="major", color=GRID, linestyle="--", linewidth=0.7, zorder=0)
    ax.grid(True, which="minor", color=GRID, linestyle=":",  linewidth=0.4, alpha=0.5, zorder=0)

    title_main = f"Binary Envelope (Txy) @ {pressure_Pa:.0f} Pa"
    title_sub  = f"{{ {comp1}, {comp2} }} / Model: {package_tag}"
    ax.set_title(title_main, color="white", fontsize=13, fontweight="bold", pad=28)
    ax.text(
        0.5, 1.03, title_sub,
        transform=ax.transAxes, ha="center", va="bottom",
        color="#aaaaaa", fontsize=9.5,
    )

    ax.legend(
        facecolor="#2a2a2a", edgecolor="#555555",
        labelcolor="white", fontsize=10,
        loc="upper right", framealpha=0.9,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.97])

    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, f"txy_{comp1}_{comp2}.png")
    fig.savefig(png_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)

    print(f"[txy_engine] Plot saved: {png_path}")
    return png_path


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN PUBLIC FUNCTION  (called by Group 1 orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

def run_txy_task(
    task: dict,
    dwsim_path: str = DWSIM_PATH,
    output_dir: str = None,
    plot: bool = True,
    temp_unit: str = "K",
) -> pd.DataFrame:
    """
    Run a full Txy Binary Phase Envelope task.

    Parameters
    ----------
    task       : validated task dict (see txy_input_schema.json)
    dwsim_path : path to DWSIM installation (Windows)
    output_dir : where to write CSV/PNG/.dwxmz  (default: repo/output/)
    plot       : whether to generate and save a PNG plot
    temp_unit  : 'K' or 'C' for the plot y-axis

    Returns
    -------
    df : pd.DataFrame with columns:
         x1 | T_bubble_K | T_bubble_C | T_dew_K | T_dew_C
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    validate_task(task)

    comp1    = task["component_1"]
    comp2    = task["component_2"]
    pressure = float(task.get("pressure_Pa", 101325.0))
    n_points = int(task.get("n_points", 20))
    pkg_tag  = task.get("property_package") or _auto_select_package(comp1, comp2)
    temp_K   = float(task.get("temperature_K", 298.15))

    print(f"[txy_engine] Task: {comp1}/{comp2} | P={pressure:.0f} Pa | pkg={pkg_tag} | n={n_points}")

    dwsim_ready = DWSIM_AVAILABLE and os.path.isdir(dwsim_path)

    if dwsim_ready:
        df = _run_dwsim_binary_envelope(
            comp1, comp2, pressure, temp_K, pkg_tag, dwsim_path, output_dir
        )
    else:
        if DWSIM_AVAILABLE and not os.path.isdir(dwsim_path):
            print(f"[txy_engine] DWSIM path not found: {dwsim_path} — falling back to mock.")
        df = _mock_txy(comp1, comp2, pressure, n_points)

    # ── save CSV ──────────────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, f"txy_{comp1}_{comp2}.csv")
    df.to_csv(csv_path, index=False)
    print(f"[txy_engine] CSV saved: {csv_path}")

    # ── generate plot ─────────────────────────────────────────────────────────
    if plot:
        generate_txy_plot(df, comp1, comp2, pressure, pkg_tag, output_dir, temp_unit)

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a Txy Binary Phase Envelope from a JSON spec file."
    )
    parser.add_argument(
        "json_file",
        help="Path to task JSON, e.g. DWSIM_ry_test/tasks/examples/ethanol_water.json"
    )
    parser.add_argument("--dwsim-path", default=DWSIM_PATH)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--temp-unit", choices=["K", "C"], default="K",
        help="Temperature unit for the plot y-axis (default: K)"
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Skip PNG generation"
    )
    args = parser.parse_args()

    with open(args.json_file) as f:
        task = json.load(f)

    df = run_txy_task(
        task,
        dwsim_path=args.dwsim_path,
        output_dir=args.output_dir,
        plot=not args.no_plot,
        temp_unit=args.temp_unit,
    )

    print("\n" + "-" * 60)
    print(f"  Binary Phase Envelope: {task['component_1']} / {task['component_2']}")
    print("-" * 60)
    print(df.to_string(index=False))
