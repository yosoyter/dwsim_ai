# execution_script.py
from DWSim_Library import DWSIMWrapper

# 1. Initialize
dwsim = DWSIMWrapper(r"C:\Users\rickyyu\AppData\Local\DWSIM\\")

# 2. Setup Thermodynamics
dwsim.add_compound("Water")
dwsim.set_property_package("SteamTables")

# 3. Create Objects
dwsim.add_material_stream("Water_Inlet", temp_k=300.0, press_pa=101325.0, mass_flow_kgs=100.0)
dwsim.add_material_stream("Heated_Outlet", temp_k=300.0, press_pa=101325.0, mass_flow_kgs=100.0)
dwsim.add_energy_stream("Heater_Power")
dwsim.add_heater("HTR-001", target_temp_k=400.0)

# 4. Connect
dwsim.connect_objects("Water_Inlet", "HTR-001")
dwsim.connect_objects("HTR-001", "Heated_Outlet")
dwsim.connect_objects("Heater_Power", "HTR-001")

# 5. Solve & Extract (The Validation Step)
success, errors = dwsim.solve()
if success:
    results = {
        "Inlet": dwsim.get_stream_results("Water_Inlet"),
        "Outlet": dwsim.get_stream_results("Heated_Outlet"),
        "Heater": dwsim.get_heater_results("HTR-001")
    }
    print("Simulation Validated:", results)
    dwsim.save_flowsheet("LLM_WaterHeater.dwxmz")
else:
    print("Errors occurred:", errors)