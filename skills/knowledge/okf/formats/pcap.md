---
type: format-family
title: pcap format
description: Structure, build skeleton, and bug-prone areas of the pcap input format.
resource: cybergym://format/pcap
tags: [pcap, stun, dpi, packet]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
## Identification
Network capture / DPI. Either a libpcap file (magic `D4 C3 B2 A1` LE or `A1 B2 C3 D4` BE) or, for
nDPI-style fuzzers, a raw L2/L3 packet passed straight to the dissector.

## Structure
- pcap: global header (24B) then per-packet `[ts_sec(4) ts_usec(4) caplen(4) origlen(4)]` + packet bytes.
- Raw packet path: Ethernet(14) → IPv4(20)/IPv6(40) → UDP(8)/TCP(20) → payload (the dissected protocol).

## Where bugs hide
- `caplen` vs `origlen` mismatch; caplen larger than the remaining file.
- A dissector reading fixed offsets into a payload shorter than the header it assumes
  (e.g. STUN reads `payload[7..12]`/`payload[11..12]` past a short UDP payload).
- Per-attribute TLV loops (`type,len,value`) where `len` runs past the packet.

## How to build (`struct`/`construct`; or seed-mutate a .pcap)
```python
import struct
def ipv4_udp(payload, proto=17):
    udp = struct.pack('>HHHH', 1,2, 8+len(payload), 0) + payload
    ip  = struct.pack('>BBHHHBBH4s4s', 0x45,0, 20+len(udp), 0,0, 64,proto,0, b'\x7f\0\0\1', b'\x7f\0\0\1')
    return ip + udp
```
Make the framing valid enough to route to the target dissector, then under-size the payload.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'seed-sweep': 1}
- Format families (observed): {'pcap': 1}
- Abstract sink shapes (observed): heap-buffer-overflow:WRITE

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
