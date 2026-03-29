# DWSIM Automation: Single Component Flash Calculator

import json
from DWSIM_Library import DWSIMWrapper

# 1. Simulate the parsed JSON payload received from Group 1 (the LLM)
llm_output = '''
{
  "component": "Water",
  "pressure_Pa": 101325.0,
  "temperature_K": 373.15,
  "flash_type": "PT",
  "property_package": "Steam Tables"
}
'''
task_data = json.loads(llm_output)

# 2. Initialize the DWSIM Wrapper
# IMPORTANT: Update this path to where DWSIM is installed on the Parallels VM!
DWSIM_PATH = r"C:\Users\rickyyu\AppData\Local\DWSIM\\"
print("Initializing DWSIM Engine...")
dwsim = DWSIMWrapper(DWSIM_PATH)

# 3. Setup Simulation Parameters based on JSON payload
comp      = task_data["component"]
pressure  = task_data["pressure_Pa"]
temp      = task_data["temperature_K"]
flash_type = task_data["flash_type"].upper()
prop_pack = task_data["property_package"]

print(f"\nSetting up single-component system: {comp}")
dwsim.add_compound(comp)

print(f"Applying Thermodynamic Package: {prop_pack}")
dwsim.set_property_package(prop_pack)

# 4. Create a single stream to act as our flash vessel
stream_name = "SingleComp_Flash_Stream"
dwsim.add_material_stream(stream_name)

# Pure component: mole fraction = 1.0
dwsim.set_composition(stream_name, {comp: 1.0})

# 5. Run the appropriate flash type and interpret the phase result
#
#    Flash logic mirrors SingleCompFlash.vb:
#      PT  -> compare Pvap vs P, and T vs Tfus to determine phase
#      PH  -> locate enthalpy between saturation / fusion enthalpies
#      PS  -> locate entropy between saturation / fusion entropies
#      TV  -> return Psat; split between liquid+vapor or solid+vapor
#      PV  -> return Tsat; split between liquid+vapor or solid+vapor

print(f"\nRunning {flash_type} Flash for {comp} at {pressure} Pa, {temp} K...")
print("-" * 65)

results = {}

if flash_type == "PT":
    # --- PT Flash ---
    # Phase determined by: Pvap vs P, and T vs Tfus
    result   = dwsim.flash_PT(stream_name, pressure_pa=pressure, temperature_k=temp)
    Pvap     = result["vapor_pressure_Pa"]
    Tfus     = result["fusion_temperature_K"]
    is_solid = result.get("is_solid", False)

    if is_solid:
        phase    = "Solid"
        L, V, S  = 0.0, 0.0, 1.0
    elif Pvap > pressure:
        phase    = "Vapor"
        L, V, S  = 0.0, 1.0, 0.0
    elif temp < Tfus:
        phase    = "Solid"
        L, V, S  = 0.0, 0.0, 1.0
    else:
        phase    = "Liquid"
        L, V, S  = 1.0, 0.0, 0.0

    results = {
        "flash_type":            flash_type,
        "T_K":                   temp,
        "P_Pa":                  pressure,
        "vapor_pressure_Pa":     Pvap,
        "fusion_temperature_K":  Tfus,
        "phase":                 phase,
        "liquid_fraction":       L,
        "vapor_fraction":        V,
        "solid_fraction":        S,
    }

    print(f"{'Property':<30} | {'Value'}")
    print("-" * 65)
    print(f"{'Temperature (K)':<30} | {temp}")
    print(f"{'Pressure (Pa)':<30} | {pressure}")
    print(f"{'Vapor Pressure (Pa)':<30} | {Pvap:.4f}")
    print(f"{'Fusion Temperature (K)':<30} | {Tfus:.4f}")
    print(f"{'Predicted Phase':<30} | {phase}")
    print(f"{'Liquid Fraction':<30} | {L:.3f}")
    print(f"{'Vapor Fraction':<30} | {V:.3f}")
    print(f"{'Solid Fraction':<30} | {S:.3f}")

elif flash_type == "TV":
    # --- TV Flash ---
    # Given T and vapor fraction V, find Psat; split is L+V or S+V
    V_spec   = task_data.get("vapor_fraction", 0.5)
    result   = dwsim.flash_TV(stream_name, temperature_k=temp, vapor_fraction=V_spec)
    Psat     = result["saturation_pressure_Pa"]
    Tfus     = result["fusion_temperature_K"]

    if temp > Tfus:
        phase = "Liquid + Vapor"
        L, V, S = 1.0 - V_spec, V_spec, 0.0
    else:
        phase = "Solid + Vapor"
        L, V, S = 0.0, V_spec, 1.0 - V_spec

    results = {
        "flash_type":            flash_type,
        "T_K":                   temp,
        "V_spec":                V_spec,
        "saturation_pressure_Pa": Psat,
        "fusion_temperature_K":  Tfus,
        "phase":                 phase,
        "liquid_fraction":       L,
        "vapor_fraction":        V,
        "solid_fraction":        S,
    }

    print(f"{'Property':<30} | {'Value'}")
    print("-" * 65)
    print(f"{'Temperature (K)':<30} | {temp}")
    print(f"{'Specified Vapor Fraction':<30} | {V_spec:.3f}")
    print(f"{'Saturation Pressure (Pa)':<30} | {Psat:.4f}")
    print(f"{'Fusion Temperature (K)':<30} | {Tfus:.4f}")
    print(f"{'Phase Region':<30} | {phase}")
    print(f"{'Liquid Fraction':<30} | {L:.3f}")
    print(f"{'Vapor Fraction':<30} | {V:.3f}")
    print(f"{'Solid Fraction':<30} | {S:.3f}")

