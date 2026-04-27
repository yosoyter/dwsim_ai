"""
DWSIM_ry_test/tasks/hx_master.py
=====================================
Master two-stream heat exchanger template — used by LLM #2 code-generation pipeline.
Mirrors heat_master.py in structure.

Input JSON format (from LLM #1):
{
  "task_type":  "hx",
  "calc_mode":  "hot_outlet_T" | "cold_outlet_T" | "duty",
  "components": {
    "hot":  {"Propane": 0.5, "n-Butane": 0.5},
    "cold": {"Propane": 0.5, "n-Butane": 0.5}
  },
  "hot_feed": {
    "flow_kmol_h":   100.0,
    "temperature_C": 120.0,
    "pressure_bar":  14.0
  },
  "cold_feed": {
    "flow_kmol_h":   80.0,
    "temperature_C": 30.0,
    "pressure_bar":  14.0
  },
  "hot_outlet_T_C":         60.0,   ← required if calc_mode == "hot_outlet_T"
  "cold_outlet_T_C":        90.0,   ← required if calc_mode == "cold_outlet_T"
  "duty_kW":                500.0,  ← required if calc_mode == "duty"
  "hot_pressure_drop_bar":  0.0,
  "cold_pressure_drop_bar": 0.0,
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


def _build_hx(task: dict, interf, sim, hot_comp_names: list, cold_comp_names: list):
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.GlobalSettings import Settings
    from DWSIM_ry_test.lib.units.heat_exchanger import build_hx, hx_duty_results

    hot_cfg  = task["hot_feed"]
    cold_cfg = task["cold_feed"]
    calc_mode = task["calc_mode"]

    hot_comp_dict  = {k: float(v) for k, v in task["components"]["hot"].items()}
    cold_comp_dict = {k: float(v) for k, v in task["components"]["cold"].items()}

    # Build feed streams
    hot_feed  = sim.AddObject(ObjectType.MaterialStream,  50, 200, "HOT_FEED" ).GetAsObject()
    cold_feed = sim.AddObject(ObjectType.MaterialStream,  50, 400, "COLD_FEED").GetAsObject()

    def _set_feed(obj, cfg, comp_dict, comp_names):
        flow_mols = float(cfg.get("flow_kmol_h", 100.0)) * 1000 / 3600
        obj.SetTemperature(float(cfg["temperature_C"]) + 273.15)
        obj.SetPressure(float(cfg["pressure_bar"]) * 1e5)
        obj.SetMolarFlow(flow_mols)
        for comp_name in comp_names:
            mf = float(comp_dict.get(comp_name, 0.0))
            obj.SetOverallCompoundMolarFlow(comp_name, mf * flow_mols)

    _set_feed(hot_feed,  hot_cfg,  hot_comp_dict,  hot_comp_names)
    _set_feed(cold_feed, cold_cfg, cold_comp_dict, cold_comp_names)

    # Build HX unit op
    hx_kwargs = dict(
        name                   = "HX1",
        calc_mode              = calc_mode,
        hot_pressure_drop_bar  = float(task.get("hot_pressure_drop_bar",  0.0)),
        cold_pressure_drop_bar = float(task.get("cold_pressure_drop_bar", 0.0)),
        x_pos = 300, y_pos = 300,
    )
    if calc_mode == "hot_outlet_T":
        hx_kwargs["hot_outlet_T_C"]  = float(task["hot_outlet_T_C"])
    elif calc_mode == "cold_outlet_T":
        hx_kwargs["cold_outlet_T_C"] = float(task["cold_outlet_T_C"])
    elif calc_mode == "duty":
        hx_kwargs["duty_kW"]         = float(task["duty_kW"])

    hx = build_hx(sim, **hx_kwargs)

    # Wire
    sim.ConnectObjects(hot_feed.GraphicObject,  hx.obj.GraphicObject,               -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,    hx.hot_outlet_stream.GraphicObject,  -1, -1)
    sim.ConnectObjects(cold_feed.GraphicObject, hx.obj.GraphicObject,               -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,    hx.cold_outlet_stream.GraphicObject, -1, -1)
    sim.AutoLayout()

    # Solve
    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors is not None and len(errors) > 0:
        raise RuntimeError("[hx_master] Solver errors:\n" +
                           "\n".join(str(e) for e in errors))

    print("[hx_master] HX solved.")
    return hot_feed, cold_feed, hx.hot_outlet_stream, hx.cold_outlet_stream, hx.obj


def run_hx_master(task: dict, output_block_fn, output_dir: str = OUTPUT_DIR):
    """
    Full pipeline: init DWSIM → build topology → solve → call output_block_fn.

    Results dict passed to output_block_fn:
    {
        "calc_mode":        "hot_outlet_T" | "cold_outlet_T" | "duty",
        "property_package": str,
        "hot_components":   list[str],
        "cold_components":  list[str],
        "hot_feed":         stream dict,
        "cold_feed":        stream dict,
        "hot_outlet":       stream dict,
        "cold_outlet":      stream dict,
        "duty": {
            "duty_kW":  float,   # heat transferred hot → cold (always positive)
            "duty_kJh": float,
            "LMTD_K":   float,
        }
    }
    """
    import pythoncom
    pythoncom.CoInitialize()

    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet,
    )
    from DWSIM_ry_test.lib.units.heat_exchanger import hx_duty_results

    hot_comp_dict  = {k: float(v) for k, v in task["components"]["hot"].items()}
    cold_comp_dict = {k: float(v) for k, v in task["components"]["cold"].items()}
    hot_comp_list  = list(hot_comp_dict.keys())
    cold_comp_list = list(cold_comp_dict.keys())
    all_comps      = list(dict.fromkeys(hot_comp_list + cold_comp_list))  # preserve order, dedupe

    raw_pkg = task.get("property_package")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg else _auto_select_package(all_comps)

    interf = init_dwsim(DWSIM_PATH)
    sim    = create_flowsheet(interf)

    for comp in all_comps:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    # Split comp_names back into hot/cold for extraction
    hot_comp_names  = [c for c in comp_names if c in hot_comp_dict]
    cold_comp_names = [c for c in comp_names if c in cold_comp_dict]

    select_property_package(sim, pkg_tag)

    hot_feed_obj, cold_feed_obj, hot_out_obj, cold_out_obj, hx_obj = _build_hx(
        task, interf, sim, hot_comp_names, cold_comp_names
    )

    hot_feed_r  = _extract_stream(hot_feed_obj,  "HOT_FEED",  hot_comp_names)
    cold_feed_r = _extract_stream(cold_feed_obj, "COLD_FEED", cold_comp_names)
    hot_out_r   = _extract_stream(hot_out_obj,   "HOT_OUT",   hot_comp_names)
    cold_out_r  = _extract_stream(cold_out_obj,  "COLD_OUT",  cold_comp_names)
    duty_r      = hx_duty_results(hx_obj)

    results = {
        "calc_mode":        task["calc_mode"],
        "property_package": pkg_tag,
        "hot_components":   hot_comp_names,
        "cold_components":  cold_comp_names,
        "hot_feed":         hot_feed_r,
        "cold_feed":        cold_feed_r,
        "hot_outlet":       hot_out_r,
        "cold_outlet":      cold_out_r,
        "duty":             duty_r,
    }

    os.makedirs(output_dir, exist_ok=True)
    save_flowsheet(interf, sim,
                   os.path.join(output_dir, "hx_result.dwxmz"))

    output_block_fn(results)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _default_output_block(results: dict):
    print("\n" + "=" * 60)
    print(f"HX RESULTS ({results['calc_mode'].upper()}, {results['property_package']})")
    print("=" * 60)
    for key, label in (("hot_feed", "Hot  inlet"), ("hot_outlet", "Hot  outlet"),
                       ("cold_feed", "Cold inlet"), ("cold_outlet", "Cold outlet")):
        s = results[key]
        print(f"\n  {label}")
        print(f"    T = {s['T_C']:.2f} C  |  P = {s['P_bar']:.3f} bar  "
              f"|  V_frac = {s['vapor_fraction']:.4f}  "
              f"|  flow = {s['molar_flow_molh']:.2f} mol/h")
    d = results["duty"]
    print(f"\n  Duty transferred : {abs(d['duty_kW']):.2f} kW  "
          f"({abs(d['duty_kJh']):.1f} kJ/h)  |  LMTD = {d['LMTD_K']:.2f} K")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HX master template — run with a task JSON for testing."
    )
    parser.add_argument("task_json", help="Path to task JSON file")
    args = parser.parse_args()

    with open(args.task_json, "r") as f:
        task = json.load(f)

    run_hx_master(task, _default_output_block)