---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The token linter checks structure, not values


`lint_tokens.py` catches raw colours, tier-1 references and undeclared names. It
is completely blind to what a token is *worth*, because a re-map is not a
rename. Three real defects were sitting behind that blindness:

- `--surface-inset` was **lighter** than `--surface-raised` — the recessed token
  out-ranked the raised one — and base-to-raised was ΔL\* 2.8, below the
  just-noticeable difference for two large adjacent fields. That is why every
  region needed a border to read at all.
- `--entity-undefined` measured **2.70:1** on `--surface-overlay`. An undefined
  acronym still has to be readable; the dashed underline is what marks it.
- A proposed `--text-quiet: --grey-500` measured **3.34:1** and would have been
  applied to every micro-label in the app.

**Consequence:** `tests/test_contracts.py` now parses `tokens.css`, resolves
`var()` indirection and asserts every `--text-*` at ≥4.5:1 against every
`--surface-*`, every `--entity-*` at ≥3:1, and that the surface ladder is
monotonic with a ≥5 L\* base-to-raised step. It caught `--entity-undefined` on
the first run.

`RAW_COLOR` also grew `color-mix|lab|lch|oklab|color(` — it caught `oklch(` and
missed the rest, so a component could synthesise any colour and pass. The one
legitimate use (a mention's hover wash, which is a function of `currentColor`
and therefore not expressible as a token) is annotated `/* token-lint: ok */`,
mirroring `lint_determinism.py`'s existing escape.

**Also removed:** `--edge-single-source`, which was declared, referenced by
nothing, and composited to 1.22:1 — invisible had anything used it. Single-source
edges are dashed, which is what `DESIGN.md` requires anyway, since epistemic
weakness has to survive greyscale.
