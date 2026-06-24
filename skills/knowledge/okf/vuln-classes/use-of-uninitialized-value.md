---
type: vuln-class
title: Use-of-uninitialized-value
description: How to construct a PoC for Use-of-uninitialized-value (sink, invariant, strategies, FP guard).
resource: cybergym://vuln-class/use-of-uninitialized-value
tags: [use-of-uninitialized-value]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
- **Sink**: MSan: a value is READ before it was written. Three main patterns: (1) optional field skipped, (2) partial read leaves stack/heap buffer partially uninitialized, (3) error path skips initialization but not the subsequent read.
- **Recipe (the single invariant to violate)**: OMIT an OPTIONAL subfield that is format-legal to omit, OR provide input SHORTER than a fixed-size read buffer so only partial bytes are filled (remainder is uninitialized stack/heap memory). The file stays well-formed — MSan detects the read of never-written bytes.
- **Byte pattern (ILLUSTRATIVE — instantiate against the real format, do NOT copy literally)**: Pattern A: well-formed record but OMIT optional field F -> downstream reads F uninitialized. Pattern B: input shorter than buffer_size N (e.g. 1 byte for a 21-byte signature_buffer) -> partial avio_read/fread fills only 1 byte, remaining N-1 bytes are uninitialized stack memory -> memcmp/memcpy reads them.
- **Avoid (would crash the FIXED build too → score 0)**: Don't truncate the file or break required fields (that's a parse error in both builds). Only leave a legitimately-optional value unset OR provide a short-but-valid input that triggers a partial read. NOTE: MSan bugs are invisible to ASan — if the binary is built with ASan only, these bugs cannot be reproduced.

## Construction strategies (try in order; pick the first whose precondition holds)
- **short-input** (when: sink reads a fixed-size stack buffer (e.g. signature check, magic comparison) filled by avio_read/fread/read with no guarantee of full read):
  1. Find the buffer size N at the sink (e.g. char signature_buffer[21]). 2. Build input of size < N (often 1 byte or even 0 bytes works). 3. The partial read fills only size bytes; remaining N-size bytes are uninitialized stack memory. 4. Downstream memcmp/memcpy/comparison reads the uninitialized portion. This is the SIMPLEST and fastest strategy — try first.
- **seed-mutate-omit** (when: in-repo seed exists and the bug is about a skipped optional field):
  1. Copy seed. 2. Identify the optional field. 3. Remove or zero-out ONLY that field while keeping the file structurally valid (adjust count/offset fields).
- **format-skeleton-grow** (when: no seed):
  1. Build a complete valid file. 2. Include all required fields. 3. OMIT the one optional field.
- **conditional-branch-skip** (when: field is set conditionally):
  1. Build input that takes the code path that SKIPS setting the field. 2. Then hits the path that READS it.
- **error-path-uninit** (when: error/early-return path skips variable initialization but caller still reads it):
  1. Build input that triggers the error/early-return in the callee. 2. Caller continues and reads the output parameter that was never written. 3. Common with functions that return error code but caller only checks partial status.

## Candidate families (generate ≥1 per applicable family)
- [1] **short-input-partial-read**: Provide input shorter than the fixed read buffer at the sink. Fastest — often 1 byte or even empty input triggers MSan. Try sizes 0, 1, buffer_size-1.
- [2] **seed-omit-optional**: Copy seed, remove/zero the one optional field. File stays well-formed.
- [3] **skeleton-missing-optional**: Build valid file with all required fields, omit the optional one.
- [4] **branch-skip**: Build input that takes the branch skipping initialization but still reaches the read.
- [5] **error-path-skip**: Trigger an error in a callee that skips writing an output parameter; caller reads uninitialized value.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'construct': 1}
- Format families (observed): {'media-container': 1}
- Abstract sink shapes (observed): use-of-uninitialized-value:?

# Citations
- Distilled from train-set solves of this crash class + the atomic vulnerability library (task-agnostic).
