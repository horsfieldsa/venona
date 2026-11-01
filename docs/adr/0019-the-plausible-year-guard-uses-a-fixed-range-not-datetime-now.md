---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — The plausible-year guard uses a fixed range, not `datetime.now()`


`_date_from_html` rejects absurd dates (epoch defaults, `9999`, template zeros).
The first version bounded the upper end with `datetime.now().year + 1`, which
`make lint:determinism` correctly flagged: a fixture recorded today would behave
differently when replayed next year.

Now a fixed `2000..2100` range. It catches every real failure mode and is
reproducible forever.
