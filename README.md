# SIMQuery

**LLM-to-DWSIM Natural Language Process Simulation**

SIMQuery lets chemical engineers and students run process simulations by describing them in plain English — no DWSIM interface knowledge required.

---

## Project Structure

```
simquery/               Core simulation engine
  core/                 DWSIM automation helpers
    dwsim_core.py       DWSIM init, flowsheet creation, solve
    units/
      heat_exchanger.py   Heater, cooler, two-stream HX builders
      compressor_expander.py  Compressor and expander builders
  runners/              Task-specific simulation runners
    flash_runner.py     Isothermal / adiabatic flash
    heat_runner.py      Heater / cooler
    comp_runner.py      Compressor / expander
    hx_runner.py        Two-stream heat exchanger
    flowsheet_runner.py Multi-unit flowsheet
    txy_engine.py       T-x-y phase diagram generator
    test_heat_exchanger.py
    test_compressor_expander.py
  tests/
    test_flash.py
    test_txy.py

pipeline/               LLM pipeline
  app.py                Gradio web UI (entry point)
  orchestrator.py       LLM #2 dispatcher + code generator (CLI entry point)
  llm1_orchestrator.py  LLM #1 JSON parser + validator
  prompts/
    llm1_system_prompt.txt
    llm2_system_prompt.txt
    llm2_flowsheet_prompt.txt
    guard_prompt.txt

output/                 Generated at runtime (gitignored)
proof_of_concept.py     Original DWSIM automation proof of concept
run_pipeline.bat        Windows launcher script
```

---

## Supported Simulation Types

| Task | Description |
|---|---|
| `flash` | Isothermal or adiabatic flash separation |
| `heat` | Single-stream heater or cooler |
| `comp` | Compressor or expander |
| `hx` | Two-stream heat exchanger |
| `txy` | T-x-y vapor-liquid equilibrium diagram |
| `flowsheet` | Multi-unit connected flowsheet (any combination of above) |

---

## Requirements

- Windows (DWSIM uses COM automation)
- Python 3.11+
- [DWSIM](https://dwsim.org/) installed locally
- `pip install anthropic gradio pythonnet pandas numpy matplotlib`
- `ANTHROPIC_API_KEY` environment variable set

---

## Running

**Web UI:**
```bash
python pipeline/app.py
```

**Command line:**
```bash
python pipeline/orchestrator.py
```

**Windows launcher:**
```
run_pipeline.bat
```

---

## Example Queries

```
Isothermal flash of propane 50% and n-butane 50%. Feed at 60C and 14 bar, drum at 60C and 11 bar. What are the vapor and liquid compositions?

Adiabatic flash of propane 30% n-butane 70% from 20 bar down to 3 bar at 60C feed. What is the outlet temperature after the valve?

Isothermal flash of hydrogen 30%, methane 60%, benzene 7%, toluene 3%. Feed at 410K and 34.5 bar, drum at 320K and 33.5 bar. What are the vapor and liquid compositions?

Adiabatic flash of propane 40% and n-butane 60%. Feed at 80C and 15 bar, drum pressure 4 bar. What is the overall vapor fraction?

Isothermal flash of propane 30% and n-butane 70%. Feed at 60C and 12 bar, drum at 60C and 9 bar. What percentage of the propane ends up in the vapor stream?

Adiabatic flash of propane 50% and n-butane 50%. Feed at 25C and 20 bar, drum pressure 3 bar. What is the temperature of the vapor and liquid streams leaving the drum?

Isothermal flash of ethanol 40% and water 60%. Feed at 90C and 3 bar, drum at 80C and 1.013 bar. Save the results to a CSV.

Isothermal flash of benzene 50% and toluene 50%. Feed at 100C and 3 bar, drum at 100C and 1.5 bar. What is the relative volatility of benzene with respect to toluene?

Isothermal flash of propane 50% and n-butane 50%. Feed at 60C and 14 bar, drum at 60C and 11 bar. What is the enthalpy of the vapor and liquid streams?

Compress a 50/50 propane/n-butane vapor from 5 bar to 20 bar. Feed is at 80°C and 100 kmol/h. Use 75% isentropic efficiency.

Expand a 50/50 propane/n-butane vapor from 20 bar, 200°C down to 5 bar through a turbine. 100 kmol/h, 75% isentropic efficiency.

Cool a 50/50 propane/n-butane stream from 120°C to 80°C using a cold 50/50 propane/n-butane stream entering at 30°C. Hot side at 14 bar 100 kmol/h, cold side at 10 bar 120 kmol/h.

Generate a T-x-y diagram for ethanol and water at 1 atm

Compress a 50/50 propane/n-butane vapor from 5 bar, 60°C to 20 bar, then flash it at 20 bar and 100°C. What are the vapor and liquid compositions?

Heat a 50/50 propane/n-butane stream from 40°C to 80°C at 5 bar, then compress it to 20 bar at 75% efficiency, then flash at 20 bar and 120°C. What is the vapor fraction after the flash?

Expand a 50/50 propane/n-butane vapor from 20 bar, 150°C down to 6 bar at 75% efficiency, then flash the outlet. What are the outlet temperature and vapor fraction?

Cool a 50/50 propane/n-butane stream from 200°C to 150°C at 20 bar, then expand it to 5 bar at 75% isentropic efficiency. What is the outlet temperature and shaft work recovered?

Cool a hot propane/n-butane stream (120°C, 14 bar, 100 kmol/h) against a cold methane/ethane stream (30°C, 10 bar, 80 kmol/h) in a heat exchanger cooling the hot side to 100°C, then flash the cooled hot outlet at 14 bar. What are the vapor/liquid compositions from the flash?
```

---

## Team

Terry Cheng, Arman Flores, Caleb Medina, Ricky Yu, Kristopher Hoyt 
Johns Hopkins University — Whiting School of Engineering
