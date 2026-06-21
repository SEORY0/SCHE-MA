# SCHE-MA - Security CHallenge Exploitation Multi-Agent

> Mythos+ Task Force

SCHE-MA is a local CyberGym agent for reproducing C/C++ memory-safety
vulnerabilities. Given a CyberGym task, it generates the vulnerable source
workspace, runs a staged LLM workflow, attempts to synthesize a crash-triggering
PoC, submits candidate PoCs to a local CyberGym server, and records the result
under `runs/`.

The active code path is local CyberGym only:

```text
task id -> gen_task -> route -> recon -> analyze -> generate -> submit -> confirm -> record
```

The retired AgentBeats A2A / arena integration has been moved to
`src/schemata/legacy/` and `legacy/deploy/`; see `legacy/README.md` if that path
ever needs to be revived.

## Install

Requires Python 3.12+.

```bash
bash scripts/setup.sh
source .venv/bin/activate
schema --help
```

The setup script creates `.venv`, installs this package in editable mode, and
copies `.env.example` to `.env` when needed. It can also link a local CyberGym
clone if `CYBERGYM_CLONE_DIR` is set.

`scripts/setup.sh` also creates local config files from tracked templates:
`config/templates/schemata.toml -> config/schemata.toml` and
`config/templates/routing_rules.json -> config/routing_rules.json`. The generated
`config/*` files are git-ignored so each machine can keep its own CyberGym paths.
For local `arvo:*` tasks, either set `CYBERGYM_CLONE_DIR` or `CYBERGYM_DIR`
before setup so `external/cybergym` is symlinked, or override `CYBERGYM_PYTHON`,
`CYBERGYM_DATA_DIR`, and `CYBERGYM_MASK_MAP` in your shell.

Manual install:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Console scripts are installed by the editable install:

- `schema` - interactive REPL
- `schemata` - Typer CLI

If `schema` is not found, activate the venv or call it directly:

```bash
./.venv/bin/schema --help
```

## Configuration

Main config lives in `config/schemata.toml`.

Important values:

- `[backend].default`: `claude_code` or `claude_api`
- `[server]`: local CyberGym server URL, data dir, mask map, and CyberGym Python
- `[budget]`: global and per-task soft budget
- `[models]`: stage model aliases
- `[stages.*]`: tool permissions and max turns per stage

Environment variables override the CyberGym / Anthropic pieces where supported:

- `ANTHROPIC_API_KEY`
- `CYBERGYM_SERVER_URL`
- `CYBERGYM_DATA_DIR`
- `CYBERGYM_MASK_MAP`
- `CYBERGYM_PYTHON`

For `claude_api`, put the key in `.env` or export it:

```bash
ANTHROPIC_API_KEY=sk-...
```

`claude_code` uses the local Claude Code CLI login instead of an API key.

## Local CyberGym Server

Task execution expects a local CyberGym submit server. The helper script uses
`CYBERGYM_DIR` and defaults to `/data/seory0/projects/cybergym`:

```bash
scripts/start_server.sh
```

Optional overrides:

```bash
CYBERGYM_DIR=/path/to/cybergym PORT=8666 scripts/start_server.sh
```

The SCHE-MA config must point at the same CyberGym data, mask map, and Python
environment used by that server.

## Interactive Runner

Launch the REPL:

```bash
schema
schema --backend claude_api
schema --config config/schemata.toml
```

Useful commands:

```text
schema> /help
schema> /task arvo:10400
schema> /subset 5
schema> /backend claude_api
schema> /model sonnet
schema> /config
schema> /cost
schema> /exit
```

Free-form prompts without a leading `/` are sent to the active backend as a
normal chat-style request.

## CLI

Run one task:

```bash
schemata run-task --task-id arvo:10400 --backend claude_code
```

Run a subset from `data/subset_tasks.txt`:

```bash
schemata run-subset --backend claude_code --limit 5
```

Launch the same REPL through the CLI:

```bash
schemata repl
```

There is also a thin wrapper that defaults to `run-task`:

```bash
python scripts/run_task.py --task-id arvo:10400 --backend claude_code
```

## Pipeline

Routing is controlled by `config/routing_rules.json`.

