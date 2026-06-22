"""Pre-inject the fuzz-harness source (and crash report) into the Recon prompt.

Stage 1 Recon used to navigate big repos via a tree-sitter outline tool (`read_outline`,
backed by codemap.py). We removed that indirection: instead the harness mechanically
locates the fuzz entry point and feeds its FULL source straight into Recon's prompt, so
the cheap model reads the actual crash-relevant code rather than skeleton-walking it.
The agent still reads the rest of the repo in full with `Read` — see skills/stages/recon.md.

Scanning is read-only and reads members straight out of `repo-vul.tar.gz` (no extraction,
no race with the agent's own `tar -xzf`). On any failure it returns a short fallback line
telling the agent to locate the harness itself — recon never hard-fails on this.
"""
from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path

from ..core.util import truncate

# Fuzz-entry markers, most specific first. The first marker present in a source file
# decides that file is a harness; the marker's rank orders candidates.
_ENTRY_MARKERS = ("LLVMFuzzerTestOneInput", 'extern "C" int', "int main(")

_SRC_EXTS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".cl"}
_NAME_HINTS = ("fuzz", "harness", "target", "llvmfuzzer", "entry")  # tie-breakers

_MAX_MEMBER_BYTES = 1_000_000   # skip multi-MB generated blobs
_MAX_SCAN = 6000                # bound work on pathologically large repos
_MAX_HITS = 24                  # stop collecting once we clearly have the harness
_PER_FILE_CHARS = 24_000        # cap one injected harness file (~600 lines)
_MAX_FILES = 2                  # inject at most this many harness files
_ERROR_CHARS = 4_000
_SEED_HINTS = ("corpus", "seed", "testdata", "fixtures", "/test", "sample", "example")
_BINARY_EXTS = {
    ".bin", ".dat", ".gif", ".png", ".jpg", ".jpeg", ".mng", ".tif", ".tiff", ".bmp",
    ".pdf", ".xml", ".json", ".txt", ".pcap", ".zip", ".gz", ".tar", ".ttf", ".otf",
    ".woff", ".mp3", ".mp4", ".avi", ".mov", ".wav", ".mkv", ".md3",
}

_DESC_STOP = frozenset({
    "in", "at", "the", "of", "on", "is", "and", "or", "for", "to", "from",
    "with", "by", "this", "that", "not", "but", "are", "was", "were", "has",
    "have", "had", "been", "its", "type", "read", "write", "size", "data",
    "buffer", "heap", "stack", "use", "after", "free", "null", "overflow",
    "underflow", "address", "unknown", "value", "void", "int", "char",
    "unsigned", "const", "static", "struct", "src", "bug", "error",
})


def _crash_identifiers(description: str) -> list[str]:
    """Extract function/module identifiers from crash description for harness relevance."""
    idents: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        if len(token) > 2 and token.lower() not in _DESC_STOP and token not in seen:
            idents.append(token)
            seen.add(token)

    for m in re.finditer(r"\bin\s+([A-Za-z_][A-Za-z0-9_:]*)", description):
        for part in m.group(1).split("::"):
            _add(part)
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*\.(?:c|cc|cpp|cxx|h|hpp|hxx))", description):
        _add(Path(m.group(1)).stem)
    return idents


def _rerank_by_crash_context(
    hits: list[tuple[str, str]], task_dir: Path,
) -> list[tuple[str, str]]:
    """Among harness candidates, prefer those whose source references crash-related identifiers."""
    if len(hits) <= 1:
        return hits
    desc_path = task_dir / "description.txt"
    if not desc_path.is_file():
        return hits
    try:
        desc = desc_path.read_text(errors="replace")[:2000]
    except OSError:
        return hits
    idents = _crash_identifiers(desc)
    if not idents:
        return hits
    scored = []
    for name, text in hits:
        rank = _marker_rank(text)
        rank = rank if rank is not None else 99
        combined = text + " " + name
        relevance = sum(1 for ident in idents if ident in combined)
        scored.append((rank, -relevance, -_name_score(name), name, (name, text)))
    scored.sort()
    return [item for *_, item in scored]


def _is_src(name: str) -> bool:
    return Path(name).suffix.lower() in _SRC_EXTS


def _name_score(name: str) -> int:
    low = name.lower()
    return sum(h in low for h in _NAME_HINTS)


def _marker_rank(text: str) -> int | None:
    """Rank of the first fuzz-entry marker found in `text`, or None if none match."""
    for rank, marker in enumerate(_ENTRY_MARKERS):
        if marker in text:
            return rank
    return None


