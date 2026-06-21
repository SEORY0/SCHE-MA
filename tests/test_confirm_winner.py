"""Regression: _resolve_winning_poc must find the PoC even when the model early-stops
on a crash (empty closing JSON) and names the PoC something other than 'poc'.

This is the bug M3-6 surfaced: agent crashed arvo:10400 (poc_mng.mng) but outcome.success
was reported False because the confirmer only looked at structured_output + a 'poc' file.
"""
from pathlib import Path
from types import SimpleNamespace

from schemata.core.models import SubmissionRecord, TaskOutcome, Verdict
from schemata.pipeline import orchestrator
from schemata.pipeline.orchestrator import (
    _classify_failure,
    _resolve_winning_poc,
    _verify_official_score,
)


def _h(tmp: Path):
    return SimpleNamespace(task_dir=tmp)


def test_prefers_explicit_winning_poc(tmp_path):
    (tmp_path / "poc_mng.mng").write_bytes(b"x")
    assert _resolve_winning_poc(_h(tmp_path), {}, [], "poc_mng.mng") == tmp_path / "poc_mng.mng"


def test_from_structured_output(tmp_path):
    (tmp_path / "out.bin").write_bytes(b"x")
    p = _resolve_winning_poc(_h(tmp_path), {"generate": {"winning_poc_path": "out.bin"}}, [], None)
    assert p == tmp_path / "out.bin"


def test_from_crashing_submission(tmp_path):
    # the exact M3-6 case: early-stop -> empty JSON -> winning_poc only in the submission record
    (tmp_path / "poc_mng.mng").write_bytes(b"x")
    subs = [SubmissionRecord(poc_path="poc_mng.mng", exit_code=1)]
    assert _resolve_winning_poc(_h(tmp_path), {}, subs, None) == tmp_path / "poc_mng.mng"


def test_ignores_noncrashing_submission(tmp_path):
    (tmp_path / "nope").write_bytes(b"x")
    subs = [SubmissionRecord(poc_path="nope", exit_code=0)]
    assert _resolve_winning_poc(_h(tmp_path), {}, subs, None) is None


def test_poc_filename_fallback(tmp_path):
    (tmp_path / "poc").write_bytes(b"x")
    assert _resolve_winning_poc(_h(tmp_path), {}, [], None) == tmp_path / "poc"


def test_none_when_nothing(tmp_path):
    assert _resolve_winning_poc(_h(tmp_path), {}, [], None) is None


def test_candidate_batch_submits_manifest_until_crash(tmp_path, monkeypatch):
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    submitted = []

    class FakeClient:
        def __init__(self, **kwargs): pass
        @staticmethod
        def sha256(p):
            return Path(p).read_bytes().hex()
        def submit(self, path):
            submitted.append(Path(path).name)
            return Verdict(exit_code=1 if Path(path).name == "b" else 0, output="asan", poc_id=Path(path).name)

    monkeypatch.setattr(orchestrator, "SubmitClient", FakeClient)
    settings = SimpleNamespace(
        require_flag=False, rate_limit_max=20, rate_limit_window_s=60, server_url="http://x",
        stage_cfg=lambda stage: {"batch_submit_candidates": True, "max_candidates": 50} if stage == "generate" else {},
    )
    handle = SimpleNamespace(task_dir=tmp_path, masked_id="m", agent_id="a", checksum="c")
    prior = {"generate": {"candidate_poc_paths": ["a", "b"]}}
    subs = []
    winner = orchestrator._submit_candidate_batch(handle, prior, settings, tmp_path, subs)
    assert submitted == ["a", "b"]
    assert winner == str(tmp_path / "b")
    assert [s.exit_code for s in subs] == [0, 1]
    assert (tmp_path / "candidate_batch.json").is_file()


def test_failure_taxonomy_maps_fix_crash_to_post_patch():
    outcome = TaskOutcome(
        task_id="t", backend="b", success=False, final_exit_code=1,
        official_verified=True, official_reproduced=False,
        official_vul_exit_code=1, official_fix_exit_code=1,
    )
    details = _classify_failure(outcome, [SubmissionRecord(poc_path="p", exit_code=1)], {})
    assert details["class"] == "post_patch_crash"
    assert outcome.failure_class == "post_patch_crash"


def test_failure_taxonomy_maps_discriminator_wrong_sink_to_wrong_path():
    outcome = TaskOutcome(task_id="t", backend="b", success=False, final_exit_code=1)
    details = _classify_failure(
        outcome,
        [SubmissionRecord(poc_path="p", exit_code=1)],
        {"discriminate": {"failure_class": "wrong_sink"}},
    )
    assert details["class"] == "wrong_path"


def test_official_score_overrides_vulnerable_only_success(tmp_path, monkeypatch):
    class FakeClient:
        def verify_agent_pocs(self, agent_id, api_key):
            return {"poc_ids": ["p1"]}
        def query_pocs(self, api_key, *, agent_id=None, task_id=None):
            return [{"poc_id": "p1", "vul_exit_code": 1, "fix_exit_code": 1}]

    monkeypatch.setattr(orchestrator, "_make_submit_client", lambda handle, settings: FakeClient())
    handle = SimpleNamespace(agent_id="agent")
    settings = SimpleNamespace(cybergym_api_key="key")
    outcome = TaskOutcome(
        task_id="t", backend="b", success=True, final_exit_code=1, poc_id="p1"
    )
    _verify_official_score(handle, settings, tmp_path, outcome)
    assert outcome.official_verified is True
    assert outcome.official_reproduced is False
    assert outcome.success is False
    assert (tmp_path / "official_score.json").is_file()
