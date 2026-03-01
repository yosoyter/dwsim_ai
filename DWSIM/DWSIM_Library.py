# Modular interface for LLM-driven DWSIM execution

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

    def set_property_package(self, package_type: str):
        """Sets the thermodynamic property package."""
        from DWSIM.Thermodynamics import PropertyPackages
        
        # Expanded to support common property packages for Txy analysis
        if package_type == "NRTL":
            package = PropertyPackages.NRTLPropertyPackage()
        elif package_type == "Peng-Robinson":
            package = PropertyPackages.PengRobinsonPropertyPackage()
        elif package_type == "UNIFAC":
            package = PropertyPackages.UNIFACPropertyPackage()
        elif package_type == "Raoults_Law":
            package = PropertyPackages.RaoultsLawPropertyPackage()
        elif package_type == "SteamTables":
            package = PropertyPackages.SteamTablesPropertyPackage()
        else:
            raise ValueError(f"Unsupported property package: {package_type}")
        
        self.sim.AddPropertyPackage(package)
        return package.ComponentName

    def add_material_stream(self, name: str):
        """Creates a basic material stream."""
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        obj = self.sim.AddObject(ObjectType.MaterialStream, 0, 0, name).GetAsObject()
        self.objects[name] = obj
        return obj

    def set_composition(self, stream_name: str, comp_dict: dict):
        """
        Sets the mole fractions for a stream. 
        Example comp_dict: {"Ethanol": 0.3, "Water": 0.7}
        """
        obj = self.objects[stream_name]
        
        # DWSIM requires an array of doubles matching the order of SelectedCompounds
        comp_array = [0.0] * self.sim.SelectedCompounds.Count
        
        for i, comp in enumerate(self.sim.SelectedCompounds.Values):
            if comp.Name in comp_dict:
                comp_array[i] = float(comp_dict[comp.Name])
                
        obj.SetOverallComposition(comp_array)

    def calculate_Txy_point(self, stream_name: str, pressure_pa: float, vapor_fraction: float):
        """
        Forces a Flash calculation based on Pressure and Vapor Fraction.
        vapor_fraction = 0.0 -> Calculates Bubble Point (Liquid starts to boil)
        vapor_fraction = 1.0 -> Calculates Dew Point (Gas starts to condense)
        """
        from DWSIM.Interfaces.Enums import StreamSpec
        obj = self.objects[stream_name]
        
        # Change the flash calculation specification
        obj.SpecType = StreamSpec.Pressure_and_Vapor_Fraction
        
        # Set parameters
        obj.SetPressure(pressure_pa)
        obj.VaporFraction = vapor_fraction
        
        # Run calculation just for this stream
        obj.Calculate()
        
        # Return the temperature DWSIM calculated to satisfy those conditions
        return obj.GetTemperature()