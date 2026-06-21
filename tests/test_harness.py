"""Tests for src/schemata/harness.py — Stage 1 Recon's pre-injected crash context.

Recon used to skeleton-walk big repos via a tree-sitter `read_outline` tool; that was
removed. The harness now mechanically locates the fuzz entry point straight out of
`repo-vul.tar.gz` and injects its FULL source into the Recon prompt. These tests guard
the location + full-body injection, plus the no-harness and error.txt paths.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from schemata.pipeline.harness import harness_contract, recon_context


def _make_tar(tar_path: Path, files: dict[str, str]) -> None:
    with tarfile.open(tar_path, "w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


_HARNESS = """
#include <stddef.h>
#include <stdint.h>
extern int parse_thing(const uint8_t *data, size_t size);
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 4) return 0;            // format gate
    return parse_thing(data, size);
}
""".strip()


def test_injects_full_harness_from_tar(tmp_path):
    _make_tar(tmp_path / "repo-vul.tar.gz", {
        "repo-vul/src/fuzz_target.cc": _HARNESS,
        "repo-vul/src/parser.c": "int parse_thing(const unsigned char* d, unsigned long n){return d[n];}",
        "repo-vul/README.md": "not source",
    })
    ctx = recon_context(tmp_path)
    assert "LLVMFuzzerTestOneInput" in ctx
    assert "format gate" in ctx            # the FULL body, not a signature skeleton
    assert "fuzz_target.cc" in ctx         # the harness filename is shown
    assert "provided IN FULL" in ctx
    assert "Deterministic harness contract JSON" in ctx


def test_harness_contract_detects_libfuzzer_and_seed(tmp_path):
    _make_tar(tmp_path / "repo-vul.tar.gz", {
        "repo-vul/fuzz/fuzz_target.cc": _HARNESS,
        "repo-vul/testdata/min.bin": "ABCD",
    })
    c = harness_contract(tmp_path)
    assert c["entry_point"] == "LLVMFuzzerTestOneInput"
    assert c["input_mode"] == "libfuzzer-bytes"
    assert c["fuzzer_convention"] == "libfuzzer"
    assert c["min_realistic_size"] == 4
    assert c["seed_candidates"][0]["path"] == "repo-vul/testdata/min.bin"


def test_fallback_when_no_harness(tmp_path):
    _make_tar(tmp_path / "repo-vul.tar.gz", {
        "repo-vul/src/lib.c": "int helper(int x){return x+1;}",
    })
    ctx = recon_context(tmp_path)
    assert "could not be auto-located" in ctx


def test_prefers_llvmfuzzer_over_plain_main(tmp_path):
    _make_tar(tmp_path / "repo-vul.tar.gz", {
        "repo-vul/tools/cli.c": "int main(int argc, char** argv){return 0;}",
        "repo-vul/fuzz/entry.cc": _HARNESS,
    })
    ctx = recon_context(tmp_path)
    # the libfuzzer entry (rank 0) must be chosen ahead of the plain `int main(` (rank 2)
    assert "entry.cc" in ctx
    cli_at = ctx.index("cli.c") if "cli.c" in ctx else len(ctx)
    assert ctx.index("entry.cc") < cli_at


def test_error_txt_is_appended(tmp_path):
    _make_tar(tmp_path / "repo-vul.tar.gz", {"repo-vul/fuzz.cc": _HARNESS})
    (tmp_path / "error.txt").write_text(
        "==ERROR: AddressSanitizer: heap-buffer-overflow\nSUMMARY: asan ...")
    ctx = recon_context(tmp_path)
    assert "AddressSanitizer" in ctx
    assert "error.txt" in ctx


def test_no_tar_no_raise(tmp_path):
    # nothing at all -> fallback string, never an exception
    ctx = recon_context(tmp_path)
    assert "could not be auto-located" in ctx