def _rank_key(name: str, text: str, rank: int) -> tuple:
    """Sort key: more-specific marker first, then stronger name hint, then name."""
    return (rank, -_name_score(name), name)


def _ordered(hits: list[tuple[str, str, int]]) -> list[tuple[str, str]]:
    hits.sort(key=lambda h: _rank_key(h[0], h[1], h[2]))
    return [(name, text) for name, text, _ in hits]


def _scan_tar(tar_path: Path) -> list[tuple[str, str]]:
    """[(member, text)] for source members containing a fuzz-entry marker, best first."""
    hits: list[tuple[str, str, int]] = []
    scanned = 0
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            for m in tf:
                if scanned >= _MAX_SCAN or len(hits) >= _MAX_HITS:
                    break
                if not m.isfile() or not _is_src(m.name) or m.size > _MAX_MEMBER_BYTES:
                    continue
                f = tf.extractfile(m)
                if f is None:
                    continue
                scanned += 1
                text = f.read().decode("utf-8", "replace")
                rank = _marker_rank(text)
                if rank is not None:
                    hits.append((m.name, text, rank))
    except (tarfile.TarError, OSError):
        return []
    return _ordered(hits)


def _scan_dir(root: Path) -> list[tuple[str, str]]:
    """Fallback for an already-extracted tree (no tar present)."""
    hits: list[tuple[str, str, int]] = []
    scanned = 0
    for p in root.rglob("*"):
        if scanned >= _MAX_SCAN or len(hits) >= _MAX_HITS:
            break
        if not p.is_file() or not _is_src(p.name):
            continue
        try:
            if p.stat().st_size > _MAX_MEMBER_BYTES:
                continue
            text = p.read_text(errors="replace")
        except OSError:
            continue
        scanned += 1
        rank = _marker_rank(text)
        if rank is not None:
            hits.append((str(p.relative_to(root)), text, rank))
    return _ordered(hits)


def _harness_hits(task_dir: Path) -> list[tuple[str, str]]:
    tar = task_dir / "repo-vul.tar.gz"
    if tar.is_file():
        hits = _scan_tar(tar)
        if hits:
            return _rerank_by_crash_context(hits, task_dir)
    hits = _scan_dir(task_dir)
    return _rerank_by_crash_context(hits, task_dir) if hits else hits


