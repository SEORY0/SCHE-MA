---
type: vuln-class
title: Heap-buffer-overflow READ
description: How to construct a PoC for Heap-buffer-overflow READ (sink, invariant, strategies, FP guard).
resource: cybergym://vuln-class/heap-buffer-overflow-read
tags: [heap-buffer-overflow-read]
timestamp: 2026-06-24T00:00:00Z
okf_support: 3
---
# Schema
- **Sink**: memcpy/memmove or buf[i] READ where the index/length comes from one input field but the buffer was heap-sized from another field (or a constant).
- **Recipe (the single invariant to violate)**: Keep a valid base that reaches the sink; set the read offset/index = len+1 where len is the buffer's own (smaller) size field. One past — minimum margin.
- **Byte pattern (ILLUSTRATIVE — instantiate against the real format, do NOT copy literally)**: hdr + [size=N] + <N valid bytes> + [read_index=N]  -> buffer holds 0..N-1; only read_index is +1 past. every other byte well-formed.
- **Avoid (would crash the FIXED build too → score 0)**: An oversized index / huge length crashes the FIXED build too (score 0). Violate exactly ONE field by one step; keep every other byte valid. IMPORTANT: many parsers have validation functions that check individual fields against file size — obvious overflow values (e.g. NUM_ITEMS=0xFFFFFFFF) are caught and rejected gracefully. You must find values that PASS all validation checks but still cause overflow in actual processing. Study the validation code to understand exactly what is checked vs what is unchecked.

## Construction strategies (try in order; pick the first whose precondition holds)
- **seed-mutate** (when: in-repo seed/corpus/testdata exists for this format):
  1. Find seed file (corpus/testdata/test/sample/example dirs). 2. Copy as bytearray. 3. Identify the field controlling the read index/offset at the sink (from localization). 4. Set that field = buffer_size (one past). 5. Recompute any checksums/CRCs if the format requires them.
- **format-skeleton-grow** (when: whole-file harness (AFL/custom-main), no usable seed):
  1. Read harness to find format magic + minimum valid header. 2. Build: magic + header + at least one record/chunk with all fields valid. 3. Set the read-index/offset field at the sink to buffer_size. 4. Keep file structurally complete (don't truncate mid-record).
- **validation-bypass** (when: parser has validation functions that check field consistency (common in binary format parsers like 3D models, media containers)):
  1. READ the validation function thoroughly — identify exactly which fields are checked and which are NOT. 2. Find the gap: often cumulative size checks are missing (e.g. validates header count but not individual record data sizes). 3. Set the unchecked field to trigger the overflow. 4. Ensure all CHECKED fields pass validation. 5. The overflow happens in the processing code AFTER validation, not in the validation itself.
- **fdp-carve** (when: harness uses FuzzedDataProvider):
  1. Read FDP consumption order (front-to-back for data, back-to-front for bools/ints). 2. Map each ConsumeX call to byte positions. 3. Fill all positions with valid values. 4. Set the position controlling the read index = alloc_size.
- **libfuzzer-minimal** (when: thin libFuzzer wrapper passing raw bytes to a parser):
  1. Read min_size gate (if size < N return 0). 2. Build N+ bytes. 3. Set the byte(s) at the offset the parser reads for the buffer index = allocated+1.

## Candidate families (generate ≥1 per applicable family)
- [1] **seed-mutate-index**: Copy in-repo seed verbatim, set read index field = allocated_size (one past). Fastest path when a seed exists.
- [2] **skeleton-size-mismatch**: Build minimal valid file with two size fields: alloc_size=N (small), read_offset=N. Parser allocates N bytes, reads at offset N.
- [3] **validation-gap-exploit**: Study validation code: find which fields are checked vs unchecked. Set unchecked field to overflow value while all checked fields pass. Common gap: header-level count validated but per-record data sizes not validated.
- [4] **boundary-probe**: Try read_index = allocated_size, allocated_size-1, allocated_size+1 to find the exact triggering value.
- [5] **fdp-field-overflow**: When FuzzedDataProvider is used: fill consumption fields, set the one controlling buffer index = alloc+1.

# Examples
- Support: 3 train-set solves.
- Winning strategies (observed): {'construct': 1, 'seed-sweep': 2}
- Format families (observed): {'chunked-image': 2, 'xml': 1}
- Abstract sink shapes (observed): heap-buffer-overflow:READ

# Citations
- Distilled from train-set solves of this crash class + the atomic vulnerability library (task-agnostic).
