"""
DWSIM/lib/dwsim_core.py
========================
Reusable DWSIM automation helpers for headless (no-GUI) operation.

Requirements: Python 3.9, pythonnet==2.5.2, DWSIM installed on workstation.
Activate correct env before use:  bash setup_env.sh  (takes ~3-4 min on remote)

All functions here are stateless helpers — call them from any task script.
"""

import sys
import os

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION  — edit DWSIM_PATH to match your workstation install location
# ─────────────────────────────────────────────────────────────────────────────
DWSIM_PATH = r"C:\\Users\\rickyyu\\AppData\\Local\\DWSIM\\"   # default Windows install

# Property package display names as DWSIM registers them internally
PROPERTY_PACKAGES = {
    "NRTL":    "Raoult's Law / NRTL",
    "UNIQUAC": "UNIQUAC",
    "PR":      "Peng-Robinson (PR)",
    "SRK":     "Soave-Redlich-Kwong (SRK)",
    "IDEAL":   "Raoult's Law",
}

# Components known to be polar — used for auto property package selection
POLAR_COMPONENTS = {
    "WATER", "ETHANOL", "METHANOL", "ACETONE", "ACETIC ACID",
    "ISOPROPANOL", "1-PROPANOL", "N-PROPANOL", "FORMIC ACID",
    "ETHYLENE GLYCOL", "DIETHYL ETHER",
}


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER: AUTO PROPERTY PACKAGE SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def get_recommended_package(comp1: str, comp2: str) -> str:
    """
    Heuristic: return the best property package tag for a binary pair.

    - Any polar component → NRTL  (activity coefficient; handles non-ideal liquids)
    - Both non-polar      → PR    (equation of state; good for hydrocarbons)

    Parameters
    ----------
    comp1, comp2 : str  component names (case-insensitive)

    Returns
    -------
    str  property package key from PROPERTY_PACKAGES dict
    """
    if {comp1.upper(), comp2.upper()} & POLAR_COMPONENTS:
        return "NRTL"
    return "PR"


# ─────────────────────────────────────────────────────────────────────────────
#  DWSIM INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def init_dwsim(dwsim_path: str = DWSIM_PATH):
    """
    Load DWSIM .NET assemblies into Python via pythonnet (clr).

    Must be called ONCE per session before any other DWSIM operations.

    Parameters
    ----------
    dwsim_path : str  path to DWSIM installation folder

    Returns
    -------
    interf : DWSIM.Interfaces module

    Raises
    ------
    ImportError   if pythonnet/clr is not installed
    RuntimeError  if DWSIM DLLs are not found at dwsim_path
    """
    try:
        import clr
    except ImportError:
        raise ImportError(
            "pythonnet (clr) not found.\n"
            "  1. Activate the conda env:  bash setup_env.sh\n"
            "  2. Run:  pip install pythonnet==2.5.2"
        )

    if not os.path.isdir(dwsim_path):
        raise RuntimeError(
            f"DWSIM path not found: {dwsim_path}\n"
            "Edit DWSIM_PATH in DWSIM/lib/dwsim_core.py to point to your install."
        )

    sys.path.append(dwsim_path)

    assemblies = [
        "DWSIM.Interfaces",
        "DWSIM.GlobalSettings",
        "DWSIM.SharedClasses",
        "DWSIM.Thermodynamics",
        "DWSIM.UnitOperations",
        "DWSIM.Inspector",
        "System",
    ]
    for asm in assemblies:
        clr.AddReference(asm)

    import DWSIM.GlobalSettings  # noqa: F401
    import DWSIM.Interfaces as interf

    print(f"[dwsim_core] DWSIM assemblies loaded from: {dwsim_path}")
    return interf


# ─────────────────────────────────────────────────────────────────────────────
#  FLOWSHEET CREATION
# ─────────────────────────────────────────────────────────────────────────────

def create_flowsheet(interf):
    """
    Create and return an empty headless DWSIM flowsheet.

    Parameters
    ----------
    interf : module  returned by init_dwsim()

    Returns
    -------
    flowsheet : DWSIM.Interfaces.Flowsheet.FlowsheetObject
    """
    flowsheet = interf.Flowsheet.FlowsheetObject()
    flowsheet.Initialize()
    print("[dwsim_core] Empty flowsheet created.")
    return flowsheet


# ─────────────────────────────────────────────────────────────────────────────
#  PROPERTY PACKAGE
# ─────────────────────────────────────────────────────────────────────────────

def select_property_package(flowsheet, package_tag: str):
    """
    Attach a property package to the flowsheet by tag name.

    Parameters
    ----------
    flowsheet   : DWSIM flowsheet object
    package_tag : str  key from PROPERTY_PACKAGES (e.g. 'NRTL', 'PR')

    Returns
    -------
    pp : property package object

    Raises
    ------
    ValueError   if package_tag is not in PROPERTY_PACKAGES
    RuntimeError if DWSIM can't find the package in its registry
    """
    if package_tag not in PROPERTY_PACKAGES:
        raise ValueError(
            f"Unknown property package tag: '{package_tag}'\n"
            f"Valid options: {list(PROPERTY_PACKAGES.keys())}"
        )

    display_name = PROPERTY_PACKAGES[package_tag]

    pp = None
    for available_pp in flowsheet.AvailablePropertyPackages:
        if available_pp.Name == display_name:
            pp = available_pp
            break

    if pp is None:
        raise RuntimeError(
            f"Property package '{display_name}' not found in DWSIM.\n"
            "Check that the NRTL / PR packages are installed."
        )

    flowsheet.SelectedPropertyPackage = pp
    print(f"[dwsim_core] Property package set: {display_name}")
    return pp


# ─────────────────────────────────────────────────────────────────────────────
#  COMPONENT MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def add_component(flowsheet, component_name: str):
    """
    Add a pure component to the flowsheet's compound list.

    Parameters
    ----------
    flowsheet      : DWSIM flowsheet object
    component_name : str  must match DWSIM compound database name exactly
                         (e.g. 'Ethanol', 'Water', 'Benzene')

    Raises
    ------
    RuntimeError if component is not found in the DWSIM database
    """
    try:
        flowsheet.SelectedCompounds.Add(component_name)
        print(f"[dwsim_core] Added component: {component_name}")
    except Exception as e:
        raise RuntimeError(
            f"Failed to add component '{component_name}'.\n"
            "Verify the name matches DWSIM's compound database exactly.\n"
            f"Original error: {e}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  SOLVER
# ─────────────────────────────────────────────────────────────────────────────

def solve_flowsheet(flowsheet) -> list:
    """
    Run DWSIM's sequential-modular solver.

    Returns
    -------
    errors : list[str]  solver error messages (empty list = success)
    """
    from DWSIM.FlowsheetSolver import FlowsheetSolver
    solver = FlowsheetSolver()
    errors = []
    try:
        solver.SolveFlowsheet(flowsheet)
        print("[dwsim_core] Flowsheet solved successfully.")
    except Exception as e:
        errors.append(str(e))
        print(f"[dwsim_core] Solver error: {e}")
    return errors


# ─────────────────────────────────────────────────────────────────────────────
#  FILE I/O
# ─────────────────────────────────────────────────────────────────────────────

def save_flowsheet(flowsheet, output_path: str):
    """
    Save flowsheet as a .dwxmz file.

    Parameters
    ----------
    output_path : str  full path including filename (e.g. 'output/txy_run.dwxmz')
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    flowsheet.SaveToXML(output_path)
    print(f"[dwsim_core] Flowsheet saved: {output_path}")
