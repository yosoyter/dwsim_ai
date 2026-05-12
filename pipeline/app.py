import gradio as gr
import sys, io, os
os.environ["DWSIM_AI_VERBOSE"] = "0"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _ROOT)

from pipeline.orchestrator import guard_query, run_full_pipeline

def run_pipeline(user_input):
    user_input = user_input.strip()
    if not user_input:
        yield "Please enter a question."
        return

    # Patch stdout so we can intercept prints line by line
    output_so_far = []

    class StreamingBuffer:
        def write(self, text):
            if text and text != "\n":
                filtered = _filter_line(text.rstrip())
                if filtered is not None:
                    output_so_far.append(filtered)
        def flush(self):
            pass

    sys.stdout = StreamingBuffer()
    try:
        guard = guard_query(user_input)
        verdict = guard.get("verdict", "pass")

        if verdict == "reject":
            output_so_far.append(f"Sorry, I can't help with that.")
            output_so_far.append(guard.get("reason", ""))
            output_so_far.append("\nI handle: flash, heat exchanger, compressor, expander, and flowsheet simulations.")
        elif verdict == "clarify":
            output_so_far.append("I need a bit more information:")
            output_so_far.append(guard.get("followup", "Could you provide more details?"))
        else:
            run_full_pipeline(user_input)
    except Exception as e:
        output_so_far.append(f"[ERROR] {e}")
    finally:
        sys.stdout = sys.__stdout__

    yield "\n".join(output_so_far)


def _filter_line(line: str):
    """Return the line if it should be shown in UI, else None."""
    skip_prefixes = (
        "=" * 10,
        "LLM ",
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
        "[flowsheet_master]",
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
        "Your question:",
    )
    stripped = line.strip()
    if not stripped:
        return None
    if any(stripped.startswith(p) for p in skip_prefixes):
        return None
    if stripped.startswith("{") or stripped.startswith('"task_type"'):
        return None
    return line


# ── Progress messages injected at key stages ──────────────────────────────
# To show live progress, wrap run_full_pipeline stages with explicit yields.
# Since Gradio generators require yields, we use a simpler approach:
# show a "Running simulation..." placeholder while processing.

import threading

def run_with_progress(user_input):
    user_input = user_input.strip()
    if not user_input:
        yield "Please enter a question."
        return

    yield "Evaluating query..."

    guard = guard_query(user_input)
    verdict = guard.get("verdict", "pass")

    if verdict == "reject":
        yield f"Sorry, I can't help with that.\n{guard.get('reason', '')}\n\nI handle: flash, heat exchanger, compressor, expander, and flowsheet simulations."
        return
    if verdict == "clarify":
        yield f"I need a bit more information:\n\n{guard.get('followup', 'Could you provide more details?')}"
        return

    yield "Query accepted. Generating simulation plan..."

    # Run pipeline in background thread so we can yield progress
    result_holder = {"output": None, "error": None, "stage": "codegen"}
    buffer = io.StringIO()

    def pipeline_thread():
        sys.stdout = buffer
        try:
            run_full_pipeline(user_input)
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            sys.stdout = sys.__stdout__
            result_holder["stage"] = "done"

    thread = threading.Thread(target=pipeline_thread, daemon=True)
    thread.start()

    # Poll and yield status updates while pipeline runs
    import time
    shown_dwsim = False
    while thread.is_alive():
        time.sleep(1.5)
        current = buffer.getvalue()
        if not shown_dwsim and ("[dwsim_core]" in current or "[flowsheet_master]" in current
                                or "[heat_master]" in current or "[flash_master]" in current
                                or "[comp_master]" in current or "[hx_master]" in current):
            yield "Plan generated. Running DWSIM simulation..."
            shown_dwsim = True

    thread.join()

    if result_holder["error"]:
        yield f"[ERROR] {result_holder['error']}"
        return

    raw = buffer.getvalue()
    lines = [_filter_line(l) for l in raw.splitlines()]
    result = "\n".join(l for l in lines if l is not None).strip()
    yield result if result else "Simulation complete — no output was returned."


with gr.Blocks(title="SIMQuery") as demo:
    gr.Markdown("# SIMQuery\nAsk a process simulation question in plain English. I handle: flash, heat exchanger, compressor, expander, and flowsheet simulations.")

    with gr.Row():
        question = gr.Textbox(
            lines=3,
            placeholder='e.g. "Flash a 50/50 propane/n-butane mixture at 10 bar and 50°C"',
            label="Your Question",
            scale=4,
        )
        run_btn = gr.Button("Run", variant="primary", scale=1)

    output = gr.Textbox(lines=25, label="Results")

    run_btn.click(fn=run_with_progress, inputs=question, outputs=output)
    question.submit(fn=run_with_progress, inputs=question, outputs=output)

if __name__ == "__main__":
    demo.launch()
