# DWSIM Automation: Binary Txy Diagram Generator 

import json
from DWSIM_Library import DWSIMWrapper

# 1. Simulate the parsed JSON payload received from Group 1 (the LLM)
llm_output = '''
{
  "component_1": "Ethanol",
  "component_2": "Water",
  "pressure_Pa": 101325.0,
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
comp1 = task_data["component_1"]
comp2 = task_data["component_2"]
pressure = task_data["pressure_Pa"]
prop_pack = task_data["property_package"]

print(f"\nSetting up binary mixture: {comp1} and {comp2}")
dwsim.add_compound(comp1)
dwsim.add_compound(comp2)

print(f"Applying Thermodynamic Package: {prop_pack}")
dwsim.set_property_package(prop_pack)

# 4. Create a single stream to act as our flash vessel
stream_name = "Txy_Flash_Stream"
dwsim.add_material_stream(stream_name)

print(f"\nRunning Txy Sweep at {pressure} Pa...")
print("-" * 65)
print(f"{'x (' + comp1 + ')':<15} | {'T_bubble (K)':<15} | {'T_dew (K)':<15}")
print("-" * 65)

# 5. Execute the Txy Sweep
results_table = []
steps = 10 # Change to 20 or 50 for a smoother graph later

for i in range(steps + 1):
    # Calculate mole fractions
    x1 = i / steps
    x2 = 1.0 - x1
    
    # Update stream composition using our library method
    dwsim.set_composition(stream_name, {comp1: x1, comp2: x2})
    
    # Calculate Bubble Point (Vapor Fraction = 0.0)
    t_bubble = dwsim.calculate_Txy_point(stream_name, pressure_pa=pressure, vapor_fraction=0.0)
    
    # Calculate Dew Point (Vapor Fraction = 1.0)
    t_dew = dwsim.calculate_Txy_point(stream_name, pressure_pa=pressure, vapor_fraction=1.0)
    
    # Save the data point
    results_table.append({
        f"x_{comp1}": round(x1, 3),
        f"x_{comp2}": round(x2, 3),
        "T_bubble_K": round(t_bubble, 2),
        "T_dew_K": round(t_dew, 2)
    })
    
    # Print progress to terminal
    print(f"{x1:<15.3f} | {t_bubble:<15.2f} | {t_dew:<15.2f}")

print("-" * 65)

# 6. Export the data for Group 3 (Validation & Plotting)
output_filename = "txy_results.json"
with open(output_filename, "w") as outfile:
    json.dump({
        "metadata": task_data,
        "data": results_table
    }, outfile, indent=4)
    
print(f"\nSuccess! Exported simulation data to {output_filename} for Group 3.")