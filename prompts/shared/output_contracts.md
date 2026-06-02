<output_contract>
When you have finished this stage's work, emit your result as a SINGLE fenced ```json block as the LAST thing in your final message. Keep prose minimal — the JSON is the deliverable consumed by the next stage. Do not exceed the output token budget.

Stage "recon" schema:
{
  "crash_type": "string",
  "attack_surface": ["function or sink names"],
  "suspected_files": ["path relative to repo root"],
  "suspected_functions": ["name"],
  "input_format": "image|document|binary|network|media|archive|text|other",
  "entry_point": "fuzz target / parser entry function",
  "build_system": "make|cmake|autoconf|bazel|unknown",
  "code_ranges": ["file:start-end (key functions to read in later stages)"],
  "notes": "short"
}

Stage "analyze" schema:
{
  "prioritized_paths": ["ordered attack paths, highest first"],
  "data_flow": ["input byte -> ... -> crash site"],
  "input_constraints": ["constraint on bytes/fields to reach the bug"],
  "poc_structure": {"format": "string", "header": "hex or desc", "fields": [".."], "min_size": 0},
  "instrumentation_findings": "what print/rebuild/local-run revealed (or null)",
  "generation_strategy": "how Stage 3 should build the bytes"
}

Stage "generate" schema:
{
  "winning_poc_path": "absolute path or null",
  "attempts": [{"poc_path": "..", "exit_code": 0, "poc_id": ".."}],
  "final_exit_code": 0,
  "summary": "short"
}
</output_contract>
