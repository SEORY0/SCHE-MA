# SCHE-MA — Security CHallenge Exploitation Multi-Agent

> Mythos+ Task Force

Cost-efficient multi-agent system for the **CyberGym** benchmark (1,507 real-world
vulnerability PoC-generation tasks). Runs on **two interchangeable backends** behind one
`AgentBackend` interface:

- **Claude Code backend** (`claude_code`) — headless `claude -p` agentic sessions. Tools,
  caching, and the agent loop come from the Claude Code runtime. *(implemented — M1/M2)*
- **Claude API backend** (`claude_api`) — direct Anthropic Messages tool-loop with prompt
  caching, programmatic tool calling, and per-stage model routing. *(M3)*

## Pipeline

`Task Queue → Orchestrator/Router → Stage 1 Recon (Haiku + Semgrep) → [Stage 2 Analyze &
Reason (Opus, +ARVO instrumentation)] → Stage 3 Generate & Verify (Opus) → CyberGym server`

Adaptive routing (from `tasks_metadata.json`): **easy** skips Stage 2 (Sonnet, minimal
context), **medium** runs the full 3 stages (Opus + instrumentation), **hard** adds MCP
pre-indexing + extended thinking. Success = vulnerable build returns `exit_code != 0`.

Design rationale and full milestone plan: `~/.claude/plans/cybergym-federated-yao.md` §12.

## Quick start

```bash
bash scripts/setup_env.sh            # venv (py3.12) + deps + symlinks (one-time)
bash scripts/start_server.sh         # start the CyberGym submission server on :8666
.venv/bin/python -m schemata run-task --task-id arvo:10400 --backend claude_code
.venv/bin/python -m schemata run-subset --backend claude_code   # the 10-task subset
```

Outputs land in `runs/<timestamp>/<task>/`: `outcome.json`, `stage_*.json`,
`submissions.jsonl`, and `runs/<timestamp>/cost.json`.

## Layout

- `src/schemata/backends/` — `base.py` (the ABC), `claude_code.py`, `claude_api.py` (M3)
- `src/schemata/orchestrator.py` — route → stages → submit → confirm → record
- `src/schemata/cybergym/` — `task_gen.py` (subprocess to cybergym), `submit.py`, `ids.py`
- `src/schemata/{router,cost_tracker,prompt_loader,instrument,recon}.py`
- `src/schemata/indexing/` — Hard-repo MCP pre-indexing (M4)
- `prompts/` — stage system prompts + shared situational-context / output-contract / tool-profile
- `config/schemata.toml`, `config/routing_rules.json`

## Notes

- `external/cybergym` is a symlink to the local clone (avoids a 240 GB re-download);
  swap for a real submodule on a fresh machine.
- The Claude Code backend runs headless with `permission_mode = bypassPermissions`
  (configurable in `config/schemata.toml`) because `claude -p` cannot answer prompts; the
  agent runs bash/docker freely inside the task dir. Run on trusted machines only.
- A live agent run uses the `claude` CLI's auth and **spends real budget** (a trivial call
  already costs ~$0.15 due to the large default system prompt — caching amortizes this).
