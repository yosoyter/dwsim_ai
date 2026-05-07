import gradio as gr
import sys, io
import os

# Tell the pipeline to run in quiet mode
os.environ["DWSIM_AI_VERBOSE"] = "0"

from llm2_orchestrator import guard_query, run_full_pipeline

SEPARATOR = "=" * 60

def run_pipeline(user_input):
    user_input = user_input.strip()
    if not user_input:
        return "Please enter a question."

    buffer = io.StringIO()
    sys.stdout = buffer
    try:
        guard = guard_query(user_input)
        verdict = guard.get("verdict", "pass")

        if verdict == "reject":
            print(f"Sorry, I can't help with that.\n{guard.get('reason', '')}")
            print("\nI handle: flash separation, heaters/coolers, heat exchangers, compressors, expanders, and flowsheets of these.")
        elif verdict == "clarify":
            print(f"I need a bit more information:\n{guard.get('followup', 'Could you provide more details?')}")
        else:
            run_full_pipeline(user_input)
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sys.stdout = sys.__stdout__

    # Filter output — keep only lines that matter to the user
    raw = buffer.getvalue()
    filtered = _filter_output(raw)
    return filtered


def _filter_output(raw: str) -> str:
    """Strip internal pipeline noise, keep results and errors."""
    keep = []
    skip_prefixes = (
        "=" * 10,          # banners
        "GROUP ",
        "[LLM]",
        "[LLM #2]",
        "[llm2]",
        "[VALIDATION]",
        "[PIPELINE]",
        "[OUTPUT]",
        "[dwsim_core] DWSIM loaded",
        "[dwsim_core] Flowsheet created",
        "[dwsim_core] Property package set",
        "[dwsim_core] Saved",
        "[flowsheet_master] Building",
        "[flowsheet_master] Assembly",
        "[flowsheet_master] Solving",
        "[heat_exchanger]",
        "[compressor_expander]",
        "[hx_master]",
        "[flash_master]",
        "[heat_master]",
        "[comp_master]",
        "[guard]",
        "----",
        "User Question:",
        "FINAL PAYLOAD",
        "DWSIM-AI",
    )
    for line in raw.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(p) for p in skip_prefixes):
            continue
        if stripped.startswith("{") or stripped.startswith('"task_type"'):
            continue  # skip raw JSON lines
        keep.append(line)

    # Remove leading/trailing blank lines
    result = "\n".join(keep).strip()
    return result


demo = gr.Interface(
    fn=run_pipeline,
    inputs=gr.Textbox(
        lines=3,
        placeholder='e.g. "Flash a 50/50 propane/n-butane mixture at 10 bar and 50°C"',
        label="Your Question",
    ),
    outputs=gr.Textbox(lines=25, label="Results"),
    title="SIMQuery",
    description="Ask a process simulation question in plain English. I can handle flash separation, heaters/coolers, heat exchangers, compressors/expanders, and flowsheets of these",
    flagging_mode="never",
)

if __name__ == "__main__":
    demo.launch()