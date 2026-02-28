# =============================================================================
# DWSIM Automation: Simple Water Heater Simulation
# Run this script from VSCode (or any terminal) — no DWSIM GUI needed
# =============================================================================

# --- STEP A: COM Threading Initialization (Windows only) ---
# This MUST come before any other imports to avoid STA threading errors
import pythoncom
pythoncom.CoInitialize()

# --- STEP B: Import pythonnet and load .NET assemblies ---
import clr
import os
from System.IO import Directory, Path, File
from System import String, Environment

# --- STEP C: Set the DWSIM installation path ---
# IMPORTANT: Change this to YOUR DWSIM installation directory!
# Common locations:
#   "C:\\Users\\YourName\\AppData\\Local\\DWSIM\\"
#   "C:\\Users\\YourName\\AppData\\Local\\DWSIM8\\"
#   "C:\\Program Files\\DWSIM\\"

dwsimpath = r"C:\\Users\\rickyyu\\AppData\\Local\\DWSIM\\"

# Verify the path exists
if not os.path.isdir(dwsimpath):
    raise FileNotFoundError(
        f"DWSIM path not found: {dwsimpath}\n"
        f"Please update 'dwsimpath' to your DWSIM installation directory."
    )

# --- STEP D: Load all required DWSIM .NET assemblies ---
clr.AddReference(dwsimpath + "CapeOpen.dll")
clr.AddReference(dwsimpath + "DWSIM.Automation.dll")
clr.AddReference(dwsimpath + "DWSIM.Interfaces.dll")
clr.AddReference(dwsimpath + "DWSIM.GlobalSettings.dll")
clr.AddReference(dwsimpath + "DWSIM.SharedClasses.dll")
clr.AddReference(dwsimpath + "DWSIM.Thermodynamics.dll")
clr.AddReference(dwsimpath + "DWSIM.Thermodynamics.ThermoC.dll")
clr.AddReference(dwsimpath + "DWSIM.UnitOperations.dll")
clr.AddReference(dwsimpath + "DWSIM.Inspector.dll")
clr.AddReference(dwsimpath + "System.Buffers.dll")

# --- STEP E: Import DWSIM classes ---
from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
from DWSIM.Thermodynamics import Streams, PropertyPackages
from DWSIM.UnitOperations import UnitOperations
from DWSIM.Automation import Automation3
from DWSIM.GlobalSettings import Settings

# Set working directory to DWSIM path (required for internal resource loading)
Directory.SetCurrentDirectory(dwsimpath)

# =============================================================================
# BUILD THE FLOWSHEET
# =============================================================================

print("=" * 60)
print("DWSIM Automation: Water Heater Simulation")
print("=" * 60)

# --- 1. Create the automation manager and a blank flowsheet ---
print("\n[1] Initializing DWSIM automation engine...")
interf = Automation3()
sim = interf.CreateFlowsheet()
print("    Flowsheet created successfully.")

# --- 2. Add compounds to the simulation ---
print("[2] Adding compounds...")

# Method A: Direct dictionary access (works for all versions)
water = sim.AvailableCompounds["Water"]
sim.SelectedCompounds.Add(water.Name, water)
print("    Added: Water")

# --- 3. Add a thermodynamic property package ---
print("[3] Adding property package...")
stables = PropertyPackages.SteamTablesPropertyPackage()
sim.AddPropertyPackage(stables)
print("    Added: Steam Tables (IAPWS-IF97)")

# --- 4. Create flowsheet objects (streams + unit operations) ---
print("[4] Creating flowsheet objects...")

# AddObject(ObjectType, x_position, y_position, name)
# The x,y positions are for the PFD layout — they don't affect calculations
m1 = sim.AddObject(ObjectType.MaterialStream, 50, 50, "Water_Inlet")
m2 = sim.AddObject(ObjectType.MaterialStream, 250, 50, "Heated_Outlet")
e1 = sim.AddObject(ObjectType.EnergyStream, 150, 150, "Heater_Power")
h1 = sim.AddObject(ObjectType.Heater, 150, 50, "HTR-001")

# CRITICAL: Call .GetAsObject() to cast from ISimulationObject to the
# concrete class. Without this, you can't access specific properties
# like .DeltaQ, .SetTemperature(), etc.
m1 = m1.GetAsObject()
m2 = m2.GetAsObject()
e1 = e1.GetAsObject()
h1 = h1.GetAsObject()

print("    Created: Water_Inlet (Material Stream)")
print("    Created: Heated_Outlet (Material Stream)")
print("    Created: Heater_Power (Energy Stream)")
print("    Created: HTR-001 (Heater)")

# --- 5. Connect the objects ---
print("[5] Connecting flowsheet objects...")

# ConnectObjects(from_graphic_object, to_graphic_object, from_port, to_port)
# Use -1 for automatic port assignment
sim.ConnectObjects(m1.GraphicObject, h1.GraphicObject, -1, -1)   # inlet → heater
sim.ConnectObjects(h1.GraphicObject, m2.GraphicObject, -1, -1)   # heater → outlet
sim.ConnectObjects(e1.GraphicObject, h1.GraphicObject, -1, -1)   # energy → heater

print("    Water_Inlet → HTR-001 → Heated_Outlet")
print("    Heater_Power → HTR-001")

# Auto-arrange the PFD layout
sim.AutoLayout()

# --- 6. Set inlet stream conditions ---
print("[6] Setting inlet stream conditions...")

