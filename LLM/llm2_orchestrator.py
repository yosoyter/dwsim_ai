"""
LLM/llm2_orchestrator.py
=========================
LLM #2 pipeline: takes a validated flash task JSON (from LLM #1 / orchestrator_ft.py),
calls Claude to generate a targeted output_block function, and executes it against
the flash_master.py runner.

Pipeline:
  orchestrator_ft.py → [task JSON] → llm2_orchestrator.py → [generated code]
                                          → flash_master.run_flash_master()
                                              → output_block(results) → user output

Usage:
  python LLM/llm2_orchestrator.py
  (prompts for user question; runs full LLM #1 → LLM #2 → DWSIM pipeline)

  OR call run_full_pipeline(user_question) directly from other scripts.

Requirements:
  pip install anthropic
  ANTHROPIC_API_KEY must be set in environment.
"""

import os
import sys
import json
import re
import textwrap
import anthropic

# ─────────────────────────────────────────────────────────────────────────────
#  PATH SETUP
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _ROOT)

LLM2_SYSTEM_PROMPT_PATH = os.path.join(_HERE, "llm2_system_prompt.txt")

with open(LLM2_SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
    LLM2_SYSTEM_PROMPT = f.read()

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1: Run LLM #1 to get the task JSON
#  (reuses orchestrator_ft.py — no duplication)
# ─────────────────────────────────────────────────────────────────────────────

def get_task_json(user_question: str) -> dict | None:
    """
    Calls LLM #1 (orchestrator_ft.py) to parse the user question into a task JSON.
    Returns the validated payload dict, or None on failure.

    Note: only "flash" task_type is supported here.
    For "txy" tasks, use orchestrator_ft.py + txy_engine.py as before.
    """
    from LLM.orchestrator_ft import run_pipeline
    payload = run_pipeline(user_question)

    if payload is None:
        return None

    # reject tasks that are not flash
    # if payload.get("task_type") != "flash":
    #     print(f"[llm2] task_type is '{payload.get('task_type')}' — "
    #           "LLM #2 only supports flash tasks. "
    #           "For txy, use orchestrator_ft.py directly.")
    #     return None

    return payload


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2: Call LLM #2 to generate the output_block function body
# ─────────────────────────────────────────────────────────────────────────────

def generate_output_block_code(user_question: str, task_json: dict) -> str:
    """
    Sends the user question + task JSON to Claude (LLM #2).
    Returns the raw Python code string for the output_block function body.

    The LLM receives:
      - The system prompt (llm2_system_prompt.txt)
      - The user's original question
      - The task JSON for context (components, mode, conditions)
    """
    client = anthropic.Anthropic()

    # Build the user message: original question + task context
    user_message = f"""User's original question:
\"{user_question}\"

Task JSON (already validated, simulation will be run with these parameters):
{json.dumps(task_json, indent=2)}

Write the output_block function body to answer exactly what the user asked for."""

    print("\n[LLM #2] Generating output block...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=LLM2_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message}
        ]
    )

    raw_code = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw_code = re.sub(r"```python|```", "", raw_code).strip()

    print(f"[LLM #2] Generated output block ({len(raw_code.splitlines())} lines).")
    return raw_code


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3: Validate the generated code (lightweight safety check)
# ─────────────────────────────────────────────────────────────────────────────

# Functions available in flash_master.py that LLM #2 is allowed to use
ALLOWED_CALLS = {
    "print", "round", "len", "list", "dict", "str", "float", "int",
    "enumerate", "zip", "range", "sorted", "sum", "max", "min",
    "os.path.join", "os.makedirs", "csv.writer", "csv.DictWriter",
    "json.dumps", "open",
}

# Blocked patterns — things that should never appear in generated code
BLOCKED_PATTERNS = [
    r"\bimport\s+dwsim",
    r"\bimport\s+DWSIM",
    r"\bfrom\s+DWSIM",
    r"\bfrom\s+dwsim_core",
    r"\bfrom\s+flash_engine",
    r"\bfrom\s+flash_master",
    r"\bexec\b",
    r"\beval\b",
    r"\b__import__\b",
    r"\bsubprocess\b",
    r"\bos\.system\b",
    r"\bos\.popen\b",
]

