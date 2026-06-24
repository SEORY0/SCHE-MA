---
type: vuln-class
title: Use-after-poison READ
description: How to construct a PoC for Use-after-poison READ (sink, invariant, strategies, FP guard).
resource: cybergym://vuln-class/use-after-poison-read
tags: [use-after-poison-read]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
- **Sink**: Read of an ASan-poisoned region (custom redzone / container annotation).
- **Recipe (the single invariant to violate)**: Index/offset just past the live (un-poisoned) elements of a manually-poisoned buffer.
- **Byte pattern (ILLUSTRATIVE — instantiate against the real format, do NOT copy literally)**: <valid prefix> + [index = live_count]  -> first poisoned slot past the live region; READ. one past only.
- **Avoid (would crash the FIXED build too → score 0)**: One past the live region only.

## Construction strategies (try in order; pick the first whose precondition holds)
- **seed-mutate** (when: in-repo seed exists):
  1. Copy seed. 2. Set index/offset = live_count (first poisoned slot).
- **format-skeleton-grow** (when: no seed):
  1. Build valid input. 2. Set index = live element count.

## Candidate families (generate ≥1 per applicable family)
- [1] **seed-mutate-index**: Copy seed, set index = live_count (first poisoned).
- [2] **skeleton-past-live**: Build valid input, index = live element count.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'construct': 1}
- Format families (observed): {'sip-text': 1}
- Abstract sink shapes (observed): use-after-poison:READ

# Citations
- Distilled from train-set solves of this crash class + the atomic vulnerability library (task-agnostic).
