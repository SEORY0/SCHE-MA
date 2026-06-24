---
type: format-family
title: chunked-image format
description: Construction notes for the chunked-image input format.
resource: cybergym://format/chunked-image
tags: [chunked-image, png, mng]
timestamp: 2026-06-24T00:00:00Z
okf_support: 2
---
# Schema
- PNG/MNG-style: 8-byte signature + repeated [len(4,BE)|type(4)|data|crc(4)]. CRC is typically unchecked by the decoder. Build with `construct`; violate one length/field while keeping the chunk stream otherwise valid.
# Examples
- Winning strategies for this format: {'construct': 1, 'seed-sweep': 1}
# Citations
- Distilled from 2 train-set solves with this format.
