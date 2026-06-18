#!/usr/bin/env bash
# Portable SCHE-MA setup. Works on any machine with Python 3.12+.
# Usage: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. pick a Python 3.12+ interpreter
PY="${SCHEMA_PYTHON:-}"
_try_py() {
  # echo the resolved absolute path if $1 runs and is >= 3.12, else nothing.
  local cand="$1" abs ver maj min
  command -v "$cand" >/dev/null 2>&1 || return 1
  ver=$("$cand" -c 'import sys; print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || return 1
  [ -n "$ver" ] || return 1
  maj=${ver%.*}; min=${ver#*.}
  [ "$maj" -ge 3 ] 2>/dev/null && [ "$min" -ge 12 ] 2>/dev/null || return 1
  abs=$("$cand" -c 'import sys; print(sys.executable)' 2>/dev/null) || return 1
  echo "$abs"
}
if [ -z "$PY" ]; then
  for cand in python3.13 python3.12 python3; do
    PY=$(_try_py "$cand") && [ -n "$PY" ] && break
    PY=""
  done
fi
# pyenv fallback: if shims aren't pointing at a working 3.12+, search installed versions directly.
if [ -z "$PY" ] && command -v pyenv >/dev/null 2>&1; then
  pyenv_root=$(pyenv root 2>/dev/null || echo "$HOME/.pyenv")
  for v in $(ls "$pyenv_root/versions" 2>/dev/null | sort -rV); do
    cand="$pyenv_root/versions/$v/bin/python3"
    PY=$(_try_py "$cand") && [ -n "$PY" ] && break
    PY=""
  done
fi
if [ -z "$PY" ]; then
  echo "error: Python 3.12+ not found. Install it and re-run, or set SCHEMA_PYTHON=/path/to/python." >&2
  exit 1
fi
echo "[setup] python = $PY ($("$PY" --version))"

# 2. venv + editable install
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "[setup] created .venv"
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e .
echo "[setup] installed schemata (editable)"

# 3. optional: cybergym symlink (only if env var or auto-detect)
mkdir -p external
if [ ! -e external/cybergym ]; then
  CYBERGYM_LINK_SRC="${CYBERGYM_CLONE_DIR:-${CYBERGYM_DIR:-}}"
  if [ -n "$CYBERGYM_LINK_SRC" ] && [ -d "$CYBERGYM_LINK_SRC" ]; then
    ln -s "$CYBERGYM_LINK_SRC" external/cybergym
    echo "[setup] external/cybergym -> $CYBERGYM_LINK_SRC"
  else
    echo "[setup] external/cybergym not linked (set CYBERGYM_CLONE_DIR or CYBERGYM_DIR to enable arvo:* tasks)"
  fi
fi

# 4. local config files
mkdir -p config
if [ ! -f config/schemata.toml ]; then
  cp config/templates/schemata.toml config/schemata.toml
  echo "[setup] copied config/templates/schemata.toml -> config/schemata.toml"
fi
if [ ! -f config/routing_rules.json ]; then
  cp config/templates/routing_rules.json config/routing_rules.json
  echo "[setup] copied config/templates/routing_rules.json -> config/routing_rules.json"
fi

# 5. .env stub
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "[setup] copied .env.example -> .env  (edit ANTHROPIC_API_KEY if you'll use claude_api)"
fi

cat <<EOF

[setup] done.

  source .venv/bin/activate
  schema --help

Tips:
  - free-form prompts default to claude_code (uses your local Claude Code login).
  - switch to API: \`schema --backend claude_api\`  (needs ANTHROPIC_API_KEY).
EOF
