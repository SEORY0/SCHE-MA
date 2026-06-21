"""Backend-independent PoC submission client (reproduces submit.sh).

Used by the orchestrator for the final independent confirmation, and by the
Claude API backend's submit_poc tool. The Claude Code backend lets the agent run
submit.sh itself, but the orchestrator still uses this to re-confirm the winner.
"""
from __future__ import annotations

import collections
import hashlib
import json
import time
from pathlib import Path

import requests

from ..core.models import Verdict


class _RateLimiter:
    def __init__(self, max_req: int, window_s: int):
        self.max_req = max_req
        self.window_s = window_s
        self._times: collections.deque[float] = collections.deque()

    def acquire(self, now: float) -> None:
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()
        if len(self._times) >= self.max_req:
            sleep_for = self.window_s - (now - self._times[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._times.append(time.monotonic())


class SubmitClient:
    def __init__(
        self,
        server_url: str,
        masked_id: str,
        agent_id: str,
        checksum: str,
        require_flag: bool = False,
        rate_limit_max: int = 20,
        rate_limit_window_s: int = 60,
        timeout: float = 120.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.masked_id = masked_id
        self.agent_id = agent_id
        self.checksum = checksum
        self.require_flag = require_flag
        self.timeout = timeout
        self._rl = _RateLimiter(rate_limit_max, rate_limit_window_s)

    def submit(self, poc_path: str | Path) -> Verdict:
        poc_path = Path(poc_path)
        metadata = {
            "task_id": self.masked_id,
            "agent_id": self.agent_id,
            "checksum": self.checksum,
            "require_flag": self.require_flag,
        }
        self._rl.acquire(time.monotonic())
        with open(poc_path, "rb") as fh:
            resp = requests.post(
                f"{self.server_url}/submit-vul",
                data={"metadata": json.dumps(metadata)},
                files={"file": (poc_path.name, fh)},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        body = resp.json()
        return Verdict(
            exit_code=int(body.get("exit_code", 0)),
            output=body.get("output", ""),
            poc_id=body.get("poc_id"),
        )

    def verify_agent_pocs(self, agent_id: str, api_key: str, timeout: float = 1200.0) -> dict:
        """Ask the private CyberGym endpoint to run submitted PoCs on vul+fix.

        `/submit-vul` only reports the vulnerable build. Official reproduction requires
        `vul_exit_code != 0` and `fix_exit_code == 0`, so local evaluation must call this
        verifier and then query the stored PoC records.
        """
        resp = requests.post(
            f"{self.server_url}/verify-agent-pocs",
            json={"agent_id": agent_id},
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def query_pocs(
        self,
        api_key: str,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        timeout: float = 120.0,
    ) -> list[dict]:
        resp = requests.post(
            f"{self.server_url}/query-poc",
            json={"agent_id": agent_id, "task_id": task_id},
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def sha256(poc_path: str | Path) -> str:
        h = hashlib.sha256()
        with open(poc_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
