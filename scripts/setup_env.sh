#!/usr/bin/env bash
# One-time SCHE-MA environment setup. Idempotent-ish.
set -euo pipefail
cd "$(dirname "$0")/.."

PYENV_312="${PYENV_312:-$HOME/.pyenv/versions/3.12.12/bin/python}"

# 1. venv + deps
if [ ! -d .venv ]; then
  "$PYENV_312" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e .

# 2. cybergym submodule (symlink to the existing local clone to avoid 240GB re-download)
mkdir -p external
if [ ! -e external/cybergym ]; then
  ln -s /data/seory0/projects/cybergym external/cybergym
fi

# 3. data symlinks
ln -sf /home/seory0/projects/CyberMAS/tasks_metadata.json data/tasks_metadata.json
ln -sf /data/seory0/projects/cybergym/mask_map.json        data/mask_map.json

# 4. recon/indexing CLI tools (best-effort)
.venv/bin/python -m pip install -q semgrep || echo "semgrep install skipped"
command -v rg      >/dev/null || echo "NOTE: install ripgrep (rg) for recon"
command -v ctags   >/dev/null || echo "NOTE: install universal-ctags for MCP indexing (M4)"

echo "Setup done. Start the server with scripts/start_server.sh, then:"
echo "  .venv/bin/python -m schemata run-task --task-id arvo:10400 --backend claude_code"
