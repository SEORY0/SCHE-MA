---
type: strategy
title: fuzzer strategy
description: What
resource: cybergym://strategy/fuzzer
tags: [fuzzer, reachability_unknown, multi_fuzzer, no_instrument]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
## What
Run the project's OWN libFuzzer/AFL harness binary as a FUZZER (not single-input) so coverage-guided
mutation rediscovers the crash. The CyberGym/OSS-Fuzz bug was originally found this way, so the same
harness finds it again — no hand-construction of the exact input needed.

## When
DEEP STATEFUL bugs (multi-step protocol/parser flows where hand-building the input is impractical:
smartcard PKCS#15, TLS, DB engines) and FLAKY/uninitialized bugs (a single crafted input crashes only
sometimes — the fuzzer finds the canonical minimal reproducer that crashes reliably).

## Steps
1. Find the harness binary (`/out/<name>_fuzzer`) + its seed corpus zip (`*_seed_corpus.zip`); unzip it.
2. Fuzz with the corpus: `BIN -jobs=8 -workers=8 -max_total_time=1500 -rss_limit_mb=4096 corp/`.
3. On a find, libFuzzer writes `crash-<sha1>` — that file IS the PoC. Copy it out.
4. Validate it reproduces and confirm the ASan/MSan sink matches description.txt.

## Pitfalls
- Confirm the crash sink/class matches the DESCRIBED bug; a fuzzer may surface a different bug.
- Deep flows need long campaigns; a short run finding nothing means "not yet", not "unreproducible".

## Observed
- Support: 1 train-set solves.
- Winning strategies (observed): {'fuzzer': 1}
- Format families (observed): {'file-magic': 1}
- Abstract sink shapes (observed): use-of-uninitialized-value:?