def validate_output_block(code: str) -> bool:
    """
    Lightweight safety check on the generated output block code.
    Blocks DWSIM imports, exec/eval, and shell calls.
    Returns True if safe, False otherwise.
    """
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            print(f"[llm2] BLOCKED: generated code contains forbidden pattern: {pattern}")
            return False

    # Must be valid Python syntax
    try:
        compile(code, "<llm2_output_block>", "exec")
    except SyntaxError as e:
        print(f"[llm2] BLOCKED: generated code has syntax error: {e}")
        return False

    print("[llm2] Output block passed safety check. ✓")
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4: Build the callable output_block function from generated code
# ─────────────────────────────────────────────────────────────────────────────

def build_output_block_fn(code: str):
    """
    Wraps the generated code string into a callable Python function.

    The code is indented and embedded inside a def statement,
    then exec()'d in a sandboxed namespace. Returns the function.
    """
    # Indent each line by 4 spaces to form a valid function body
    indented = textwrap.indent(code, "    ")

    fn_source = f"def output_block(results):\n{indented}\n"

    namespace = {"os": os, "json": json}
    try:
        import csv
        namespace["csv"] = csv
    except ImportError:
        pass

    exec(fn_source, namespace)
    return namespace["output_block"]


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5: Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_full_pipeline(user_question: str) -> None:
    """
    End-to-end pipeline:
      1. LLM #1 parses user question → task JSON
      2. LLM #2 generates output_block code from user question + task JSON
      3. Safety check on generated code
      4. flash_master.run_flash_master() builds/solves DWSIM flowsheet
      5. output_block(results) produces user-facing output

    Parameters
    ----------
    user_question : str   the user's original natural language question
    """
    print("\n" + "=" * 60)
    print("DWSIM-AI  |  LLM #2 Pipeline")
    print("=" * 60)

    # ── Step 1: LLM #1 ────────────────────────────────────────────────────────
    task_json = get_task_json(user_question)
    if task_json is None:
        print("[llm2] Aborting: LLM #1 could not produce a valid flash task JSON.")
        return

    # Attach original user question to task JSON for LLM #2 context
    task_json["user_request"] = user_question

    # ── Txy: no code generation needed — route directly to txy_engine ─────────
    if task_json.get("task_type") == "txy":
        from DWSIM_ry_test.tasks.txy_engine import run_txy_task
        print("\n[llm2] Txy task detected — routing to txy_engine (no code generation).")
        df = run_txy_task(task_json, temp_unit="C")
        print(f"\n[llm2] Txy complete. {len(df)} envelope points computed.")
        print(f"       Plot + CSV saved to output/")
        print("\n[llm2] Pipeline complete.")
        return
    
    # ── Heat: route to heat_master with LLM #2 code generation ───────────────
    if task_json.get("task_type") == "heat":
        print("\n[llm2] Heat task detected — generating output block for heater/cooler.")
        generated_code = generate_output_block_code(user_question, task_json)

        print("\n[LLM #2] Generated output_block code:")
        print("-" * 40)
        print(generated_code)
        print("-" * 40)

        if not validate_output_block(generated_code):
            print("[llm2] Aborting: generated code failed safety check.")
            return

        output_block_fn = build_output_block_fn(generated_code)

        from DWSIM_ry_test.tasks.heat_master import run_heat_master
        run_heat_master(task_json, output_block_fn)

        print("\n[llm2] Pipeline complete.")
        return

    # ── Step 2: LLM #2 ────────────────────────────────────────────────────────
    generated_code = generate_output_block_code(user_question, task_json)

    print("\n[LLM #2] Generated output_block code:")
    print("-" * 40)
    print(generated_code)
    print("-" * 40)

    # ── Step 3: Safety check ──────────────────────────────────────────────────
    if not validate_output_block(generated_code):
        print("[llm2] Aborting: generated code failed safety check.")
        return

    # ── Step 4 + 5: Build function and run ────────────────────────────────────
    output_block_fn = build_output_block_fn(generated_code)

    from DWSIM_ry_test.tasks.flash_master import run_flash_master
    run_flash_master(task_json, output_block_fn)

    print("\n[llm2] Pipeline complete.")


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("DWSIM-AI — Unified Pipeline (Flash + Txy)")
    print("Supports: isothermal_PT_flash, adiabatic_PT_flash, txy\n")
    print("Example prompts:")
    print('  "Flash a 50/50 propane/n-butane mix at 60C and 12 bar, drum at 6 bar."')
    print('  "Txy diagram for ethanol and water at 1 atm."')
    print('  "Phase envelope for benzene and toluene at 2 bar."')
    print()

    user_input = input("Your question: ").strip()

    if not user_input:
        print("[ERROR] No input provided. Exiting.")
    else:
        run_full_pipeline(user_input)
