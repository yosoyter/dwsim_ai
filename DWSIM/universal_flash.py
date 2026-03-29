# DWSIM Automation: Universal Multicomponent Flash Calculator
# Logic based on DWSIM UniversalFlash.vb by Daniel Wagner O. de Medeiros & Gregor Reichert

import json
from DWSIM_Library import DWSIMWrapper

# 1. Simulate the parsed JSON payload received from Group 1 (the LLM)
llm_output = '''
{
  "components": {
    "Ethanol": 0.4,
    "Water":   0.4,
    "Acetone": 0.2
  },
  "pressure_Pa": 101325.0,
  "temperature_K": 360.0,
  "flash_type": "PT",
  "equilibrium_type": "Default",
  "use_IO_flash": false,
  "failsafe_mode": 0,
  "property_package": "NRTL"
}
'''
task_data = json.loads(llm_output)

# --- equilibrium_type options ---
# "Default"  -> run heuristics test to auto-select from below
# "VLE"      -> Vapour-Liquid Equilibrium         (NestedLoops or InsideOut)
# "VLLE"     -> Vapour-Liquid-Liquid Equilibrium  (NestedLoops3PV3 or InsideOut3P)
# "SVLE"     -> Solid-Vapour-Liquid Equilibrium   (NestedLoopsSLE)
# "SVLLE"    -> Solid-Vapour-Liquid-Liquid        (NestedLoopsSVLLE)
# "NoFlash"  -> skips equilibrium, assigns phases by Pvap vs P only
#
# --- failsafe_mode options (used when primary solver throws) ---
# 0 -> Rigorous VLE fallback  (NestedLoops with same property package)
# 1 -> Ideal VLE fallback     (NestedLoops with Raoult / ideal package)
# 2 -> NoFlash fallback       (Pvap-only phase assignment, no iteration)
# 3 -> Re-raise the exception (no fallback)
#
# --- use_IO_flash ---
# false -> Nested Loops solver  (default, more robust)
# true  -> Inside-Out solver    (faster for large component sets)

# 2. Initialize the DWSIM Wrapper
# IMPORTANT: Update this path to where DWSIM is installed on the Parallels VM!
DWSIM_PATH = r"C:\Users\rickyyu\AppData\Local\DWSIM\\"
print("Initializing DWSIM Engine...")
dwsim = DWSIMWrapper(DWSIM_PATH)

# 3. Setup Simulation Parameters based on JSON payload
components   = task_data["components"]          # dict of {name: mole_fraction}
pressure     = task_data["pressure_Pa"]
temp         = task_data["temperature_K"]
flash_type   = task_data["flash_type"].upper()
eq_type      = task_data["equilibrium_type"]    # heuristics selector
use_io       = task_data["use_IO_flash"]        # solver backend switch
failsafe     = task_data["failsafe_mode"]       # fallback strategy
prop_pack    = task_data["property_package"]

# Validate mole fractions sum to 1.0
total_z = sum(components.values())
if abs(total_z - 1.0) > 1e-6:
    raise ValueError(f"Mole fractions must sum to 1.0 (got {total_z:.6f}). Please check the input payload.")

comp_names = list(components.keys())
print(f"\nSetting up mixture: {', '.join(comp_names)}")
for name in comp_names:
    dwsim.add_compound(name)

print(f"Applying Thermodynamic Package: {prop_pack}")
dwsim.set_property_package(prop_pack)

# 4. Create a single stream to act as our flash vessel
stream_name = "Universal_Flash_Stream"
dwsim.add_material_stream(stream_name)
dwsim.set_composition(stream_name, components)

# 5. Run the heuristics test when equilibrium_type is "Default"
#    Mirrors UniversalFlash.vb PerformHeuristicsTest():
#      - SolidPhase     -> any component has Tfus > T or is flagged IsSolid
#      - LiquidPhaseSplit -> miscibility gap detected (e.g. NRTL activity > threshold)
#    Result maps to: VLE / VLLE / SVLE / SVLLE

if eq_type == "Default":
    print(f"\nRunning Phase Heuristics Test...")
    heuristics = dwsim.perform_heuristics_test(stream_name, pressure_pa=pressure, temperature_k=temp)

    has_solid        = heuristics["solid_phase"]
    has_liquid_split = heuristics["liquid_phase_split"]
    has_forced_solid = heuristics["forced_solids"]

    if has_forced_solid or (has_solid and has_liquid_split):
        eq_type = "SVLLE"
    elif has_solid:
        eq_type = "SVLE"
    elif has_liquid_split:
        eq_type = "VLLE"
    else:
        eq_type = "VLE"

    print(f"Heuristics Result -> Selected Equilibrium Type: {eq_type}")
