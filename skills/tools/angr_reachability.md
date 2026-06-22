<tool_skill name="angr" availability="always">
# angr — Static Binary Reachability Analysis

## When to Use
- Verifying a suspected sink function is reachable from the harness entry point BEFORE building a PoC
- When the repo has multiple fuzzers (4-15+) and you need to confirm which code paths the actual harness exercises
- When coverage_check is unavailable (no instrument container) and you need static reachability
- Extracting the call graph to plan your PoC's execution path

## When NOT to Use
- For source-level code reading — use `read_file`/`grep` directly on the C/C++ source
- When coverage_check IS available — dynamic verification is more reliable than static analysis
- For very large binaries (>50MB) — CFG analysis may timeout

## Usage via bash tool

### Check if a function exists in the binary
```bash
python3 -c "
import angr
proj = angr.Project('./binary', auto_load_libs=False)
cfg = proj.analyses.CFGFast()
names = {f.name for f in cfg.kb.functions.values() if f.name}
target = 'vuln_function'
print(f'{target} exists: {target in names}')
if target in names:
    f = [f for f in cfg.kb.functions.values() if f.name == target][0]
    print(f'  address: {hex(f.addr)}, size: {f.size}')
" 2>/dev/null
```

### Check reachability from entry to sink
```bash
python3 -c "
import angr, networkx as nx
proj = angr.Project('./binary', auto_load_libs=False)
cfg = proj.analyses.CFGFast()
funcs = {f.name: f for f in cfg.kb.functions.values() if f.name}

entry_name = 'LLVMFuzzerTestOneInput'
sink_name = 'vuln_function'

if entry_name not in funcs:
    print(f'ERROR: {entry_name} not found')
elif sink_name not in funcs:
    print(f'ERROR: {sink_name} not found in binary')
else:
    cg = cfg.kb.callgraph
    entry_addr = funcs[entry_name].addr
    sink_addr = funcs[sink_name].addr
    if nx.has_path(cg, entry_addr, sink_addr):
        path = nx.shortest_path(cg, entry_addr, sink_addr)
        chain = []
        for addr in path:
            fn = cfg.kb.functions.get(addr)
            chain.append(fn.name if fn and fn.name else hex(addr))
        print(f'REACHABLE: {\" -> \".join(chain)}')
    else:
        print(f'NOT REACHABLE: {sink_name} cannot be called from {entry_name}')
        # Show what IS reachable from entry
        reachable = nx.descendants(cg, entry_addr)
        named = sorted(f.name for a in reachable
                       if (f := cfg.kb.functions.get(a)) and f.name
                       and not f.name.startswith('_'))[:20]
        print(f'Reachable functions: {named}')
" 2>/dev/null
```

### List all functions callable from the harness
```bash
python3 -c "
import angr, networkx as nx
proj = angr.Project('./binary', auto_load_libs=False)
cfg = proj.analyses.CFGFast()
funcs = {f.name: f for f in cfg.kb.functions.values() if f.name}
entry = funcs.get('LLVMFuzzerTestOneInput') or funcs.get('main')
if not entry:
    print('No entry point found')
else:
    cg = cfg.kb.callgraph
    reachable = nx.descendants(cg, entry.addr)
    named = sorted(f.name for a in reachable
                   if (f := cfg.kb.functions.get(a)) and f.name
                   and not f.name.startswith('_'))
    print(f'Functions reachable from {entry.name} ({len(named)}):')
    for n in named[:50]:
        print(f'  {n}')
" 2>/dev/null
```

## Getting the Binary

angr needs the compiled binary file. Sources:

1. **From instrument container** (if available):
```bash
# Find the binary inside the container
docker exec <container> find /out -type f -executable | head -5
# Copy it out
docker cp <container>:/out/target_binary ./binary
```

2. **Compile from source** (if build system is available):
```bash
tar -xzf repo-vul.tar.gz
cd repo-vul
# Look for build instructions
cat Makefile 2>/dev/null || cat CMakeLists.txt 2>/dev/null || cat build.sh 2>/dev/null
```

3. **From /out directory** (some CyberGym tasks pre-build):
```bash
ls -la /out/ 2>/dev/null
```

## Decision Flow

```
Suspected sink function identified from description.txt
  │
  ├─ instrument container available?
  │   ├─ YES → Use coverage_check (dynamic, more reliable)
  │   └─ NO  → Use angr (static, no binary execution needed)
  │
  └─ angr result:
      ├─ REACHABLE → Proceed to PoC construction
      ├─ NOT REACHABLE → This function cannot be triggered from the harness
      │   → Look for alternative entry paths or different sink candidates
      └─ FUNCTION NOT FOUND → Wrong function name, check with nm/objdump
```

## Limitations
- Static analysis: may report paths that are infeasible at runtime (false reachable)
- Indirect calls (function pointers, vtables) may not be resolved
- Large binaries: CFGFast is fast but may miss some paths; CFGEmulated is thorough but slow
- Stripped binaries: function names may be missing — use addresses instead
</tool_skill>
