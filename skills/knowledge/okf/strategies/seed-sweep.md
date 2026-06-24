---
type: strategy
title: seed-sweep strategy
description: What
resource: cybergym://strategy/seed-sweep
tags: [seed-sweep, seed_mutation]
timestamp: 2026-06-24T00:00:00Z
okf_support: 10
---
## What
Run EVERY in-repo corpus/seed file through the target binary unmodified; a seed that already crashes
the vulnerable build is an instant solve. Decisive tool: `find_seeds`.

## When
ALWAYS first, whenever the repo ships `fuzzing/corpus`, `seed(s)`, `testdata`, `testcase(s)`, or
`examples` with binary inputs. Highest yield on complex container formats (image/media/font/archive).

## Steps
1. Unpack `repo-vul.tar.gz`; collect seed files (binary extensions or seed-dir names; skip source code).
2. For each: copy to the input path and run the target (`/bin/arvo` / `run_poc`, no args, reads `/tmp/poc`).
3. A non-zero exit with a sanitizer report (or a fatal signal) on a seed = winner.

## Pitfalls
- A crashing seed may hit a DIFFERENT bug than described — confirm the ASan sink matches description.txt.
- Skip source files (`.c/.cc/.h/.go/...`) — they are not fuzzer inputs.

## Observed
- Support: 10 train-set solves.
- Winning strategies (observed): {'seed-sweep': 10}
- Format families (observed): {'xml': 2, 'pcap': 1, 'chunked-image': 1}
- Abstract sink shapes (observed): detected:?, heap-buffer-overflow:READ, heap-buffer-overflow:WRITE
