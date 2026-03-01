# =============================================================================
# Group 1: LLM Orchestrator — Txy Calculation Request
# File: LLM/orchestrator.py
#
# What this script does:
#   1. Takes a natural language user question as input
#   2. Sends it to the LLM with a strict system prompt
#   3. Parses the LLM's JSON response
#   4. Validates it against the Txy schema
#   5. Prints the clean JSON payload ready for Group 2
#
# Usage:
#   python orchestrator.py
#   (then type your question when prompted)
#
# Requirements:
#   pip install anthropic jsonschema
#   Set your API key: set ANTHROPIC_API_KEY=your_key_here  (Windows)
#                  or export ANTHROPIC_API_KEY=your_key_here (Mac/Linux)
# =============================================================================

import os
import json
import anthropic
import jsonschema

# =============================================================================
# STEP 1: Load the system prompt
# =============================================================================

# Read the system prompt from the text file (keeps things modular and editable)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_PATH = os.path.join(SCRIPT_DIR, "system_prompt.txt")
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.json")

with open(SYSTEM_PROMPT_PATH, "r") as f:
    SYSTEM_PROMPT = f.read()

with open(SCHEMA_PATH, "r") as f:
    TXY_SCHEMA = json.load(f)

# =============================================================================
# STEP 2: Define the LLM call function
# =============================================================================

def call_llm(user_question: str) -> dict:
    """
    Sends the user's question to Claude and returns the parsed JSON payload.

    Args:
        user_question: Natural language question from the user.

    Returns:
        Parsed JSON dict if successful.

    Raises:
        ValueError: If the LLM response cannot be parsed as JSON.
    """
    # Initialize the Anthropic client
    # It automatically reads ANTHROPIC_API_KEY from environment variables
    client = anthropic.Anthropic()

    print(f"\n[LLM] Sending request to Claude...")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",   # Use the latest capable model
        max_tokens=512,                      # JSON payload is small, 512 is plenty
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_question}
        ]
    )

    # Extract the raw text response
    raw_text = response.content[0].text.strip()
    print(f"[LLM] Raw response received:\n    {raw_text}\n")

    # Parse the JSON — if the LLM followed instructions, this will work cleanly
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM did not return valid JSON.\n"
            f"Raw response: {raw_text}\n"
            f"JSON error: {e}"
        )

    return parsed


# =============================================================================
# STEP 3: Validate the parsed JSON against the schema
# =============================================================================

def validate_payload(payload: dict) -> bool:
    """
    Validates the parsed JSON against the Txy schema.

    Returns True if valid, prints errors and returns False if invalid.
    """
    # Check for an "error" key first — means LLM flagged incomplete input
    if "error" in payload:
        print(f"[VALIDATION] LLM flagged an issue: {payload['error']}")
        return False

    try:
        jsonschema.validate(instance=payload, schema=TXY_SCHEMA)
        print("[VALIDATION] Payload is valid. ✓")
        return True
    except jsonschema.ValidationError as e:
        print(f"[VALIDATION] Schema validation failed: {e.message}")
        return False


# =============================================================================
# STEP 4: Main pipeline
# =============================================================================

def run_pipeline(user_question: str) -> dict | None:
    """
    Full Group 1 pipeline: question → LLM → validated JSON payload.

    Returns the validated payload dict, or None if something went wrong.
    This payload is what gets handed off to Group 2's DWSIM engine.
    """
    print("=" * 60)
    print("GROUP 1: LLM Orchestrator — Txy Pipeline")
    print("=" * 60)
    print(f"User Question: \"{user_question}\"")

    # Step A: Call the LLM
    try:
        payload = call_llm(user_question)
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        return None

    # Step B: Validate
    is_valid = validate_payload(payload)

    if not is_valid:
        print("\n[PIPELINE] Payload failed validation. Cannot pass to Group 2.")
        print("[PIPELINE] Please refine the user question and try again.")
        return None

    # Step C: Output the clean payload for Group 2
    print("\n" + "=" * 60)
    print("FINAL PAYLOAD (ready for Group 2 - DWSIM Engine):")
    print("=" * 60)
    print(json.dumps(payload, indent=2))

    # Optionally save the payload to a file so Group 2 can read it
    output_path = os.path.join(SCRIPT_DIR, "..", "output", "txy_request.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n[OUTPUT] Payload saved to: output/txy_request.json")
    print("[OUTPUT] Group 2 can now read this file to run DWSIM.")

    return payload


# =============================================================================
# STEP 5: Entry point — run interactively
# =============================================================================

if __name__ == "__main__":
    print("\nTxy Phase Diagram — LLM Orchestrator")
    print("Type your question below. Example:")
    print('  "Generate a Txy diagram for ethanol and water at 1 atm"\n')

    user_input = input("Your question: ").strip()

    if not user_input:
        print("[ERROR] No input provided. Exiting.")
    else:
        result = run_pipeline(user_input)

        if result:
            print("\n[DONE] Pipeline completed successfully.")
            print("       Payload is ready for Group 2 (DWSIM engine).")
        else:
            print("\n[DONE] Pipeline ended with errors. Check messages above.")
