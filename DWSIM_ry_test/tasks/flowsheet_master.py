"""
DWSIM_ry_test/tasks/flowsheet_master.py
========================================
Multi-unit flowsheet scaffold — used by the LLM #2 code-generation pipeline
for connected flowsheets (e.g. Heater → Flash, Cooler → Flash, etc.).

Architecture
------------
This file owns the FIXED parts:
  - DWSIM init
  - Component loading
  - Property package selection
  - Solve call
  - Flowsheet save

LLM #2 fills in TWO injected blocks:

  1. ASSEMBLY BLOCK  — builds the topology:
       adds streams, wires unit ops, sets feed conditions
       calls: add_feed(), build_heater(), build_cooler(), connect(), solve_flowsheet()
       returns: a plain dict called `streams` mapping label → stream object

  2. OUTPUT BLOCK    — extracts and presents results:
       reads from `results` dict (built by flowsheet_master from `streams`)
       prints / saves whatever the user asked for

Available functions passed into LLM #2's assembly block namespace
-----------------------------------------------------------------
  add_feed(sim, name, T_C, P_bar, flow_kmol_h, comp_fracs)
      → MaterialStream object (configured, not yet wired)

  add_stream(sim, name, x, y)
      → blank MaterialStream object

  build_heater(sim, name, T_out_C, pressure_drop_bar, x_pos, y_pos)
      → SimpleNamespace(.obj, .outlet_stream, .energy_stream)

  build_cooler(sim, name, T_out_C, pressure_drop_bar, x_pos, y_pos)
      → SimpleNamespace(.obj, .outlet_stream, .energy_stream)

  connect(sim, from_obj, to_obj)
      → calls sim.ConnectObjects(from_obj.GraphicObject, to_obj.GraphicObject, -1, -1)

  add_flash(sim, name, T_C, P_bar, x_pos, y_pos)
      → SimpleNamespace(.obj, .vapor_stream, .liquid_stream, .energy_stream)

  extract(stream_obj, label)
      → stream results dict (T, P, flow, H, compositions)

  calc_duty(feed_result, outlet_result)
      → {"duty_kW": float, "duty_kJh": float}

Input JSON format (from LLM #1):
{
  "task_type":   "flowsheet",
  "steps": [
    {"unit": "heater", "name": "H1", "T_out_C": 120.0, "pressure_drop_bar": 0.0},
    {"unit": "flash",  "name": "F1", "flash_mode": "isothermal", "T_C": 120.0, "P_bar": 6.0}
  ],
  "feed": {
    "flow_kmol_h":   100.0,
    "temperature_C": 60.0,
    "pressure_bar":  14.0
  },
  "components":       {"Propane": 0.5, "n-Butane": 0.5},
  "property_package": "PR",
  "user_request":     "<original user question>"
}
"""

import os
import sys
import json
import textwrap
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


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER FUNCTIONS — injected into LLM #2's assembly block namespace
# ─────────────────────────────────────────────────────────────────────────────

def _make_add_feed(sim, comp_names):
    """Factory: returns add_feed() bound to this sim and comp_names."""
    def add_feed(name: str, T_C: float, P_bar: float,
                 flow_kmol_h: float, comp_fracs: dict):
        """
        Add and configure a feed MaterialStream.

        Parameters
        ----------
        name         : stream label on flowsheet
        T_C          : temperature [°C]
        P_bar        : pressure [bar]
        flow_kmol_h  : molar flow [kmol/h]
        comp_fracs   : {component_name: mole_fraction}  — must sum to 1.0

        Returns
        -------
        DWSIM MaterialStream object (configured, not yet wired)
        """
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        obj = sim.AddObject(ObjectType.MaterialStream, 50, 300, name).GetAsObject()
        flow_mols = flow_kmol_h * 1000 / 3600
        obj.SetTemperature(T_C + 273.15)
        obj.SetPressure(P_bar * 1e5)
        obj.SetMolarFlow(flow_mols)
        for comp, frac in comp_fracs.items():
            obj.SetOverallCompoundMolarFlow(comp, frac * flow_mols)
        return obj
    return add_feed


def _make_add_stream(sim):
    """Factory: returns add_stream() bound to this sim."""
    def add_stream(name: str, x: int = 400, y: int = 300):
        """
        Add a blank MaterialStream (intermediate or outlet).

        Parameters
        ----------
        name  : stream label
        x, y  : canvas position

        Returns
        -------
        DWSIM MaterialStream object
        """
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        return sim.AddObject(ObjectType.MaterialStream, x, y, name).GetAsObject()
    return add_stream


