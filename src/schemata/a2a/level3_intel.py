"""Mechanical extraction of CyberGym level3 ground-truth from green-supplied files.

At level3, the green sends `patch.diff` (the fix) and `error.txt` (the sanitizer report).
Together these *deterministically* tell us:
  - Which files/functions/line-ranges contain the bug.
  - Which sanitizer fired and the crash type.
  - The crashing call stack frames.

That is exactly what the LLM recon stage would otherwise spend ~$0.30–$0.80 per task
*trying to discover*. We extract it here with regex (cheap, exact, no hallucination) and
feed the result into the generate stage as `prior["recon"]`, letting us skip the recon
LLM call entirely on level3 tasks.

The returned dict matches the recon output schema in `prompts/shared/output_contracts.md`
so the generate prompt's `{{recon_json}}` rendering doesn't need to change for level3.
Two extra advisory fields (`patch_intel`, `error_intel`) carry the raw parsed structure
for the generate prompt to lean on when present.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---- patch.diff (unified diff) ---------------------------------------------------

# `+++ b/PATH` works for both git-style (`diff --git a/X b/Y`) and mercurial-style
# (`diff -r H1 -r H2 PATH`) patches — CyberGym tasks include both (e.g. arvo:10400 is hg).
# Strip any trailing whitespace + timestamp (hg patches append a date column).
_PLUS_FILE_RE = re.compile(r"^\+\+\+ b/(?P<path>\S+)", re.MULTILINE)
# Hunk header: @@ -old_start,old_count +new_start,new_count @@ <context>
# Trailing context (after the second @@) is the patch tool's "function" hint.
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<ctx>.*)$",
    re.MULTILINE,
)
# C/C++ function-signature shape inside the hunk context line.
_FUNC_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def parse_patch_diff(text: str) -> dict[str, Any]:
    """Extract file/function/hunk intel from a unified diff (git or mercurial style).

    Returns:
        {
          "files":     ["path/to/a.c", ...],          # b-side paths (post-fix)
          "functions": ["ReadImage", "ParseFoo"],     # heuristic from hunk @@ context
          "code_ranges": ["a.c:100-105", ...],        # b-side line ranges per hunk
          "hunks": [{"file","old_start","old_count","new_start","new_count","context"}, ...],
        }
    """
    if not text:
        return {"files": [], "functions": [], "code_ranges": [], "hunks": []}

    files: list[str] = []
    seen_files: set[str] = set()
    hunks: list[dict[str, Any]] = []
    functions: list[str] = []
    seen_funcs: set[str] = set()
    code_ranges: list[str] = []

    # Walk file blocks; for each, collect its hunks until the next `+++ b/...` line.
    file_matches = list(_PLUS_FILE_RE.finditer(text))
    for i, fm in enumerate(file_matches):
        b_path = fm.group("path")
        if b_path not in seen_files:
            files.append(b_path); seen_files.add(b_path)
        block_end = file_matches[i + 1].start() if i + 1 < len(file_matches) else len(text)
        block = text[fm.end():block_end]
        for hm in _HUNK_RE.finditer(block):
            old_start = int(hm.group("old_start"))
            old_count = int(hm.group("old_count") or 1)
            new_start = int(hm.group("new_start"))
            new_count = int(hm.group("new_count") or 1)
            ctx = (hm.group("ctx") or "").strip()
            hunks.append({
                "file": b_path,
                "old_start": old_start, "old_count": old_count,
                "new_start": new_start, "new_count": new_count,
                "context": ctx,
            })
            # b-side range tells the generate stage exactly where to `Read` in the FIXED tree;
            # the bug exists in the VUL tree at roughly the same lines minus patch additions.
            code_ranges.append(f"{b_path}:{new_start}-{new_start + max(new_count - 1, 0)}")
            fn = _FUNC_NAME_RE.search(ctx)
            if fn and fn.group(1) not in seen_funcs:
                functions.append(fn.group(1)); seen_funcs.add(fn.group(1))
    return {"files": files, "functions": functions, "code_ranges": code_ranges, "hunks": hunks}


# ---- error.txt (sanitizer report) ------------------------------------------------

_SANITIZER_MAP = [
    ("AddressSanitizer", "asan"),
    ("MemorySanitizer", "msan"),
    ("UndefinedBehaviorSanitizer", "ubsan"),
    ("ThreadSanitizer", "tsan"),
    ("LeakSanitizer", "lsan"),
]
# ASAN error line: "==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x..."
_ERR_LINE_RE = re.compile(
    r"ERROR:\s*(?:AddressSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer|"
    r"ThreadSanitizer|LeakSanitizer)\s*:\s*([A-Za-z0-9_\- ]+?)(?:\s+on\s+address|\s+at\s|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
# Trailing "SUMMARY: AddressSanitizer: heap-buffer-overflow path/to/x.c:123:5 in fnname"
# C++ fn may contain spaces & parens — grab through end-of-line and strip.
_SUMMARY_RE = re.compile(
    r"SUMMARY:\s*(?:AddressSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer|"
    r"ThreadSanitizer|LeakSanitizer)\s*:\s*(?P<crash>[A-Za-z0-9_\- ]+?)\s+"
    r"(?P<path>\S+?):(?P<line>\d+)(?::\d+)?\s+in\s+(?P<fn>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Stack frame: "    #0 0x... in fnname path/to/x.c:123:5"
# C++ frames have function names with spaces & parens (e.g. "fn::do(int const&) /path:1:2"),
# so we anchor on the trailing `path:line[:col]` at end-of-line and use lazy fn matching.
_FRAME_RE = re.compile(
    r"^\s*#(?P<idx>\d+)\s+0x[0-9a-fA-F]+\s+in\s+(?P<fn>.+?)\s+"
    r"(?P<path>\S+?):(?P<line>\d+)(?::\d+)?\s*$",
    re.MULTILINE,
)
# READ/WRITE size hint: "READ of size 4 at 0x..."
_RW_RE = re.compile(r"\b(READ|WRITE|MEMCPY)\s+of\s+size\s+(\d+)", re.IGNORECASE)


def parse_error_txt(text: str) -> dict[str, Any]:
    """Extract sanitizer/crash-type/frames from a typical sanitizer error report.

    Returns:
        {
          "sanitizer":  "asan"|"msan"|"ubsan"|"tsan"|"lsan"|"unknown",
          "crash_type": "heap-buffer-overflow" | "use-after-free" | "" (normalized lowercase),
          "rw":         "READ"|"WRITE"|None,
          "rw_size":    int|None,
          "frames":     [{"idx":0,"fn":"...","file":"...","line":int}, ...],
          "summary":    {"crash":"...","fn":"...","file":"...","line":int} | None,
        }
    """
    if not text:
        return {"sanitizer": "unknown", "crash_type": "", "rw": None, "rw_size": None,
                "frames": [], "summary": None}

    san = "unknown"
    for needle, code in _SANITIZER_MAP:
        if needle in text:
            san = code
            break

    crash_type = ""
    m = _ERR_LINE_RE.search(text)
    if m:
        crash_type = m.group(1).strip().lower().replace(" ", "-")

    rw = None
    rw_size = None
    rm = _RW_RE.search(text)
    if rm:
        rw = rm.group(1).upper()
        try:
            rw_size = int(rm.group(2))
        except ValueError:
            rw_size = None

    frames = []
    for fm in _FRAME_RE.finditer(text):
        frames.append({
            "idx": int(fm.group("idx")),
            "fn": fm.group("fn"),
            "file": fm.group("path"),
            "line": int(fm.group("line")),
        })
        if len(frames) >= 12:  # tail frames are rarely useful (libc, sanitizer runtime)
            break

    summary = None
    sm = _SUMMARY_RE.search(text)
    if sm:
        summary = {
            "crash": sm.group("crash").strip().lower().replace(" ", "-"),
            "fn": sm.group("fn"),
            "file": sm.group("path"),
            "line": int(sm.group("line")),
        }

    # Prefer summary's crash type when the top ERROR line was ambiguous.
    if not crash_type and summary:
        crash_type = summary["crash"]

    return {"sanitizer": san, "crash_type": crash_type, "rw": rw, "rw_size": rw_size,
            "frames": frames, "summary": summary}


# ---- compose ---------------------------------------------------------------------

def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    except OSError:
        return ""


# Source-code extensions worth listing as "suspected files"; everything else
# (ChangeLog, .html docs, version bumps) is noise that comes along with the security
# fix commit but isn't bug-relevant. Bloats the recon JSON without helping the model.
_SRC_EXTS = (
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".py", ".rs", ".go", ".java", ".js", ".jsx", ".ts", ".tsx", ".m", ".mm",
    ".swift", ".kt", ".rb", ".php", ".lua", ".s", ".S", ".asm",
)


def _is_source(path: str) -> bool:
    return path.lower().endswith(_SRC_EXTS)


def extract_level3_recon(task_dir: Path) -> dict[str, Any] | None:
    """Build a synthetic recon-stage JSON from patch.diff + error.txt at level3.

    Returns None when neither file is present or parseable (caller falls back to LLM recon).
    The returned shape is a superset of the recon output contract: existing fields are
    populated from ground truth, plus two advisory dicts (`patch_intel`, `error_intel`)
    that the generate prompt can read for finer-grained context.
    """
    patch_text = _read_text(task_dir / "patch.diff")
    error_text = _read_text(task_dir / "error.txt")
    if not patch_text and not error_text:
        return None

    patch = parse_patch_diff(patch_text)
    err = parse_error_txt(error_text)
    if not patch["files"] and not err["frames"] and not err["crash_type"]:
        return None

    # Top frames from the sanitizer trace are the most likely sinks (after the
    # sanitizer's own internal frames, which usually appear as ldname or _asan_*).
    attack_surface: list[str] = []
    seen = set()
    for f in err["frames"]:
        fn = f["fn"]
        if fn.startswith("__asan") or fn.startswith("__sanitizer") or fn in seen:
            continue
        attack_surface.append(fn); seen.add(fn)
        if len(attack_surface) >= 6:
            break
    for fn in patch["functions"]:
        if fn not in seen:
            attack_surface.append(fn); seen.add(fn)

    # Suspect files: drop docs/build noise (ChangeLog, *.html, version bumps), then
    # add bug-relevant source files from the top error frames. OSS-Fuzz frame paths
    # are typically /src/<project>/<repo-relative>; strip the prefix to match patch paths.
    suspected_files = [f for f in patch["files"] if _is_source(f)]
    for f in err["frames"][:6]:
        path = f["file"]
        if not path or not _is_source(path):
            continue
        norm = re.sub(r"^/src/[^/]+/", "", path)  # /src/proj/foo/bar.c -> foo/bar.c
        if norm not in suspected_files:
            suspected_files.append(norm)

    from ..atomic_vulns import classify_from_crash_type
    return {
        "crash_type": err["crash_type"] or "unknown",
        "vuln_classes": classify_from_crash_type(err["crash_type"]),  # mechanical: LLM recon skipped at level3
        "attack_surface": attack_surface,
        "suspected_files": suspected_files,
        "suspected_functions": list(patch["functions"]),
        "input_format": "unknown",     # level3 does not directly reveal this
        "entry_point": "",             # generator must locate the fuzz harness itself
        "build_system": "unknown",
        "code_ranges": [r for r in patch["code_ranges"] if _is_source(r.split(":", 1)[0])],
        "notes": (
            "level3 mechanical recon: bug location and sanitizer extracted from "
            "patch.diff + error.txt (ground truth). LLM recon was skipped."
        ),
        # Advisory extras for the generate prompt (non-schema):
        "patch_intel": patch,
        "error_intel": err,
    }
