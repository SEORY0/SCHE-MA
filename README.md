# SCHE-MA — Security CHallenge Exploitation Multi-Agent

> Mythos+ Task Force

SCHE-MA is a C/C++ memory-safety vulnerability reproduction agent: given a
vulnerable source bundle plus optional sanitizer trace, patch diff, and harness
evidence, it tries to synthesize a crash-triggering PoC. **CyberGym** remains the
primary benchmark adapter (1,507 real-world tasks; ARVO + OSS-Fuzz), not the only
target. The engine ships with two interchangeable backends behind one
`AgentBackend` interface:

- **Claude Code backend** (`claude_code`) — headless `claude -p` sessions, no API key
  required (uses Claude Code subscription auth).
- **Claude API backend** (`claude_api`) — direct Anthropic Messages tool-loop with
  prompt caching and per-stage model routing.

The AgentBeats CyberGym leaderboard purple agent runs on top of the same engine
via the A2A wrapper in `src/schemata/a2a/`.

---

## Install

Requires **Python 3.12+** (see `pyproject.toml`). The `schema` and `schemata`
console scripts are installed by `pip install -e .` into the active environment;
they are NOT pre-built binaries on disk. If you type `schema` and get
`command not found`, either the venv isn't activated or the editable install
hasn't happened.

### Quick path — on the dev box (with `pyenv 3.12.12` + local cybergym clone)

```bash
bash scripts/setup_env.sh        # one-time: creates .venv, pip install -e ., symlinks
source .venv/bin/activate        # activate so `schema` is on PATH
schema --help                    # smoke test
```

### Fresh machine (no pyenv, no local cybergym)

```bash
# 1) ensure Python 3.12+ is available
python3 --version                # must be >= 3.12

# 2) create a venv (use python3.12 explicitly if `python3` is older)
python3 -m venv .venv
source .venv/bin/activate

# 3) install SCHE-MA in editable mode — this registers the `schema` + `schemata`
#    console scripts in .venv/bin/
pip install --upgrade pip
pip install -e .

# 4) verify
which schema                     # should print <repo>/.venv/bin/schema
schema --help
```

If you skip `source .venv/bin/activate`, you can still call the script directly:

```bash
./.venv/bin/schema               # equivalent to `schema` after activation
```

### Permanent PATH (optional)

If you don't want to `source .venv/bin/activate` every shell:

```bash
echo 'export PATH="'"$PWD"'/.venv/bin:$PATH"' >> ~/.bashrc
exec $SHELL
schema --help
```

### Troubleshooting `command not found: schema`

| Symptom | Fix |
|---|---|
| `schema: command not found` after fresh clone | run `pip install -e .` inside an activated venv |
| `pip install -e .` errored on Python 3.11 or older | install Python 3.12 (`pyenv install 3.12.12` or distro package) and re-create the venv |
| Installed in a venv that isn't activated | `source .venv/bin/activate` or use `./.venv/bin/schema` |
| `cybergym` symlink missing (only matters for `/task arvo:*`) | rerun `bash scripts/setup_env.sh` (edit the path inside if your clone lives elsewhere) |

---

## `schema` — interactive runner (Claude Code-style REPL)

```bash
schema                            # launches the REPL
schema --backend claude_api       # override default backend
schema --config path/to/toml      # override config
```

Inside the REPL:

```
schema> /help                     show slash commands
schema> /task arvo:10400          run a single CyberGym task
schema> /subset 5                 run first 5 of data/subset_tasks.txt
schema> /backend claude_api       switch backend mid-session
schema> /config                   print resolved settings
schema> /cost                     session cost totals
schema> what's a heap-buffer-overflow?     # free-form prompt → current backend
schema> /exit
```

Free-form prompts (no leading `/`) route by the active backend:

- `backend=claude_code` → `claude -p <prompt>` subprocess (Claude Code auth, **no API**)
- `backend=claude_api` → Anthropic Messages API (uses `ANTHROPIC_API_KEY`)

`/task` and `/subset` always honor the active backend.

The runner lives in `src/schemata/runner/` as four small modules
(`repl.py`, `commands.py`, `prompt_runner.py`, `__main__.py`). It does not
import the engine modules apart from `orchestrator.run_task`, so engine and
arena code are unaffected by REPL changes.

---

## Classic CLI

```bash
schemata run-task   --task-id arvo:10400 --backend claude_code
schemata run-subset --backend claude_code --limit 5
schemata reproduce  --repo ./vul-src --harness-cmd './fuzzer {poc}' --description bug.txt
schemata repl       # same as `schema`
```

Outputs land in `runs/<timestamp>/<task>/`: `outcome.json`, `stage_*.json`,
`submissions.jsonl`, and `runs/<timestamp>/cost.json`.

