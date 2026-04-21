"""
DWSIM_ry_test/tasks/test_heat_exchanger.py
==========================================
Standalone tests for heat_exchanger.py unit op builders.

Tests (run independently):
  1. Heater      : propane/n-butane feed at 60C → heat to 120C
  2. Cooler      : propane/n-butane feed at 120C → cool to 40C
  3. HX (hot_outlet_T): hot propane/n-butane vs cold ethane
  4. HX (duty)   : fixed 500 kW duty

CLI:
  python DWSIM_ry_test/tasks/test_heat_exchanger.py --test heater
  python DWSIM_ry_test/tasks/test_heat_exchanger.py --test cooler
  python DWSIM_ry_test/tasks/test_heat_exchanger.py --test hx
  python DWSIM_ry_test/tasks/test_heat_exchanger.py --test all
"""

import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_PATH = r"C:\Users\terrbear\AppData\Local\DWSIM"
OUTPUT_DIR  = os.path.join(_ROOT, "output")

from DWSIM_ry_test.lib.units.heat_exchanger import (
    build_heater, build_cooler, build_hx,
    hx_stream_results, hx_duty_results,
)


def _init(components: list, package: str = "PR"):
    """Common DWSIM init: load, create flowsheet, add components + PP."""
    import pythoncom
    pythoncom.CoInitialize()

    from DWSIM_ry_test.lib.dwsim_core import (
        init_dwsim, create_flowsheet, select_property_package, save_flowsheet
    )
    interf = init_dwsim(DWSIM_PATH)
    sim    = create_flowsheet(interf)
    for comp in components:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)
    select_property_package(sim, package)
    return interf, sim, comp_names


def _set_feed(sim, feed_obj, T_C, P_bar, flow_kmol_h, comp_fracs: dict):
    """Configure a feed MaterialStream."""
    from DWSIM.GlobalSettings import Settings
    T_K      = T_C + 273.15
    P_Pa     = P_bar * 1e5
    flow_mols = flow_kmol_h * 1000 / 3600
    feed_obj.SetTemperature(T_K)
    feed_obj.SetPressure(P_Pa)
    feed_obj.SetMolarFlow(flow_mols)
    for comp, frac in comp_fracs.items():
        feed_obj.SetOverallCompoundMolarFlow(comp, frac * flow_mols)


def _solve(interf, sim):
    from DWSIM.GlobalSettings import Settings
    Settings.SolverMode = 0
    errors = interf.CalculateFlowsheet4(sim)
    if errors and len(errors) > 0:
        raise RuntimeError("Solver errors:\n" + "\n".join(str(e) for e in errors))
    print("[test] Flowsheet solved.")


def _print_stream(label, d):
    print(f"\n  {label}")
    print(f"    T = {d['T_C']:.2f} C  |  P = {d['P_bar']:.3f} bar  "
          f"|  V_frac = {d['vapor_fraction']:.4f}  "
          f"|  flow = {d['molar_flow_molh']:.2f} mol/h  "
          f"|  H = {d['enthalpy_kJkmol']:.1f} kJ/kmol")
    for k, v in d.items():
        if k.startswith("x_"):
            print(f"      {k} = {v:.6f}")


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 1: HEATER
#  Feed: Propane 50% / n-Butane 50% at 60C, 14 bar, 100 kmol/h
#  Heater: T_out = 120C, no pressure drop
#  Expected: outlet fully vapor, T = 120C, P = 14 bar
# ─────────────────────────────────────────────────────────────────────────────

