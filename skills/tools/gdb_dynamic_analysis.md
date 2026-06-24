---
name: gdb_dynamic_analysis
description: Dynamic analysis and debugging via GDB for crash verification and path tracing
type: tool
availability: instrument_container
required_package: ~
requires_tools: [gdb_script, coverage_check]
triggers: [no_crash, wrong_sink, wrong_crash_type, coverage_unknown, reachability_unknown]
token_cost: medium
---
<tool_skill name="gdb" availability="instrument_container">
# GDB — Dynamic Analysis & Debugging

## When to Use
- Verifying your PoC reaches the suspected vulnerable function BEFORE submitting
- Inspecting memory state at the crash point to confirm the crash type
- Tracing execution path to understand why your input doesn't trigger the bug
- Comparing actual vs expected crash location after a wrong_crash rejection

## When NOT to Use
- When no instrument container is attached (GDB tools are unavailable)
- For initial code analysis — read the source first, use GDB to VERIFY hypotheses
- For MSan-only bugs — GDB won't show uninitialized memory reads

## Tool: gdb_script

Runs GDB batch commands against the vulnerable binary with your PoC as input.

### Verify a function is reached
```json
{
  "poc_path": "poc",
  "commands": "break parse_chunk\nrun\nbt"
}
```
If the breakpoint is hit, your input reaches `parse_chunk`. If GDB exits without hitting it, your input is rejected earlier in the parser.

### Inspect crash details
```json
{
  "poc_path": "poc",
  "commands": "run\nbt full\ninfo registers\nx/32xb $rsp"
}
```
After a crash: `bt full` shows the call stack with locals, `info registers` shows register state, `x/32xb $rsp` dumps stack bytes.

### Trace execution through parser stages
```json
{
  "poc_path": "poc",
  "commands": "break read_header\nbreak parse_body\nbreak process_data\nbreak vuln_function\nrun\ncontinue\ncontinue\ncontinue"
}
```
Set breakpoints at each parser stage to see how far your input progresses.

### Watch a specific variable
```json
{
  "poc_path": "poc",
  "commands": "break vuln_function\nrun\nprint buffer_size\nprint allocated_size\nprint buffer_size - allocated_size"
}
```
Check if your overflow value actually reaches the vulnerable comparison.

## Tool: coverage_check

Simplified reachability verification — provide function names, get REACHED/NOT_REACHED.

### Check if input reaches the sink
```json
{
  "poc_path": "poc",
  "functions": ["LLVMFuzzerTestOneInput", "parse_header", "decode_frame", "vuln_memcpy"]
}
```
Returns:
```
REACHED: LLVMFuzzerTestOneInput, parse_header
NOT REACHED: decode_frame, vuln_memcpy
```
This tells you the input passes the entry and header parsing but doesn't reach the vulnerable code — fix the body/payload structure.

### Iterative refinement workflow
1. Build initial PoC with valid header
2. `coverage_check` with [entry, parser_stage1, parser_stage2, ..., sink]
3. Find where the chain breaks
4. Fix that stage's format requirements
5. Repeat until sink is REACHED
6. Then `submit_poc`

## Diagnostic Patterns

### "exit_code=0, no crash" diagnosis
Your input doesn't reach the vulnerable path. Use coverage_check to find WHERE it stops:
```json
{
  "poc_path": "poc",
  "functions": ["LLVMFuzzerTestOneInput", "main_parser", "sub_parser", "vuln_func"]
}
```

### "wrong crash type" diagnosis
You crashed, but at the wrong location. Use gdb_script to see the ACTUAL crash:
```json
{
  "poc_path": "poc",
  "commands": "run\nbt\nframe 0\ninfo locals"
}
```
Compare the crash function/line against what description.txt describes.

### "crash in both vul and fix" diagnosis
Your input is too malformed — it crashes in early parsing, not at the patched bug. Use GDB to check if the crash is in the target function:
```json
{
  "poc_path": "poc",
  "commands": "break target_vuln_function\nrun\nbt"
}
```
If the breakpoint is never hit, your crash is elsewhere → make the input more structurally valid.

## Limitations
- Requires instrument container (arvo tasks only in local mode)
- ASan-instrumented binaries may behave differently under GDB
- MSan bugs are not detectable via GDB — use submit_poc directly
- GDB adds ~2-5 seconds per invocation — use sparingly, not every turn
</tool_skill>
