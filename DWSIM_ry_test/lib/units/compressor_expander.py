"""
DWSIM_ry_test/lib/units/compressor_expander.py
===============================================
Standalone compressor and expander unit operation builders for DWSIM headless automation.

Provides two build functions:
  - build_compressor() : single-stream adiabatic compressor (raises pressure)
  - build_expander()   : single-stream adiabatic expander / turbine (drops pressure)

Design principles
-----------------
- Each function ONLY adds the unit op object and sets its parameters.
- NO wiring, NO solving happens here — that is the caller's responsibility.
- Each function returns a SimpleNamespace with .obj and named stream slots.

Usage pattern
-------------
    comp = build_compressor(sim, name="C1", P_out_bar=20.0, efficiency=0.75)

    # Wire (caller's responsibility):
    sim.ConnectObjects(feed.GraphicObject,              comp.obj.GraphicObject,           -1, -1)
    sim.ConnectObjects(comp.obj.GraphicObject,          comp.outlet_stream.GraphicObject, -1, -1)
    sim.ConnectObjects(comp.energy_stream.GraphicObject, comp.obj.GraphicObject,          -1, -1)

    # After solve:
    results = compressor_results(feed, comp.outlet_stream, comp.obj, comp_names)

Port index reference (DWSIM Compressor / Expander)
--------------------------------------------------
  ConnectObjects uses -1 (auto-detect) reliably for single-stream units.

  Explicit indices if needed:
    Material inlet  = 0
    Material outlet = 1
    Energy stream   = 2  (Compressor: energy IN; Expander: energy OUT)

Tested against DWSIM 8.x (Windows, Automation3 API, Python.NET 3.0).
"""

import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)


# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _add_stream(sim, x: int, y: int, name: str):
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    return sim.AddObject(ObjectType.MaterialStream, x, y, name).GetAsObject()


def _add_energy(sim, x: int, y: int, name: str):
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    return sim.AddObject(ObjectType.EnergyStream, x, y, name).GetAsObject()


