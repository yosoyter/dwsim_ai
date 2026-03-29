# DWSIM Automation: Multicomponent Flash Calculator

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
  "property_package": "NRTL"
}
'''
task_data = json.loads(llm_output)

# 2. Initialize the DWSIM Wrapper
# IMPORTANT: Update this path to where DWSIM is installed on the Parallels VM!
DWSIM_PATH = r"C:\Users\rickyyu\AppData\Local\DWSIM\\"
print("Initializing DWSIM Engine...")
dwsim = DWSIMWrapper(DWSIM_PATH)

# 3. Setup Simulation Parameters based on JSON payload
components  = task_data["components"]   # dict of {name: mole_fraction}
pressure    = task_data["pressure_Pa"]
temp        = task_data["temperature_K"]
flash_type  = task_data["flash_type"].upper()
prop_pack   = task_data["property_package"]

# Validate mole fractions sum to 1.0
total_z = sum(components.values())
if abs(total_z - 1.0) > 1e-6:
    raise ValueError(f"Mole fractions must sum to 1.0 (got {total_z:.6f}). Please check the input payload.")

comp_names = list(components.keys())
print(f"\nSetting up multicomponent mixture: {', '.join(comp_names)}")
for name in comp_names:
    dwsim.add_compound(name)

print(f"Applying Thermodynamic Package: {prop_pack}")
dwsim.set_property_package(prop_pack)

# 4. Create a single stream to act as our flash vessel
stream_name = "MultiComp_Flash_Stream"
dwsim.add_material_stream(stream_name)

# Set feed composition from the payload dict
dwsim.set_composition(stream_name, components)

# 5. Run the appropriate flash type
#
#    Key difference vs. single-component flash:
#      PT  -> Rachford-Rice + K-values (Raoult/EOS) → genuine L+V split possible
#      PH  -> PT flash nested inside enthalpy objective function (Brent solver on T)
#      PS  -> PT flash nested inside entropy objective function  (Brent solver on T)
#      TV  -> bubble/dew point bracketing at fixed T, solve for P
#      PV  -> bubble/dew point bracketing at fixed P, solve for T

print(f"\nRunning {flash_type} Flash for [{', '.join(comp_names)}]")
print(f"Feed composition: { {k: round(v,3) for k,v in components.items()} }")
print(f"Conditions: {pressure} Pa, {temp} K")
print("-" * 75)

results = {}

if flash_type == "PT":
    # --- PT Flash ---
    # For a multicomponent mixture the solver uses K-values (Ki = yi/xi) from
    # the property package and iterates the Rachford-Rice equation to find the
    # vapor fraction V that satisfies material balance. This can yield any
    # V in [0, 1], unlike single-component PT which always returns a pure phase.
    result = dwsim.flash_PT(stream_name, pressure_pa=pressure, temperature_k=temp)

    V       = result["vapor_fraction"]
    L       = result["liquid_fraction"]
    x       = result["liquid_composition"]   # dict {component: xi}
    y       = result["vapor_composition"]    # dict {component: yi}
    K       = result["K_values"]             # dict {component: Ki}

    if V >= 0.9999:
        phase = "Vapor"
    elif V <= 0.0001:
        phase = "Liquid"
    else:
        phase = "Liquid + Vapor (Two-Phase)"

    results = {
        "flash_type":        flash_type,
        "T_K":               temp,
        "P_Pa":              pressure,
        "phase":             phase,
        "vapor_fraction":    round(V, 6),
        "liquid_fraction":   round(L, 6),
        "feed_composition":  components,
        "vapor_composition": {k: round(v, 6) for k, v in y.items()},
        "liquid_composition":{k: round(v, 6) for k, v in x.items()},
        "K_values":          {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Phase':<35} | {phase}")
    print(f"{'Vapor Fraction (V)':<35} | {V:.6f}")
    print(f"{'Liquid Fraction (L)':<35} | {L:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<12} | {'x (liquid)':<12} | {'y (vapor)':<12} | {'K = y/x'}")
    print("-" * 75)
    for comp in comp_names:
        z_i = components[comp]
        x_i = x.get(comp, 0.0)
        y_i = y.get(comp, 0.0)
        K_i = K.get(comp, 0.0)
        print(f"{comp:<15} | {z_i:<12.4f} | {x_i:<12.6f} | {y_i:<12.6f} | {K_i:.6f}")

elif flash_type == "TV":
    # --- TV Flash ---
    # Fix T and vapor fraction V; solve for the equilibrium pressure P.
    # The solver brackets between Pbubble (V=0) and Pdew (V=1) then
    # interpolates to the specified V.
    V_spec = task_data.get("vapor_fraction", 0.5)
    result  = dwsim.flash_TV(stream_name, temperature_k=temp, vapor_fraction=V_spec)

    P_eq = result["equilibrium_pressure_Pa"]
    x    = result["liquid_composition"]
    y    = result["vapor_composition"]
    K    = result["K_values"]

    results = {
        "flash_type":              flash_type,
        "T_K":                     temp,
        "V_spec":                  V_spec,
        "equilibrium_pressure_Pa": round(P_eq, 4),
        "feed_composition":        components,
        "vapor_composition":       {k: round(v, 6) for k, v in y.items()},
        "liquid_composition":      {k: round(v, 6) for k, v in x.items()},
        "K_values":                {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Specified Vapor Fraction':<35} | {V_spec:.4f}")
    print(f"{'Equilibrium Pressure (Pa)':<35} | {P_eq:.4f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<12} | {'x (liquid)':<12} | {'y (vapor)':<12} | {'K = y/x'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<12.4f} | {x.get(comp,0):<12.6f} | {y.get(comp,0):<12.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PV":
    # --- PV Flash ---
    # Fix P and vapor fraction V; solve for the equilibrium temperature T.
    # The solver brackets between Tbubble (V=0) and Tdew (V=1).
    V_spec = task_data.get("vapor_fraction", 0.5)
    result  = dwsim.flash_PV(stream_name, pressure_pa=pressure, vapor_fraction=V_spec)

    T_eq = result["equilibrium_temperature_K"]
    x    = result["liquid_composition"]
    y    = result["vapor_composition"]
    K    = result["K_values"]

    results = {
        "flash_type":                  flash_type,
        "P_Pa":                        pressure,
        "V_spec":                      V_spec,
        "equilibrium_temperature_K":   round(T_eq, 4),
        "feed_composition":            components,
        "vapor_composition":           {k: round(v, 6) for k, v in y.items()},
        "liquid_composition":          {k: round(v, 6) for k, v in x.items()},
        "K_values":                    {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Specified Vapor Fraction':<35} | {V_spec:.4f}")
    print(f"{'Equilibrium Temperature (K)':<35} | {T_eq:.4f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<12} | {'x (liquid)':<12} | {'y (vapor)':<12} | {'K = y/x'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<12.4f} | {x.get(comp,0):<12.6f} | {y.get(comp,0):<12.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PH":
    # --- PH Flash ---
    # Outer Brent solver on T; inner PT flash at each T candidate computes
    # the mixture enthalpy H(T) = L*Hl(T) + V*Hv(T) weighted by phase fractions
    # and molecular weights. Solver converges when H(T) = H_spec.
    H_spec = task_data.get("enthalpy_kJ_kg", 500.0)
    result  = dwsim.flash_PH(stream_name, pressure_pa=pressure, enthalpy_kJ_kg=H_spec)

    T_calc = result["temperature_K"]
    V      = result["vapor_fraction"]
    L      = result["liquid_fraction"]
    x      = result["liquid_composition"]
    y      = result["vapor_composition"]
    K      = result["K_values"]
    phase  = result["phase"]

    results = {
        "flash_type":         flash_type,
        "P_Pa":               pressure,
        "H_spec_kJ_kg":       H_spec,
        "T_calc_K":           round(T_calc, 4),
        "phase":              phase,
        "vapor_fraction":     round(V, 6),
        "liquid_fraction":    round(L, 6),
        "feed_composition":   components,
        "vapor_composition":  {k: round(v, 6) for k, v in y.items()},
        "liquid_composition": {k: round(v, 6) for k, v in x.items()},
        "K_values":           {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Specified Enthalpy (kJ/kg)':<35} | {H_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<35} | {T_calc:.4f}")
    print(f"{'Phase':<35} | {phase}")
    print(f"{'Vapor Fraction':<35} | {V:.6f}")
    print(f"{'Liquid Fraction':<35} | {L:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<12} | {'x (liquid)':<12} | {'y (vapor)':<12} | {'K = y/x'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<12.4f} | {x.get(comp,0):<12.6f} | {y.get(comp,0):<12.6f} | {K.get(comp,0):.6f}")

elif flash_type == "PS":
    # --- PS Flash ---
    # Same structure as PH but the objective function minimises the entropy
    # error S(T) - S_spec instead of enthalpy error.
    S_spec = task_data.get("entropy_kJ_kg_K", 1.5)
    result  = dwsim.flash_PS(stream_name, pressure_pa=pressure, entropy_kJ_kg_K=S_spec)

    T_calc = result["temperature_K"]
    V      = result["vapor_fraction"]
    L      = result["liquid_fraction"]
    x      = result["liquid_composition"]
    y      = result["vapor_composition"]
    K      = result["K_values"]
    phase  = result["phase"]

    results = {
        "flash_type":         flash_type,
        "P_Pa":               pressure,
        "S_spec_kJ_kg_K":     S_spec,
        "T_calc_K":           round(T_calc, 4),
        "phase":              phase,
        "vapor_fraction":     round(V, 6),
        "liquid_fraction":    round(L, 6),
        "feed_composition":   components,
        "vapor_composition":  {k: round(v, 6) for k, v in y.items()},
        "liquid_composition": {k: round(v, 6) for k, v in x.items()},
        "K_values":           {k: round(v, 6) for k, v in K.items()},
    }

    print(f"{'Specified Entropy (kJ/kg.K)':<35} | {S_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<35} | {T_calc:.4f}")
    print(f"{'Phase':<35} | {phase}")
    print(f"{'Vapor Fraction':<35} | {V:.6f}")
    print(f"{'Liquid Fraction':<35} | {L:.6f}")
    print("-" * 75)
    print(f"{'Component':<15} | {'z (feed)':<12} | {'x (liquid)':<12} | {'y (vapor)':<12} | {'K = y/x'}")
    print("-" * 75)
    for comp in comp_names:
        print(f"{comp:<15} | {components[comp]:<12.4f} | {x.get(comp,0):<12.6f} | {y.get(comp,0):<12.6f} | {K.get(comp,0):.6f}")

else:
    raise ValueError(f"Unsupported flash_type '{flash_type}'. Choose from: PT, PH, PS, TV, PV.")

print("-" * 75)

# 6. Export the data for Group 3 (Validation & Plotting)
output_filename = "multicomp_flash_results.json"
with open(output_filename, "w") as outfile:
    json.dump({
        "metadata": task_data,
        "results":  results
    }, outfile, indent=4)

print(f"\nSuccess! Exported flash results to {output_filename} for Group 3.")
