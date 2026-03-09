@echo off
REM ============================================================
REM  DWSIM-AI Full Pipeline — run_pipeline.bat
REM
REM  USAGE (interactive — will prompt for your question):
REM    run_pipeline.bat
REM
REM  USAGE (non-interactive):
REM    run_pipeline.bat "Txy diagram for ethanol and water at 1 atm"
REM
REM  PREREQUISITES (one-time setup — see SETUP below):
REM    1. conda activate DWSim
REM    2. set ANTHROPIC_API_KEY=sk-ant-api03-...
REM    3. pip install anthropic
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
echo  STEP 1 / 2  --  LLM Orchestrator  (Group 1^)
echo ============================================================
echo.

IF "%~1"=="" (
    python LLM/orchestrator.py
) ELSE (
    echo %~1 | python LLM/orchestrator.py
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

echo.
echo [INFO] JSON produced: DWSIM_ry_test\tasks\examples\%LATEST_JSON%

REM ── 6. STEP 2: DWSIM Txy Engine produces CSV + PNG ───────────
echo.
echo ============================================================
echo  STEP 2 / 2  --  DWSIM Txy Engine  (Group 2^)
echo ============================================================
echo.

python DWSIM_ry_test/tasks/txy_engine.py ^
    "DWSIM_ry_test/tasks/examples/%LATEST_JSON%" 

IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Txy engine failed. See messages above.
    exit /b 1
)

echo.
echo ============================================================
echo  ALL DONE.  Outputs in:  output\
echo ============================================================
echo.
dir output\*.csv output\*.png 2>nul
