# SCHE-MA CyberGym Purple Agent (A2A) — AgentBeats submission image.
# LEGACY/archived: the arena is retired (local CyberGym only). Build context is still
# the repo root, so build with:  docker build -f legacy/deploy/Dockerfile -t <tag> .
# Reviving also requires re-adding a2a-sdk + uvicorn to pyproject.toml (see legacy/README.md).
FROM python:3.12-slim

# Recon tools the agent uses at level1: rg (grep), xxd/hexdump (byte inspection), file
# (format id). Dropped: universal-ctags + binutils (no target binary / no MCP index at
# level1) and curl (the agent runs offline behind the CyberGym firewall) — ~40MB smaller.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep bsdmainutils file ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# config/ and skills/ live at the repo root (not inside the installed package). Copy them
# in and point the settings/prompt loaders at /app via SCHEMATA_ROOT — the brain needs both
# at runtime; without this load_settings() fails and the agent submits only a fallback PoC.
COPY config ./config
COPY skills ./skills
ENV SCHEMATA_ROOT=/app

EXPOSE 9009
# AgentBeats compose appends: --host 0.0.0.0 --port 9009 --card-url http://<name>:9009
ENTRYPOINT ["python", "-m", "schemata.legacy.a2a.server"]
