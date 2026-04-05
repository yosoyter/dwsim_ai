# =============================================================================
# Group 1: LLM Orchestrator — Txy + Flash Calculation Requests
# File: LLM/orchestrator.py
#
# What this script does:
#   1. Takes a natural language user question as input
#   2. Sends it to the Claude API with a strict system prompt
#   3. Detects task_type ("txy" or "flash") from the LLM response
#   4. Validates the payload has all required fields for that task type
#   5. Saves a JSON file to DWSIM_ry_test/tasks/examples/ for Group 2
#
# Usage:
#   python orchestrator.py
#   (then type your question when prompted)
#
# Requirements:
#   pip install anthropic
#   Set your API key: set ANTHROPIC_API_KEY=sk-ant-api03-...
# =============================================================================

import os
import json
import re
import anthropic

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_PATH = os.path.join(SCRIPT_DIR, "system_prompt_0329.txt")

# This is where txy_engine.py and flash_engine.py look for input files
OUTPUT_EXAMPLES_DIR = os.path.join(
    SCRIPT_DIR, "..", "DWSIM_ry_test", "tasks", "examples"
)

with open(SYSTEM_PROMPT_PATH, "r") as f:
    SYSTEM_PROMPT = f.read()

# =============================================================================
# REQUIRED FIELDS PER TASK TYPE
# =============================================================================

REQUIRED_FIELDS = {
    "txy": [
        "task_type", "component_1", "component_2",
        "pressure_Pa", "n_points", "property_package",
    ],
    "flash": [
        "task_type",
        "flash_mode",
        "components",
        "property_package",
    ],
}

# =============================================================================
# STEP 1: Call the LLM
# =============================================================================

def call_llm(user_question: str) -> dict:
    """
    Sends the user's question to Claude.
    Returns a parsed dict in Group 2's task format.
    """
    client = anthropic.Anthropic()

    print(f"\n[LLM] Sending to Claude...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_question}
        ]
    )

    raw_text = response.content[0].text.strip()
    print(f"[LLM] Raw response:\n    {raw_text}\n")

    # Strip markdown code fences if the LLM added them
    raw_text = re.sub(r"```json|```", "", raw_text).strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM did not return valid JSON.\n"
            f"Raw response: {raw_text}\n"
            f"Error: {e}"
        )

    return parsed

# =============================================================================
# STEP 2: Validate the payload
# =============================================================================

def validate_payload(payload: dict) -> bool:
    """
    Validates the payload has all required fields for its task_type.
    Supports "txy" and "flash".
    Returns True if valid, False otherwise.
    """
    # Check for LLM-flagged errors
    if "error" in payload:
        print(f"[VALIDATION] LLM flagged an issue: {payload['error']}")
        return False

    task_type = payload.get("task_type")

    if task_type not in REQUIRED_FIELDS:
        print(f"[VALIDATION] Unknown task_type: '{task_type}'")
        print(f"             Supported types: {list(REQUIRED_FIELDS.keys())}")
        return False

    missing = [f for f in REQUIRED_FIELDS[task_type] if f not in payload]
    if missing:
        print(f"[VALIDATION] Missing required fields for '{task_type}': {missing}")
        return False

    # Flash-specific checks
    if task_type == "flash":
        comps = payload.get("components", {})
        if not isinstance(comps, dict):
            print("[VALIDATION] 'components' must be a dict {name: mole_fraction}.")
            return False

        if len(comps) < 1:
            print("[VALIDATION] 'components' must contain at least one component.")
            return False

        total = sum(comps.values())
        if abs(total - 1.0) > 0.02:
            print(f"[VALIDATION] Component mole fractions sum to {total:.3f}, not 1.0")
            return False

        flash_mode = payload.get("flash_mode")

        if flash_mode == "isothermal_PT_flash":
            feed = payload.get("feed", {})
            flash_drum = payload.get("flash_drum", {})

            for field in ["temperature_C", "pressure_bar"]:
                if field not in feed:
                    print(f"[VALIDATION] Missing feed.{field} for isothermal_PT_flash.")
                    return False
                if field not in flash_drum:
                    print(f"[VALIDATION] Missing flash_drum.{field} for isothermal_PT_flash.")
                    return False

            if flash_drum["pressure_bar"] >= feed["pressure_bar"]:
                print("[VALIDATION] flash_drum.pressure_bar must be less than feed.pressure_bar.")
                return False

        else:
            print(f"[VALIDATION] Unsupported flash_mode: '{flash_mode}'. Only 'isothermal_PT_flash' is supported for now.")
            return False

    print("[VALIDATION] Payload is valid. ✓")
    return True

