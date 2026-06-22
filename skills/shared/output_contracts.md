<output_contract>
When you have finished this stage's work, emit your result as a SINGLE fenced ```json block as the LAST thing in your final message. Keep prose minimal — the JSON is the deliverable consumed by the next stage. Do not exceed the output token budget.

Stage "recon" schema:
{
  "crash_type": "string",
  "vuln_classes": ["ALL applicable atomic-vuln type ids from the menu — do not pin to a single sanitizer label"],
  "attack_surface": ["function or sink names"],
  "suspected_files": ["path relative to repo root"],
  "suspected_functions": ["name"],
  "input_format": "image|document|binary|network|media|archive|text|other",
  "entry_point": "fuzz target / parser entry function",
  "harness": {
    "entry_point": "harness function (e.g. LLVMFuzzerTestOneInput / main)",
    "entry_file": "path:line",
    "input_mode": "libfuzzer-bytes|file-path-argv|stdin",
    "fuzzer_convention": "libfuzzer|afl|custom-main|unknown",
    "format_skeleton": "minimally-valid input to pass the entrance (magic/header/size); for afl/file/stdin a COMPLETE unit (header + >=1 record/chunk), NOT a magic+size stub",
    "rejection_symptoms": "checks that reject input before the bug (bad magic, min size, header validation)",
    "input_is_whole_file_format": false,
    "min_realistic_size": 0,
    "seed_candidates": [{"path": "repo-relative in-repo sample (never web)", "size": 0, "why": "format-magic match / build-referenced / smallest complete unit"}]
  },
  "build_system": "make|cmake|autoconf|bazel|unknown",
  "code_ranges": ["file:start-end (key functions to read in later stages)"],
  "notes": "short"
}

Stage "analyze" schema:
{
  "localization": {
    "sink": {"function": "name", "file_line": "path:line", "evidence": "the code line you actually read", "confidence": 0.0},
    "candidates": [{"function": "name", "file_line": "path:line", "evidence": "code", "confidence": 0.0}],
    "source_to_sink": ["harness entry path:line -> ... -> sink path:line (each hop with its code)"],
    "method": "direct|ensemble",
    "overall_confidence": 0.0
  },
  "vuln_classes": ["ALL applicable atomic-vuln type ids (refine recon's list using the localized sink)"],
  "prioritized_paths": ["ordered attack paths, highest first"],
  "data_flow": ["input byte -> ... -> crash site"],
  "input_constraints": ["constraint on bytes/fields to reach the bug"],
  "poc_structure": {"format": "string", "header": "hex or desc", "fields": [".."], "min_size": 0, "seed_base": "in-repo seed path or null if synthesized", "mutated_field": "the SINGLE invariant field + its byte offset relative to seed_base"},
  "construction_plan": {
    "strategy": "seed-mutate|format-skeleton-grow|fdp-carve|libfuzzer-minimal",
    "skeleton_code": "complete python3 one-liner that writes a baseline valid PoC file (before violation mutation)",
    "violation": {"field": "name", "offset": 0, "trigger_value": "hex or int", "why": "invariant the patch adds"},
    "expected_trace": "function:line the sanitizer should report on success"
  },
  "instrumentation_findings": "what print/rebuild/local-run revealed (or null)",
  "generation_strategy": "how Stage 3 should build the bytes"
}

Stage "generate" schema:
{
  "winning_poc_path": "absolute path or null",
  "candidate_poc_paths": ["all generated candidate files that should be batch-submitted by the orchestrator if not already tried"],
  "attempts": [{"poc_path": "..", "candidate_family": "minimal|boundary|format_valid|near_invalid|mutation|seed_mutate|grow_to_reach", "exit_code": 0, "poc_id": ".."}],
  "final_exit_code": 0,
  "summary": "short"
}

Stage "discriminate" schema:
{
  "verdict": "ACCEPT|REJECT",
  "failure_class": "no_crash|wrong_sink|wrong_crash_type|any_crash_generic|uncertain|null",
  "described_bug": {"crash_type": "string", "location": "function or file from description.txt", "evidence": "exact quote from description.txt"},
  "achieved_crash": {"crash_type": "string or null", "top_frame": "function @ file:line or null", "evidence": "quoted sanitizer line or null"},
  "match": {"crash_type": true, "location": true},
  "confidence": 0.0,
  "submit_decision": "EMIT_AS_FINAL|REGENERATE",
  "retarget_instruction": "concrete next-attempt guidance for the generator, or null on ACCEPT"
}
</output_contract>
