"""Tests for the OKF knowledge catalog loader/retriever and leakage controls."""
from __future__ import annotations

import pytest

from schemata.knowledge import okf_catalog


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "okf"
    (root / "vuln-classes").mkdir(parents=True)
    (root / "formats").mkdir()
    (root / "index.md").write_text("---\ntype: index\n---\n# OKF bundle\n")
    (root / "log.md").write_text("# log\n")
    (root / "vuln-classes" / "heap-buffer-overflow-read.md").write_text(
        "---\n"
        "type: vuln-class\n"
        "title: Heap-buffer-overflow READ\n"
        "resource: cybergym://vuln-class/heap-buffer-overflow-read\n"
        "tags: [asan, oob-read]\n"
        "okf_support: 7\n"
        "---\n"
        "# Schema\nRead one past a heap allocation sized from one field.\n"
        "# Examples\nALLOC=N, READ index N (abstract).\n"
    )
    (root / "formats" / "isobmff.md").write_text(
        "---\n"
        "type: format-family\n"
        "title: ISOBMFF box container\n"
        "tags: [heic, heif, avif, mp4]\n"
        "okf_support: 3\n"
        "---\n"
        "# Schema\nNested boxes: size(4) + type(4) + payload. Seed-mutate is strongest.\n"
    )
    # a file without frontmatter type -> must be ignored (OKF conformance)
    (root / "stray.md").write_text("no frontmatter here\n")
    monkeypatch.setattr(okf_catalog, "_BUNDLE", root)
    okf_catalog.reload()
    yield root
    okf_catalog.reload()


def test_match_by_vuln_class(bundle):
    out = okf_catalog.retrieve(vuln_classes=["heap-buffer-overflow-read"])
    assert "<okf_examples>" in out
    assert "Heap-buffer-overflow READ" in out
    assert "ISOBMFF" not in out          # format concept not matched


def test_match_format_by_tag_synonym(bundle):
    # input_format "heic" should match the isobmff concept via its tags
    out = okf_catalog.retrieve(input_format="heic")
    assert "ISOBMFF box container" in out


def test_no_signal_returns_empty(bundle):
    assert okf_catalog.retrieve() == ""
    assert okf_catalog.retrieve(vuln_classes=[], input_format=None) == ""


def test_unmatched_returns_empty(bundle):
    assert okf_catalog.retrieve(vuln_classes=["bad-cast"], input_format="zzz") == ""


def test_stray_file_ignored(bundle):
    # the no-frontmatter file must never surface
    out = okf_catalog.retrieve(vuln_classes=["heap-buffer-overflow-read"], input_format="heic")
    assert "no frontmatter here" not in out


def test_no_task_ids_in_output(bundle):
    # leakage guard: rendered knowledge must not contain task-id-shaped tokens
    import re
    out = okf_catalog.retrieve(vuln_classes=["heap-buffer-overflow-read"], input_format="heic")
    assert not re.search(r"\b(arvo|oss-fuzz):\d+\b", out)
