"""
simquery/runners/comp_runner.py
=====================================
Master compressor/expander template — used by LLM #2 code-generation pipeline.
Mirrors heat_master.py in structure.

Supported modes:
  - "compressor" : FEED → Compressor → OUT  (raise P)
  - "expander"   : FEED → Expander   → OUT  (lower P, recover work)

Input JSON format (from LLM #1):
{
  "task_type":  "comp",
  "comp_mode":  "compressor" | "expander",
  "components": {"Propane": 0.5, "n-Butane": 0.5},
  "feed": {
    "flow_kmol_h":   100.0,
    "temperature_C": 80.0,
    "pressure_bar":  5.0
  },
  "P_out_bar":        20.0,
  "efficiency":       0.75,
  "property_package": "PR"
}
"""

import json
import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_PATH = r"C:\Users\terrbear\AppData\Local\DWSIM"
OUTPUT_DIR  = os.path.join(_ROOT, "output")

PACKAGE_ALIASES = {
    "PENG-ROBINSON": "PR", "PENGROBINSON": "PR", "PENG ROBINSON": "PR",
    "PR": "PR", "NRTL": "NRTL", "SRK": "SRK",
    "SOAVE-REDLICH-KWONG": "SRK", "UNIQUAC": "UNIQUAC",
    "IDEAL": "IDEAL", "RAOULT": "IDEAL",
}

POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL",
}


def _normalize_package(pkg_str: str) -> str:
    key = pkg_str.upper().replace("-", "").replace(" ", "")
    for alias_key, val in PACKAGE_ALIASES.items():
        if alias_key.replace("-", "").replace(" ", "") == key:
            return val
    return pkg_str


def _auto_select_package(comp_list: list) -> str:
    if any(c.upper() in POLAR_COMPONENTS for c in comp_list):
        return "NRTL"
    return "PR"


def _extract_stream(stream_obj, stream_name: str, comp_names: list) -> dict:
    def _get(val):
        if val is None:
            return 0.0
        try:
            return float(val)
        except TypeError:
            try:
                return float(val.GetValueOrDefault())
            except Exception:
                return 0.0

    p0 = stream_obj.Phases[0].Properties
    p2 = stream_obj.Phases[2].Properties

    result = {
        "stream":           stream_name,
        "T_K":              round(_get(p0.temperature), 4),
        "T_C":              round(_get(p0.temperature) - 273.15, 4),
        "P_Pa":             round(_get(p0.pressure), 2),
        "P_bar":            round(_get(p0.pressure) / 1e5, 4),
        "molar_flow_molh":  round(_get(p0.molarflow) * 3600, 4),
        "mass_flow_kgh":    round(_get(p0.massflow) * 3600, 4),
        "enthalpy_kJkmol":  round(_get(p0.enthalpy), 4),
        "vapor_fraction":   round(_get(p2.molarfraction), 6),
    }
    for comp_name in comp_names:
        try:
            xval = _get(stream_obj.Phases[0].Compounds[comp_name].MoleFraction)
        except Exception:
            xval = 0.0
        result[f"x_{comp_name}"] = round(xval, 6)
    return result


def _build_comp(task: dict, interf, sim, comp_names: list):
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.GlobalSettings import Settings
    from simquery.core.units.compressor_expander import (
        build_compressor, build_expander,
    )

    feed_cfg  = task["feed"]
    comp_mode = task["comp_mode"]
    P_out_bar = float(task["P_out_bar"])
    efficiency = float(task.get("efficiency", 0.75))

    feed_temp_K = float(feed_cfg["temperature_C"]) + 273.15
    feed_press  = float(feed_cfg["pressure_bar"]) * 1e5
    feed_flow   = float(feed_cfg.get("flow_kmol_h", 100.0)) * 1000 / 3600
    comp_dict   = {k: float(v) for k, v in task["components"].items()}

    feed_obj = sim.AddObject(ObjectType.MaterialStream, 50, 300, "FEED").GetAsObject()
    feed_obj.SetTemperature(feed_temp_K)
    feed_obj.SetPressure(feed_press)
    feed_obj.SetMolarFlow(feed_flow)
    for comp_name in comp_names:
        mf = float(comp_dict.get(comp_name, 0.0))
        feed_obj.SetOverallCompoundMolarFlow(comp_name, mf * feed_flow)

    if comp_mode == "compressor":
        unit = build_compressor(sim, name="C1", P_out_bar=P_out_bar,
                                efficiency=efficiency, x_pos=300, y_pos=300)
        sim.ConnectObjects(feed_obj.GraphicObject,              unit.obj.GraphicObject,           -1, -1)
        sim.ConnectObjects(unit.obj.GraphicObject,              unit.outlet_stream.GraphicObject, -1, -1)
        sim.ConnectObjects(unit.energy_stream.GraphicObject,    unit.obj.GraphicObject,           -1, -1)
    else:
        unit = build_expander(sim, name="E1", P_out_bar=P_out_bar,
                              efficiency=efficiency, x_pos=300, y_pos=300)
        sim.ConnectObjects(feed_obj.GraphicObject,   unit.obj.GraphicObject,           -1, -1)
        sim.ConnectObjects(unit.obj.GraphicObject,   unit.outlet_stream.GraphicObject, -1, -1)
        sim.ConnectObjects(unit.obj.GraphicObject,   unit.energy_stream.GraphicObject, -1, -1)

    sim.AutoLayout()

    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors is not None and len(errors) > 0:
        raise RuntimeError("[comp_master] Solver errors:\n" +
                           "\n".join(str(e) for e in errors))

    print(f"[comp_master] {comp_mode.capitalize()} solved.")
    feed_result = _extract_stream(feed_obj, "FEED", comp_names)
    return feed_result, feed_obj, unit.outlet_stream, unit.obj