| Difficulty | Stages | Instrumentation | Thinking | Generate model |
|---|---|---:|---:|---|
| `easy` | `recon`, `generate` | no | no | `sonnet` |
| `medium` | `recon`, `analyze`, `generate` | yes | no | `sonnet` |
| `hard` | `recon`, `analyze`, `generate` | yes | yes | `opus` |

Stage model defaults are configured in generated local `config/schemata.toml`
(template: `config/templates/schemata.toml`):

```toml
[models]
recon = "haiku"
analyze = "sonnet"
discriminate = "sonnet"

[models.by_difficulty]
easy = "sonnet"
medium = "sonnet"
hard = "opus"
```

`orchestrator.run_task` can promote an `easy` task to include `analyze` when
cheap recon fails to localize the bug, and can retry `generate` once with Opus
when no PoC was produced.

## Backends

Both backends implement the same `AgentBackend` stage contract.

### `claude_code`

Runs each stage as a headless `claude -p` subprocess in the task directory.
The generate stage submits with `bash submit.sh <poc>`, and the orchestrator
then independently confirms the winning PoC through `SubmitClient`.

This backend uses:

- local Claude Code authentication
- `permission_mode = "bypassPermissions"` from config
- no `ANTHROPIC_API_KEY`

### `claude_api`

Runs an Anthropic Messages tool loop with SCHE-MA's local tool dispatcher.
The generate stage uses the `submit_poc` tool, and the orchestrator still
performs an independent confirmation afterward.

This backend uses:

- `ANTHROPIC_API_KEY`
- prompt-cache helpers in `src/schemata/backends/prompt_cache.py`
- tool schemas and permissions in `src/schemata/backends/tools/`

## Outputs

Runs are written to `runs/<timestamp>/<task>/`.

Typical files:

- `outcome.json` - final task outcome and route plan
- `stage_recon.json`
- `stage_analyze.json`
- `stage_generate.json`
- `submissions.jsonl`
- `escalation.json` when recon promoted the task
- `no_submit_retry.json` when the no-PoC retry fired
- `runs/<timestamp>/cost.json`
- `runs/<timestamp>/subset_summary.json` for subset runs

Analyze existing subset runs:

```bash
python scripts/analyze_subset.py --run runs/<run_id> --out-md docs/subset_results.md
```

## Layout

- `src/schemata/cli/` - both entry points: `main.py` (`schemata` Typer CLI) and `repl.py`/`commands.py`/`prompt_runner.py`/`ui.py` (`schema` REPL)
- `src/schemata/pipeline/` - the reproduction pipeline: `orchestrator.py` (local CyberGym task driver), `router.py` (task metadata → pipeline plan), `prompt_loader.py` (stage prompt rendering + `StageRequest` assembly), `recon.py`, `harness.py`, `instrument.py`, and `discriminate.py` (backend-agnostic Stage 4 referee; not wired into the active local route by default)
- `src/schemata/core/` - shared spine: `models.py` (Pydantic models), `config.py` (settings + paths), `cost_tracker.py`, `util.py`
- `src/schemata/knowledge/` - `atomic_vulns.py` vulnerability taxonomy + recipe retrieval
- `src/schemata/backends/` - backend interface, Claude Code backend, Claude API backend
- `src/schemata/backends/tools/` - Claude API tool definitions, permissions, dispatcher
- `src/schemata/cybergym/` - task generation, task ID metadata, submit client
- `src/schemata/legacy/` - retired AgentBeats A2A / arena code
- `config/templates/` - tracked config templates; generated `config/*` is local/git-ignored
- `skills/` - stage prompts (`stages/`), shared context (`shared/`), atomic-vuln knowledge (`knowledge/`)
- `scripts/` - setup, server, task wrapper, run analysis helpers
- `data/subset_tasks.txt` - default subset list
- `docs/` - design notes and experiment reports

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Current local check:

```text
80 passed, 49 skipped
```

Most skipped tests cover the retired A2A / arena integration.

## Notes

- The active path assumes a local CyberGym clone and server for `arvo:*` /
  `oss-fuzz:*` task runs.
- The config in this repository currently contains machine-local CyberGym paths;
  override them with environment variables or a custom config on another machine.
- `claude_api` calls spend real Anthropic API budget.
- `claude_code` runs with broad local permissions inside the task workspace; use it
  only on trusted machines.
