"""LEGACY — AgentBeats A2A "purple agent" / CyberGym arena code.

Retired: the project now tests only against a LOCAL CyberGym server
(`orchestrator.run_task` + `cybergym.submit.SubmitClient`). Nothing in the active
code path imports this subpackage; it is kept for reference and possible revival.

Contents (all arena-only):
- ``legacy.a2a``       — A2A server, executor, brain, level3 mechanical recon
- ``legacy.transport`` — ``A2AGreenSubmit`` green test_vulnerable round-trip (Seam 2)
- ``legacy.intake``    — ``A2ATaskSource`` + ``infer_level``/``infer_label`` (Seam 1)

Deploy/tooling for the arena lives at the repo-root ``legacy/deploy/`` directory
(Dockerfile, amber-manifest.json5, scenario.leaderboard.toml, the GHCR build
workflow, and the smoke script). See ``legacy/README.md`` for how to revive it.
"""
