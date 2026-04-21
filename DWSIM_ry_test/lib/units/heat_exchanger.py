"""
DWSIM_ry_test/lib/units/heat_exchanger.py
==========================================
Standalone heat exchanger unit operation builder for DWSIM headless automation.

Provides three build functions — one per operating mode:
  - build_heater()   : single-stream heater (outlet T specified)
  - build_cooler()   : single-stream cooler (outlet T specified)
  - build_hx()       : two-stream shell-and-tube heat exchanger (hot + cold sides)

Design principles
-----------------
- Each function ONLY adds the unit op object and sets its parameters.
- NO wiring, NO solving happens here — that is the caller's responsibility.
- Each function returns a plain namespace object with .obj (the DWSIM unit op)
  and named inlet/outlet stream slots so the caller can wire cleanly without
  knowing port indices.

Usage pattern (in flowsheet_master.py or LLM #2 assembly block)
---------------------------------------------------------------
    heater = build_heater(sim, name="HX1", T_out_C=120.0, pressure_drop_bar=0.1)

    # Wire (caller's responsibility):
    sim.ConnectObjects(feed_stream.GraphicObject, heater.inlet.GraphicObject, -1, -1)
    sim.ConnectObjects(heater.outlet.GraphicObject, next_unit.GraphicObject, -1, -1)

    # After solve, extract results:
    info = heater_results(heater.outlet_stream_obj, comp_names)

Port index reference (DWSIM Heater / Cooler)
--------------------------------------------
  GraphicObject ports (ConnectObjects uses these):
    Inlet  material stream  → port index -1 (auto-detect) works for simple cases
    Outlet material stream  → port index -1
    Energy stream           → port index -1

  Explicit indices if needed:
    Material inlet  = 0
    Material outlet = 1
    Energy inlet    = 2   (Heater only — energy IN)
    Energy outlet   = 2   (Cooler only — energy OUT)

Port index reference (DWSIM HeatExchanger)
------------------------------------------
    Hot side inlet    = 0
    Hot side outlet   = 1
    Cold side inlet   = 2
    Cold side outlet  = 3

Tested against DWSIM 8.x (windows branch, Automation3 API).
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
    """Add a MaterialStream to the flowsheet and return its object."""
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    return sim.AddObject(ObjectType.MaterialStream, x, y, name).GetAsObject()


def _add_energy(sim, x: int, y: int, name: str):
    """Add an EnergyStream to the flowsheet and return its object."""
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    return sim.AddObject(ObjectType.EnergyStream, x, y, name).GetAsObject()


# ─────────────────────────────────────────────────────────────────────────────
#  HEATER
#  Single-stream heat addition. Outlet temperature is specified.
#  Topology: INLET_STREAM → [Heater] → OUTLET_STREAM
#                                   ↑
#                              ENERGY_STREAM (energy in)
# ─────────────────────────────────────────────────────────────────────────────

def build_heater(
    sim,
    name: str       = "Heater",
    T_out_C: float  = 100.0,
    pressure_drop_bar: float = 0.0,
    x_pos: int      = 300,
    y_pos: int      = 300,
) -> SimpleNamespace:
    """
    Add a Heater unit operation to the flowsheet.

    The Heater raises a single stream to a fixed outlet temperature.
    An EnergyStream is automatically created and wired to the Heater's
    energy inlet port.

    Parameters
    ----------
    sim               : DWSIM flowsheet object (from create_flowsheet)
    name              : label for this unit op on the flowsheet
    T_out_C           : outlet temperature [°C]
    pressure_drop_bar : pressure drop across the heater [bar]  (default 0)
    x_pos, y_pos      : canvas position of the unit op

    Returns
    -------
    ns : SimpleNamespace with attributes:
         .obj              — the Heater DWSIM object (wire upstream stream directly to this)
         .energy_stream    — EnergyStream object (wire to heater energy port)
         .outlet_stream    — MaterialStream on the RIGHT (wire to downstream unit op)

    Wiring (caller must do this after calling build_heater):
        sim.ConnectObjects(upstream_stream.GraphicObject,
                           ns.obj.GraphicObject,              -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,
                           ns.outlet_stream.GraphicObject,    -1, -1)
        sim.ConnectObjects(ns.energy_stream.GraphicObject,
                           ns.obj.GraphicObject,              -1, -1)

    Note: MaterialStream → MaterialStream connections are NOT allowed in DWSIM.
    Always wire: MaterialStream → UnitOp → MaterialStream.
    """
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    # Place unit op on canvas
    heater_obj = sim.AddObject(
        ObjectType.Heater, x_pos, y_pos, name
    ).GetAsObject()

    # Configure operating parameters
    T_out_K = T_out_C + 273.15
    heater_obj.OutletTemperature  = T_out_K
    heater_obj.CalcMode = DWSIMUnitOps.Heater.CalculationMode.OutletTemperature
    heater_obj.DeltaP   = pressure_drop_bar * 1e5   # Pa

    # Create outlet and energy streams (caller wires them)
    # Note: no inlet_stream placeholder — wire upstream directly to heater_obj
    outlet_stream = _add_stream(sim, x_pos + 150, y_pos,       f"{name}_OUT")
    energy_stream = _add_energy(sim, x_pos,       y_pos + 150, f"{name}_Q")

    print(f"[heat_exchanger] Heater '{name}': T_out = {T_out_C:.1f} C, "
          f"dP = {pressure_drop_bar:.3f} bar")

    return SimpleNamespace(
        obj           = heater_obj,
        outlet_stream = outlet_stream,
        energy_stream = energy_stream,
        T_out_C       = T_out_C,
        mode          = "heater",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  COOLER
#  Single-stream heat removal. Outlet temperature is specified.
#  Topology: INLET_STREAM → [Cooler] → OUTLET_STREAM
#                                   ↓
#                              ENERGY_STREAM (energy out)
# ─────────────────────────────────────────────────────────────────────────────

def build_cooler(
    sim,
    name: str       = "Cooler",
    T_out_C: float  = 40.0,
    pressure_drop_bar: float = 0.0,
    x_pos: int      = 300,
    y_pos: int      = 300,
) -> SimpleNamespace:
    """
    Add a Cooler unit operation to the flowsheet.

    The Cooler reduces a single stream to a fixed outlet temperature.
    An EnergyStream is automatically created for the heat removed.

    Parameters
    ----------
    sim               : DWSIM flowsheet object
    name              : label for this unit op on the flowsheet
    T_out_C           : outlet temperature [°C]
    pressure_drop_bar : pressure drop across the cooler [bar]  (default 0)
    x_pos, y_pos      : canvas position

    Returns
    -------
    ns : SimpleNamespace with attributes:
         .obj              — the Cooler DWSIM object
         .energy_stream    — EnergyStream object (energy removed)
         .inlet_stream     — MaterialStream placeholder (upstream)
         .outlet_stream    — MaterialStream placeholder (downstream)

    Wiring: same pattern as build_heater (see its docstring).
    """
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    cooler_obj = sim.AddObject(
        ObjectType.Cooler, x_pos, y_pos, name
    ).GetAsObject()

    T_out_K = T_out_C + 273.15
    cooler_obj.OutletTemperature = T_out_K
    cooler_obj.CalcMode = DWSIMUnitOps.Cooler.CalculationMode.OutletTemperature
    cooler_obj.DeltaP   = pressure_drop_bar * 1e5

    # No inlet_stream placeholder — wire upstream directly to cooler_obj
    outlet_stream = _add_stream(sim, x_pos + 150, y_pos,       f"{name}_OUT")
    energy_stream = _add_energy(sim, x_pos,       y_pos + 150, f"{name}_Q")

    print(f"[heat_exchanger] Cooler '{name}': T_out = {T_out_C:.1f} C, "
          f"dP = {pressure_drop_bar:.3f} bar")

    return SimpleNamespace(
        obj           = cooler_obj,
        outlet_stream = outlet_stream,
        energy_stream = energy_stream,
        T_out_C       = T_out_C,
        mode          = "cooler",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  TWO-STREAM HEAT EXCHANGER (shell-and-tube)
#  Hot side loses heat, cold side gains heat.
#  Topology:
#    HOT_IN  → [HX] → HOT_OUT
#    COLD_IN → [HX] → COLD_OUT
# ─────────────────────────────────────────────────────────────────────────────

# DWSIM HeatExchanger calculation mode integers
HX_CALC_MODE_KEYS = {
    "hot_outlet_T":  0,
    "cold_outlet_T": 1,
    "area":          2,
    "duty":          3,
}

def build_hx(
    sim,
    name: str              = "HX",
    calc_mode: str         = "hot_outlet_T",
    hot_outlet_T_C: float  = None,
    cold_outlet_T_C: float = None,
    duty_kW: float         = None,
    area_m2: float         = None,
    U_kWm2K: float         = None,
    hot_pressure_drop_bar: float  = 0.0,
    cold_pressure_drop_bar: float = 0.0,
    x_pos: int = 300,
    y_pos: int = 300,
) -> SimpleNamespace:
    """
    Add a two-stream Heat Exchanger (shell-and-tube) to the flowsheet.

    Supported calc_mode values and required parameters:
      "hot_outlet_T"   → set hot_outlet_T_C     (most common)
      "cold_outlet_T"  → set cold_outlet_T_C
      "duty"           → set duty_kW             (positive = heat transferred hot→cold)
      "area"           → set area_m2 AND U_kWm2K

    Parameters
    ----------
    sim                    : DWSIM flowsheet object
    name                   : label on flowsheet
    calc_mode              : one of "hot_outlet_T", "cold_outlet_T", "duty", "area"
    hot_outlet_T_C         : hot-side outlet temperature [°C]   (for hot_outlet_T mode)
    cold_outlet_T_C        : cold-side outlet temperature [°C]  (for cold_outlet_T mode)
    duty_kW                : heat duty [kW]                     (for duty mode)
    area_m2                : heat transfer area [m²]            (for area mode)
    U_kWm2K                : overall heat transfer coeff [kW/m²K] (for area mode)
    hot_pressure_drop_bar  : hot-side pressure drop [bar]
    cold_pressure_drop_bar : cold-side pressure drop [bar]
    x_pos, y_pos           : canvas position

    Returns
    -------
    ns : SimpleNamespace with attributes:
         .obj                — HeatExchanger DWSIM object
         .hot_inlet_stream   — hot-side inlet  MaterialStream
         .hot_outlet_stream  — hot-side outlet MaterialStream
         .cold_inlet_stream  — cold-side inlet  MaterialStream
         .cold_outlet_stream — cold-side outlet MaterialStream

    Wiring example (use -1 for all port indices — auto-detect):
        sim.ConnectObjects(hot_feed.GraphicObject,
                           ns.obj.GraphicObject,               -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,
                           ns.hot_outlet_stream.GraphicObject, -1, -1)
        sim.ConnectObjects(cold_feed.GraphicObject,
                           ns.obj.GraphicObject,               -1, -1)
        sim.ConnectObjects(ns.obj.GraphicObject,
                           ns.cold_outlet_stream.GraphicObject,-1, -1)
    """
    from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
    from DWSIM.UnitOperations import UnitOperations as DWSIMUnitOps

    hx_obj = sim.AddObject(
        ObjectType.HeatExchanger, x_pos, y_pos, name
    ).GetAsObject()

    # Set calc mode — use SetCalculationMode() which accepts int directly
    if calc_mode not in HX_CALC_MODE_KEYS:
        raise ValueError(
            f"[heat_exchanger] Unknown calc_mode '{calc_mode}'. "
            f"Valid: {list(HX_CALC_MODE_KEYS.keys())}"
        )
    hx_obj.SetCalculationMode(HX_CALC_MODE_KEYS[calc_mode])  # sets CalcMode correctly
    hx_obj.CalculationMode = hx_obj.CalcMode                 # sync CalculationMode from CalcMode

    # Set operating parameters based on mode
    if calc_mode == "hot_outlet_T":
        if hot_outlet_T_C is None:
            raise ValueError("[heat_exchanger] hot_outlet_T_C required for calc_mode='hot_outlet_T'")
        hx_obj.HotSideOutletTemperature = hot_outlet_T_C + 273.15
        print(f"[heat_exchanger] HX '{name}': hot outlet = {hot_outlet_T_C:.1f} C")

    elif calc_mode == "cold_outlet_T":
        if cold_outlet_T_C is None:
            raise ValueError("[heat_exchanger] cold_outlet_T_C required for calc_mode='cold_outlet_T'")
        hx_obj.ColdSideOutletTemperature = cold_outlet_T_C + 273.15
        print(f"[heat_exchanger] HX '{name}': cold outlet = {cold_outlet_T_C:.1f} C")

    elif calc_mode == "duty":
        if duty_kW is None:
            raise ValueError("[heat_exchanger] duty_kW required for calc_mode='duty'")
        hx_obj.HeatLoad = duty_kW * 1000.0   # DWSIM expects W
        print(f"[heat_exchanger] HX '{name}': duty = {duty_kW:.2f} kW")

    elif calc_mode == "area":
        if area_m2 is None or U_kWm2K is None:
            raise ValueError("[heat_exchanger] area_m2 and U_kWm2K required for calc_mode='area'")
        hx_obj.Area        = area_m2
        hx_obj.OverallCoefficient = U_kWm2K * 1000.0   # DWSIM expects W/m²K
        print(f"[heat_exchanger] HX '{name}': A = {area_m2:.2f} m², U = {U_kWm2K:.4f} kW/m²K")

    # Pressure drops
    hx_obj.HotSidePressureDrop  = hot_pressure_drop_bar  * 1e5
    hx_obj.ColdSidePressureDrop = cold_pressure_drop_bar * 1e5

    # Create associated streams at sensible canvas positions
    hot_outlet   = _add_stream(sim, x_pos + 150, y_pos - 60,  f"{name}_HOT_OUT")
    cold_outlet  = _add_stream(sim, x_pos + 150, y_pos + 60,  f"{name}_COLD_OUT")

    return SimpleNamespace(
        obj                 = hx_obj,
        hot_outlet_stream   = hot_outlet,
        cold_outlet_stream  = cold_outlet,
        mode                = "hx",
        calc_mode           = calc_mode,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  RESULT EXTRACTOR  (mirrors _extract_stream in flash_master.py)
#  Call this AFTER the flowsheet is solved to get a plain dict from a stream.
# ─────────────────────────────────────────────────────────────────────────────

def hx_stream_results(stream_obj, stream_name: str, comp_names: list) -> dict:
    """
    Extract T, P, flows, enthalpy, and compositions from a solved outlet stream.

    Parameters
    ----------
    stream_obj  : DWSIM MaterialStream object (solved)
    stream_name : label for this stream in the results dict
    comp_names  : list of component name strings (from sim.SelectedCompounds.Keys)

    Returns
    -------
    dict with keys:
        stream, T_K, T_C, P_Pa, P_bar,
        molar_flow_molh, mass_flow_kgh, enthalpy_kJkmol,
        vapor_fraction, x_<CompName> for each component
    """
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
        "enthalpy_kJkmol":  round(_get(p0.enthalpy) * 1000, 4),
        "vapor_fraction":   round(_get(p2.molarfraction), 6),
    }

    for comp_name in comp_names:
        try:
            xval = _get(stream_obj.Phases[0].Compounds[comp_name].MoleFraction)
        except Exception:
            xval = 0.0
        result[f"x_{comp_name}"] = round(xval, 6)

    return result


def hx_duty_results(hx_obj, inlet_results: dict = None, outlet_results: dict = None) -> dict:
    """
    Compute heat duty from stream enthalpy difference (preferred) or DeltaQ fallback.
    For Heater/Cooler, pass inlet_results and outlet_results from hx_stream_results().
    For two-stream HX, pass hot_in and hot_out results.
    """
    def _get(val):
        if val is None:
            return 0.0
        try:
            return float(val)
        except Exception:
            return 0.0

    lmtd = 0.0

    # Preferred: enthalpy balance from solved stream dicts
    if inlet_results is not None and outlet_results is not None:
        H_in    = inlet_results["enthalpy_kJkmol"]       # kJ/kmol
        H_out   = outlet_results["enthalpy_kJkmol"]      # kJ/kmol
        F       = outlet_results["molar_flow_molh"]      # mol/h
        duty_kW = (H_out - H_in) * (F / 1000) / 3600    # kW
    else:
        duty_kW = _get(hx_obj.DeltaQ)

    # LMTD for two-stream HX only
    try:
        lmtd = _get(hx_obj.LMTD)
    except AttributeError:
        pass

    return {
        "duty_kW":  round(duty_kW,        2),
        "duty_kJh": round(duty_kW * 3600, 2),
        "LMTD_K":   round(lmtd,           4),
    }