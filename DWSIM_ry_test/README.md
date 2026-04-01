# Group 2: DWSIM Automation & Txy Generation

**Branch:** `ry_tc`  
**Focus:** Back-end DWSIM Python engine for Txy phase-equilibrium diagrams.

---

## What This Does

Takes a clean JSON task spec from Group 1's orchestrator → initializes DWSIM headless → selects the correct property package (NRTL or Peng-Robinson) → runs a bubble-point flash sweep across the full composition range → returns phase equilibrium data as a pandas DataFrame + CSV.

**Output columns:**

| Column | Description |
|--------|-------------|
| `x1`   | Liquid mole fraction of component 1 |
| `y1`   | Vapor mole fraction of component 1  |
| `T_K`  | Equilibrium temperature [Kelvin]    |
| `T_C`  | Equilibrium temperature [Celsius]   |

---

## File Structure

```
dwsim_ai/                          ← repo root (unchanged)
├── DWSIM_ry_test/                         ← your existing folder
│   ├── lib/
│   │   └── dwsim_core.py          ← reusable DWSIM helpers (init, flowsheet, PP, save)
│   ├── tasks/
│   │   ├── txy_engine.py          ← main Txy engine (Group 2 core)
│   │   ├── schemas/
│   │   │   └── txy_input_schema.json   ← JSON contract with Group 1
│   │   └── examples/
│   │       ├── ethanol_water.json
│   │       ├── benzene_toluene.json
│   │       └── methanol_water.json
│   └── tests/
│       └── test_txy_engine.py     ← 30 tests
├── LLM/                           ← Group 1's territory (untouched)
├── output/                        ← CSVs and .dwxmz files go here
└── DWSim_proof_of_concept.py      ← existing file (untouched)

```

---

## Setup (Workstation with DWSIM)

### 1. Activate the correct conda environment

```bash
# On the remote workstation, run the BASH script to set up Python 3.9
bash /path/to/setup_conda_env.sh   # sets up env each login (~3-4 min)
conda activate dwsim_env
```

### 2. Install Python dependencies

```bash
pip install pythonnet==2.5.2 pandas numpy matplotlib pytest
```

> **Note:** pythonnet 2.x requires Python ≤ 3.9. The workstation BASH script handles this.

### 3. Set your DWSIM path

Edit `dwsim_lib/dwsim_core.py` line 19:
```python
DWSIM_PATH = r"C:\Users\Public\DWSIM"   # change to actual install path
```

---

## Running

### Quick demo (mock mode — no DWSIM needed)

```bash
python demo_txy.py
```

Outputs `output/txy_Ethanol_Water.csv` and `output/txy_ethanol_water.png`.

### From a JSON task file

```bash
python tasks/txy_engine.py tasks/example_ethanol_water.json --output-dir output
```

### From another Python script (e.g., Group 1 orchestrator)

```python
from tasks.txy_engine import run_txy_task

task = {
    "task_type":        "txy",
    "component_1":      "Ethanol",
    "component_2":      "Water",
    "pressure_Pa":      101325.0,
    "n_points":         20,
    "property_package": "NRTL",    # optional — auto-selected if omitted
}

df = run_txy_task(task, output_dir="output")
print(df)
```

### Run tests

```bash
python -m pytest tests/test_txy_engine.py -v
```

---

## Property Package Selection

| System type | Recommended package | Why |
|-------------|--------------------|----|
| Polar + polar (e.g., Ethanol–Water) | **NRTL** | Activity coefficient model; captures non-ideal liquid behavior and azeotropes |
| Polar + non-polar (e.g., Ethanol–Hexane) | **NRTL** | Non-ideality in liquid phase |
| Non-polar + non-polar (e.g., Benzene–Toluene) | **Peng-Robinson** | EOS handles hydrocarbon mixtures well |

Auto-selection is based on component name matching against a list of known polar compounds.

---

## Validation Notes (Group 3 interface)

The Txy output CSV is designed to be handed off to Group 3 for:
- Comparison against NIST/literature bubble/dew point data
- Checking for expected azeotrope behavior (e.g., Ethanol–Water azeotrope at x₁ ≈ 0.894, 78.1°C)
- Generating publication-quality Matplotlib plots

**Known limitations of mock mode:**
- Uses Raoult's Law (ideal liquid) — no NRTL activity corrections
- Will NOT capture the Ethanol–Water azeotrope (requires DWSIM + NRTL)
- Antoine coefficients are approximate for 1 atm only

---

## Handoff to Group 1

Group 1's orchestrator calls `run_txy_task(task_dict)` and receives a `pd.DataFrame`.  
The JSON schema at `tasks/txy_task_schema.json` defines exactly what fields Group 1 must provide.





Set Key:
ANTHROPIC_API_KEY=your_key_here