def run_comp_master(task: dict, output_block_fn, output_dir: str = OUTPUT_DIR):
    """
    Full pipeline: init DWSIM → build topology → solve → call output_block_fn.

    Results dict passed to output_block_fn:
    {
        "comp_mode":        "compressor" | "expander",
        "property_package": str,
        "components":       list[str],
        "feed":             stream dict,
        "outlet":           stream dict,
        "shaft_work": {
            "shaft_work_kW":  float,   # positive = work in (compressor), negative = work out (expander)
            "shaft_work_kJh": float,
        }
    }
    """
    import pythoncom
    pythoncom.CoInitialize()

    from simquery.core.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet,
    )

    comp_dict = {k: float(v) for k, v in task["components"].items()}
    comp_list = list(comp_dict.keys())
    comp_mode = task["comp_mode"]

    raw_pkg = task.get("property_package")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg else _auto_select_package(comp_list)

    interf = init_dwsim(DWSIM_PATH)
    sim    = create_flowsheet(interf)

    for comp in comp_list:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    select_property_package(sim, pkg_tag)

    feed_result, feed_obj, outlet_obj, unit_obj = _build_comp(
        task, interf, sim, comp_names
    )

    outlet_result = _extract_stream(outlet_obj, "OUT", comp_names)

    try:
        work_kW = float(unit_obj.DeltaQ)
    except Exception:
        work_kW = 0.0

    results = {
        "comp_mode":        comp_mode,
        "property_package": pkg_tag,
        "components":       comp_names,
        "feed":             feed_result,
        "outlet":           outlet_result,
        "shaft_work": {
            "shaft_work_kW":         round(work_kW, 2),
            "shaft_work_kJh":        round(work_kW * 3600, 2),
            "isentropic_efficiency": float(task.get("efficiency", 0.75)),
        },
    }

    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    save_flowsheet(interf, sim,
                   os.path.join(output_dir, f"{comp_mode}_{tag}.dwxmz"))

    output_block_fn(results)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _default_output_block(results: dict):
    print("\n" + "=" * 60)
    print(f"COMP RESULTS ({results['comp_mode'].upper()}, {results['property_package']})")
    print("=" * 60)
    for key in ("feed", "outlet"):
        s = results[key]
        print(f"\n  {s['stream']}")
        print(f"    T = {s['T_C']:.2f} C  |  P = {s['P_bar']:.3f} bar  "
              f"|  V_frac = {s['vapor_fraction']:.4f}  "
              f"|  flow = {s['molar_flow_molh']:.2f} mol/h  "
              f"|  H = {s['enthalpy_kJkmol']:.1f} kJ/kmol")
        for k, v in s.items():
            if k.startswith("x_"):
                print(f"      {k} = {v:.6f}")
    w = results["shaft_work"]
    sign = "IN" if w["shaft_work_kW"] >= 0 else "OUT"
    print(f"\n  Shaft work ({sign}) : {abs(w['shaft_work_kW']):.2f} kW  "
          f"({abs(w['shaft_work_kJh']):.1f} kJ/h)")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Comp master template — run with a task JSON for testing."
    )
    parser.add_argument("task_json", help="Path to task JSON file")
    args = parser.parse_args()

    with open(args.task_json, "r") as f:
        task = json.load(f)

    run_comp_master(task, _default_output_block)
