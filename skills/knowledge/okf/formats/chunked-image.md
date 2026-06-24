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
- CRC is almost always **unchecked** by decoders → set it to 0.
- PNG order: IHDR (13B) first, then PLTE/IDAT/…/IEND. MNG: MHDR (≥16B, usually 28B) first.
- IHDR data: width(4) height(4) bitdepth(1) colortype(1) compression(1) filter(1) interlace(1).

## Where bugs hide
- A chunk handler reads N bytes from `data` but only checks `length > 0` (not `length >= N`)
  → short chunk causes an over-read (e.g. the MNG LOOP/`mng_get_long` family).
- Per-chunk integer fields (counts, offsets, palette sizes) used without bounds checks.
- Width*height*bpp multiplication overflow sizing a row/image buffer.

## How to build (use the `construct` tool)
```python
from construct import Struct, Int32ub, Bytes, this, Rebuild, len_
Chunk = Struct("length"/Rebuild(Int32ub, len_(this.data)), "ctype"/Bytes(4),
               "data"/Bytes(this.length), "crc"/Int32ub)
sig = b"\x89PNG\r\n\x1a\n"
ihdr = Chunk.build(dict(ctype=b"IHDR", data=bytes(13), crc=0))
poc  = sig + ihdr + Chunk.build(dict(ctype=b"<buggy>", data=b"\x00", crc=0))  # short -> over-read
```
Keep every field valid EXCEPT the one length/count that violates the just-added check.

## Seeds & reachability
In-repo `*.png`/`*.mng` corpus is common → seed-mutate first. To reach a late chunk handler,
keep a valid IHDR/MHDR prefix; many decoders bail on a bad signature or first chunk.

# Examples
- Support: 2 train-set solves.
- Winning strategies (observed): {'construct': 1, 'seed-sweep': 1}
- Format families (observed): {'chunked-image': 2}
- Abstract sink shapes (observed): heap-buffer-overflow:READ

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
