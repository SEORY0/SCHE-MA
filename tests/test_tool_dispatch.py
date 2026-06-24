"""M3-1: tool layer — permission tiers, bash allow-list, cwd jail, side effects."""
import asyncio
from pathlib import Path

from schemata.backends.tools import permissions
from schemata.backends.tools.dispatcher import Dispatcher
from schemata.core.config import load_settings
from schemata.core.models import StageRequest, Verdict


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
    assert permissions.bash_allowed(
        "read_only",
        r'grep -rn "LLVMFuzzerTestOneInput\|int main(" src-vul/ | head -20',
    )[0]
    assert permissions.bash_allowed(
        "read_only",
        r'rg "LLVMFuzzerTestOneInput|main" src-vul/ --max-count=10',
    )[0]
    assert permissions.bash_allowed("read_only", r"grep -rn 'foo|bar' src-vul/ | head")[0]
    assert permissions.bash_allowed("read_only", r'grep -rn "cblk->x0" src-vul/ | head')[0]
    assert permissions.bash_allowed("read_only", r'grep -rn "a > b" src-vul/ | head')[0]
    assert permissions.bash_allowed("read_only", r'grep -rn "a < b" src-vul/ | head')[0]
    assert not permissions.bash_allowed("read_only", "rm -rf src")[0]
    assert not permissions.bash_allowed("read_only", "grep -rn foo . | rm -rf src")[0]
    assert not permissions.bash_allowed("read_only", "cat x > y")[0]
    assert not permissions.bash_allowed("read_only", "cat < x")[0]
    assert not permissions.bash_allowed("read_only", "cat <<EOF")[0]
    # full tier is unrestricted
    assert permissions.bash_allowed("full", "rm -rf build && make < input")[0]


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


def test_bash_exit_guide_for_search_no_match(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    d = _disp(tmp_path, tier="read_only")
    out, is_err = asyncio.run(d.execute("bash", {"cmd": "grep -rn nope ."}))
    assert not is_err
    assert "[exit 1]" in out
    assert "<agent guide> no matches found by grep/rg" in out


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


def test_auto_probe_submit_with_seed(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    seed_dir = tmp_path / "corpus"
    seed_dir.mkdir()
    (seed_dir / "seed1.bin").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=0, output="no crash", poc_id="probe1"))
    d = _disp(tmp_path, prior_results={
        "harness_contract": {"seed_candidates": ["corpus/seed1.bin"]},
    })
    result = asyncio.run(d.auto_probe_submit())
    assert result is not None
    parsed = __import__("json").loads(result)
    assert parsed["auto_probe"] is True
    assert parsed["exit_code"] == 0
    # Probe is recorded in submissions for logging
    assert len(d.submissions) == 1
    # But does NOT affect early-stop counters
    assert d.failures == 0
    assert d.consec_nocrash == 0
    # Seed content was copied
    assert (tmp_path / "_auto_probe").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_auto_probe_submit_no_seed_uses_null_byte(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=0, output="too short", poc_id="probe2"))
    d = _disp(tmp_path, prior_results={})
    result = asyncio.run(d.auto_probe_submit())
    assert result is not None
    assert (tmp_path / "_auto_probe").read_bytes() == b"\x00"
    assert d.failures == 0


def test_auto_probe_crash_sets_winning_poc(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=1, output="ASAN crash", poc_id="lucky"))
    d = _disp(tmp_path, prior_results={})
    result = asyncio.run(d.auto_probe_submit())
    assert d.crash_found
    assert d.winning_poc == "_auto_probe"
    assert d.failures == 0  # crash doesn't count as failure


def test_gdb_script_requires_container(tmp_path):
    d = _disp(tmp_path)
    out, is_err = asyncio.run(d.execute("gdb_script", {"poc_path": "poc", "commands": "bt"}))
    assert is_err and "no instrument container" in out


def test_coverage_check_requires_container(tmp_path):
    d = _disp(tmp_path)
    out, is_err = asyncio.run(d.execute("coverage_check", {"poc_path": "poc", "functions": ["main"]}))
    assert is_err and "no instrument container" in out


def test_coverage_check_requires_functions(tmp_path):
    d = _disp(tmp_path, instrument_container="fake_container")
    (tmp_path / "poc").write_bytes(b"\x00")
    out, is_err = asyncio.run(d.execute("coverage_check", {"poc_path": "poc", "functions": []}))
    assert is_err and "no target functions" in out


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


def test_submit_gate_blocks_unvalidated_with_container(tmp_path):
    # Container attached + local mode + poc never validated -> submit refused.
    (tmp_path / "poc").write_bytes(b"\x00")
    d = _disp(tmp_path, instrument_container="fake_container")
    out, is_err = asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert is_err and "refused" in out and "validate" in out


def test_submit_gate_clears_after_local_validation(tmp_path):
    # After a poc is recorded as locally validated, the gate no longer fires;
    # we reach the submit-config path instead (proves the gate passed).
    (tmp_path / "poc").write_bytes(b"\x00")
    d = _disp(tmp_path, instrument_container="fake_container", checksum=None)
    d.validated_pocs.add(str((tmp_path / "poc").resolve()))
    out, is_err = asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert "refused" not in out
    assert "submission is not configured" in out


def test_submit_gate_skipped_without_container(tmp_path, monkeypatch):
    # No instrument container -> gate does not apply (cannot validate locally).
    from schemata.backends.tools import dispatcher as disp_mod
    (tmp_path / "poc").write_bytes(b"\x00")
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=1, output="crash", poc_id="x"))
    d = _disp(tmp_path)  # no instrument_container
    out, is_err = asyncio.run(d.execute("submit_poc", {"poc_path": "poc"}))
    assert "refused" not in out and not is_err
    assert d.crash_found


def test_tool_calls_counter(tmp_path):
    # B0: execute() records a per-tool call count for adoption measurement.
    d = _disp(tmp_path)
    asyncio.run(d.execute("glob", {"pattern": "*"}))
    asyncio.run(d.execute("glob", {"pattern": "*.c"}))
    assert d.tool_calls.get("glob") == 2