elif flash_type == "PV":
    # --- PV Flash ---
    # Given P and vapor fraction V, find Tsat; split is L+V or S+V
    V_spec   = task_data.get("vapor_fraction", 0.5)
    result   = dwsim.flash_PV(stream_name, pressure_pa=pressure, vapor_fraction=V_spec)
    Tsat     = result["saturation_temperature_K"]
    Tfus     = result["fusion_temperature_K"]

    if Tsat > Tfus:
        phase = "Liquid + Vapor"
        L, V, S = 1.0 - V_spec, V_spec, 0.0
    else:
        phase = "Solid + Vapor"
        L, V, S = 0.0, V_spec, 1.0 - V_spec

    results = {
        "flash_type":               flash_type,
        "P_Pa":                     pressure,
        "V_spec":                   V_spec,
        "saturation_temperature_K": Tsat,
        "fusion_temperature_K":     Tfus,
        "phase":                    phase,
        "liquid_fraction":          L,
        "vapor_fraction":           V,
        "solid_fraction":           S,
    }

    print(f"{'Property':<30} | {'Value'}")
    print("-" * 65)
    print(f"{'Pressure (Pa)':<30} | {pressure}")
    print(f"{'Specified Vapor Fraction':<30} | {V_spec:.3f}")
    print(f"{'Saturation Temperature (K)':<30} | {Tsat:.4f}")
    print(f"{'Fusion Temperature (K)':<30} | {Tfus:.4f}")
    print(f"{'Phase Region':<30} | {phase}")
    print(f"{'Liquid Fraction':<30} | {L:.3f}")
    print(f"{'Vapor Fraction':<30} | {V:.3f}")
    print(f"{'Solid Fraction':<30} | {S:.3f}")

elif flash_type == "PH":
    # --- PH Flash ---
    # Locate enthalpy relative to saturation / fusion enthalpies to determine
    # phase and temperature (uses Brent solver internally for superheated /
    # subcooled / solid regions, matching SingleCompFlash.vb OBJ_FUNC_PH_FLASH)
    H_spec = task_data.get("enthalpy_kJ_kg", 2676.0)
    result  = dwsim.flash_PH(stream_name, pressure_pa=pressure, enthalpy_kJ_kg=H_spec)

    T_calc = result["temperature_K"]
    L      = result["liquid_fraction"]
    V      = result["vapor_fraction"]
    S      = result["solid_fraction"]
    phase  = result["phase"]

    results = {
        "flash_type":      flash_type,
        "P_Pa":            pressure,
        "H_spec_kJ_kg":    H_spec,
        "T_calc_K":        round(T_calc, 4),
        "phase":           phase,
        "liquid_fraction": round(L, 4),
        "vapor_fraction":  round(V, 4),
        "solid_fraction":  round(S, 4),
    }

    print(f"{'Property':<30} | {'Value'}")
    print("-" * 65)
    print(f"{'Pressure (Pa)':<30} | {pressure}")
    print(f"{'Specified Enthalpy (kJ/kg)':<30} | {H_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<30} | {T_calc:.4f}")
    print(f"{'Phase':<30} | {phase}")
    print(f"{'Liquid Fraction':<30} | {L:.4f}")
    print(f"{'Vapor Fraction':<30} | {V:.4f}")
    print(f"{'Solid Fraction':<30} | {S:.4f}")

elif flash_type == "PS":
    # --- PS Flash ---
    # Locate entropy relative to saturation / fusion entropies to determine
    # phase and temperature (uses Brent solver internally, matching
    # SingleCompFlash.vb OBJ_FUNC_PS_FLASH)
    S_spec = task_data.get("entropy_kJ_kg_K", 7.355)
    result  = dwsim.flash_PS(stream_name, pressure_pa=pressure, entropy_kJ_kg_K=S_spec)

    T_calc = result["temperature_K"]
    L      = result["liquid_fraction"]
    V      = result["vapor_fraction"]
    Sx     = result["solid_fraction"]
    phase  = result["phase"]

    results = {
        "flash_type":        flash_type,
        "P_Pa":              pressure,
        "S_spec_kJ_kg_K":   S_spec,
        "T_calc_K":          round(T_calc, 4),
        "phase":             phase,
        "liquid_fraction":   round(L, 4),
        "vapor_fraction":    round(V, 4),
        "solid_fraction":    round(Sx, 4),
    }

    print(f"{'Property':<30} | {'Value'}")
    print("-" * 65)
    print(f"{'Pressure (Pa)':<30} | {pressure}")
    print(f"{'Specified Entropy (kJ/kg.K)':<30} | {S_spec:.4f}")
    print(f"{'Calculated Temperature (K)':<30} | {T_calc:.4f}")
    print(f"{'Phase':<30} | {phase}")
    print(f"{'Liquid Fraction':<30} | {L:.4f}")
    print(f"{'Vapor Fraction':<30} | {V:.4f}")
    print(f"{'Solid Fraction':<30} | {Sx:.4f}")

else:
    raise ValueError(f"Unsupported flash_type '{flash_type}'. Choose from: PT, PH, PS, TV, PV.")

print("-" * 65)

# 6. Export the data for Group 3 (Validation & Plotting)
output_filename = "single_comp_flash_results.json"
with open(output_filename, "w") as outfile:
    json.dump({
        "metadata": task_data,
        "results":  results
    }, outfile, indent=4)

print(f"\nSuccess! Exported flash results to {output_filename} for Group 3.")