def _get(val):
    """Safe float extractor for DWSIM property values."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except TypeError:
        try:
            return float(val.GetValueOrDefault())
        except Exception:
            return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  COMPRESSOR
#  Single-stream adiabatic compression.
#  Topology: INLET_STREAM → [Compressor] → OUTLET_STREAM
#                                       ↑
#                                 ENERGY_STREAM (shaft work in)
# ─────────────────────────────────────────────────────────────────────────────

def build_compressor(
    sim,
    name: str            = "Compressor",
    P_out_bar: float     = None,
    efficiency: float    = 0.75,
    x_pos: int           = 300,
    y_pos: int           = 300,
) -> SimpleNamespace:
    """
    Add an adiabatic Compressor unit operation to the flowsheet.

    The compressor raises a stream to a specified outlet pressure using
    isentropic efficiency. An EnergyStream is automatically created for
    the shaft work input.

    Parameters
    ----------
    sim          : DWSIM flowsheet object (from create_flowsheet)
    name         : label for this unit op on the flowsheet
    P_out_bar    : outlet pressure [bar]  (required)
    efficiency   : isentropic efficiency, 0–1  (default 0.75)
    x_pos, y_pos : canvas position of the unit op

    Returns
    -------
    ns : SimpleNamespace with attributes:
         .obj              — Compressor DWSIM object
         .outlet_stream    — MaterialStream (discharge side)
         .energy_stream    — EnergyStream   (shaft work input)

    Wiring (caller must do after build_compressor):
        sim.ConnectObjects(feed.GraphicObject,               ns.obj.GraphicObject,           -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,             ns.outlet_stream.GraphicObject, -1, -1)
        sim.ConnectObjects(ns.energy_stream.GraphicObject,   ns.obj.GraphicObject,           -1, -1)
        sim.AutoLayout()

    Notes
    -----
    - CalcMode is set to OutletPressure (the only supported mode here).
    - Efficiency is isentropic (adiabatic). DWSIM default is 0.75.
    - Feed stream is wired directly to .obj — no inlet_stream placeholder.
    """
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    if P_out_bar is None:
        raise ValueError("[compressor_expander] P_out_bar is required for build_compressor")

    comp_obj = sim.AddObject(
        ObjectType.Compressor, x_pos, y_pos, name
    ).GetAsObject()

    # Outlet pressure in Pa
    comp_obj.set_POut(P_out_bar * 1e5)

    # Isentropic efficiency (0–1)
    comp_obj.AdiabaticEfficiency = efficiency

    # CalcMode: OutletPressure = 0 for Compressor
    comp_obj.CalcMode = DWSIMUnitOps.Compressor.CalculationMode.OutletPressure

    # Create outlet and energy streams
    outlet_stream = _add_stream(sim, x_pos + 150, y_pos,       f"{name}_OUT")
    energy_stream = _add_energy(sim, x_pos,       y_pos + 150, f"{name}_W")

    print(f"[compressor_expander] Compressor '{name}': "
          f"P_out = {P_out_bar:.2f} bar, efficiency = {efficiency:.2f}")

    return SimpleNamespace(
        obj           = comp_obj,
        outlet_stream = outlet_stream,
        energy_stream = energy_stream,
        P_out_bar     = P_out_bar,
        efficiency    = efficiency,
        mode          = "compressor",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  EXPANDER  (turbine / isentropic expansion)
#  Single-stream adiabatic expansion.
#  Topology: INLET_STREAM → [Expander] → OUTLET_STREAM
#                                     ↓
#                               ENERGY_STREAM (shaft work out)
# ─────────────────────────────────────────────────────────────────────────────

def build_expander(
    sim,
    name: str            = "Expander",
    P_out_bar: float     = None,
    efficiency: float    = 0.75,
    x_pos: int           = 300,
    y_pos: int           = 300,
) -> SimpleNamespace:
    """
    Add an adiabatic Expander (turbine) unit operation to the flowsheet.

    The expander drops a stream to a specified outlet pressure and recovers
    shaft work. An EnergyStream is automatically created for the work output.

    Parameters
    ----------
    sim          : DWSIM flowsheet object
    name         : label for this unit op on the flowsheet
    P_out_bar    : outlet pressure [bar]  (required, must be < inlet pressure)
    efficiency   : isentropic efficiency, 0–1  (default 0.75)
    x_pos, y_pos : canvas position

    Returns
    -------
    ns : SimpleNamespace with attributes:
         .obj              — Expander DWSIM object
         .outlet_stream    — MaterialStream (low-pressure discharge)
         .energy_stream    — EnergyStream   (shaft work recovered)

    Wiring (caller must do after build_expander):
        sim.ConnectObjects(feed.GraphicObject,             ns.obj.GraphicObject,           -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,           ns.outlet_stream.GraphicObject, -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,           ns.energy_stream.GraphicObject, -1, -1)
        sim.AutoLayout()

    Notes
    -----
    - Energy stream is wired FROM expander (work OUT), unlike compressor (work IN).
    - CalcMode is set to OutletPressure.
    - Efficiency is isentropic (adiabatic).
    """
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    if P_out_bar is None:
        raise ValueError("[compressor_expander] P_out_bar is required for build_expander")

    exp_obj = sim.AddObject(
        ObjectType.Expander, x_pos, y_pos, name
    ).GetAsObject()

    # Outlet pressure in Pa
    exp_obj.set_POut(P_out_bar * 1e5)

    # Isentropic efficiency (0–1)
    exp_obj.AdiabaticEfficiency = efficiency

    # CalcMode: OutletPressure = 0 for Expander
    exp_obj.CalcMode = DWSIMUnitOps.Expander.CalculationMode.OutletPressure

    # Create outlet and energy streams
    outlet_stream = _add_stream(sim, x_pos + 150, y_pos,       f"{name}_OUT")
    energy_stream = _add_energy(sim, x_pos + 150, y_pos + 100, f"{name}_W")

    energy_stream.GraphicObject.Calculated = False
    try:
        energy_stream.EnergyFlow = None   # clear any default
    except Exception:
        pass

    print(f"[compressor_expander] Expander '{name}': "
          f"P_out = {P_out_bar:.2f} bar, efficiency = {efficiency:.2f}")

    return SimpleNamespace(
        obj           = exp_obj,
        outlet_stream = outlet_stream,
        energy_stream = energy_stream,
        P_out_bar     = P_out_bar,
        efficiency    = efficiency,
        mode          = "expander",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  RESULT EXTRACTOR
#  Call AFTER the flowsheet is solved.
# ─────────────────────────────────────────────────────────────────────────────

def compressor_results(
    inlet_stream_obj,
    outlet_stream_obj,
    unit_obj,
    comp_names: list,
    label: str = "compressor",
) -> dict:
    """
    Extract T, P, enthalpy, flow, and shaft work from a solved compressor or expander.

    Parameters
    ----------
    inlet_stream_obj  : DWSIM MaterialStream (feed, solved)
    outlet_stream_obj : DWSIM MaterialStream (discharge, solved)
    unit_obj          : DWSIM Compressor or Expander object (solved)
    comp_names        : list of component name strings
    label             : "compressor" or "expander" — affects sign convention note

    Returns
    -------
    dict with keys:
        label,
        inlet:  {T_C, P_bar, molar_flow_molh, enthalpy_kJkmol, vapor_fraction, x_<comp>}
        outlet: {same keys}
        shaft_work_kW   — positive = work IN (compressor), negative = work OUT (expander)
        shaft_work_kJh
        isentropic_efficiency
    """
    def _stream_dict(stream_obj, name):
        p0 = stream_obj.Phases[0].Properties
        p2 = stream_obj.Phases[2].Properties
        d = {
            "stream":           name,
            "T_K":              round(_get(p0.temperature), 4),
            "T_C":              round(_get(p0.temperature) - 273.15, 4),
            "P_Pa":             round(_get(p0.pressure), 2),
            "P_bar":            round(_get(p0.pressure) / 1e5, 4),
            "molar_flow_molh":  round(_get(p0.molarflow) * 3600, 4),
            "mass_flow_kgh":    round(_get(p0.massflow) * 3600, 4),
            "enthalpy_kJkmol":  round(_get(p0.enthalpy) * 1000, 4),
            "vapor_fraction":   round(_get(p2.molarfraction), 6),
        }
        for comp_name in comp_names:
            try:
                xval = _get(stream_obj.Phases[0].Compounds[comp_name].MoleFraction)
            except Exception:
                xval = 0.0
            d[f"x_{comp_name}"] = round(xval, 6)
        return d

    inlet_d  = _stream_dict(inlet_stream_obj,  f"{label}_IN")
    outlet_d = _stream_dict(outlet_stream_obj, f"{label}_OUT")

    # Shaft work directly from DWSIM solver result (W → kW)
    try:
        work_kW = float(unit_obj.DeltaQ)
    except Exception:
        # Fallback to enthalpy balance if DeltaQ unavailable
        H_in    = inlet_d["enthalpy_kJkmol"]
        H_out   = outlet_d["enthalpy_kJkmol"]
        F       = outlet_d["molar_flow_molh"]
        work_kW = (H_out - H_in) * (F / 1000) / 3600

    try:
        eta = float(unit_obj.AdiabaticEfficiency)
    except Exception:
        eta = 0.0

    return {
        "label":                  label,
        "inlet":                  inlet_d,
        "outlet":                 outlet_d,
        "shaft_work_kW":          round(work_kW, 2),
        "shaft_work_kJh":         round(work_kW * 3600, 2),
        "isentropic_efficiency":  round(eta, 4),
    }
