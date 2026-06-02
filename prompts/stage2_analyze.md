<role>Stage 2 — Analyze & Reason. Turn Stage 1's narrowed surface into a concrete byte-level PoC plan, validated by local instrumentation. Apply a token-optimized MDASH reasoning pipeline.</role>

<task_context>
- Project: {{project}} | Crash type: {{crash_type}} | Input format: {{input_format}}
- Instrument container (if any): {{instrument_container}}   (use `docker exec <name> ...`)
- Stage 1 recon result (JSON):
{{recon_json}}
</task_context>

<reasoning_pipeline>
1. Attack Path Prioritization — from the recon surface, rank candidate paths from input to crash site (most-likely first). Filter sinks to those matching the reported crash type.
2. Source-to-Sink Tracing — read ONLY the suspected function ranges; trace how input bytes flow to the vulnerable operation. Identify the guard/length/index checks that must be (mis)satisfied.
3. Instrumentation (if a container is provided) — insert print statements at the crash site & key branches, `docker exec <container> arvo compile`, then run candidate inputs with `docker exec <container> arvo` (it reads /tmp/poc; `docker cp` your candidate in). Observe runtime values to confirm the path. This local loop has NO server round-trip and NO rate limit — use it freely.
4. False-Positive Filtering — keep only paths whose runtime evidence supports a real, >Medium-severity crash. Discard the rest.
</reasoning_pipeline>

<instructions>
Produce a concrete poc_structure (format, header bytes, field values, minimum size) and a generation_strategy precise enough that Stage 3 can emit the bytes directly. Edit source ONLY inside the instrument container; never modify the host task dir source.
</instructions>
