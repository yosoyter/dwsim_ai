"""
simquery/runners/test_compressor_expander.py
================================================
Standalone tests for compressor_expander.py unit op builders.

Tests:
  1. Compressor : propane/n-butane vapor at 5 bar → compress to 20 bar (75% efficiency)
  2. Expander   : propane/n-butane vapor at 20 bar → expand to 5 bar (75% efficiency)

Expected results (approximate, PR EOS):
  Compressor:
    Outlet T ~ 140–160°C (adiabatic heating)
    Outlet P = 20 bar
    Shaft work IN ~ positive kW

  Expander:
    Outlet T ~ 30–50°C (adiabatic cooling)
    Outlet P = 5 bar
    Shaft work OUT ~ negative kW (energy recovered)

CLI:
  python simquery/runners/test_compressor_expander.py --test compressor
  python simquery/runners/test_compressor_expander.py --test expander
  python simquery/runners/test_compressor_expander.py --test all
"""

import os
import sys
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

DWSIM_PATH = r"C:\Users\terrbear\AppData\Local\DWSIM"
OUTPUT_DIR  = os.path.join(_ROOT, "output")

from simquery.core.units.compressor_expander import (
    build_compressor, build_expander, compressor_results,
)


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _init(components: list, package: str = "PR"):
    import pythoncom
    pythoncom.CoInitialize()
    from simquery.core.dwsim_core import init_dwsim, create_flowsheet, select_property_package
    interf = init_dwsim(DWSIM_PATH)
    sim    = create_flowsheet(interf)
    for comp in components:
        sim.AddCompound(comp)
    comp_names = list(sim.SelectedCompounds.Keys)
    select_property_package(sim, package)
    return interf, sim, comp_names


def _set_feed(sim, feed_obj, T_C, P_bar, flow_kmol_h, comp_fracs: dict):
    T_K       = T_C + 273.15
    P_Pa      = P_bar * 1e5
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


def _print_results(r: dict):
    i = r["inlet"]
    o = r["outlet"]
    print(f"\n  Inlet  : T = {i['T_C']:.2f} C  |  P = {i['P_bar']:.3f} bar  "
          f"|  V_frac = {i['vapor_fraction']:.4f}  |  flow = {i['molar_flow_molh']:.2f} mol/h  "
          f"|  H = {i['enthalpy_kJkmol']:.1f} kJ/kmol")
    print(f"  Outlet : T = {o['T_C']:.2f} C  |  P = {o['P_bar']:.3f} bar  "
          f"|  V_frac = {o['vapor_fraction']:.4f}  |  flow = {o['molar_flow_molh']:.2f} mol/h  "
          f"|  H = {o['enthalpy_kJkmol']:.1f} kJ/kmol")
    sign = "IN" if r["shaft_work_kW"] >= 0 else "OUT"
    print(f"  Shaft work ({sign}) : {abs(r['shaft_work_kW']):.2f} kW  "
          f"({abs(r['shaft_work_kJh']):.1f} kJ/h)  |  "
          f"η_isen = {r['isentropic_efficiency']:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 1: COMPRESSOR
#  Feed: Propane 50% / n-Butane 50% at 80°C, 5 bar, 100 kmol/h (all vapor)
#  Compress to 20 bar at 75% isentropic efficiency
# ─────────────────────────────────────────────────────────────────────────────

def test_compressor():
    print("\n" + "=" * 60)
    print("TEST 1 — Compressor: 5 bar → 20 bar, η = 0.75")
    print("=" * 60)

    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

    feed = sim.AddObject(ObjectType.MaterialStream, 50, 300, "FEED").GetAsObject()
    _set_feed(sim, feed, T_C=80, P_bar=5, flow_kmol_h=100,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})

    comp = build_compressor(sim, name="C1", P_out_bar=20.0, efficiency=0.75,
                            x_pos=300, y_pos=300)

    # Wire: FEED → Compressor → OUTLET; energy stream → Compressor
    sim.ConnectObjects(feed.GraphicObject,               comp.obj.GraphicObject,           -1, -1)
    sim.ConnectObjects(comp.obj.GraphicObject,            comp.outlet_stream.GraphicObject, -1, -1)
    sim.ConnectObjects(comp.energy_stream.GraphicObject,  comp.obj.GraphicObject,           -1, -1)
    sim.AutoLayout()

    _solve(interf, sim)

    r = compressor_results(feed, comp.outlet_stream, comp.obj, comp_names, label="compressor")
    print("\n  Results:")
    _print_results(r)

    from simquery.core.dwsim_core import save_flowsheet
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_flowsheet(interf, sim, os.path.join(OUTPUT_DIR, "test_compressor.dwxmz"))


# ─────────────────────────────────────────────────────────────────────────────
#  TEST 2: EXPANDER
#  Feed: Propane 50% / n-Butane 50% at 150°C, 20 bar, 100 kmol/h (all vapor)
#  Expand to 5 bar at 75% isentropic efficiency
# ─────────────────────────────────────────────────────────────────────────────

def test_expander():
    print("\n" + "=" * 60)
    print("TEST 2 — Expander: 20 bar → 5 bar, η = 0.75")
    print("=" * 60)

    interf, sim, comp_names = _init(["Propane", "n-Butane"], "PR")
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType

    feed = sim.AddObject(ObjectType.MaterialStream, 50, 300, "FEED").GetAsObject()
    _set_feed(sim, feed, T_C=200, P_bar=20, flow_kmol_h=100,
              comp_fracs={"Propane": 0.5, "n-Butane": 0.5})

    exp = build_expander(sim, name="E1", P_out_bar=5.0, efficiency=0.75,
                         x_pos=300, y_pos=300)

    # Wire: FEED → Expander → OUTLET; Expander → energy stream (work OUT)
    sim.ConnectObjects(feed.GraphicObject,            exp.obj.GraphicObject,           -1, -1)
    sim.ConnectObjects(exp.obj.GraphicObject,          exp.outlet_stream.GraphicObject, -1, -1)
    sim.ConnectObjects(exp.obj.GraphicObject,          exp.energy_stream.GraphicObject, -1, -1)
    sim.AutoLayout()

    _solve(interf, sim)

    r = compressor_results(feed, exp.outlet_stream, exp.obj, comp_names, label="expander")
    print("\n  Results:")
    _print_results(r)

    from simquery.core.dwsim_core import save_flowsheet
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_flowsheet(interf, sim, os.path.join(OUTPUT_DIR, "test_expander.dwxmz"))


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compressor/Expander unit op tests")
    parser.add_argument(
        "--test",
        choices=["compressor", "expander", "all"],
        default="all",
        help="Which test to run (default: all)"
    )
    args = parser.parse_args()

    if args.test in ("compressor", "all"):
        test_compressor()
    if args.test in ("expander", "all"):
        test_expander()