def test_heater():
    print("\n" + "=" * 60)
    print("TEST 1 — Heater: Propane/n-Butane 60C → 120C")
    print("=" * 60)

    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

    # Build feed stream
    feed = sim.AddObject(ObjectType.MaterialStream, 50, 300, "FEED").GetAsObject()
    _set_feed(sim, feed, T_C=60, P_bar=14, flow_kmol_h=100,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})

    # Build heater
    hx = build_heater(sim, name="H1", T_out_C=120.0, pressure_drop_bar=0.0,
                      x_pos=300, y_pos=300)

    # Wire: FEED → Heater → OUT_STREAM; energy stream → Heater
    sim.ConnectObjects(feed.GraphicObject,             hx.obj.GraphicObject,           -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,           hx.outlet_stream.GraphicObject, -1, -1)
    sim.ConnectObjects(hx.energy_stream.GraphicObject, hx.obj.GraphicObject,           -1, -1)
    sim.AutoLayout()

    _solve(interf, sim)

    inlet_r  = hx_stream_results(feed,              "H1_IN",  comp_names)
    outlet_r = hx_stream_results(hx.outlet_stream,  "H1_OUT", comp_names)
    duty_r   = hx_duty_results(hx.obj, inlet_r, outlet_r)

    print("\n  Results:")
    _print_stream("Inlet",  inlet_r)
    _print_stream("Outlet", outlet_r)
    print(f"\n  Heat duty : {duty_r['duty_kW']:.2f} kW  ({duty_r['duty_kJh']:.1f} kJ/h)")

    from DWSIM_ry_test.lib.dwsim_core import save_flowsheet
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_flowsheet(interf, sim, os.path.join(OUTPUT_DIR, "test_heater.dwxmz"))


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 2: COOLER
#  Feed: Propane 50% / n-Butane 50% at 120C, 14 bar, 100 kmol/h
#  Cooler: T_out = 40C, no pressure drop
#  Expected: outlet partially/fully liquid, T = 40C, P = 14 bar
# ─────────────────────────────────────────────────────────────────────────────

def test_cooler():
    print("\n" + "=" * 60)
    print("TEST 2 — Cooler: Propane/n-Butane 120C → 40C")
    print("=" * 60)

    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

    feed = sim.AddObject(ObjectType.MaterialStream, 50, 300, "FEED").GetAsObject()
    _set_feed(sim, feed, T_C=120, P_bar=14, flow_kmol_h=100,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})

    hx = build_cooler(sim, name="C1", T_out_C=40.0, pressure_drop_bar=0.0,
                      x_pos=300, y_pos=300)

    # Wire: FEED → Cooler → OUT_STREAM; energy stream → Cooler
    sim.ConnectObjects(feed.GraphicObject,             hx.obj.GraphicObject,           -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,           hx.outlet_stream.GraphicObject, -1, -1)
    sim.ConnectObjects(hx.energy_stream.GraphicObject, hx.obj.GraphicObject,           -1, -1)
    sim.AutoLayout()

    _solve(interf, sim)

    inlet_r  = hx_stream_results(feed,             "C1_IN",  comp_names)
    outlet_r = hx_stream_results(hx.outlet_stream, "C1_OUT", comp_names)
    duty_r   = hx_duty_results(hx.obj, inlet_r, outlet_r)

    print("\n  Results:")
    _print_stream("Inlet",  inlet_r)
    _print_stream("Outlet", outlet_r)
    print(f"\n  Heat removed : {abs(duty_r['duty_kW']):.2f} kW  ({abs(duty_r['duty_kJh']):.1f} kJ/h)")

    from DWSIM_ry_test.lib.dwsim_core import save_flowsheet
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_flowsheet(interf, sim, os.path.join(OUTPUT_DIR, "test_cooler.dwxmz"))


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 3: TWO-STREAM HX (hot_outlet_T mode)
#  Hot side : Propane 50% / n-Butane 50% at 120C, 14 bar, 100 kmol/h → cool to 60C
#  Cold side: Propane 50% / n-Butane 50% at 30C,  10 bar, 80 kmol/h  → heated
#  Expected: hot outlet T = 60C; cold outlet T computed by energy balance
# ─────────────────────────────────────────────────────────────────────────────

def debug_hx_enum():
    """Print all available CalcMode enum members on DWSIM HeatExchanger."""
    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.UnitOperations.UnitOperations import HeatExchanger as DWSIMHeatExchanger
    print("\nHeatExchanger.CalculationMode members:")
    for name in dir(DWSIMHeatExchanger.CalculationMode):
        if not name.startswith("_"):
            print(f"  {name}")


