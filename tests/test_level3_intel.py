"""Tests for the level3 mechanical-recon extractors (src/schemata/a2a/level3_intel.py).

Two parsers (patch.diff, error.txt) plus the composer `extract_level3_recon`. Each parser
is checked against (a) a synthetic minimal example for behavior, and (b) the real
arvo:10400 fixture (graphicsmagick MNG heap-buffer-overflow) so we know the regexes
survive what the cybergym dataset actually ships — mercurial-style diffs and C++ frames
with spaces in the function signature, both of which broke the first cut.

We also assert the brain skips the LLM recon stage when level3 intel is present.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from schemata.a2a import agent as brain_mod
from schemata.a2a.level3_intel import (extract_level3_recon, parse_error_txt,
                                       parse_patch_diff)


# ---- synthetic patch.diff (git-style + mercurial-style) --------------------------

GIT_PATCH = """\
diff --git a/src/parser.c b/src/parser.c
index abcd..efgh 100644
--- a/src/parser.c
+++ b/src/parser.c
@@ -100,6 +100,8 @@ static int parse_header(buf_t *b)
   uint32_t n = read_u32(b);
+  if (n > b->size - 8)
+    return -1;
   memcpy(out, b->data + 8, n);
   return 0;
 }
diff --git a/include/parser.h b/include/parser.h
--- a/include/parser.h
+++ b/include/parser.h
@@ -10,3 +10,4 @@
 #endif
"""

HG_PATCH = """\
diff -r 11111111 -r 22222222 coders/png.c
--- a/coders/png.c\tDate1
+++ b/coders/png.c\tDate2
@@ -5023,3 +5023,4 @@
 some context
-              if (length > 0)
+              if (length >= 5)
   more
"""


def test_parse_patch_git_style():
    p = parse_patch_diff(GIT_PATCH)
    assert p["files"] == ["src/parser.c", "include/parser.h"]
    assert "parse_header" in p["functions"]
    # b-side range: new_start=100, new_count=8 -> 100-107
    assert "src/parser.c:100-107" in p["code_ranges"]
    assert len(p["hunks"]) == 2


def test_parse_patch_hg_style():
    p = parse_patch_diff(HG_PATCH)
    assert p["files"] == ["coders/png.c"]
    assert p["code_ranges"] == ["coders/png.c:5023-5026"]


def test_parse_patch_empty():
    p = parse_patch_diff("")
    assert p == {"files": [], "functions": [], "code_ranges": [], "hunks": []}


# ---- synthetic error.txt ---------------------------------------------------------

ASAN_ERROR = """\
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x60200000 at pc 0x...
READ of size 4 at 0x60200000 thread T0
    #0 0x7c6cbe in foo::bar(int const&) /src/proj/lib/foo.cc:42:5
    #1 0x7b46a7 in parse_input /src/proj/parser.c:100:13
    #2 0x646f9a in LLVMFuzzerTestOneInput /src/proj/fuzz.cc:20:15
    #3 0x4eada7 in __asan_internal /asan/asan_runtime.cc:1
SUMMARY: AddressSanitizer: heap-buffer-overflow /src/proj/lib/foo.cc:42:5 in foo::bar(int const&)
"""


def test_parse_error_asan():
    e = parse_error_txt(ASAN_ERROR)
    assert e["sanitizer"] == "asan"
    assert e["crash_type"] == "heap-buffer-overflow"
    assert e["rw"] == "READ" and e["rw_size"] == 4
    # asan internal frame is captured (filtering of __asan happens in extract_level3_recon)
    fns = [f["fn"] for f in e["frames"]]
    assert "foo::bar(int const&)" in fns  # C++ signature with spaces survives
    assert "parse_input" in fns
    assert e["summary"]["fn"] == "foo::bar(int const&)"
    assert e["summary"]["line"] == 42


def test_parse_error_msan_no_summary():
    txt = ("==1==ERROR: MemorySanitizer: use-of-uninitialized-value\n"
           "    #0 0x1 in fn /x.c:1:1\n")
    e = parse_error_txt(txt)
    assert e["sanitizer"] == "msan"
    assert e["crash_type"] == "use-of-uninitialized-value"


def test_parse_error_empty():
    e = parse_error_txt("")
    assert e["sanitizer"] == "unknown"
    assert e["frames"] == []


# ---- extract_level3_recon (composition + filtering) ------------------------------

def test_extract_level3_recon_filters_noise(tmp_path: Path):
    (tmp_path / "patch.diff").write_text(GIT_PATCH + """
