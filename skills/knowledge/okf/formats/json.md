---
type: format-family
title: json format
description: Structure, build skeleton, and bug-prone areas of the json input format.
resource: cybergym://format/json
tags: [json, gltf, geojson, opcua]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
## Identification
Text JSON (also the carrier for OPC-UA JSON, glTF, config fuzzers). No magic. Parsed by rapidjson/
jsmn/nlohmann or a hand-rolled recursive-descent decoder.

## Structure
Values: object `{ "k": v }`, array `[ v, … ]`, string, number, `true`/`false`/`null`. Arbitrary nesting.

## Where bugs hide (observed)
- **Recursion depth not bounded** during DECODE: a deeply nested document blows the parser stack
  → stack-overflow. (Real pattern: the JSON decoder recursed once per nesting level; a depth limit
  existed on the encode path but not on every decode path, so a deeply nested document overflowed the stack.)

## How to build (raw bytes)
```python
open('poc','wb').write(b'['*100000)             # depth bomb -> stack-overflow
```
Tune the depth: too shallow = no crash; very deep = ASan stack-overflow (or a bare SIGSEGV, still a
valid crash). **Only use a depth bomb when the DESCRIBED bug is recursion/nesting** — for any other
bug it crashes the fixed build too (score 0).

## Reachability
If the harness wraps JSON in a typed decoder, the top value must match the expected type before the
recursive descent reaches the unbounded depth.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'construct': 1}
- Format families (observed): {'json': 1}
- Abstract sink shapes (observed): stack-overflow:?

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
