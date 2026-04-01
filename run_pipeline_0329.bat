@echo off
REM ============================================================
REM  DWSIM-AI Full Pipeline — run_pipeline.bat
REM
REM  Supports TWO task types (auto-detected from LLM JSON output):
REM    txy   → DWSIM_ry_test/tasks/txy_engine.py
REM    flash → DWSIM_ry_test/tasks/flash_engine.py
REM
REM  USAGE (interactive — will prompt for your question):
REM    run_pipeline.bat
REM
REM  USAGE (non-interactive, pass query as argument):
REM    run_pipeline.bat "Txy diagram for ethanol and water at 1 atm"
REM    run_pipeline.bat "Flash separation of H2, CH4, Benzene, Toluene at 3447370 Pa and 410 K"
REM
REM  PREREQUISITES (one-time setup):
REM    1. conda activate DWSim
REM    2. set ANTHROPIC_API_KEY=sk-ant-api03-...
REM    3. pip install anthropic pandas numpy matplotlib
REM
REM  LLM FILES (as of 2025-03-29):
REM    LLM/orchestrator_0329.py
REM    LLM/system_prompt_0329.txt   (read automatically by orchestrator_0329.py)
REM ============================================================

REM ── 1. Check API key ─────────────────────────────────────────
IF "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo [ERROR] ANTHROPIC_API_KEY is not set.
    echo.
    echo   Run:  set ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
    echo   Then re-run this script.
    echo.
    exit /b 1
)

REM ── 2. Move to repo root (same folder as this .bat file) ─────
cd /d "%~dp0"

REM ── 3. Activate DWSim conda environment ──────────────────────
call conda activate DWSim 2>nul
IF ERRORLEVEL 1 (
    echo [WARNING] Could not activate 'DWSim' conda env. Using current Python.
)

REM ── 4. STEP 1: LLM Orchestrator produces JSON ────────────────
echo.
echo ============================================================
echo  STEP 1 / 2  --  LLM Orchestrator  (Group 1)
echo ============================================================
echo.

IF "%~1"=="" (
    python LLM/orchestrator_0329.py
) ELSE (
    echo %~1 | python LLM/orchestrator_0329.py
)

IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Orchestrator failed. See messages above.
    exit /b 1
)

REM ── 5. Find the JSON just written ────────────────────────────
FOR /F "delims=" %%F IN ('dir /b /od DWSIM_ry_test\tasks\examples\*.json 2^>nul') DO SET LATEST_JSON=%%F

IF "%LATEST_JSON%"=="" (
    echo.
    echo [ERROR] No JSON found in DWSIM_ry_test\tasks\examples\
    exit /b 1
)

SET LATEST_JSON_PATH=DWSIM_ry_test\tasks\examples\%LATEST_JSON%
echo.
echo [INFO] JSON produced: %LATEST_JSON_PATH%

REM ── 6. Detect task_type from JSON ────────────────────────────
REM  Use Python one-liner to extract "task_type" field — handles
REM  any whitespace/formatting from the LLM output.
FOR /F "usebackq delims=" %%T IN (
    `python -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('task_type','txy'))" "%LATEST_JSON_PATH%"`
) DO SET TASK_TYPE=%%T

echo [INFO] Detected task_type: %TASK_TYPE%

REM ── 7. STEP 2: Route to the correct engine ───────────────────
echo.
echo ============================================================
echo  STEP 2 / 2  --  DWSIM Engine  (Group 2)
echo ============================================================
echo.

IF /I "%TASK_TYPE%"=="txy" (
    echo [INFO] Running Txy engine...
    python DWSIM_ry_test/tasks/txy_engine.py "%LATEST_JSON_PATH%"
    IF ERRORLEVEL 1 (
        echo.
        echo [ERROR] Txy engine failed. See messages above.
        exit /b 1
    )
) ELSE IF /I "%TASK_TYPE%"=="flash" (
    echo [INFO] Running Flash engine...
    python DWSIM_ry_test/tasks/flash_engine.py "%LATEST_JSON_PATH%"
    IF ERRORLEVEL 1 (
        echo.
        echo [ERROR] Flash engine failed. See messages above.
        exit /b 1
    )
) ELSE (
    echo.
    echo [ERROR] Unknown task_type: "%TASK_TYPE%"
    echo         Supported types: txy, flash
    echo         Check LLM/system_prompt_0329.txt to ensure it outputs one of these.
    exit /b 1
)

REM ── 8. Done — show outputs ────────────────────────────────────
echo.
echo ============================================================
echo  ALL DONE.  Outputs in:  output\
echo ============================================================
echo.
echo --- CSV files ---
dir output\*.csv 2>nul
echo.
echo --- PNG files ---
dir output\*.png 2>nul
echo.
echo --- DWSIM flowsheet files ---
dir output\*.dwxmz 2>nul