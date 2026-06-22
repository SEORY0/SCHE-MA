---
name: construct_format_builder
description: Declarative binary format builder for complex multi-field binary files
type: tool
availability: always
required_package: construct
requires_tools: []
triggers: [format_complex, nested_structures, binary_format]
token_cost: medium
---
<tool_skill name="construct" availability="always">
# construct — Declarative Binary Format Builder

## When to Use
- Building complex multi-field binary files (fonts, media containers, 3D models, archives, image formats)
- The target format has nested structures, variable-length fields, or conditional sections
- Manual `bytes([...])` or `struct.pack()` is too error-prone for the format complexity

## When NOT to Use
- Simple flat byte sequences (< 20 bytes) — use `python3 -c "import sys; sys.stdout.buffer.write(...)"` directly
- Seed mutation — copy the seed and patch specific offsets instead

## Usage via bash tool

### Basic: Build a binary structure
```bash
python3 -c "
from construct import *
fmt = Struct(
    'magic' / Const(b'\x89PNG\r\n\x1a\n'),
    'chunk_len' / Int32ub,
    'chunk_type' / Bytes(4),
    'data' / Bytes(this.chunk_len),
    'crc' / Int32ub,
)
poc = fmt.build({'chunk_len': 13, 'chunk_type': b'IHDR', 'data': b'\x00'*13, 'crc': 0})
open('poc', 'wb').write(poc)
"
```

### Nested structures with validation bypass
```bash
python3 -c "
from construct import *

header = Struct(
    'magic' / Const(b'RIFF'),
    'size' / Int32ul,
    'format' / Const(b'WAVE'),
)
chunk = Struct(
    'id' / Bytes(4),
    'size' / Int32ul,
    'data' / Bytes(this.size),
)
# Build valid header + malicious chunk
h = header.build({'size': 100})
c = chunk.build({'id': b'fmt ', 'size': 0xFFFFFFFF, 'data': b''})  # overflow size
open('poc', 'wb').write(h + c)
"
```

### Variable-length arrays (e.g. font tables, container atoms)
```bash
python3 -c "
from construct import *

table_record = Struct(
    'tag' / Bytes(4),
    'checksum' / Int32ub,
    'offset' / Int32ub,
    'length' / Int32ub,
)
font_header = Struct(
    'sfVersion' / Int32ub,
    'numTables' / Int16ub,
    'searchRange' / Int16ub,
    'entrySelector' / Int16ub,
    'rangeShift' / Int16ub,
    'tables' / Array(this.numTables, table_record),
)
data = font_header.build({
    'sfVersion': 0x00010000,
    'numTables': 2,
    'searchRange': 16, 'entrySelector': 1, 'rangeShift': 0,
    'tables': [
        {'tag': b'CFF2', 'checksum': 0, 'offset': 64, 'length': 100},
        {'tag': b'head', 'checksum': 0, 'offset': 164, 'length': 54},
    ],
})
open('poc', 'wb').write(data)
"
```

## Key construct Types
| Type | Description | Example |
|------|-------------|---------|
| `Int8ub/Int16ub/Int32ub` | Unsigned big-endian | `'width' / Int32ub` |
| `Int8ul/Int16ul/Int32ul` | Unsigned little-endian | `'size' / Int32ul` |
| `Int8sb/Int16sb/Int32sb` | Signed big-endian | `'offset' / Int16sb` |
| `Bytes(n)` | Fixed-length bytes | `'magic' / Bytes(4)` |
| `Const(b'...')` | Fixed constant | `'sig' / Const(b'\x89PNG')` |
| `Array(n, subcon)` | Fixed-count array | `'tables' / Array(3, entry)` |
| `this.field` | Reference another field | `'data' / Bytes(this.length)` |
| `Computed(val)` | Virtual field | `'padding' / Computed(0)` |
| `Padded(n, subcon)` | Pad to alignment | `Padded(4, Int8ub)` |

## PoC Construction Pattern
1. Define the format skeleton with all required fields valid
2. `build()` with valid defaults to create a baseline
3. Patch the ONE violation field (the bug trigger) by changing its value
4. Verify the rest of the file remains structurally valid
</tool_skill>