def _make_connect(sim):
    """Factory: returns connect() bound to this sim."""
    def connect(from_obj, to_obj):
        """
        Wire two DWSIM objects together.

        Parameters
        ----------
        from_obj  : DWSIM object (stream or unit op) — SOURCE
        to_obj    : DWSIM object (stream or unit op) — DESTINATION

        Usage examples:
            connect(feed, heater.obj)            # stream → unit op
            connect(heater.obj, heater.outlet_stream)   # unit op → stream
            connect(heater.energy_stream, heater.obj)   # energy → unit op
        """
        sim.ConnectObjects(from_obj.GraphicObject, to_obj.GraphicObject, -1, -1)
    return connect


def _make_build_heater(sim):
    """Factory: returns build_heater() bound to this sim."""
    def build_heater(name: str, T_out_C: float,
                     pressure_drop_bar: float = 0.0,
                     x_pos: int = 300, y_pos: int = 300):
        """
        Add a Heater unit op.

        Parameters
        ----------
        name              : label on flowsheet
        T_out_C           : outlet temperature [°C]
        pressure_drop_bar : pressure drop [bar]  (default 0)
        x_pos, y_pos      : canvas position

        Returns
        -------
        SimpleNamespace with:
          .obj            — Heater DWSIM object  (wire: feed → .obj → .outlet_stream)
          .outlet_stream  — outlet MaterialStream
          .energy_stream  — EnergyStream (wire: .energy_stream → .obj)

        Wiring pattern:
            connect(feed, heater.obj)
            connect(heater.obj, heater.outlet_stream)
            connect(heater.energy_stream, heater.obj)
        """
        from DWSIM_ry_test.lib.units.heat_exchanger import build_heater as _bh
        return _bh(sim, name=name, T_out_C=T_out_C,
                   pressure_drop_bar=pressure_drop_bar,
                   x_pos=x_pos, y_pos=y_pos)
    return build_heater


def _make_build_cooler(sim):
    """Factory: returns build_cooler() bound to this sim."""
    def build_cooler(name: str, T_out_C: float,
                     pressure_drop_bar: float = 0.0,
                     x_pos: int = 300, y_pos: int = 300):
        """
        Add a Cooler unit op.

        Parameters
        ----------
        name              : label on flowsheet
        T_out_C           : outlet temperature [°C]
        pressure_drop_bar : pressure drop [bar]  (default 0)
        x_pos, y_pos      : canvas position

        Returns
        -------
        SimpleNamespace with:
          .obj            — Cooler DWSIM object
          .outlet_stream  — outlet MaterialStream
          .energy_stream  — EnergyStream (wire: .energy_stream → .obj)

        Wiring pattern:
            connect(feed, cooler.obj)
            connect(cooler.obj, cooler.outlet_stream)
            connect(cooler.energy_stream, cooler.obj)
        """
        from DWSIM_ry_test.lib.units.heat_exchanger import build_cooler as _bc
        return _bc(sim, name=name, T_out_C=T_out_C,
                   pressure_drop_bar=pressure_drop_bar,
                   x_pos=x_pos, y_pos=y_pos)
    return build_cooler


def _make_add_flash(sim):
    """Factory: returns add_flash() bound to this sim."""
    def add_flash(name: str, T_C: float = None, P_bar: float = None,
                  x_pos: int = 500, y_pos: int = 300):
        """
        Add a Flash Drum (Vessel) unit op.

        For isothermal flash: the feed stream's T and P drive equilibrium.
        Set T_C and P_bar on the FEED stream before connecting — the drum
        inherits them. T_C and P_bar here are only used if you need to
        override the drum conditions separately (rare).

        Parameters
        ----------
        name         : label on flowsheet
        T_C          : drum temperature [°C]  — usually None (inherits from feed)
        P_bar        : drum pressure [bar]    — usually None (inherits from feed)
        x_pos, y_pos : canvas position

        Returns
        -------
        SimpleNamespace with:
          .obj           — Vessel DWSIM object
          .vapor_stream  — vapor outlet MaterialStream
          .liquid_stream — liquid outlet MaterialStream
          .energy_stream — EnergyStream

        Wiring pattern:
            connect(upstream_stream, flash.obj)
            connect(flash.obj, flash.vapor_stream)
            connect(flash.obj, flash.liquid_stream)
            connect(flash.energy_stream, flash.obj)
        """
        from types import SimpleNamespace
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

        obj = sim.AddObject(ObjectType.Vessel, x_pos, y_pos, name).GetAsObject()

        vapor_stream  = sim.AddObject(ObjectType.MaterialStream,
                                      x_pos + 150, y_pos - 80, f"{name}_V").GetAsObject()
        liquid_stream = sim.AddObject(ObjectType.MaterialStream,
                                      x_pos + 150, y_pos + 80, f"{name}_L").GetAsObject()
        energy_stream = sim.AddObject(ObjectType.EnergyStream,
                                      x_pos,       y_pos + 150, f"{name}_E").GetAsObject()

        return SimpleNamespace(
            obj           = obj,
            vapor_stream  = vapor_stream,
            liquid_stream = liquid_stream,
            energy_stream = energy_stream,
        )
    return add_flash


