---
type: format-family
title: chunked-image format
description: Structure, build skeleton, and bug-prone areas of the chunked-image input format.
resource: cybergym://format/chunked-image
tags: [chunked-image, png, mng, apng]
timestamp: 2026-06-24T00:00:00Z
okf_support: 2
---
# Schema
## Identification
PNG/MNG/APNG family. Magic: PNG = `89 50 4E 47 0D 0A 1A 0A`; MNG = `8A 4D 4E 47 0D 0A 1A 0A`.
A chunk stream follows the 8-byte signature.

## Structure
Repeated chunks, each: `length(4, BE)` + `type(4 ASCII)` + `data(length bytes)` + `crc(4, BE)`.
- `length` counts ONLY the data, not type/crc.
- CRC is almost always **unchecked** by the decoder → set it to 0.
- PNG order: IHDR (13B) first, then PLTE/IDAT/…/IEND. MNG: MHDR (≥16B, usually 28B) first.

## Where bugs hide (observed)
- A chunk handler reads a fixed number of bytes from `data` but only checks `length > 0` (not
  `length >= N`) → a short chunk causes an over-read. (Real pattern: an MNG `LOOP` chunk handler
  read a 4-byte integer from the chunk after only checking `length > 0`; a 1-byte `LOOP` chunk
  then reads 3 bytes past the heap allocation.)

## How to build (use the `construct` tool)
```python
from construct import Struct, Int32ub, Bytes, this, Rebuild, len_
Chunk = Struct("length"/Rebuild(Int32ub, len_(this.data)), "ctype"/Bytes(4),
               "data"/Bytes(this.length), "crc"/Int32ub)
sig = b"\x8aMNG\r\n\x1a\n"
mhdr = Chunk.build(dict(ctype=b"MHDR", data=bytes(28), crc=0))     # valid 28-byte header
poc  = sig + mhdr + Chunk.build(dict(ctype=b"LOOP", data=b"\x00", crc=0))  # 1-byte -> over-read
```
Keep every field valid EXCEPT the one length/count that violates the just-added check; CRC=0 is fine.

## Seeds & reachability
In-repo `*.png`/`*.mng` corpus is common → seed-sweep / seed-mutate first. To reach a late chunk
handler, keep a valid signature + first header chunk; decoders bail early on a bad prefix.

# Examples
- Support: 2 train-set solves.
- Winning strategies (observed): {'construct': 1, 'seed-sweep': 1}
- Format families (observed): {'chunked-image': 2}
- Abstract sink shapes (observed): heap-buffer-overflow:READ

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
