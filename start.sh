#!/usr/bin/env bash
# =============================================================================
#  PodClip launcher (macOS / Linux)
#  First run: creates .venv, installs dependencies, copies .env from
#  .env.example, checks your OpenRouter key, then starts the app.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# ---- pick a Python 3.10-3.13 (prefer 3.12; skip any 3.14+) ----------------
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done
if [ -z "$PY" ]; then
    printf '\n[podclip] Python was not found. Install Python 3.10-3.13, then run ./start.sh again.\n'
    exit 1
fi

# ---- .env from example ------------------------------------------------------
if [ ! -f ".env" ]; then
    cp .env.example .env
    printf '[podclip] Created .env from .env.example.\n'
fi

# ---- early key check (stdlib only) ------------------------------------------
if ! "$PY" check_env.py; then
    printf '\n[podclip] Open .env and paste your key after OPENROUTER_API_KEY=\n          Save the file, then run ./start.sh again.\n'
    exit 1
fi

# ---- venv -------------------------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
    printf '[podclip] Creating virtual environment...\n'
    "$PY" -m venv .venv
fi

printf '[podclip] Installing dependencies (first run takes a few minutes)...\n'
".venv/bin/python" -m pip install --upgrade pip --quiet
".venv/bin/python" -m pip install -r requirements.txt

printf '[podclip] Starting PodClip... opening the browser shortly.\n'
( sleep 1.5; xdg-open http://localhost:8501 2>/dev/null || open http://localhost:8501 2>/dev/null || true ) &
".venv/bin/python" -m streamlit run app.py