else:
    print(f"\nEquilibrium Type manually set to: {eq_type}")

# Solver backend label for terminal output
solver_label = "Inside-Out" if use_io else "Nested Loops"
print(f"Solver Backend: {solver_label}")

# 6. Execute the flash with the selected equilibrium type and fail-safe chain
#
#    Fail-safe chain (mirrors UniversalFlash.vb):
#      On solver exception -> check failsafe_mode:
#        0 -> retry with NestedLoops + same property package (Rigorous VLE)
#        1 -> retry with NestedLoops + Raoult/ideal package  (Ideal VLE)
#        2 -> fall back to NoFlash (Pvap-only, no iteration)
#        3 -> re-raise the original exception

print(f"\nRunning {flash_type} Flash [{eq_type}] for [{', '.join(comp_names)}]")
print(f"Feed composition: { {k: round(v, 3) for k, v in components.items()} }")
print(f"Conditions: {pressure} Pa | {temp} K")
print("-" * 75)

FAILSAFE_LABELS = {
    0: "Rigorous VLE (NestedLoops, same property package)",
    1: "Ideal VLE (NestedLoops, Raoult/ideal package)",
    2: "NoFlash (Pvap-only phase assignment)",
    3: "None (re-raise exception)",
}

results = {}

if flash_type == "PT":
    # --- PT Flash ---
    # eq_type drives which sub-solver is called:
    #   VLE    -> NestedLoops.Flash_PT  or BostonBrittInsideOut.Flash_PT
    #   VLLE   -> NestedLoops3PV3       or BostonFournierInsideOut3P (or Immiscible variant)
    #   SVLE   -> NestedLoopsSLE.Flash_PT
    #   SVLLE  -> NestedLoopsSVLLE.Flash_PT
    #   NoFlash-> Pvap vs P per component, no iteration
    result = dwsim.flash_PT(
        stream_name,
        pressure_pa=pressure,
        temperature_k=temp,
        equilibrium_type=eq_type,
        use_io=use_io,
        failsafe_mode=failsafe,
    )

    V    = result["vapor_fraction"]
    L1   = result["liquid1_fraction"]
    L2   = result.get("liquid2_fraction", 0.0)   # VLLE / SVLLE only
    S    = result.get("solid_fraction",   0.0)   # SVLE / SVLLE only
    x1   = result["liquid1_composition"]
    x2   = result.get("liquid2_composition", {})
    y    = result["vapor_composition"]
    xs   = result.get("solid_composition",   {})
    K    = result["K_values"]
    algo = result.get("algorithm_used", eq_type)
    fs   = result.get("failsafe_triggered", False)

    # Determine phase label from non-zero fractions
    active = []
    if V  > 1e-6: active.append("Vapor")
    if L1 > 1e-6: active.append("Liquid-1")
    if L2 > 1e-6: active.append("Liquid-2")
    if S  > 1e-6: active.append("Solid")
    phase = " + ".join(active) if active else "Unknown"

    results = {
        "flash_type":          flash_type,
        "equilibrium_type":    algo,
        "failsafe_triggered":  fs,
        "T_K":                 temp,
        "P_Pa":                pressure,
        "phase":               phase,
        "vapor_fraction":      round(V,  6),
        "liquid1_fraction":    round(L1, 6),
        "liquid2_fraction":    round(L2, 6),
        "solid_fraction":      round(S,  6),
        "feed_composition":    components,
        "vapor_composition":   {k: round(v, 6) for k, v in y.items()},
        "liquid1_composition": {k: round(v, 6) for k, v in x1.items()},
        "liquid2_composition": {k: round(v, 6) for k, v in x2.items()},
        "solid_composition":   {k: round(v, 6) for k, v in xs.items()},
        "K_values":            {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Algorithm Used':<30} | {algo}")
    if fs: print(f"{'*** Fail-Safe Triggered':<30} | {FAILSAFE_LABELS[failsafe]}")
    print(f"{'Phase':<30} | {phase}")
    print(f"{'Vapor Fraction (V)':<30} | {V:.6f}")
    print(f"{'Liquid-1 Fraction (L1)':<30} | {L1:.6f}")
    if L2 > 1e-6: print(f"{'Liquid-2 Fraction (L2)':<30} | {L2:.6f}")
    if S  > 1e-6: print(f"{'Solid Fraction (S)':<30}  | {S:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<10} | {'x1 (liq1)':<11} | {'x2 (liq2)':<11} | {'y (vap)':<10} | {'K = y/x1'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<10.4f} | {x1.get(comp,0):<11.6f} | {x2.get(comp,0):<11.6f} | {y.get(comp,0):<10.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PH":
    # --- PH Flash ---
    # Outer Brent solver on T; inner PT flash at each T candidate uses
    # the selected eq_type sub-solver. Fail-safe falls back to NestedLoops
    # if the primary sub-solver throws.
    H_spec = task_data.get("enthalpy_kJ_kg", 500.0)
    result  = dwsim.flash_PH(
        stream_name,
        pressure_pa=pressure,
        enthalpy_kJ_kg=H_spec,
        T_ref_k=temp,
        equilibrium_type=eq_type,
        use_io=use_io,
        failsafe_mode=failsafe,
    )

    T_calc = result["temperature_K"]
    V      = result["vapor_fraction"]
    L1     = result["liquid1_fraction"]
    L2     = result.get("liquid2_fraction", 0.0)
    S      = result.get("solid_fraction",   0.0)
    x1     = result["liquid1_composition"]
    x2     = result.get("liquid2_composition", {})
    y      = result["vapor_composition"]
    xs     = result.get("solid_composition",   {})
    K      = result["K_values"]
    algo   = result.get("algorithm_used", eq_type)
    fs     = result.get("failsafe_triggered", False)

    active = []
    if V  > 1e-6: active.append("Vapor")
    if L1 > 1e-6: active.append("Liquid-1")
    if L2 > 1e-6: active.append("Liquid-2")
    if S  > 1e-6: active.append("Solid")
    phase = " + ".join(active) if active else "Unknown"

    results = {
        "flash_type":          flash_type,
        "equilibrium_type":    algo,
        "failsafe_triggered":  fs,
        "P_Pa":                pressure,
        "H_spec_kJ_kg":        H_spec,
        "T_calc_K":            round(T_calc, 4),
        "phase":               phase,
        "vapor_fraction":      round(V,  6),
        "liquid1_fraction":    round(L1, 6),
        "liquid2_fraction":    round(L2, 6),
        "solid_fraction":      round(S,  6),
        "feed_composition":    components,
        "vapor_composition":   {k: round(v, 6) for k, v in y.items()},
        "liquid1_composition": {k: round(v, 6) for k, v in x1.items()},
        "liquid2_composition": {k: round(v, 6) for k, v in x2.items()},
        "solid_composition":   {k: round(v, 6) for k, v in xs.items()},
        "K_values":            {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Algorithm Used':<30} | {algo}")
    if fs: print(f"{'*** Fail-Safe Triggered':<30} | {FAILSAFE_LABELS[failsafe]}")
    print(f"{'Specified Enthalpy (kJ/kg)':<30} | {H_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<30} | {T_calc:.4f}")
    print(f"{'Phase':<30} | {phase}")
    print(f"{'Vapor Fraction':<30} | {V:.6f}")
    print(f"{'Liquid-1 Fraction':<30} | {L1:.6f}")
    if L2 > 1e-6: print(f"{'Liquid-2 Fraction':<30} | {L2:.6f}")
    if S  > 1e-6: print(f"{'Solid Fraction':<30}  | {S:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<10} | {'x1 (liq1)':<11} | {'x2 (liq2)':<11} | {'y (vap)':<10} | {'K = y/x1'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<10.4f} | {x1.get(comp,0):<11.6f} | {x2.get(comp,0):<11.6f} | {y.get(comp,0):<10.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PS":
    # --- PS Flash ---
    # Same outer Brent / inner PT structure as PH, but minimises entropy
    # error S(T) - S_spec. VLLE uses NestedLoops3PV3 or BostonFournierInsideOut3P.
    S_spec = task_data.get("entropy_kJ_kg_K", 1.5)
    result  = dwsim.flash_PS(
        stream_name,
        pressure_pa=pressure,
        entropy_kJ_kg_K=S_spec,
        T_ref_k=temp,
        equilibrium_type=eq_type,
        use_io=use_io,
        failsafe_mode=failsafe,
    )

    T_calc = result["temperature_K"]
    V      = result["vapor_fraction"]
    L1     = result["liquid1_fraction"]
    L2     = result.get("liquid2_fraction", 0.0)
    S      = result.get("solid_fraction",   0.0)
    x1     = result["liquid1_composition"]
    x2     = result.get("liquid2_composition", {})
    y      = result["vapor_composition"]
    xs     = result.get("solid_composition",   {})
    K      = result["K_values"]
    algo   = result.get("algorithm_used", eq_type)
    fs     = result.get("failsafe_triggered", False)

    active = []
    if V  > 1e-6: active.append("Vapor")
    if L1 > 1e-6: active.append("Liquid-1")
    if L2 > 1e-6: active.append("Liquid-2")
    if S  > 1e-6: active.append("Solid")
    phase = " + ".join(active) if active else "Unknown"

    results = {
        "flash_type":          flash_type,
        "equilibrium_type":    algo,
        "failsafe_triggered":  fs,
        "P_Pa":                pressure,
        "S_spec_kJ_kg_K":      S_spec,
        "T_calc_K":            round(T_calc, 4),
        "phase":               phase,
        "vapor_fraction":      round(V,  6),
        "liquid1_fraction":    round(L1, 6),
        "liquid2_fraction":    round(L2, 6),
        "solid_fraction":      round(S,  6),
        "feed_composition":    components,
        "vapor_composition":   {k: round(v, 6) for k, v in y.items()},
        "liquid1_composition": {k: round(v, 6) for k, v in x1.items()},
        "liquid2_composition": {k: round(v, 6) for k, v in x2.items()},
        "solid_composition":   {k: round(v, 6) for k, v in xs.items()},
        "K_values":            {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Algorithm Used':<30} | {algo}")
    if fs: print(f"{'*** Fail-Safe Triggered':<30} | {FAILSAFE_LABELS[failsafe]}")
    print(f"{'Specified Entropy (kJ/kg.K)':<30} | {S_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<30} | {T_calc:.4f}")
    print(f"{'Phase':<30} | {phase}")
    print(f"{'Vapor Fraction':<30} | {V:.6f}")
    print(f"{'Liquid-1 Fraction':<30} | {L1:.6f}")
    if L2 > 1e-6: print(f"{'Liquid-2 Fraction':<30} | {L2:.6f}")
    if S  > 1e-6: print(f"{'Solid Fraction':<30}  | {S:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<10} | {'x1 (liq1)':<11} | {'x2 (liq2)':<11} | {'y (vap)':<10} | {'K = y/x1'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<10.4f} | {x1.get(comp,0):<11.6f} | {x2.get(comp,0):<11.6f} | {y.get(comp,0):<10.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PV":
    # --- PV Flash ---
    # Fix P and vapor fraction V; solve for equilibrium T.
    # VLLE uses NestedLoops3PV3; SVLE/SVLLE use their respective nested loops.
    # Fail-safe chain identical to PT flash.
    V_spec = task_data.get("vapor_fraction", 0.5)
    result  = dwsim.flash_PV(
        stream_name,
        pressure_pa=pressure,
        vapor_fraction=V_spec,
        T_ref_k=temp,
        equilibrium_type=eq_type,
        use_io=use_io,
        failsafe_mode=failsafe,
    )

    T_eq = result["equilibrium_temperature_K"]
    L1   = result["liquid1_fraction"]
    L2   = result.get("liquid2_fraction", 0.0)
    S    = result.get("solid_fraction",   0.0)
    x1   = result["liquid1_composition"]
    x2   = result.get("liquid2_composition", {})
    y    = result["vapor_composition"]
    xs   = result.get("solid_composition",   {})
    K    = result["K_values"]
    algo = result.get("algorithm_used", eq_type)
    fs   = result.get("failsafe_triggered", False)

    results = {
        "flash_type":                flash_type,
        "equilibrium_type":          algo,
        "failsafe_triggered":        fs,
        "P_Pa":                      pressure,
        "V_spec":                    V_spec,
        "equilibrium_temperature_K": round(T_eq, 4),
        "liquid1_fraction":          round(L1, 6),
        "liquid2_fraction":          round(L2, 6),
        "solid_fraction":            round(S,  6),
        "feed_composition":          components,
        "vapor_composition":         {k: round(v, 6) for k, v in y.items()},
        "liquid1_composition":       {k: round(v, 6) for k, v in x1.items()},
        "liquid2_composition":       {k: round(v, 6) for k, v in x2.items()},
        "solid_composition":         {k: round(v, 6) for k, v in xs.items()},
        "K_values":                  {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Algorithm Used':<30} | {algo}")
    if fs: print(f"{'*** Fail-Safe Triggered':<30} | {FAILSAFE_LABELS[failsafe]}")
    print(f"{'Specified Vapor Fraction':<30} | {V_spec:.4f}")
    print(f"{'Equilibrium Temperature (K)':<30} | {T_eq:.4f}")
    if L2 > 1e-6: print(f"{'Liquid-2 Fraction':<30} | {L2:.6f}")
    if S  > 1e-6: print(f"{'Solid Fraction':<30}  | {S:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<10} | {'x1 (liq1)':<11} | {'x2 (liq2)':<11} | {'y (vap)':<10} | {'K = y/x1'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<10.4f} | {x1.get(comp,0):<11.6f} | {x2.get(comp,0):<11.6f} | {y.get(comp,0):<10.6f} | {K.get(comp,0):.6f}")

elif flash_type == "TV":
    # --- TV Flash ---
    # Fix T and vapor fraction V; solve for equilibrium P.
    # VLLE uses NestedLoops3PV3; SVLE/SVLLE use their respective nested loops.
    # Note: TV flash has no fail-safe chain in UniversalFlash.vb (no Try/Catch).
    V_spec = task_data.get("vapor_fraction", 0.5)
    result  = dwsim.flash_TV(
        stream_name,
        temperature_k=temp,
        vapor_fraction=V_spec,
        P_ref_pa=pressure,
        equilibrium_type=eq_type,
    )

    P_eq = result["equilibrium_pressure_Pa"]
    L1   = result["liquid1_fraction"]
    L2   = result.get("liquid2_fraction", 0.0)
    S    = result.get("solid_fraction",   0.0)
    x1   = result["liquid1_composition"]
    x2   = result.get("liquid2_composition", {})
    y    = result["vapor_composition"]
    xs   = result.get("solid_composition",   {})
    K    = result["K_values"]
    algo = result.get("algorithm_used", eq_type)

    results = {
        "flash_type":              flash_type,
        "equilibrium_type":        algo,
        "T_K":                     temp,
        "V_spec":                  V_spec,
        "equilibrium_pressure_Pa": round(P_eq, 4),
        "liquid1_fraction":        round(L1, 6),
        "liquid2_fraction":        round(L2, 6),
        "solid_fraction":          round(S,  6),
        "feed_composition":        components,
        "vapor_composition":       {k: round(v, 6) for k, v in y.items()},
        "liquid1_composition":     {k: round(v, 6) for k, v in x1.items()},
        "liquid2_composition":     {k: round(v, 6) for k, v in x2.items()},
        "solid_composition":       {k: round(v, 6) for k, v in xs.items()},
        "K_values":                {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Algorithm Used':<30} | {algo}")
    print(f"{'Specified Vapor Fraction':<30} | {V_spec:.4f}")
    print(f"{'Equilibrium Pressure (Pa)':<30} | {P_eq:.4f}")
    if L2 > 1e-6: print(f"{'Liquid-2 Fraction':<30} | {L2:.6f}")
    if S  > 1e-6: print(f"{'Solid Fraction':<30}  | {S:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<10} | {'x1 (liq1)':<11} | {'x2 (liq2)':<11} | {'y (vap)':<10} | {'K = y/x1'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<10.4f} | {x1.get(comp,0):<11.6f} | {x2.get(comp,0):<11.6f} | {y.get(comp,0):<10.6f} | {K.get(comp,0):.6f}")

else:
    raise ValueError(f"Unsupported flash_type '{flash_type}'. Choose from: PT, PH, PS, PV, TV.")

print("-" * 75)

# 7. Export the data for Group 3 (Validation & Plotting)
output_filename = "universal_flash_results.json"
with open(output_filename, "w") as outfile:
    json.dump({
        "metadata": task_data,
        "results":  results
    }, outfile, indent=4)

print(f"\nSuccess! Exported flash results to {output_filename} for Group 3.")
