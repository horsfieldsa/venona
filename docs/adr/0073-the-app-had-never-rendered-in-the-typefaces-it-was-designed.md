---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The app had never rendered in the typefaces it was designed for


**Measured.** `docs/DESIGN.md`'s typography table named Charter and Inter. On
this machine:

```
Inter var      -> LiberationSans-Regular.ttf
Charter        -> LiberationSans-Regular.ttf     (not even a serif)
Source Serif 4 -> LiberationSerif-Regular.ttf
```

No `@font-face`, no font dependency, no `.woff` anywhere in `web/`. `--font-ui`
was a Helvetica clone, and `--font-prose` fell through its entire stack to
generic `ui-serif` — a Times clone, which is why the brief's headline looked
like a 1998 press release.

The weight axis is the part that actually mattered. **Liberation Sans ships 400
and 700 only**, so every `font-weight: 500 / 600 / 650` in the codebase snapped
or synthesised. The app had two weights where the design assumed four — which is
the real reason headings barely out-ranked body text, and no amount of colour or
token work could have fixed it.

**Consequence:** Inter, Source Serif 4 and JetBrains Mono are self-hosted via
`@fontsource-variable` — the project's first frontend dependency, and worth it.
`docs/DESIGN.md` now says that anything naming a family must ship it.

Also deleted: `-webkit-font-smoothing: antialiased` in `base.css`. It is a Mac
idiom; on Chromium/Linux it disables subpixel rendering and strips stem weight
from the 11–13px text this interface is mostly made of.

**Watch for:** a design decision tuned against the wrong font is worse than no
decision, because it looks deliberate. Every tracking value in the app had been
chosen against a Helvetica clone.
