# Legacy — AgentBeats A2A arena (retired)

SCHE-MA no longer runs against the **AgentBeats CyberGym arena**. Testing is now
**local CyberGym only** (`schemata run-task` / `run-subset` → `orchestrator.run_task`
→ a local `cybergym.server` reached via `cybergym.submit.SubmitClient`).

All arena/A2A code has been parked here so nothing in the active path depends on it.

## Where the arena code lives now

| Was | Now | Kind |
|---|---|---|
| `src/schemata/a2a/` | `src/schemata/legacy/a2a/` | package code (server, executor, brain, level3 recon) |
| `src/schemata/cybergym/transport.py` | `src/schemata/legacy/transport.py` | `A2AGreenSubmit` (green round-trip) |
| `src/schemata/cybergym/intake.py` | `src/schemata/legacy/intake.py` | `A2ATaskSource`, `infer_level`/`infer_label` |
| `Dockerfile` | `legacy/deploy/Dockerfile` | arena image |
| `amber-manifest.json5` | `legacy/deploy/amber-manifest.json5` | AgentBeats manifest |
| `scenario.leaderboard.toml` | `legacy/deploy/scenario.leaderboard.toml` | 49-task leaderboard scenario |
| `.github/workflows/build-image.yml` | `legacy/deploy/build-image.yml` | GHCR image build/push (now inert — outside `.github/workflows/`) |
| `scripts/smoke_arvo_10400.py` | `legacy/deploy/smoke_arvo_10400.py` | A2A-brain smoke test |

The package code stays under `src/schemata/legacy/` (not here) because it must remain
importable as `schemata.legacy.*` with its relative imports intact.

### Not moved (still active / local)
- `scripts/start_server.sh` — starts the **local** `cybergym.server` used by local runs.
- `src/schemata/discriminate.py` — generic Stage-4 false-positive referee; kept at
  top-level so the local pipeline can adopt it later (currently only the legacy brain wires it).
- `scenario.local.toml` — left in place.

## Status
- Tests for this code are marked `pytest.mark.skip` (see `tests/test_a2a_*.py`,
  `tests/test_discriminate.py`, `tests/test_transport.py`, `tests/test_intake.py`).
- `a2a-sdk` and `uvicorn` were removed from `pyproject.toml` dependencies (only the
  A2A server/executor used them).

## Reviving the arena (if ever needed)
1. Re-add `a2a-sdk[http-server]>=0.3.20,<1.0` and `uvicorn>=0.30` to `pyproject.toml`.
2. Run the server: `python -m schemata.legacy.a2a.server --host 0.0.0.0 --port 9009`.
3. Build the image from the repo root: `docker build -f legacy/deploy/Dockerfile -t <tag> .`
4. Move `build-image.yml` back under `.github/workflows/` and update its Dockerfile path.
5. Remove the skip marks from the legacy tests.
