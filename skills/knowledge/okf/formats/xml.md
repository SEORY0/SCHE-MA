---
type: format-family
title: xml format
description: Structure, build skeleton, and bug-prone areas of the xml input format.
resource: cybergym://format/xml
tags: [xml, html, svg, xpath]
timestamp: 2026-06-24T00:00:00Z
okf_support: 2
---
# Schema
## Identification
XML/HTML/SVG text. May start with `<?xml …?>` or a root tag. libxml2/expat parse it.
Note libxml2 fuzzers often use a packed input: `[4B maxAlloc][expr]\0[xml]` — read the harness.

## Structure
Element tree `<a attr="v"> … </a>`, entities `&name;`, DTD `<!DOCTYPE …>`, CDATA, namespaces `a:b`.

## Where bugs hide
- Entity-expansion / deep element nesting → recursion or OOM.
- Namespace/dict (string-interning) edge cases — empty sub-dictionaries, prefix handling.
- Null derefs on malformed/empty constructs after error recovery (`XML_PARSE_RECOVER`).

## How to build (raw text, honor the harness packing)
```python
open('poc','wb').write(b"<a>"*50000 + b"</a>"*50000)            # nesting
# libxml2 xpath fuzzer packing: maxAlloc(4) + xpath + \0 + xml
```

## Reachability
For an xpath/xinclude fuzzer, the XML must parse (RECOVER mode is lenient) before the expression runs.

# Examples
- Support: 2 train-set solves.
- Winning strategies (observed): {'seed-sweep': 2}
- Format families (observed): {'xml': 2}
- Abstract sink shapes (observed): detected:?, heap-buffer-overflow:READ

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
