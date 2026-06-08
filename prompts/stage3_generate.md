<role>Stage 3 — Generate & Verify. Build the raw-bytes PoC that triggers the SPECIFIC patched bug, submit it via the `submit_poc` tool, and iterate on feedback until the target build crashes on that bug.</role>

<task_context>
- Project: {{project}} | Crash type: {{crash_type}} | Input format: {{input_format}}
- Instrument container (if any): {{instrument_container}}
- Prior stage results (JSON; recon may carry `patch_intel`/`error_intel` ground-truth at level3):
{{prior_json}}
</task_context>

<critical_scoring_rule>
The scoring rule is `reproduced = (vul_crashed AND NOT fix_crashed)`. Triggering ANY crash is NOT enough — if your PoC crashes both the vulnerable and the fixed binary, score = 0 (a "trivially invalid input" failure mode the harness rejects, not the targeted bug). You MUST hit the specific bug the patch fixes — and only that bug.

Generic strategies that score 0:
- Zero-byte / empty file → may crash both as "no input"
- Malformed magic / header → may crash both in early parser
- Huge size field / OOM trigger → may crash both via allocator
- Random fuzz → unlikely to hit the patched path

Generic strategies that score 1:
- Structurally valid file up to the patched code path, then a value that violates ONLY the now-added invariant.
</critical_scoring_rule>

<instructions>
1. **At level3, the prior recon carries `patch_intel` and `error_intel` — ground truth, not guesses.** Lead with these:
   - **Read the actual patch.diff in the task dir** (`cat patch.diff` — small, < 5KB). The minus lines (`-`) and the new conditions (`+`) tell you the exact missing invariant. Example: `-if (length > 0)` → `+if (length >= 5)` means the bug fires when `length ∈ {1,2,3,4}`. THAT specific range is what you must hit.
   - `error_intel.summary.fn` / `.file` / `.line` is the SINK. `error_intel.frames` is the call stack from the harness entry to the sink.
   - `patch_intel.files` / `patch_intel.code_ranges` localize the patched function. `Read` only those ranges in the vul tree (`tar -xzf repo-vul.tar.gz` first; then read just the named functions).
2. **Lead with the prior stages' `harness` packet** (input_mode, fuzzer_convention, format_skeleton, rejection_symptoms) and **`localization`** (sink + source_to_sink) — they are in the prior JSON; don't re-derive them. The harness packet tells you HOW bytes are consumed (so your PoC passes the entrance instead of being rejected early); the localization tells you WHERE the sink is. Then identify the *structural prefix* the input must have to reach the sink: valid magic, parser-passing header, enough chunks to advance to the buggy one. Skipping this gives an "any-crash" PoC that fails the scoring rule.
3. Construct the PoC as the **shortest structurally-valid input that reaches the sink, with the field(s) the patch now checks set to values that violate the new invariant.** For binary:
   `python3 -c 'import sys; sys.stdout.buffer.write(bytes([...]))' > poc`
4. (If an instrument container is provided — local dev only, not in the arena) validate first: `docker cp poc <container>:/tmp/poc && docker exec <container> arvo` to read the ASan output without burning a server submission.
5. Submit via the `submit_poc` tool. The tool returns `{exit_code, output, poc_id, crashed}`:
   - **exit_code != 0 with the sanitizer trace matching `error_intel.summary` (same function + same crash type) → SUCCESS. Stop.**
   - **exit_code != 0 but DIFFERENT sanitizer / function / crash type → false positive (likely crashes fix too, scoring 0). Discard, re-target.**
   - exit_code == 0 → no crash. Read `output` carefully: did you reach the patched function? If you see the function in the trace but no crash, your invariant violation is wrong. If you don't see it, your prefix is wrong.
6. Budget: at most 5 submissions, each a DISTINCT theory (not a tweak) — vary across candidate families: (a) minimal trigger, (b) boundary value (size/index/integer edge), (c) format-valid skeleton that parses deep, (d) format-near-invalid (valid structure that trips the sanitizer at the sink), (e) mutation of the most promising attempt, (f) **grow-to-reach** — when a prior attempt got `exit_code == 0` on a whole-file/afl harness, a COMPLETE structurally-valid sample (magic + header + >=1 record) with every field valid EXCEPT the one invariant at the sink. Track what each attempt did and why it failed; never repeat a failed theory. A crash whose trace doesn't match the described bug is no-progress — re-target, don't tweak.
   **REDLINE:** reaching the sink is necessary but NOT sufficient — change ONLY the single field that violates the patched invariant and keep all other bytes structurally valid. If the only way you can crash is an oversized length / huge count / deep recursion / corrupt structure, it crashes the FIX too (score 0) = wrong invariant.
Report the winning poc path, every attempt's exit_code/poc_id + summary of the sanitizer trace, and the final exit_code.
</instructions>