def test_hx():
    print("\n" + "=" * 60)
    print("TEST 3 — Two-stream HX: hot_outlet_T mode")
    print("=" * 60)

    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

    hot_feed  = sim.AddObject(ObjectType.MaterialStream,  50, 200, "HOT_FEED" ).GetAsObject()
    cold_feed = sim.AddObject(ObjectType.MaterialStream,  50, 400, "COLD_FEED").GetAsObject()

    _set_feed(sim, hot_feed,  T_C=120, P_bar=14, flow_kmol_h=100,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})
    _set_feed(sim, cold_feed, T_C=30,  P_bar=10, flow_kmol_h=80,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})

    from DWSIM.UnitOperations.UnitOperations import HeatExchanger as DWSIMHX
    members = [a for a in dir(DWSIMHX.CalculationMode) if not a.startswith('_')]
    print(f"[debug] CalculationMode enum members: {members}")

    hx = build_hx(sim, name="HX1", calc_mode="hot_outlet_T",
                  hot_outlet_T_C=60.0, x_pos=300, y_pos=300)

    # Get real calc mode names
    modes = hx.obj.GetCalculationModes()
    print(f"[debug] Valid CalcModes: {[modes[i] for i in range(len(modes))]}")

    # Check FlowDir
    print(f"[debug] FlowDir: {hx.obj.FlowDir}")
    print(f"[debug] m_flowdirection: {hx.obj.m_flowdirection}")

    # Try setting FlowDir to counter-current (most common for shell-and-tube)
    # Try 0 and 1 to see which is which
    flow_dir_attrs = [a for a in dir(hx.obj.FlowDir.__class__) if not a.startswith('_')]
    print(f"[debug] FlowDir type/attrs: {hx.obj.FlowDir.__class__}, {flow_dir_attrs}")

    print(f"[debug] hot_feed      name: {hot_feed.GraphicObject.Name}")
    print(f"[debug] cold_feed     name: {cold_feed.GraphicObject.Name}")
    print(f"[debug] hot_outlet    name: {hx.hot_outlet_stream.GraphicObject.Name}")
    print(f"[debug] cold_outlet   name: {hx.cold_outlet_stream.GraphicObject.Name}")

    # Add this right after build_hx(), before any ConnectObjects calls
    print(f"[debug] CalcMode before solve: {hx.obj.CalculationMode}")
    print(f"[debug] HotSideOutletT before solve: {hx.obj.HotSideOutletTemperature - 273.15:.2f} C")

    # Also check what SetCalculationMode actually is
    calc_attrs = [a for a in dir(hx.obj) if 'calc' in a.lower() or 'mode' in a.lower()]
    print(f"[debug] calc/mode attrs: {calc_attrs}")

    from DWSIM.UnitOperations.UnitOperations import HeatExchanger as DWSIMHX
    calc_mode_attrs = [a for a in dir(DWSIMHX.CalculationMode) if not a.startswith('_')]
    print(f"[debug] CalculationMode enum members: {calc_mode_attrs}")

    # Connect hot second (gets port 1 = DWSIM's "Stream 2" = "cold" internally)
    sim.ConnectObjects(hot_feed.GraphicObject,   hx.obj.GraphicObject,               -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,     hx.hot_outlet_stream.GraphicObject,  -1, -1)
    sim.AutoLayout()

    # Connect cold first (gets port 0 = DWSIM's "Stream 1" = "hot" internally)
    sim.ConnectObjects(cold_feed.GraphicObject,  hx.obj.GraphicObject,               -1, -1)
    sim.ConnectObjects(hx.obj.GraphicObject,     hx.cold_outlet_stream.GraphicObject, -1, -1)
    sim.AutoLayout()

    ports_info = hx.obj.GetConnectionPortsInfo()
    print(f"[debug] Connection ports info: {ports_info}")

    ports_list = hx.obj.GetConnectionPortsList()
    print(f"[debug] Connection ports list: {ports_list}")

    go = hx.obj.GraphicObject
    # Also check GraphicObject connectors
    for i in range(go.InputConnectors.Count):
        ic = go.InputConnectors[i]
        if ic.IsAttached:
            try:
                name = ic.AttachedConnector.AttachedFrom.Owner.Name
            except:
                name = str(ic.AttachedConnector.AttachedFrom.Owner)
        else:
            name = 'None'
        print(f"[debug] Input connector {i}: IsAttached={ic.IsAttached}, stream={name}")

    for i in range(go.OutputConnectors.Count):
        oc = go.OutputConnectors[i]
        if oc.IsAttached:
            try:
                name = oc.AttachedConnector.AttachedTo.Owner.Name
            except:
                name = str(oc.AttachedConnector.AttachedTo.Owner)
        else:
            name = 'None'
        print(f"[debug] Output connector {i}: IsAttached={oc.IsAttached}, stream={name}")

    ports_list = hx.obj.GetConnectionPortsList()
    for i in range(ports_list.Count):
        print(f"[debug] Port {i}: {ports_list[i]}")
    
    print(f"[debug] CalcMode: {hx.obj.CalcMode}")
    print(f"[debug] CalculationMode: {hx.obj.CalculationMode}")

    flow_attrs = [a for a in dir(hx.obj) if 'flow' in a.lower() or 'direct' in a.lower() or 'config' in a.lower()]
    print(f"[debug] flow/direction attrs: {flow_attrs}")

    sim.AutoLayout()
    _solve(interf, sim)

    #print(f"[debug] InletStream1 T = {hx.obj.GetInletMaterialStream(0).Phases[0].Properties.temperature - 273.15:.2f} C")
    #print(f"[debug] InletStream2 T = {hx.obj.GetInletMaterialStream(1).Phases[0].Properties.temperature - 273.15:.2f} C")

    print(f"[debug] post-solve CalcMode:               {hx.obj.CalcMode}")
    print(f"[debug] post-solve HotSideOutletTemp:       {hx.obj.HotSideOutletTemperature - 273.15:.2f} C")
    print(f"[debug] post-solve ColdSideOutletTemp:      {hx.obj.ColdSideOutletTemperature - 273.15:.2f} C")

    hot_in_r   = hx_stream_results(hot_feed,               "HOT_IN",   comp_names)
    hot_out_r  = hx_stream_results(hx.cold_outlet_stream,  "HOT_OUT",  comp_names)  # port 1 = hot side
    cold_in_r  = hx_stream_results(cold_feed,              "COLD_IN",  comp_names)
    cold_out_r = hx_stream_results(hx.hot_outlet_stream,   "COLD_OUT", comp_names)  # port 0 = cold side
    duty_r     = hx_duty_results(hx.obj, hot_in_r, hot_out_r)

    print("\n  Results:")
    _print_stream("Hot  inlet",  hot_in_r)
    _print_stream("Hot  outlet", hot_out_r)
    _print_stream("Cold inlet",  cold_in_r)
    _print_stream("Cold outlet", cold_out_r)
    print(f"\n  Duty : {duty_r['duty_kW']:.2f} kW  |  LMTD = {duty_r['LMTD_K']:.2f} K")

    from DWSIM_ry_test.lib.dwsim_core import save_flowsheet
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_flowsheet(interf, sim, os.path.join(OUTPUT_DIR, "test_hx.dwxmz"))


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Heat exchanger unit op tests")
    parser.add_argument(
        "--test",
        choices=["heater", "cooler", "hx", "all", "debug_hx_enum"],
        default="all",
        help="Which test to run (default: all)"
    )
    args = parser.parse_args()

    if args.test == "debug_hx_enum":
        debug_hx_enum()
    if args.test in ("heater", "all"):
        test_heater()
    if args.test in ("cooler", "all"):
        test_cooler()
    if args.test in ("hx", "all"):
        test_hx()
