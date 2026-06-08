"""A2A server entrypoint for the SCHE-MA CyberGym purple agent.

Run: python -m schemata.a2a.server --host 0.0.0.0 --port 9009
Exposes the A2A agent card at /.well-known/agent-card.json (AgentBeats healthcheck).
"""
from __future__ import annotations

import argparse
import logging
import os

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .executor import Executor

# 256MB: CyberGym task tarballs (repo-vul/repo-fix) are large.
DEFAULT_MAX_CONTENT = 256 * 1024 * 1024


def build_app(*, host: str, port: int, card_url: str | None = None):
    skill = AgentSkill(
        id="cybergym_poc_synth",
        name="CyberGym vulnerability PoC synthesiser",
        description=(
            "Receives a vulnerability description, error log, optional fix patch, and "
            "vulnerable source from the CyberGym green agent, and produces a proof-of-concept "
            "input that triggers the bug."
        ),
        tags=["cybersecurity", "vulnerability", "fuzzing", "poc"],
        examples=["Generate a PoC for arvo:10400 given source + ASan log"],
    )
    card = AgentCard(
        name="SCHE-MA CyberGym Purple Agent",
        description=(
            "SCHE-MA multi-stage PoC-synthesis agent for the CyberGym / Pi-Bench benchmark "
            "(cost-efficient Claude pipeline)."
        ),
        url=card_url or f"http://{host}:{port}/",
        version="0.1.0",
        skills=[skill],
        default_input_modes=["text", "file"],
        default_output_modes=["text", "file"],
        capabilities=AgentCapabilities(streaming=True),
    )
    handler = DefaultRequestHandler(agent_executor=Executor(), task_store=InMemoryTaskStore())
    max_len = int(os.environ.get("A2A_MAX_CONTENT_LENGTH", str(DEFAULT_MAX_CONTENT)))
    app = A2AStarletteApplication(agent_card=card, http_handler=handler, max_content_length=max_len)
    return app.build()


def main() -> None:
    parser = argparse.ArgumentParser(description="SCHE-MA CyberGym Purple Agent (A2A)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9009)
    parser.add_argument("--card-url", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Startup banner — confirms the manifest's env wiring (esp. ANTHROPIC_API_KEY). If this
    # logs "anthropic_key=False" at runtime, no API calls will succeed and the brain will
    # submit SKELETON_POC for every task; see amber-manifest.json5.
    ak = os.environ.get("ANTHROPIC_API_KEY") or ""
    logging.getLogger(__name__).info(
        "SCHE-MA A2A purple starting: host=%s port=%d anthropic_key=%s (len=%d, prefix=%s)",
        args.host, args.port, bool(ak), len(ak), (ak[:10] + "…") if ak else "",
    )
    uvicorn.run(
        build_app(host=args.host, port=args.port, card_url=args.card_url),
        host=args.host, port=args.port, timeout_keep_alive=3600,
    )


if __name__ == "__main__":
    main()
