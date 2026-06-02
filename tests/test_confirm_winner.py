"""Regression: _resolve_winning_poc must find the PoC even when the model early-stops
on a crash (empty closing JSON) and names the PoC something other than 'poc'.

This is the bug M3-6 surfaced: agent crashed arvo:10400 (poc_mng.mng) but outcome.success
was reported False because the confirmer only looked at structured_output + a 'poc' file.
"""
from pathlib import Path
from types import SimpleNamespace

from schemata.models import SubmissionRecord
from schemata.orchestrator import _resolve_winning_poc


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
