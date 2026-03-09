# =============================================================================
# Group 1: LLM Orchestrator — Txy Calculation Request
# File: LLM/orchestrator.py
#
# What this script does:
#   1. Takes a natural language user question as input
#   2. Sends it to the Claude API with a strict system prompt
#   3. Parses the LLM's JSON response
#   4. Saves a JSON file in Group 2's exact format to the examples/ folder
#
# Usage:
#   python orchestrator.py
#   (then type your question when prompted)
#
# Requirements:
#   pip install anthropic jsonschema
#   Set your API key: export ANTHROPIC_API_KEY=your_key_here
# =============================================================================

import os
import json
import re
import anthropic

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_PATH = os.path.join(SCRIPT_DIR, "system_prompt.txt")

# This is where Group 2's txy_engine.py looks for input files
OUTPUT_EXAMPLES_DIR = os.path.join(SCRIPT_DIR, "..", "DWSIM_ry_test", "tasks", "examples")

with open(SYSTEM_PROMPT_PATH, "r") as f:
    SYSTEM_PROMPT = f.read()

# =============================================================================
# STEP 1: Call the LLM and get back a structured JSON task
# =============================================================================

def call_llm(user_question: str) -> dict:
    """
    Sends the user's question to Claude.
    Returns a parsed dict in Group 2's exact task format.
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
# STEP 2: Validate the payload has everything Group 2 needs
# =============================================================================

REQUIRED_FIELDS = ["task_type", "component_1", "component_2", "pressure_Pa", "n_points", "property_package"]

def validate_payload(payload: dict) -> bool:
    """
    Checks the payload has all required fields in Group 2's format.
    Returns True if valid, False otherwise.
    """
    if "error" in payload:
        print(f"[VALIDATION] LLM flagged an issue: {payload['error']}")
        return False

    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        print(f"[VALIDATION] Missing required fields: {missing}")
        return False

    if payload.get("task_type") != "txy":
        print(f"[VALIDATION] task_type must be 'txy', got: {payload.get('task_type')}")
        return False

    print("[VALIDATION] Payload is valid. ✓")
    return True

# =============================================================================
# STEP 3: Save the JSON file where Group 2 can find it
# =============================================================================

def save_payload(payload: dict) -> str:
    """
    Saves the validated payload as a JSON file in Group 2's examples folder.
    Filename is auto-generated from the component names, e.g. ethanol_water.json
    """
    os.makedirs(OUTPUT_EXAMPLES_DIR, exist_ok=True)

    # Build a clean filename from component names
    c1 = payload["component_1"].lower().replace(" ", "_").replace("-", "_")
    c2 = payload["component_2"].lower().replace(" ", "_").replace("-", "_")
    filename = f"{c1}_{c2}.json"
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

    print("\n" + "=" * 60)
    print("FINAL PAYLOAD (Group 2 format):")
    print("=" * 60)
    print(json.dumps(payload, indent=2))
    print(f"\n[OUTPUT] Saved to: {filepath}")
    print("[OUTPUT] Group 2 can now run: python tasks/txy_engine.py tasks/examples/" + os.path.basename(filepath))

    return payload

# =============================================================================
# STEP 5: Entry point
# =============================================================================

if __name__ == "__main__":
    print("\nTxy Phase Diagram — LLM Orchestrator")
    print("Example prompts:")
    print('  "Generate a Txy diagram for ethanol and water at 1 atm"')
    print('  "I need a phase envelope for benzene and toluene at 2 bar"')
    print('  "Txy diagram for methanol and acetone at atmospheric pressure"\n')

    user_input = input("Your question: ").strip()

    if not user_input:
        print("[ERROR] No input provided. Exiting.")
    else:
        result = run_pipeline(user_input)
        if result:
            print("\n[DONE] Pipeline completed successfully.")
        else:
            print("\n[DONE] Pipeline ended with errors. Check messages above.")