def _line_no(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 1


def _input_mode(text: str) -> str:
    if "LLVMFuzzerTestOneInput" in text:
        return "libfuzzer-bytes"
    low = text.lower()
    if "argv[1]" in text or re.search(r"\bargc\b.*[<>]=?\s*2", text):
        return "file-path-argv"
    if "stdin" in low or "getchar(" in text or re.search(r"\bread\s*\(\s*0\s*,", text):
        return "stdin"
    return "unknown"


def _fuzzer_convention(text: str, mode: str) -> str:
    low = text.lower()
    if "LLVMFuzzerTestOneInput" in text:
        return "libfuzzer"
    if "__afl" in low or "afl" in low or "honggfuzz" in low:
        return "afl"
    if "int main(" in text:
        return "custom-main"
    return "unknown" if mode == "unknown" else "custom-main"


def _format_gates(text: str) -> list[str]:
    out: list[str] = []
    gate_re = re.compile(
        r"(size\s*[<>]=?\s*\d+|len(?:gth)?\s*[<>]=?\s*\d+|argc\s*[<>]=?\s*\d+|"
        r"memcmp\s*\(|strncmp\s*\(|magic|signature|header|fread\s*\(|read\s*\()",
        re.I,
    )
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s and gate_re.search(s):
            out.append(f"{i}: {s[:220]}")
        if len(out) >= 8:
            break
    return out


def _min_size(text: str) -> int:
    vals = []
    for pat in (
        r"\bsize\s*<\s*(\d+)",
        r"\bSize\s*<\s*(\d+)",
        r"\blen(?:gth)?\s*<\s*(\d+)",
        r"\bif\s*\(\s*\w+\s*<\s*(\d+)",
    ):
        vals.extend(int(x) for x in re.findall(pat, text))
    return max(vals) if vals else 0


def _parser_calls(text: str) -> list[str]:
    calls: list[str] = []
    skip = {
        "if", "for", "while", "switch", "return", "sizeof", "malloc", "free", "memcpy",
        "memcmp", "strncmp", "fread", "fopen", "close", "read", "printf", "fprintf",
    }
    for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_:]*)\s*\(", text):
        base = name.split("::")[-1]
        if base not in skip and base not in calls and not base.startswith("LLVMFuzzer"):
            calls.append(base)
        if len(calls) >= 12:
            break
    return calls


def _seed_candidates_from_tar(tar_path: Path) -> list[dict]:
    seeds: list[dict] = []
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            for m in tf:
                low = m.name.lower()
                if not m.isfile() or m.size <= 0:
                    continue
                if any(h in low for h in _SEED_HINTS) or Path(low).suffix in _BINARY_EXTS:
                    seeds.append({"path": m.name, "size": int(m.size), "why": "in-repo seed/sample candidate"})
                if len(seeds) >= 24:
                    break
    except (tarfile.TarError, OSError):
        return []
    seeds.sort(key=lambda s: (s["size"], s["path"]))
    return seeds[:3]


def _seed_candidates_from_dir(root: Path) -> list[dict]:
    seeds: list[dict] = []
    for p in root.rglob("*"):
        if len(seeds) >= 24:
            break
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        low = rel.lower()
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        if any(h in low for h in _SEED_HINTS) or p.suffix.lower() in _BINARY_EXTS:
            seeds.append({"path": rel, "size": int(size), "why": "in-repo seed/sample candidate"})
    seeds.sort(key=lambda s: (s["size"], s["path"]))
    return seeds[:3]


def _seed_candidates(task_dir: Path) -> list[dict]:
    tar = task_dir / "repo-vul.tar.gz"
    if tar.is_file():
        seeds = _seed_candidates_from_tar(tar)
        if seeds:
            return seeds
    return _seed_candidates_from_dir(task_dir)


def harness_contract(task_dir: str | Path) -> dict:
    """Best-effort deterministic input contract for the current CyberGym task.

    This is intentionally conservative: it does not infer exploit details, only the
    mechanics of how bytes enter the target and which obvious gates must be passed.
    """
    task_dir = Path(task_dir)
    hits = _harness_hits(task_dir)
    if not hits:
        return {
            "entry_point": "",
            "entry_file": "",
            "input_mode": "unknown",
            "fuzzer_convention": "unknown",
            "input_is_whole_file_format": False,
            "min_realistic_size": 0,
            "format_gates": [],
            "parser_calls": [],
            "seed_candidates": _seed_candidates(task_dir),
            "source": "deterministic_harness_scan",
        }

    name, text = hits[0]
    entry = "LLVMFuzzerTestOneInput" if "LLVMFuzzerTestOneInput" in text else "main"
    mode = _input_mode(text)
    convention = _fuzzer_convention(text, mode)
    whole_file = mode in {"file-path-argv", "stdin"} or convention in {"afl", "custom-main"}
    gates = _format_gates(text)
    return {
        "entry_point": entry,
        "entry_file": f"{name}:{_line_no(text, entry)}",
        "input_mode": mode,
        "fuzzer_convention": convention,
        "input_is_whole_file_format": whole_file,
        "min_realistic_size": _min_size(text),
        "format_gates": gates,
        "rejection_symptoms": "; ".join(gates[:4]),
        "parser_calls": _parser_calls(text),
        "seed_candidates": _seed_candidates(task_dir),
        "source": "deterministic_harness_scan",
    }


def recon_context(task_dir: str | Path) -> str:
    """Render the Recon prompt's pre-injected crash context.

    Returns the full fuzz-harness source (the input contract) plus error.txt when
    present. Never raises: a failure to locate the harness yields a one-line fallback
    that tells the agent to find it itself.
    """
    task_dir = Path(task_dir)
    blocks: list[str] = []

    hits = _harness_hits(task_dir)
    contract = harness_contract(task_dir)
    if hits:
        rendered = [
            f"===== {name} =====\n{truncate(text, _PER_FILE_CHARS, 0)}"
            for name, text in hits[:_MAX_FILES]
        ]
        blocks.append(
            "The fuzz harness source (the input contract — how raw input bytes reach the "
            "parser) is provided IN FULL below. Read it carefully; you do not need to grep "
            "for it.\n\n" + "\n\n".join(rendered)
        )
    else:
        blocks.append(
            "(harness source could not be auto-located; extract `repo-vul.tar.gz` and grep "
            'for `LLVMFuzzerTestOneInput` / `int main(` yourself.)'
        )

    blocks.append(
        "Deterministic harness contract JSON (tool-derived; refine only with source evidence):\n\n"
        f"```json\n{json.dumps(contract, indent=2, ensure_ascii=False)}\n```"
    )

    err = task_dir / "error.txt"
    if err.is_file():
        try:
            blocks.append(
                "Sanitizer crash report (error.txt) — the top frame is the sink, the "
                "SUMMARY line is ground truth:\n\n"
                + truncate(err.read_text(errors="replace"), _ERROR_CHARS, 0)
            )
        except OSError:
            pass

    return "\n\n".join(blocks)
