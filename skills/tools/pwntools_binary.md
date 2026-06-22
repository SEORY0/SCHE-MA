---
name: pwntools_binary
description: Binary packing and exploit primitives for flat byte-level PoC construction
type: tool
availability: always
required_package: pwn
requires_tools: []
triggers: [flat_binary, seed_mutation, integer_packing]
token_cost: low
---
<tool_skill name="pwntools" availability="always">
# pwntools — Binary Packing & Exploit Primitives

## When to Use
- Assembling flat binary PoCs with precise byte-level control
- Packing integers to exact widths with correct endianness
- Building structured byte sequences without full format declaration
- Quick seed patching at specific offsets

## When NOT to Use
- Complex nested formats with variable-length fields — use `construct` instead
- Purely text-based inputs

## Usage via bash tool

### Basic: Pack integers to bytes
```bash
python3 -c "
from pwn import p8, p16, p32, p64, flat
# Little-endian by default
poc = flat(
    b'MAGIC',          # raw bytes
    p32(0x00010000),   # 4-byte LE integer
    p16(256),          # 2-byte LE integer
    p8(0xff),          # 1-byte
    b'\x00' * 16,      # padding
)
open('poc', 'wb').write(poc)
"
```

### Big-endian packing
```bash
python3 -c "
from pwn import p32, p16
# Use keyword argument for endianness
header = p32(0x89504E47, endian='big')  # PNG magic as big-endian
length = p32(13, endian='big')          # IHDR length
open('poc', 'wb').write(header + length)
"
```

### Seed mutation at specific offset
```bash
python3 -c "
from pwn import p32
data = bytearray(open('corpus/seed1.bin', 'rb').read())
# Patch the length field at offset 8 to trigger overflow
data[8:12] = p32(0xFFFFFFFF)  # overflow value at the sink
open('poc', 'wb').write(bytes(data))
"
```

### Build structured input for FuzzedDataProvider harnesses
```bash
python3 -c "
from pwn import p8, p16, p32, flat
# FDP consumes bytes left-to-right: first ConsumeIntegral<uint32_t> reads 4 bytes, etc.
poc = flat(
    p32(3),            # first ConsumeIntegral: num_iterations = 3
    p16(0x100),        # second ConsumeIntegral: buffer_size = 256 (overflow trigger)
    b'A' * 8,          # ConsumeBytes(8): payload
    p8(1),             # ConsumeBool: enable_feature = true
)
open('poc', 'wb').write(poc)
"
```

### Cyclic pattern for crash offset discovery
```bash
python3 -c "
from pwn import cyclic
# Generate a 256-byte cyclic pattern — useful for finding exact overflow offset
open('poc', 'wb').write(cyclic(256))
"
```

## Key Functions
| Function | Description | Example |
|----------|-------------|---------|
| `p8(v)` | Pack 1-byte | `p8(0xff)` |
| `p16(v)` | Pack 2-byte LE | `p16(1024)` |
| `p32(v)` | Pack 4-byte LE | `p32(0xdeadbeef)` |
| `p64(v)` | Pack 8-byte LE | `p64(addr)` |
| `flat(...)` | Concatenate mixed types | `flat(b'A', p32(0), b'\x00'*8)` |
| `cyclic(n)` | De Bruijn pattern | `cyclic(128)` |
| `fit({off: val})` | Patch at offsets | `fit({8: p32(0xff)}, length=64)` |

## PoC Construction Pattern
1. Identify required header bytes (magic, version) from format_template
2. Use `flat()` to assemble header + valid fields + violation field
3. Use `p32`/`p16` with correct endianness for each integer field
4. For seed mutation: read seed as `bytearray`, patch with `pN()`, write back
</tool_skill>
