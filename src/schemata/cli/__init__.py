"""SCHE-MA interactive runner (Claude Code-style REPL).

Lives outside the engine modules — only imports orchestrator.run_task and the
two backends. Removing this package leaves the engine + arena agent intact.
"""