def _make_extract(comp_names):
    """Factory: returns extract() bound to comp_names."""
    def extract(stream_obj, label: str) -> dict:
        """
        Extract stream results after the flowsheet is solved.

        Parameters
        ----------
        stream_obj  : solved DWSIM MaterialStream object
        label       : name for this stream in the results dict

        Returns
        -------
        dict with keys:
          stream, T_K, T_C, P_Pa, P_bar,
          molar_flow_molh, mass_flow_kgh, enthalpy_kJkmol,
          vapor_fraction, x_<CompName> for each component
        """
        from DWSIM_ry_test.lib.units.heat_exchanger import hx_stream_results
        return hx_stream_results(stream_obj, label, comp_names)
    return extract


def _calc_duty(feed_result: dict, outlet_result: dict) -> dict:
    """
    Compute heat duty from enthalpy balance.
      Q [kW] = (H_out - H_in) [kJ/kmol] × F [mol/h] / 1000 / 3600
    Positive = heat added, Negative = heat removed.
    """
    H_in  = feed_result["enthalpy_kJkmol"]
    H_out = outlet_result["enthalpy_kJkmol"]
    F     = outlet_result["molar_flow_molh"]
    Q_kW  = (H_out - H_in) * (F / 1000) / 3600
    return {
        "duty_kW":  round(Q_kW, 4),
        "duty_kJh": round(Q_kW * 3600, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  ASSEMBLY BLOCK EXECUTOR
#  Wraps the LLM #2 generated assembly code into a callable function,
#  injects all helper functions into its namespace, and calls it.
#  Returns the `streams` dict that the assembly block must produce.
# ─────────────────────────────────────────────────────────────────────────────

def _exec_assembly_block(assembly_code: str, sim, comp_names: list,
                         task: dict) -> dict:
    """
    Execute the LLM #2-generated assembly block.

    The assembly block receives these names in its namespace:
      sim          — DWSIM flowsheet object
      task         — the full task JSON dict
      comp_names   — list of component name strings
      add_feed     — function
      add_stream   — function
      build_heater — function
      build_cooler — function
      add_flash    — function
      connect      — function
      extract      — function (for use AFTER solve — but assembly block runs before solve)
      calc_duty    — function

    The assembly block MUST assign a variable called `streams`:
      streams = {
          "FEED":   feed_obj,
          "H1_OUT": heater.outlet_stream,
          "V":      flash.vapor_stream,
          "L":      flash.liquid_stream,
          ... etc.
      }

    flowsheet_master.py will call solve, then call extract() on each stream in `streams`.
    """
    indented = textwrap.indent(assembly_code, "    ")
    fn_source = f"def assembly_block(sim, task, comp_names, add_feed, add_stream, build_heater, build_cooler, add_flash, connect, calc_duty, extract, auto_layout):\n{indented}\n    return streams\n"

    namespace = {}
    exec(fn_source, namespace)
    assembly_fn = namespace["assembly_block"]

    def auto_layout():
        sim.AutoLayout()

    streams = assembly_fn(
        sim          = sim,
        task         = task,
        comp_names   = comp_names,
        add_feed     = _make_add_feed(sim, comp_names),
        add_stream   = _make_add_stream(sim),
        build_heater = _make_build_heater(sim),
        build_cooler = _make_build_cooler(sim),
        add_flash    = _make_add_flash(sim),
        connect      = _make_connect(sim),
        calc_duty    = _calc_duty,
        extract      = _make_extract(comp_names),   # available but called post-solve
        auto_layout  = auto_layout,
    )
    return streams


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RUNNER — called by llm2_orchestrator.py
# ─────────────────────────────────────────────────────────────────────────────

def run_flowsheet_master(task: dict, assembly_code: str, output_block_fn,
                         output_dir: str = OUTPUT_DIR):
    """
    Full pipeline:
      1. Init DWSIM
      2. Load components + property package
      3. Execute assembly_code  → builds topology, returns streams dict
      4. Solve flowsheet
      5. Extract all stream results into results dict
      6. Call output_block_fn(results) → user-facing output

    Parameters
    ----------
    task             : dict   parsed task JSON from LLM #1
    assembly_code    : str    LLM #2-generated assembly block (raw Python)
    output_block_fn  : callable(results: dict) → None  (LLM #2-generated)
    output_dir       : str    where to save .dwxmz

    The results dict passed to output_block_fn:
    {
        "task_type":        "flowsheet",
        "property_package": str,
        "components":       list[str],
        "streams": {
            "<label>": {stream dict},   # one entry per stream in the assembly block's streams dict
            ...
        },
        "duties": {
            "<label>": {"duty_kW": float, "duty_kJh": float},
            ...
        }
    }
    """
    import pythoncom
    pythoncom.CoInitialize()

    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet,
    )
    from DWSIM.GlobalSettings import Settings

    comp_dict = {k: float(v) for k, v in task["components"].items()}
    comp_list = list(comp_dict.keys())

    raw_pkg = task.get("property_package")
    pkg_tag = _normalize_package(raw_pkg) if raw_pkg else _auto_select_package(comp_list)

    # Init DWSIM
    interf = init_dwsim(DWSIM_PATH)
    sim    = create_flowsheet(interf)

    for comp in comp_list:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)

    select_property_package(sim, pkg_tag)

    # ── Execute assembly block ────────────────────────────────────────────────
    print("\n[flowsheet_master] Building topology...")
    stream_objs = _exec_assembly_block(assembly_code, sim, comp_names, task)

    sim.AutoLayout()

    # ── Solve ─────────────────────────────────────────────────────────────────
    print("[flowsheet_master] Solving flowsheet...")
    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors is not None and len(errors) > 0:
        raise RuntimeError("[flowsheet_master] Solver errors:\n" +
                           "\n".join(str(e) for e in errors))
    print("[flowsheet_master] Flowsheet solved.")

    # ── Extract results ───────────────────────────────────────────────────────
    _extract = _make_extract(comp_names)
    stream_results = {}
    for label, stream_obj in stream_objs.items():
        stream_results[label] = _extract(stream_obj, label)

    results = {
        "task_type":        "flowsheet",
        "property_package": pkg_tag,
        "components":       comp_names,
        "streams":          stream_results,
        "duties":           {},   # LLM #2 output block can compute duties via calc_duty
        "calc_duty":        _calc_duty,  # pass through so output block can call it
    }

    # ── Save flowsheet ────────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    tag = "_".join(comp_list)
    save_flowsheet(interf, sim,
                   os.path.join(output_dir, f"flowsheet_{tag}.dwxmz"))

    # ── Output block ──────────────────────────────────────────────────────────
    output_block_fn(results)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI  (for manual testing)
