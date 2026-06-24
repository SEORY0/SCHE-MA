---
type: format-family
title: pcap format
description: Construction notes for the pcap input format.
resource: cybergym://format/pcap
tags: [pcap]
timestamp: 2026-06-24T00:00:00Z
okf_support: 1
---
# Schema
- Global header + per-packet [ts|caplen|len|bytes]; caplen vs len mismatch is a classic.
# Examples
- Winning strategies for this format: {'seed-sweep': 1}
# Citations
- Distilled from 1 train-set solves with this format.