# Default properties if not set: T = 298.15 K, P = 101325 Pa, W = 1 kg/s
m1.SetTemperature(300.0)       # Temperature in Kelvin
m1.SetPressure(101325.0)       # Pressure in Pascals (1 atm)
m1.SetMassFlow(100.0)          # Mass flow in kg/s

print("    Temperature: 300 K (26.85 °C)")
print("    Pressure:    101,325 Pa (1 atm)")
print("    Mass Flow:   100 kg/s")

# --- 7. Set heater specifications ---
print("[7] Setting heater specifications...")

# Heater calculation modes:
#   OutletTemperature  — specify desired outlet T, calc heat duty
#   HeatAdded          — specify Q, calc outlet T
#   OutletVaporFraction — specify vapor fraction at outlet
h1.CalcMode = UnitOperations.Heater.CalculationMode.OutletTemperature
h1.OutletTemperature = 400.0   # Desired outlet temperature in Kelvin

print("    Mode: Outlet Temperature")
print("    Target Outlet Temperature: 400 K (126.85 °C)")

# =============================================================================
# SOLVE THE FLOWSHEET
# =============================================================================

print("\n[8] Solving flowsheet...")
Settings.SolverMode = 0  # 0 = synchronous solving

errors = interf.CalculateFlowsheet2(sim)

# Check for errors
if errors is not None and errors.Count > 0:
    print("\n*** SOLVER ERRORS ***")
    for e in errors:
        print(f"    {e}")
else:
    print("    Flowsheet solved successfully!")

# =============================================================================
# EXTRACT AND DISPLAY RESULTS
# =============================================================================

print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)

def fmt(val, unit=""):
    """Format a value that might be None."""
    if val is None:
        return "N/A"
    return f"{val:>12.2f} {unit}"

# Heater results
print(f"\n--- Heater (HTR-001) ---")
print(f"  Heat Duty (Q):       {fmt(h1.DeltaQ, 'kW')}")
print(f"  Pressure Drop (dP):  {fmt(h1.DeltaP, 'Pa')}")
print(f"  Outlet Temperature:  {fmt(h1.OutletTemperature, 'K')}")

# Inlet stream results
print(f"\n--- Inlet Stream (Water_Inlet) ---")
print(f"  Temperature:   {fmt(m1.GetTemperature(), 'K')}")
print(f"  Pressure:      {fmt(m1.GetPressure(), 'Pa')}")
print(f"  Mass Flow:     {fmt(m1.GetMassFlow(), 'kg/s')}")
print(f"  Molar Flow:    {fmt(m1.GetMolarFlow(), 'mol/s')}")

# Outlet stream results
print(f"\n--- Outlet Stream (Heated_Outlet) ---")
print(f"  Temperature:   {fmt(m2.GetTemperature(), 'K')}")
print(f"  Pressure:      {fmt(m2.GetPressure(), 'Pa')}")
print(f"  Mass Flow:     {fmt(m2.GetMassFlow(), 'kg/s')}")
print(f"  Molar Flow:    {fmt(m2.GetMolarFlow(), 'mol/s')}")

# Energy stream
print(f"\n--- Energy Stream (Heater_Power) ---")
try:
    print(f"  Energy Flow:   {fmt(e1.EnergyFlow, 'kW')}")
except:
    print(f"  Energy Flow:   N/A")


# =============================================================================
# SAVE THE SIMULATION FILE
# =============================================================================

print("\n[9] Saving simulation file...")

desktop_path = Environment.GetFolderPath(Environment.SpecialFolder.Desktop)
file_path = Path.Combine(desktop_path, "water_heater_simulation.dwxmz")
interf.SaveFlowsheet(sim, file_path, True)  # True = compressed format

print(f"    Saved to: {file_path}")
print(f"    (You can open this file in DWSIM GUI to inspect the flowsheet)")

# =============================================================================
# (OPTIONAL) EXPORT PFD AS PNG IMAGE
# =============================================================================

print("\n[10] Exporting PFD image...")

try:
    clr.AddReference(dwsimpath + "SkiaSharp.dll")
    clr.AddReference("System.Drawing")

    from SkiaSharp import SKBitmap, SKImage, SKCanvas, SKEncodedImageFormat
    from System.IO import MemoryStream
    from System.Drawing import Image
    from System.Drawing.Imaging import ImageFormat

    PFDSurface = sim.GetSurface()

    imgwidth = 1024
    imgheight = 768

    bmp = SKBitmap(imgwidth, imgheight)
    canvas = SKCanvas(bmp)
    PFDSurface.Center(imgwidth, imgheight)
    PFDSurface.ZoomAll(imgwidth, imgheight)
    PFDSurface.UpdateCanvas(canvas)

    d = SKImage.FromBitmap(bmp).Encode(SKEncodedImageFormat.Png, 100)
    memstream = MemoryStream()
    d.SaveTo(memstream)
    image = Image.FromStream(memstream)

    imgPath = Path.Combine(desktop_path, "water_heater_pfd.png")
    image.Save(imgPath, ImageFormat.Png)

    memstream.Dispose()
    canvas.Dispose()
    bmp.Dispose()

    print(f"    PFD image saved to: {imgPath}")

except Exception as ex:
    print(f"    PFD export skipped (non-critical): {ex}")

# =============================================================================
# DONE
# =============================================================================

print("\n" + "=" * 60)
print("Simulation complete!")
print("=" * 60)