---

## Pipeline

```
Task → classifier → seed prior.mech_intel → recon(carry-forward) → analyze → generate → submit
                                                    haiku    sonnet    sonnet|opus
```

**Task classifier** (`src/schemata/a2a/task_class.py`) inspects which attachments
the green agent sent, not metadata:

| Class | Files present | `generate` model | Notes |
|---|---|---|---|
| `arvo_level3` | description + error + patch + repo-vul | **opus** | full ground truth |
| `oss_fuzz`    | description + repo-vul                 | **opus** | hardest: no patch/error |
| `arvo_level1` | description + repo-vul (only)          | sonnet   | no ground truth to pay for opus |

Mechanical extractors (`level3_intel.py`) parse `patch.diff` (hunks + inline
-/+ bodies), `error.txt` (sanitizer, sink, frames), and the LLVMFuzzerTestOneInput
harness from `repo-vul.tar.gz` (frame-hint exact match, no name heuristic), and
summarize newly added patch invariants. The result is preserved under
`prior["mech_intel"]`; recon carries those fields forward into `prior["recon"]`
so analyze/generate can use both the immutable seed and the refined recon.

Stage models are configurable in `config/schemata.toml`:

```toml
[models]
recon = "haiku"
analyze = "sonnet"

[models.by_difficulty]
easy   = "sonnet"
medium = "sonnet"
hard   = "opus"      # arvo_level3 + oss_fuzz route here
```

## CyberGym Knowledge Policy

SCHE-MA keeps leaderboard-time knowledge separate from offline research aids.
For CyberGym Level 1 runs, use only the supplied vulnerable repo,
`description.txt`, task label if provided by the harness, generated workspace
outputs, and verifier feedback. Do not use CyberGym-wide `tasks.json`, precomputed
per-task indexes, historical PoCs, patch diffs, or sanitizer traces unless those
artifacts are part of the task setting being evaluated.

Policy and taxonomy references:

- `docs/cybergym-knowledge-policy.md`
- `docs/memory-poc-taxonomy.md`

Level 1-safe description classification:

```bash
python3 scripts/classify_cybergym_description.py --text-file path/to/description.txt
```

Offline aggregate classification of a downloaded CyberGym metadata JSON is
available only for research and prompt/taxonomy design:

```bash
python3 scripts/classify_cybergym_description.py --offline-tasks-json tasks.json --summary
```

---

## AgentBeats arena (purple agent)

The arena container is `ghcr.io/seory0/schemata-cybergym:latest`. The amber
manifest is `amber-manifest.json5`. Quick Submit Config templates live under
`submit/`:

```bash
submit/quick-submit-level3.json   # 49-task full level3 run, num_workers=5
submit/quick-submit-smoke5.json   # 5-task smoke test
```

Upload either at https://agentbeats.dev/agentbeater/cybergym Quick Submit with
your `ANTHROPIC_API_KEY` secret.

Rebuild + push:

```bash
docker build -t ghcr.io/seory0/schemata-cybergym:<tag> -t .../:latest .
docker push  ghcr.io/seory0/schemata-cybergym:<tag>
docker push  ghcr.io/seory0/schemata-cybergym:latest
```

---

## Layout

- `src/schemata/runner/` — interactive REPL (commands, prompt routing, REPL loop)
- `src/schemata/core/` — generic C/C++ memory-safety reproduction models, intake, verifier, runner
- `src/schemata/backends/` — `base.py`, `claude_code.py`, `claude_api.py`, `prompt_cache.py`
- `src/schemata/a2a/` — AgentBeats wrapper: server, executor, brain, task classifier, level3/oss-fuzz mechanical extractors
- `src/schemata/orchestrator.py` — CyberGym adapter path: route → stages → submit → confirm → record
- `src/schemata/cybergym/` — `task_gen.py`, `submit.py`, `intake.py`, `ids.py`
- `src/schemata/{router,cost_tracker,prompt_loader,instrument,recon}.py`
- `prompts/` — stage system prompts + shared situational-context / output-contract
- `config/schemata.toml`, `config/routing_rules.json`
- `scripts/classify_cybergym_description.py` — description-only memory bug family classifier
- `submit/` — Quick Submit Config JSONs for the leaderboard

---

## Notes

- The Claude Code backend runs with `permission_mode = bypassPermissions` because
  `claude -p` cannot answer prompts. Run on trusted machines only.
- `external/cybergym` is a symlink to a local clone; swap for a real submodule on
  a fresh machine.
- A live `claude_api` call spends real budget. The interactive REPL prints token
  usage to `session.last_usage`; check `/cost` for cumulative totals.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q       # 70+ tests, all paths covered
```
