---
type: format-family
title: pcap format
description: Structure, build skeleton, and bug-prone areas of the pcap input format.
resource: cybergym://format/pcap
tags: [pcap]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
## Structure
Not yet curated in detail. Identify the magic/header, keep the prefix valid to reach the sink, and prefer seed-mutate when a corpus exists.

# Examples
- Support: 1 train-set solves.
- Winning strategies (observed): {'seed-sweep': 1}
- Format families (observed): {'pcap': 1}
- Abstract sink shapes (observed): heap-buffer-overflow:WRITE

# Citations
- Distilled from train-set solves with this format + curated format knowledge.
