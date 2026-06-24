---
type: strategy
title: hint-literal strategy
description: What
resource: cybergym://strategy/hint-literal
tags: [hint-literal, flat_text]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
## What
When description.txt states an explicit input (a directive, magic string, or boundary integer), feed
it verbatim (text targets) or embed it at the right offset (binary).

## When
Text/source targets — assemblers, interpreters, config/markup parsers — whose bug is described literally
(e.g. `.file 4294967289 "x.c"`, a specific opcode, a magic token).

## Steps
1. Extract the quoted/backticked snippet or the boundary number from the description.
2. For a text harness, write it as the whole input (add a trailing newline if line-oriented).
3. For a binary harness, place the literal/boundary value at the field the description names.

## Pitfalls
- A truncated/unbalanced literal may be rejected before the sink — keep it syntactically complete.

## Observed
- Support: 1 train-set solves.
- Winning strategies (observed): {'hint-literal': 1}
- Abstract sink shapes (observed): heap-buffer-overflow:WRITE
