"""
DWSIM_ry_test/lib/dwsim_core.py
================================
Reusable DWSIM automation helpers - headless mode.
Based on working Automation3 API pattern (matches DWSim_proof_of_concept.py).
Requires: Python 3.9, pythonnet, pythoncom, DWSIM installed.
"""

import os
import sys

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION — update this if DWSIM moves
# ─────────────────────────────────────────────────────────────────────────────
DWSIM_PATH = r"C:\\Users\\rickyyu\AppData\\Local\\DWSIM\\"

POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL",
}


def get_recommended_package(comp1: str, comp2: str) -> str:
    """
    Auto-select property package based on component polarity.
    Polar pair  → NRTL  (activity coefficient; handles non-ideal liquids)
    Non-polar   → PR    (equation of state; good for hydrocarbons)
    """
    if {comp1.upper(), comp2.upper()} & POLAR_COMPONENTS:
        return "NRTL"
    return "PR"


# ─────────────────────────────────────────────────────────────────────────────
#  INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def init_dwsim(dwsim_path: str = DWSIM_PATH):
    """
    Initialize DWSIM via the Automation3 API (headless, no GUI).

    This mirrors the pattern in DWSim_proof_of_concept.py exactly:
      - CoInitialize first
      - Load all DLLs by full path
      - SetCurrentDirectory to DWSIM folder
      - Return Automation3 instance

    Parameters
    ----------
    dwsim_path : str  path to DWSIM installation folder

    Returns
    -------
    interf : Automation3 instance  (use this to create/solve/save flowsheets)
    """
    # MUST be first — avoids STA threading errors on Windows
    import pythoncom
    pythoncom.CoInitialize()

    import clr
    from System.IO import Directory

    if not os.path.isdir(dwsim_path):
        raise RuntimeError(
            f"DWSIM path not found: {dwsim_path}\n"
            "Update DWSIM_PATH in DWSIM_ry_test/lib/dwsim_core.py"
        )

    # Load all required DLLs with full absolute path
    required_dlls = [
        "CapeOpen.dll",
        "DWSIM.Automation.dll",
        "DWSIM.Interfaces.dll",
        "DWSIM.GlobalSettings.dll",
        "DWSIM.SharedClasses.dll",
        "DWSIM.Thermodynamics.dll",
        "DWSIM.Thermodynamics.ThermoC.dll",
        "DWSIM.UnitOperations.dll",
        "DWSIM.Inspector.dll",
        "System.Buffers.dll",
    ]
    for dll in required_dlls:
        dll_full = os.path.join(dwsim_path, dll)
        if os.path.isfile(dll_full):
            clr.AddReference(dll_full)
        else:
            print(f"[dwsim_core] WARNING: DLL not found, skipping: {dll}")

    # Required for DWSIM to find its internal resources
    Directory.SetCurrentDirectory(dwsim_path)

    from DWSIM.Automation import Automation3
    interf = Automation3()

    print(f"[dwsim_core] DWSIM loaded from: {dwsim_path}")
    return interf


# ─────────────────────────────────────────────────────────────────────────────
#  FLOWSHEET
# ─────────────────────────────────────────────────────────────────────────────

def create_flowsheet(interf):
    """
    Create and return a blank DWSIM flowsheet.

    Parameters
    ----------
    interf : Automation3 instance returned by init_dwsim()

    Returns
    -------
    sim : DWSIM flowsheet object
    """
    sim = interf.CreateFlowsheet()
    print("[dwsim_core] Flowsheet created.")
    return sim


# ─────────────────────────────────────────────────────────────────────────────
#  COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

def add_component(sim, component_name: str):
    """
    Add a pure compound to the flowsheet's compound list.

    Parameters
    ----------
    sim            : DWSIM flowsheet object
    component_name : str  must match DWSIM compound database name exactly
                         e.g. 'Ethanol', 'Water', 'Benzene', 'Toluene'

    Raises
    ------
    RuntimeError if the component name is not found in DWSIM database
    """
    try:
        compound = sim.AvailableCompounds[component_name]
        sim.SelectedCompounds.Add(compound.Name, compound)
        print(f"[dwsim_core] Added component: {component_name}")
    except Exception as e:
        raise RuntimeError(
            f"Could not add component '{component_name}'.\n"
            "Check that the name matches the DWSIM compound database exactly.\n"
            f"Original error: {e}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  PROPERTY PACKAGE
# ─────────────────────────────────────────────────────────────────────────────

def select_property_package(sim, package_tag: str):
    """
    Instantiate and attach a property package to the flowsheet.

    Parameters
    ----------
    sim         : DWSIM flowsheet object
    package_tag : str  one of: NRTL, PR, SRK, UNIQUAC, IDEAL, STEAM

    Returns
    -------
    pp : property package object attached to sim

    Raises
    ------
    ValueError if package_tag is not recognized
    """
    from DWSIM.Thermodynamics import PropertyPackages

    pp_map = {
        "NRTL":    PropertyPackages.NRTLPropertyPackage,
        "PR":      PropertyPackages.PengRobinsonPropertyPackage,
        "SRK":     PropertyPackages.SRKPropertyPackage,
        "UNIQUAC": PropertyPackages.UNIQUACPropertyPackage,
        "IDEAL":   PropertyPackages.RaoultPropertyPackage,
        "STEAM":   PropertyPackages.SteamTablesPropertyPackage,
    }

    if package_tag not in pp_map:
        raise ValueError(
            f"Unknown property package tag: '{package_tag}'\n"
            f"Valid options: {list(pp_map.keys())}"
        )

    pp = pp_map[package_tag]()
    sim.AddPropertyPackage(pp)
    print(f"[dwsim_core] Property package set: {package_tag}")
    return pp


# ─────────────────────────────────────────────────────────────────────────────
#  SOLVER
# ─────────────────────────────────────────────────────────────────────────────

def solve_flowsheet(interf, sim) -> list:
    """
    Run DWSIM's flowsheet solver synchronously.

    Parameters
    ----------
    interf : Automation3 instance
    sim    : DWSIM flowsheet object

    Returns
    -------
    errors : list[str]  solver error messages (empty list = success)
    """
    from DWSIM.GlobalSettings import Settings
    Settings.SolverMode = 0  # 0 = synchronous

    errors = interf.CalculateFlowsheet2(sim)

    if errors is not None and errors.Count > 0:
        err_list = [str(e) for e in errors]
        print(f"[dwsim_core] Solver errors: {err_list}")
        return err_list

    print("[dwsim_core] Flowsheet solved successfully.")
    return []


# ─────────────────────────────────────────────────────────────────────────────
#  FILE I/O
# ─────────────────────────────────────────────────────────────────────────────

def save_flowsheet(interf, sim, output_path: str):
    """
    Save flowsheet as a compressed .dwxmz file.

    Parameters
    ----------
    interf      : Automation3 instance
    sim         : DWSIM flowsheet object
    output_path : str  full path including filename (e.g. 'output/txy_run.dwxmz')
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    interf.SaveFlowsheet(sim, output_path, True)  # True = compressed
    print(f"[dwsim_core] Saved: {output_path}")