# ─────────────────────────────────────────────────────────────────────────────

def _default_assembly(task):
    """
    Example assembly block for a Heater → Isothermal Flash flowsheet.
    Used when running from CLI for testing.
    """
    return """
feed_cfg = task["feed"]
comp_fracs = {k: float(v) for k, v in task["components"].items()}

feed = add_feed(
    name        = "FEED",
    T_C         = feed_cfg["temperature_C"],
    P_bar       = feed_cfg["pressure_bar"],
    flow_kmol_h = feed_cfg.get("flow_kmol_h", 100.0),
    comp_fracs  = comp_fracs,
)

step_h = task["steps"][0]
heater = build_heater(
    name              = step_h["name"],
    T_out_C           = step_h["T_out_C"],
    pressure_drop_bar = step_h.get("pressure_drop_bar", 0.0),
    x_pos = 300, y_pos = 300,
)
connect(feed, heater.obj)
connect(heater.obj, heater.outlet_stream)
connect(heater.energy_stream, heater.obj)
auto_layout()

step_f = task["steps"][1]
flash = add_flash(name=step_f["name"], x_pos=550, y_pos=300)
connect(heater.outlet_stream, flash.obj)
connect(flash.obj, flash.vapor_stream)
connect(flash.obj, flash.liquid_stream)
connect(flash.energy_stream, flash.obj)
auto_layout()

streams = {
    "FEED":    feed,
    "H1_OUT":  heater.outlet_stream,
    "V":       flash.vapor_stream,
    "L":       flash.liquid_stream,
}
"""


def _default_output_block(results: dict):
    print("\n" + "=" * 60)
    print(f"FLOWSHEET RESULTS ({results['property_package']})")
    print("=" * 60)
    for label, s in results["streams"].items():
        print(f"\n  {label}")
        print(f"    T = {s['T_C']:.2f} C  |  P = {s['P_bar']:.3f} bar  "
              f"|  V_frac = {s['vapor_fraction']:.4f}  "
              f"|  flow = {s['molar_flow_molh']:.2f} mol/h")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Flowsheet master — run with a task JSON for testing."
    )
    parser.add_argument("task_json", help="Path to task JSON file")
    args = parser.parse_args()

    with open(args.task_json, "r") as f:
        task = json.load(f)

    assembly_code = _default_assembly(task)
    run_flowsheet_master(task, assembly_code, _default_output_block)
