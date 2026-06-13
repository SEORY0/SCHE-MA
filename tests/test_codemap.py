import asyncio

from schemata import codemap
from schemata.backends.tools.dispatcher import Dispatcher
from schemata.config import load_settings
from schemata.models import StageRequest

C_SRC = """#include <stdio.h>
struct Hdr { int len; char magic[4]; };

static int parse(const unsigned char *p, int n) {
    if (n < 4) return -1;
    return p[n];
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    return parse(data, (int)size);
}
"""


def test_outline_tree_sitter_c(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(C_SRC)
    syms = {s["name"]: s for s in codemap.outline(f)}
    assert "parse" in syms and "LLVMFuzzerTestOneInput" in syms
    assert syms["parse"]["kind"] == "func"
    assert syms["parse"]["start_line"] == 4 and syms["parse"]["end_line"] == 7
    assert "const unsigned char *p" in syms["parse"]["signature"]
    assert syms["Hdr"]["kind"] == "struct"


def test_outline_text_is_compact(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(C_SRC)
    txt = codemap.outline_text(f)
    assert "read_file(path, start_line, end_line)" in txt
    assert "@L4-7" in txt and "func" in txt
    assert len(txt) < len(C_SRC) + 200  # a map, not the body


def test_regex_fallback_for_non_ts_extension(tmp_path):
    # .inc is not a tree-sitter ext -> regex skeleton still finds the function line.
    f = tmp_path / "snippet.inc"
    f.write_text("void do_thing(int x) {\n  return;\n}\n")
    syms = codemap.outline(f)
    assert any(s["name"] == "do_thing" and s["kind"] == "func" for s in syms)


def test_outline_missing_file_returns_empty(tmp_path):
    assert codemap.outline(tmp_path / "nope.c") == []


def _disp(tmp_path):
    req = StageRequest(stage="recon", system_prompt="x", kickoff="go", cwd=tmp_path,
                       model="haiku", allowed_tools=[], permission_tier="read_only", max_turns=5)
    return Dispatcher(req, load_settings())


def test_dispatcher_read_outline_then_range(tmp_path):
    (tmp_path / "x.c").write_text(C_SRC)
    disp = _disp(tmp_path)
    outline, err = asyncio.run(disp._t_read_outline({"path": "x.c"}))
    assert err is False and "LLVMFuzzerTestOneInput" in outline

    body, err2 = asyncio.run(disp._t_read_file({"path": "x.c", "start_line": 4, "end_line": 7}))
    assert err2 is False
    assert "return p[n];" in body          # the function body we asked for
    assert "#include" not in body          # not the rest of the file
    assert body.startswith("4\t")          # numbered from line 4
