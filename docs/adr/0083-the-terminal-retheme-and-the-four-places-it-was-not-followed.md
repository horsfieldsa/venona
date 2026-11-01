---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — The terminal retheme, and the four places it was not followed


A design handoff re-themed the app: phosphor accent, JetBrains Mono chrome with
Inter for prose only, near-square corners, a two-row chrome, a brand mark, and
three new marks. Most of it landed as a token diff — nothing outside
`tokens.css` referenced a tier-1 name, so both tiers were replaced wholesale and
~80% of the app moved without a component change.

Four deviations, all deliberate. Each looks like a mistake in isolation, which
is why they are here.

**`--text-quiet` is `--grey-450`, not the specified `--grey-500`.** At
`--grey-500` it measures 3.52:1 on `--surface-base` and 2.96:1 on
`--surface-overlay` — it fails WCAG AA and misses the handoff's own stated 3:1
floor. `test_contracts.py` had already caught and rejected that exact value once
before. `--grey-450` clears 4.5:1 on `--surface-base`, which is the recessed
ground the ContextBar actually renders on, and stays above 3:1 elsewhere.
`test_text_quiet_is_legible_where_it_is_used` asserts both bounds, so a second
consumer that puts this token on a raised surface has to argue for it there
rather than be discovered by a reader squinting at an 11px label.

**Font families keep the ` Variable` suffix.** The handoff names
`'JetBrains Mono'` and `'Inter'` because it loads them from Google Fonts; the
`@fontsource-variable` packages this app self-hosts declare the suffixed names.
Copying the handoff verbatim would have fallen through to system faces — the
exact failure recorded above from when `Inter var` and `Charter` were named and
never rendered. Do not "correct" these back.

**The Toggle's state dot is a border, not the specified inset box-shadow.**
`box-sizing: border-box` is global, so the ring costs the dot no footprint and
the label cannot shift by a pixel as you toggle. The system has no shadow token
by design, and reaching for one to buy nothing is a change of register.

**The nav's progress spine is 26px (`--space-5`), not the specified 22px.**
22px is not on the spacing scale and the scale is enforced. A rung nobody can
tell apart from the specified value beats an arbitrary dimension with a lint
exemption.

**Consequence:** `--label-micro` is 10px, following the handoff's token file and
its summary table rather than its prose, which says 9px in one place. The
handoff contradicts itself there; the token file won.