# =============================================================================
# STEP 3: Save the JSON
# =============================================================================

def save_payload(payload: dict) -> str:
    """
    Saves the validated payload as a JSON file in Group 2's examples folder.
    Filename is auto-generated from task type + component names.
      txy   → ethanol_water.json
      flash → flash_hydrogen_methane_benzene_toluene.json
    """
    os.makedirs(OUTPUT_EXAMPLES_DIR, exist_ok=True)

    task_type = payload["task_type"]

    if task_type == "txy":
        c1 = payload["component_1"].lower().replace(" ", "_").replace("-", "_")
        c2 = payload["component_2"].lower().replace(" ", "_").replace("-", "_")
        filename = f"{c1}_{c2}.json"

    elif task_type == "flash":
        comps = payload["components"]
        comp_names = list(comps.keys())
        flash_mode = payload.get("flash_mode", "flash")
        comp_slug = "_".join(
            c.lower().replace(" ", "_").replace("-", "_") for c in comp_names
        )
        mode_slug = flash_mode.lower().replace(" ", "_")
        filename = f"{mode_slug}_{comp_slug}.json"

    else:
        filename = f"task_{task_type}.json"

    filepath = os.path.join(OUTPUT_EXAMPLES_DIR, filename)

    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)

    return filepath

# =============================================================================
# STEP 4: Full pipeline
# =============================================================================

def run_pipeline(user_question: str) -> dict:
    """
    Full Group 1 pipeline: question → LLM → validated JSON → saved file.
    Returns the payload dict, or None if something went wrong.
    """
    print("=" * 60)
    print("GROUP 1: LLM Orchestrator")
    print("=" * 60)
    print(f'User Question: "{user_question}"')

    try:
        payload = call_llm(user_question)
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        return None

    is_valid = validate_payload(payload)
    if not is_valid:
        print("\n[PIPELINE] Payload failed validation. Cannot pass to Group 2.")
        return None

    filepath = save_payload(payload)

    task_type = payload["task_type"]
    engine    = "txy_engine.py" if task_type == "txy" else "flash_engine.py"

    print("\n" + "=" * 60)
    print(f"FINAL PAYLOAD (task_type: {task_type})")
    print("=" * 60)
    print(json.dumps(payload, indent=2))
    print(f"\n[OUTPUT] Saved to: {filepath}")
    print(f"[OUTPUT] Group 2 command: python DWSIM_ry_test/tasks/{engine} "
          f"DWSIM_ry_test/tasks/examples/{os.path.basename(filepath)}")

    return payload

# =============================================================================
# STEP 5: Entry point
# =============================================================================

if __name__ == "__main__":
    print("\nDWSIM-AI — LLM Orchestrator")
    print("Supports: Txy phase diagrams and Flash separations\n")
    print("Example prompts:")
    print('  "Generate a Txy diagram for ethanol and water at 1 atm"')
    print('  "Phase envelope for benzene and toluene at 2 bar"')
    print('  "Flash separate H2 30%, CH4 60%, Benzene 7.5%, Toluene 2.5%')
    print('   at 410 K and 3447370 Pa. Cool to 320 K, flash at 3350000 Pa."')
    print()

    user_input = input("Your question: ").strip()

    if not user_input:
        print("[ERROR] No input provided. Exiting.")
    else:
        result = run_pipeline(user_input)
        if result:
            print("\n[DONE] Pipeline completed successfully.")
        else:
            print("\n[DONE] Pipeline ended with errors. Check messages above.")