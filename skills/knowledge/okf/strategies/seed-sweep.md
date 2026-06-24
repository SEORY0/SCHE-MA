---
type: strategy
title: seed-sweep strategy
description: Run EVERY in-repo corpus/seed file through the target first. For complex/container formats
resource: cybergym://strategy/seed-sweep
tags: [seed-sweep, seed_mutation]
timestamp: 2026-06-24T00:00:00Z
okf_support: 10
---
# Schema
- Run EVERY in-repo corpus/seed file through the target first. For complex/container formats a shipped seed frequently already reproduces the bug (or is one field away). Decisive tool: find_seeds. Always the first move when seeds exist.
# Examples
- Used in 10 train-set solves.
# Citations
- Empirical win count: 10.
