"""tree-sitter code outline — function/struct skeletons + line ranges, NOT full bodies.

The agent's #1 cost driver is reading large C/C++ source whole (the CyberGym baseline's
main failure mode: it can't navigate big repos within the token budget). `outline()` returns
just the symbol map (signatures + line ranges) so the model can `read_file(path, start, end)`
the one function it needs instead of catting a 4000-line file.

C/C++ via the standard tree-sitter grammars; unknown languages / parse failures fall back to
a regex skeleton so the tool never hard-fails (always returns *something* useful).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Languages we can parse with tree-sitter. CyberGym targets are overwhelmingly C/C++.
_CPP_EXTS = {".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".tcc"}
_C_EXTS = {".c", ".h"}
_SRC_EXTS = _C_EXTS | _CPP_EXTS

_MAX_BYTES = 2_000_000  # don't try to parse multi-MB generated files; regex-skim instead


@lru_cache(maxsize=4)
def _parser(lang: str):
    """Build (and cache) a tree-sitter parser. Returns None if grammars unavailable."""
    try:
        from tree_sitter import Language, Parser
        if lang == "cpp":
            import tree_sitter_cpp as ts_lang
        else:
            import tree_sitter_c as ts_lang
        return Parser(Language(ts_lang.language()))
    except Exception:
        return None


def _lang_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in _CPP_EXTS:
        return "cpp"
    if ext in _C_EXTS:
        return "c"
    return None


def _find_identifier(node) -> Any:
    """Descend a declarator to the function/type name identifier."""
    if node is None:
        return None
    if node.type in ("identifier", "field_identifier", "type_identifier", "qualified_identifier"):
        return node
    for c in node.children:
        r = _find_identifier(c)
        if r is not None:
            return r
    return None


def _signature(src: bytes, node) -> str:
    """Function signature = text from the node start up to the body `{` (single line)."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    sig = src[node.start_byte:end].decode("utf-8", "replace")
    return re.sub(r"\s+", " ", sig).strip().rstrip("{").strip()


def _ts_outline(src: bytes, parser) -> list[dict]:
    """Walk the tree for function definitions (at any depth) + top-level type decls."""
    tree = parser.parse(src)
    out: list[dict] = []

    def visit(node, depth: int):
        t = node.type
        if t == "function_definition":
            idn = _find_identifier(node.child_by_field_name("declarator"))
            out.append({
                "kind": "func",
                "name": idn.text.decode("utf-8", "replace") if idn else "?",
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "signature": _signature(src, node),
            })
            return  # don't descend into a function body
        if t in ("struct_specifier", "enum_specifier", "union_specifier", "class_specifier",
                 "type_definition") and depth <= 2:
            idn = _find_identifier(node)
            name = idn.text.decode("utf-8", "replace") if idn else ""
            if name:
                out.append({
                    "kind": t.replace("_specifier", "").replace("type_definition", "typedef"),
                    "name": name,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "signature": "",
                })
        for c in node.children:
            visit(c, depth + 1)

    visit(tree.root_node, 0)
    out.sort(key=lambda s: s["start_line"])
    return out


# Regex fallback: a C-ish function-definition line at column 0 (return type + name(args) {).
_RE_FUNC = re.compile(
    r"^[A-Za-z_][\w\s\*\(\)]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:\{|$)", re.MULTILINE)
_RE_TYPE = re.compile(r"^\s*(?:typedef\s+)?(struct|enum|union|class)\s+([A-Za-z_]\w*)", re.MULTILINE)


def _regex_outline(text: str) -> list[dict]:
    out: list[dict] = []
    for m in _RE_FUNC.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append({"kind": "func", "name": m.group(1), "start_line": line,
                    "end_line": line, "signature": m.group(0).strip().rstrip("{").strip()[:120]})
    for m in _RE_TYPE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append({"kind": m.group(1), "name": m.group(2), "start_line": line,
                    "end_line": line, "signature": ""})
    out.sort(key=lambda s: s["start_line"])
    return out


def outline(path: str | Path) -> list[dict]:
    """Return [{kind, name, start_line, end_line, signature}] for a source file.

    tree-sitter for C/C++; regex fallback otherwise or on any parse failure. Never raises
    for a readable file (returns [] only if the file can't be read or has no symbols).
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return []
    lang = _lang_for(p)
    if lang and len(raw) <= _MAX_BYTES:
        parser = _parser(lang)
        if parser is not None:
            try:
                syms = _ts_outline(raw, parser)
                if syms:
                    return syms
            except Exception:
                pass  # fall through to regex
    return _regex_outline(raw.decode("utf-8", "replace"))


def outline_text(path: str | Path, max_symbols: int = 400) -> str:
    """Render the outline as a compact, model-facing map. One line per symbol."""
    syms = outline(path)
    if not syms:
        return f"(no symbols extracted from {path}; read_file it directly if small)"
    lines = [f"{Path(path).name}: {len(syms)} symbol(s) — read_file(path, start_line, end_line) to expand one"]
    for s in syms[:max_symbols]:
        if s["kind"] == "func":
            lines.append(f"  func {s['signature'] or s['name']}  @L{s['start_line']}-{s['end_line']}")
        else:
            lines.append(f"  {s['kind']} {s['name']}  @L{s['start_line']}-{s['end_line']}")
    if len(syms) > max_symbols:
        lines.append(f"  …(+{len(syms) - max_symbols} more)")
    return "\n".join(lines)
