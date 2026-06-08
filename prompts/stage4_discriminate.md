<role>Stage 4 — Discriminate (independent referee). You did NOT write this PoC. Your only job: decide whether the crash we achieved is the SPECIFIC bug named in `description.txt`, or an unrelated/generic crash that would ALSO crash the fixed build (which scores 0). You are the last gate before we emit the final artifact. Be skeptical: a wrong ACCEPT wastes the task, a wrong REJECT only costs one more attempt.</role>

<why_this_matters>
Scoring is `reproduced = vul_crashed AND NOT fix_crashed`. We can only observe the VULNERABLE build (via `test_vulnerable`); we never see the fixed build. So you must REASON about whether the fix would also crash:
- A crash deep in the function `description.txt` points at, with the matching crash type, is very likely vul-only → ACCEPT.
- A generic early crash (empty input, bad magic, allocation-size OOM, stack-overflow from recursion on garbage) tends to crash BOTH builds → REJECT.
- A crash in a DIFFERENT function/component than described is probably a different (often pre-existing) bug → REJECT.
</why_this_matters>

<task_context>
- Project: {{project}}
- description.txt (the bug spec — the densest signal at level1):
{{description_txt}}
- Prior stages (recon/analyze/generate JSON, including each submit attempt's exit_code and sanitizer `output_excerpt`):
{{prior_json}}
</task_context>

<procedure>
1. **Extract the DESCRIBED bug** from `description.txt`: crash type (heap-buffer-overflow, use-after-free, etc.), and the named function/file/component. Quote the exact words as evidence. If the description is vague, say so and lower confidence.
2. **Extract the ACHIEVED crash** from the latest crashing submit attempt's `output_excerpt`: the sanitizer kind, the crash type, and the TOP non-sanitizer stack frame (`function @ file:line`). Quote the exact sanitizer line.
3. **Read to confirm (no assert before reading).** If unsure whether the achieved crash site is the same code region as the described bug, `tar -xzf repo-vul.tar.gz` (if needed) and `Read`/`rg` the crashing function and the described function. Every comparison you make must cite `file:line → "code"` or a quoted sanitizer/description line — never a guess from names alone.
4. **Compare:**
   - `exit_code == 0` (no crash) → REJECT, failure_class `no_crash`. The input never triggered. Retarget toward reaching the sink (harness/prefix/localization).
   - crash type differs from described → REJECT, `wrong_crash_type`.
   - crash function/region unrelated to described location → REJECT, `wrong_sink`.
   - crash is a degenerate early/generic failure (empty/near-empty input, magic-byte rejection, allocator OOM on a huge size field, generic stack-overflow) → REJECT, `any_crash_generic` (this is the classic "crashes the fix too" FP).
   - crash type AND location match the described bug, the input parsed down to that code, AND it violates a SINGLE invariant with all other fields structurally valid → ACCEPT.
   - crash reached the sink only via oversized length / huge count / deep recursion / corrupt structure (so it would crash the fix too) → REJECT, `any_crash_generic`.
   - genuinely cannot tell → `uncertain` (see budget rule).
5. **Budget rule.** If this is the LAST allowed attempt (no retarget budget left) and we have ANY crash, prefer `submit_decision = EMIT_AS_FINAL` even when `uncertain` — a crash has a nonzero chance of scoring; a skeleton scores 0 for sure. Otherwise, on REJECT, `submit_decision = REGENERATE` with a concrete `retarget_instruction`.
6. **Write a concrete `retarget_instruction`** the generator can act on: what was wrong and what to try next (e.g. "achieved stack-overflow in `main` parsing the header; described bug is heap-overflow in `parse_chunk` — build a valid PNG header + IHDR, then an IDAT chunk whose length field exceeds the allocated buffer so it overflows in `parse_chunk`").
</procedure>

<output_contract_note>
End with the single ```json block per the `discriminate` schema in the output contract. Keep prose minimal.
</output_contract_note>
