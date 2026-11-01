---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Neo4j heap 512MB, not 2GB


The machine has ~3.1 GB available with 5.2 GB of swap already in use. A 2 GB
heap would swap on every run. At ~2k nodes, 512 MB heap + 512 MB pagecache is
ample.

Neo4j was kept (rather than swapped for a SQLite projection) because Cypher and
the visualisation ecosystem earn their keep — and because decision 2 means the
swap stays available as a contained change if the container ever becomes a
problem.
