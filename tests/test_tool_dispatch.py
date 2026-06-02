"""M3-1: tool layer — permission tiers, bash allow-list, cwd jail, side effects."""
import asyncio
from pathlib import Path

from schemata.config import load_settings
from schemata.models import StageRequest, Verdict
from schemata.backends.tools import permissions
from schemata.backends.tools.dispatcher import Dispatcher


def _req(cwd, *, stage="generate", tier="full", **kw):
    base = dict(
        stage=stage, system_prompt="sys", kickoff="go", cwd=Path(cwd),
        model="opus", allowed_tools=["Bash"], permission_tier=tier,
        task_id_masked="m1", agent_id="a1", checksum="c1",
        server_url="http://127.0.0.1:8666",
    )
    base.update(kw)
    return StageRequest(**base)


def _disp(cwd, **kw):
    return Dispatcher(_req(cwd, **kw), load_settings())


# -- permissions -----------------------------------------------------------------

def test_tools_for_tiers(tmp_path):
    recon = {t["name"] for t in permissions.tools_for(_req(tmp_path, stage="recon", tier="read_only"))}
    assert "submit_poc" not in recon and "write_file" not in recon
    assert {"read_file", "grep", "glob", "bash", "semgrep_scan"} <= recon

    gen = {t["name"] for t in permissions.tools_for(_req(tmp_path, tier="full"))}
    assert "submit_poc" in gen and "write_file" in gen

    instr = {t["name"] for t in permissions.tools_for(
        _req(tmp_path, tier="write", instrument_container="schema_x"))}
    assert "arvo_compile" in instr and "arvo_run" in instr


def test_bash_allowlist():
    assert permissions.bash_allowed("read_only", "tar -xzf repo-vul.tar.gz")[0]
    assert permissions.bash_allowed("read_only", "grep -rn foo . | head")[0]
    assert not permissions.bash_allowed("read_only", "rm -rf src")[0]
    assert not permissions.bash_allowed("read_only", "cat x > y")[0]
    # full tier is unrestricted
    assert permissions.bash_allowed("full", "rm -rf build && make")[0]


# -- dispatcher ------------------------------------------------------------------

def test_cwd_jail(tmp_path):
    d = _disp(tmp_path)
    out, is_err = asyncio.run(d.execute("read_file", {"path": "../../etc/passwd"}))
    assert is_err and "escapes" in out


def test_write_read_roundtrip(tmp_path):
    import base64
    d = _disp(tmp_path)
    payload = b"\x00\x01BUG\xff" * 4
    out, is_err = asyncio.run(d.execute(
        "write_file", {"path": "poc", "content_b64": base64.b64encode(payload).decode()}))
    assert not is_err and "wrote 24 bytes" in out
    assert (tmp_path / "poc").read_bytes() == payload


def test_bash_runs_in_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    d = _disp(tmp_path)
    out, is_err = asyncio.run(d.execute("bash", {"cmd": "ls"}))
    assert not is_err and "marker.txt" in out and "[exit 0]" in out


def test_submit_poc_side_effects(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    (tmp_path / "poc").write_bytes(b"crashme")

    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=1, output="ASAN heap-overflow", poc_id="p42"))
    d = _disp(tmp_path)
    out, is_err = asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert not is_err
    assert '"crashed": true' in out and "p42" in out
    assert d.crash_found and d.winning_poc == "poc"
    assert len(d.submissions) == 1 and d.submissions[0].exit_code == 1


def test_submit_poc_uses_injected_transport(tmp_path):
    # A2A mode: submit_poc routes through req.submit_fn (green test_vulnerable), not SubmitClient
    (tmp_path / "poc").write_bytes(b"x")
    calls = []

    async def fake_transport(poc_path):
        calls.append(poc_path)
        return Verdict(exit_code=1, output="AddressSanitizer", poc_id="g1")

    d = _disp(tmp_path, submit_fn=fake_transport)
    out, is_err = asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert not is_err and '"crashed": true' in out and "g1" in out
    assert d.crash_found and len(calls) == 1 and d.winning_poc == "poc"


def test_early_stop_on_consecutive_nocrash(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    (tmp_path / "poc").write_bytes(b"nope")
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=0, output="no crash", poc_id=None))
    d = _disp(tmp_path)
    for _ in range(3):
        asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert not d.crash_found
    assert d.consec_nocrash == 3 and d.should_early_stop()
