# DWSIM Automation Library
# A modular interface for LLM-driven DWSIM execution

import os
import pythoncom

# Critical: COM Initialization for Windows (Parallels) environment
pythoncom.CoInitialize()

import clr
from System.IO import Directory, Path
from System import Environment

class DWSIMWrapper:
    def __init__(self, dwsim_path: str):
        """Initializes the DWSIM automation engine and loads required assemblies."""
        self.dwsim_path = dwsim_path
        if not os.path.isdir(self.dwsim_path):
            raise FileNotFoundError(f"DWSIM path not found: {self.dwsim_path}")
            
        self._load_assemblies()
        
        # Import DWSIM classes after assemblies are loaded
        from DWSIM.Automation import Automation3
        from DWSIM.GlobalSettings import Settings
        
        self.Settings = Settings
        self.interf = Automation3()
        self.sim = self.interf.CreateFlowsheet()
        self.objects = {} # Keep track of created flowsheet objects

    def _load_assemblies(self):
        """Loads all necessary .NET DLLs from the DWSIM directory."""
        dlls = [
            "CapeOpen.dll", "DWSIM.Automation.dll", "DWSIM.Interfaces.dll",
            "DWSIM.GlobalSettings.dll", "DWSIM.SharedClasses.dll",
            "DWSIM.Thermodynamics.dll", "DWSIM.Thermodynamics.ThermoC.dll",
            "DWSIM.UnitOperations.dll", "DWSIM.Inspector.dll", "System.Buffers.dll"
        ]
        for dll in dlls:
            clr.AddReference(os.path.join(self.dwsim_path, dll))
        Directory.SetCurrentDirectory(self.dwsim_path)

    def add_compound(self, compound_name: str):
        """Adds a chemical compound to the simulation."""
        compound = self.sim.AvailableCompounds[compound_name]
        self.sim.SelectedCompounds.Add(compound.Name, compound)
        return compound.Name

    def set_property_package(self, package_type="SteamTables"):
        """Sets the thermodynamic property package."""
        from DWSIM.Thermodynamics import PropertyPackages
        if package_type == "SteamTables":
            package = PropertyPackages.SteamTablesPropertyPackage()
        # Add other packages (e.g., Peng-Robinson) here as needed
        else:
            raise ValueError(f"Unsupported property package: {package_type}")
        
        self.sim.AddPropertyPackage(package)
        return package.ComponentName

    def add_material_stream(self, name: str, temp_k: float, press_pa: float, mass_flow_kgs: float):
        """Creates a material stream and sets its initial conditions."""
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        
        obj = self.sim.AddObject(ObjectType.MaterialStream, 0, 0, name).GetAsObject()
        obj.SetTemperature(temp_k)
        obj.SetPressure(press_pa)
        obj.SetMassFlow(mass_flow_kgs)
        self.objects[name] = obj
        return obj

    def add_energy_stream(self, name: str):
        """Creates an energy stream."""
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        obj = self.sim.AddObject(ObjectType.EnergyStream, 0, 0, name).GetAsObject()
        self.objects[name] = obj
        return obj

    def add_heater(self, name: str, target_temp_k: float):
        """Creates a heater and sets its target outlet temperature."""
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        from DWSIM.UnitOperations import UnitOperations
        
        obj = self.sim.AddObject(ObjectType.Heater, 0, 0, name).GetAsObject()
        obj.CalcMode = UnitOperations.Heater.CalculationMode.OutletTemperature
        obj.OutletTemperature = target_temp_k
        self.objects[name] = obj
        return obj

    def connect_objects(self, source_name: str, target_name: str):
        """Connects two flowsheet objects automatically."""
        source = self.objects[source_name]
        target = self.objects[target_name]
        self.sim.ConnectObjects(source.GraphicObject, target.GraphicObject, -1, -1)

    def solve(self):
        """Runs the flowsheet calculation and checks for errors."""
        self.Settings.SolverMode = 0
        errors = self.interf.CalculateFlowsheet2(self.sim)
        if errors is not None and errors.Count > 0:
            error_list = [str(e) for e in errors]
            return False, error_list
        return True, []

    def get_stream_results(self, name: str):
        """Extracts key thermodynamic properties from a material stream."""
        obj = self.objects[name]
        return {
            "Temperature_K": obj.GetTemperature(),
            "Pressure_Pa": obj.GetPressure(),
            "MassFlow_kg_s": obj.GetMassFlow(),
            "MolarFlow_mol_s": obj.GetMolarFlow()
        }

    def get_heater_results(self, name: str):
        """Extracts performance metrics from a heater."""
        obj = self.objects[name]
        return {
            "HeatDuty_kW": obj.DeltaQ,
            "PressureDrop_Pa": obj.DeltaP,
            "OutletTemperature_K": obj.OutletTemperature
        }

    def save_flowsheet(self, filename: str):
        """Saves the simulation state to a .dwxmz file on the Desktop."""
        desktop = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
        file_path = Path.Combine(desktop, filename)
        self.interf.SaveFlowsheet(self.sim, file_path, True)
        return file_path