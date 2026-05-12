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
Flash a 50/50 propane/n-butane mixture at 10 bar and 50°C
Heat a methane stream from 20°C to 80°C at 5 bar
Compress propane from 5 bar, 60°C to 20 bar with 75% efficiency
Generate a T-x-y diagram for ethanol and water at 1 atm
Compress a 50/50 propane/n-butane vapor from 5 bar, 60°C to 20 bar, then flash at 20 bar and 100°C
```

---

## Team

Terry Cheng, Arman Flores, Caleb Medina, Ricky Yu, Kristopher Hoyt 
Johns Hopkins University — Whiting School of Engineering
