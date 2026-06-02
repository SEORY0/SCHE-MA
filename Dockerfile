# SCHE-MA CyberGym Purple Agent (A2A) — AgentBeats submission image.
FROM python:3.12-slim

# QoL / recon tools the agent brain (M6-b) uses: rg, ctags, xxd, file, objdump/nm.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep universal-ctags bsdmainutils file binutils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 9009
# AgentBeats compose appends: --host 0.0.0.0 --port 9009 --card-url http://<name>:9009
ENTRYPOINT ["python", "-m", "schemata.a2a.server"]