diff --git a/ChangeLog b/ChangeLog
--- a/ChangeLog
+++ b/ChangeLog
@@ -1,2 +1,3 @@
+new entry
""")
    (tmp_path / "error.txt").write_text(ASAN_ERROR)
    r = extract_level3_recon(tmp_path)
    assert r is not None
    # ChangeLog dropped, source files kept
    assert "ChangeLog" not in r["suspected_files"]
    assert "src/parser.c" in r["suspected_files"]
    # /src/proj/ prefix stripped from error-frame paths
    assert "lib/foo.cc" in r["suspected_files"]
    assert "parser.c" in r["suspected_files"][0] or any("parser" in f for f in r["suspected_files"])
    # asan internal frame is filtered from attack_surface
    assert all(not fn.startswith("__asan") for fn in r["attack_surface"])
    assert r["crash_type"] == "heap-buffer-overflow"


def test_extract_level3_recon_returns_none_when_files_absent(tmp_path):
    assert extract_level3_recon(tmp_path) is None


def test_extract_level3_recon_real_arvo_10400():
    """Regression guard: arvo:10400 is the canonical local fixture, and its hg-style
    patch + C++-laden error trace are what first exposed the regex bugs."""
    fx = Path("/data/seory0/projects/cybergym/cybergym_data/data/arvo/10400")
    if not (fx / "patch.diff").exists():
        pytest.skip("arvo:10400 fixture not present on this machine")
    r = extract_level3_recon(fx)
    assert r is not None
    assert r["crash_type"] == "heap-buffer-overflow"
    assert "mng_get_long" in r["attack_surface"]
    assert "ReadMNGImage" in r["attack_surface"]
    assert "coders/png.c" in r["suspected_files"]
    # Patch touched coders/png.c around lines 5023 and 5060 — the LOOP chunk handling.
    assert any(r_.startswith("coders/png.c:50") for r_ in r["code_ranges"])


# ---- brain wiring: level3 skips recon, level1 keeps it ---------------------------

class _RecordingBackend:
    """Backend stub that records each stage requested and returns canned results."""
    def __init__(self):
        self.stages: list[str] = []

    async def run_stage(self, req):
        from schemata.models import Artifacts, StageResult
        self.stages.append(req.stage)
        if req.stage == "generate":
            return StageResult(stage="generate", artifacts=Artifacts())
        return StageResult(stage=req.stage)


def _patch_backend(monkeypatch, backend):
    import schemata.backends.claude_api as api_mod
    import schemata.prompt_loader as pl_mod
    monkeypatch.setattr(api_mod, "ClaudeApiBackend", lambda settings: backend)

    def _fake_build(stage, plan, meta, handle, prior, settings, backend_name, **kw):
        from schemata.models import StageRequest
        return StageRequest(stage=stage, system_prompt="s", kickoff="k",
                            cwd=Path(handle.task_dir), model=plan.stage_models[stage],
                            allowed_tools=[], permission_tier="read_only")
    monkeypatch.setattr(pl_mod, "build_request", _fake_build)


def _settings():
    return SimpleNamespace(model_for=lambda stage, diff: f"{stage}-m")


def test_brain_skips_recon_on_level3_with_intel(tmp_path: Path, monkeypatch):
    (tmp_path / "patch.diff").write_text(GIT_PATCH)
    (tmp_path / "error.txt").write_text(ASAN_ERROR)
    backend = _RecordingBackend()
    _patch_backend(monkeypatch, backend)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t1", masked_id=None, level="level3")
    asyncio.run(brain_mod.run(handle, {}, _settings(), transport=None, emit=None))
    assert backend.stages == ["generate"]  # recon LLM call skipped


def test_brain_runs_recon_on_level1(tmp_path: Path, monkeypatch):
    backend = _RecordingBackend()
    _patch_backend(monkeypatch, backend)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t1", masked_id=None, level="level1")
    asyncio.run(brain_mod.run(handle, {}, _settings(), transport=None, emit=None))
    assert backend.stages == ["recon", "analyze", "generate"]


def test_brain_runs_recon_on_level3_when_intel_missing(tmp_path: Path, monkeypatch):
    """Level3 attachments absent from task_dir -> fall back to LLM recon, don't crash."""
    backend = _RecordingBackend()
    _patch_backend(monkeypatch, backend)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t1", masked_id=None, level="level3")
    asyncio.run(brain_mod.run(handle, {}, _settings(), transport=None, emit=None))
    assert backend.stages == ["recon", "analyze", "generate"]
