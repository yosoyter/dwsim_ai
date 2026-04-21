import os
import json
import re
import anthropic

LLM3_SYSTEM_PROMPT = """You are a precise evaluator for a chemical engineering simulation pipeline.

You will receive:
1. The user's original question
2. The output that the pipeline produced in response

Your only job is to evaluate whether the output correctly and completely answers
the user's question. All you need to do is return a single valid JSON object with the following keys:

This is the exact output format that you must follow. Do not include any explanations, just return the JSON.
{
  "score": <integer 1-10>,
  "answered": <true|false>,
  "complete": <true|false>,
  "issues": "<string describing any problems, or 'None' if perfect>",
  "suggestion": "<one sentence on how to improve, or 'None' if perfect>"
}

Follow this scoring guide:
10 — Perfect. Output directly and completely answers the question with correct units and formatting.
8-9 — Good. Answered correctly but minor formatting or extra information issues.
6-7 — Partial. Core answer is present but something is missing or unclear.
4-5 — Poor. Output is related but does not clearly answer what was asked.
1-3 — Bad. Output is wrong, irrelevant, or the pipeline clearly failed.

Here are some specific things to check for based on common question types:
- If the user asked for a specific value (e.g. vapor fraction), check that exact value is clearly stated.
- If the user asked for compositions, check that all components are listed with values.
- If the user asked for temperature, check the correct stream temperature is reported with units.
- If the output contains an error message or traceback, score = 1 and answered = false.
- If the output is empty or just whitespace, score = 1 and answered = false.
- Do not penalize for extra information unless it obscures the answer.
- Always return valid JSON. Nothing else.
"""

def evaluate_output(user_question: str, pipeline_output: str) -> dict:
    client = anthropic.Anthropic()

    user_message = f"""User's original question:
\"{user_question}\"

Pipeline output:
\"\"\"
{pipeline_output}
\"\"\"

Evaluate whether this output correctly answers the user's question."""

    print("\n[LLM #3] Evaluating pipeline output...")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=LLM3_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        raw_text = response.content[0].text.strip()
        raw_text = re.sub(r"```json|```", "", raw_text).strip()

        evaluation = json.loads(raw_text)

        print(f"[LLM #3] Evaluation complete.")
        return evaluation

    except Exception as e:
        print(f"[LLM #3] Evaluation failed: {e}")
        return {
            "score": 0,
            "answered": False,
            "complete": False,
            "issues": f"Evaluator error: {str(e)}",
            "suggestion": "Check API key and network connection."
        }

def print_evaluation(evaluation: dict) -> None:
    print("\n" + "=" * 60)
    print("LLM #3 Evaluation Report")
    print(f"  Score      : {evaluation.get('score', 'N/A')} / 10")
    print(f"  Answered   : {evaluation.get('answered', 'N/A')}")
    print(f"  Complete   : {evaluation.get('complete', 'N/A')}")
    print(f"  Issues     : {evaluation.get('issues', 'N/A')}")
    print(f"  Suggestion : {evaluation.get('suggestion', 'N/A')}")

if __name__ == "__main__":
    print("LLM #3 Evaluator — Standalone Test")
    print("Paste a user question and a pipeline output to evaluate.\n")

    user_q = input("User question: ").strip()
    print("Pipeline output (paste, then press Enter twice):")

    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)

    pipeline_out = "\n".join(lines)

    evaluation = evaluate_output(user_q, pipeline_out)
    print_evaluation(evaluation)
