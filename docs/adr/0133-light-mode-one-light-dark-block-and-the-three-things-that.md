---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Light mode: one `light-dark()` block, and the three things that were not in the tier-2 block


`tokens.css` promised light mode in its first commit — *"Uncomment, assign,
done"* — and `docs/DESIGN.md` sold the whole two-tier architecture on it. This
cashes it. The promise about the token layer was accurate. What it did not cover
is worth recording, because all three failures are silent.

**One block, not two.** Every tier-2 colour is `light-dark(light, dark)`,
switched by `color-scheme`. The alternative was `:root` plus
`:root[data-theme='light']` redeclaring the palette; rejected because two copies
drift, and a token retuned in one and not the other is invisible until somebody
switches themes. `test_no_colour_token_is_declared_twice` is what holds the file
to that shape.

**Light is the first argument**, inverted against a codebase that is dark-first
everywhere else, because that is the order `light-dark()` takes. It gets a banner
comment: swapping the two on one token renders correctly on whichever theme the
author happens to be looking at.

**System is the absence of `data-theme`, not a third value for it.** `:root`
declares `color-scheme: light dark` and only the two explicit choices override
it, so the pre-boot document — before any stylesheet, before `theme-boot.js` —
already agrees with the operating system. That is why `index.html` ships no
`data-theme`: with one, first paint is pinned for everybody. It is also why the
boot script and the store both DELETE as well as set; one that only ever sets can
never return to System, and the symptom appears one reload later.

### The build lowers `light-dark()` differently in dev than in build

**Measured, on the first token converted.** Vite's default `cssTarget` is older
than `light-dark()`, so on build Lightning CSS (which arrives with the Tailwind
plugin) rewrites it to `var(--lightningcss-light, A) var(--lightningcss-dark, B)`
and stamps that pair onto whichever rule declared `color-scheme` — resolved
STATICALLY, at build time. `vite dev` does no lowering and serves the function
untouched.

Both render correctly. They stop agreeing the moment the theme switches at
runtime, because the polyfill answers to the `color-scheme` Lightning CSS read in
the source rather than the one that won the cascade in the browser. Dev would
have been right and production frozen on one palette — the same shape as the
`@source` scar this project already carries, and quieter.

`cssTarget` is pinned to `light-dark()`'s own baseline in every config that ships
it, the old landing site included even though it has no Tailwind plugin and the pin
changes nothing there today. `tests/test_css_target.py` holds the copies
together.

### The contrast suite would have gone vacuous, and passed

`_resolve_tokens` kept only values matching `#rrggbb`. Under `light-dark()` NO
colour token matches, so all four sweeps would have iterated empty dicts — and
the guard against exactly that, `assert surfaces and texts`, is satisfied by one
surviving token. A contrast suite that measures nothing and reports green is
worse than none: it is the reason to stop looking.

It picks the side BEFORE following `var()` now (the sides *are* `var()`
references), splits parens-aware (`--scrim` nests `rgb(0 0 0 / 68%)`), asserts
exact family counts rather than non-emptiness, and runs every sweep over both
palettes.

The ladder test asserted a shape light mode is right to break. At night the page
is the darkest thing and a recess goes LIGHTER; on paper the page is a grey and a
recess goes DARKER. The direction is derived from `--surface-base`'s own
lightness rather than declared per theme.

### The graph would have lost its colours in both themes

A custom property's computed value is its declared token stream.
`useCytoscape.ts` did `getComputedStyle(root).getPropertyValue(name)` for twenty
colours and handed the string to a CANVAS renderer, which takes no `var()`, no
`light-dark()`, and errors on neither — an unparseable colour becomes Cytoscape's
own default. It worked only because every token happened to resolve to a
`#rrggbb` in one hop.

They resolve through a probe element now, against a sentinel colour so a token
that fails to resolve throws rather than quietly returning the inherited one.
`lint_tokens.py` refuses the next literal `getPropertyValue` on a colour token.

The stylesheet was also built once per instance, so switching theme left the
largest surface in the product in the old palette. It watches `data-theme` and
`prefers-color-scheme` from inside the composable — two sources, because neither
subsumes the other, and from there rather than a component so the canvas follows
the DOCUMENT.

### `all: initial` resets `color-scheme`, and `normal` means light

The extension's card is a shadow root, and `color-scheme`'s initial value is
`normal` — under which `light-dark()` returns the LIGHT side. So the card would
have rendered light on every page for every reader, with every token present and
correct, and nothing in the build able to see it.

There are TWO such resets: the `:host` rule in `card.css`, and an inline
`all: initial` on the host element in the outer tree (which stops a host page's
`div { margin: … }` moving a card anchored to it). The inline one also decides
the mechanism: an inline declaration outranks every author rule including
`:host([data-venona-theme='dark'])`, so an attribute-driven override would have
been generated, matched and lost. One mechanism instead of two that disagree —
`color-scheme: light dark` declared in CSS, and an inline `color-scheme` written
by `applyTheme` for an explicit choice.

### The hue target moved off white, and `forLightGround()` was retired

The extension already derived light-ground hues by binary-searching a uniform
channel scaling until each cleared 4.5:1 on WHITE. The token layer needed the
same twelve values, and two derivations of one number is exactly the drift
`emit-styles.mjs` exists to prevent — so there is one, and it lives in the tokens.

But the target moved. `--sage-500` is double-booked as `--entity-company` AND
`--signal-ok`, and the signal families are set as TEXT in ~24 places; derived on
white, sage lands at 3.72:1 on the light page, below AA in a role it holds. The
target is 4.5:1 on the light page instead, which is 5.50:1 on white — strictly
stronger, so the hairline doctrine the old function existed to satisfy is
satisfied with margin. Worst entity hue across all five light surfaces is 4.05:1
against a 3:1 contract; worst signal-as-text is 4.05:1, better than the dark
theme's own 3.71.

Uniform channel scaling is the whole method and it is not an approximation: it
leaves the ratios between channels untouched, so HSL hue and saturation are
preserved exactly and only lightness moves. The same green, darker.

### The light ladder has about 2.6 L* of room, and `--text-quiet` fixes it

L* is compressed near white. Four rungs plus the existing `base → raised ≥ 5 L*`
floor plus `--text-quiet` needing 4.5:1 on base leaves a window roughly 2.6 L*
wide. `--grey-500` is the last rung that clears the floor, at 4.53:1 — so **how
light the page may be follows from that token**, not the other way round.
Retuning `--surface-base` means re-deriving it first.

`raised → overlay` is the thin step at 2.17 L* and leans on the border more than
the dark theme does. It passes, and it is the least-bad split in the budget
available.

Three values will look wrong to the next reader and are not:

- **`--accent-deep` moves UP the ramp on paper** while the other two accent rungs
  move down. The name describes a dark value; the ROLE is "quiet enough to read
  as a rule", and quiet on paper is lighter. 3.02:1 against dark's 3.09.
- **`--text-inverse` inverts its role, not its value.** It is text on a filled
  accent, and the fill goes from bright aqua to deep teal.
- **`--edge-confirmed` has no pair.** A mid-grey is the one value that reads on
  both grounds — 3.52:1 dark, 4.53:1 light. Giving it a pair would be two values
  doing one job.

### The scrim alpha changes, 68% → 45%

Black at 68% over a light page composites to `#494b4c` — darker than the DARK
theme's own panels, which reads as a light dialog cut into a night scene rather
than as a page pushed back. 45% darkens the page 38.7 L* and leaves the white
dialog at 3.97:1 against its own backdrop. Still black and still unconditional,
never a tint of a surface token. If it reads weak, 55% is the next stop and the
last.

### An MV3 service worker has no `matchMedia`

The card and the options page have a DOM, so `color-scheme: light dark` resolves
System for them with no JavaScript at all. The toolbar badge cannot:
`setBadgeBackgroundColor` takes a literal, and the worker that calls it has no
DOM and no media queries. It needs a *stored* answer.

So a second content script, registered statically against venona's own origins —
the one place `shared/sites.ts` deliberately refuses to register the splicer,
because deriving that registration from `permissions.getAll().origins` would
inject `content.js` into the app itself. The two must never converge:
`finish.mjs` asserts that negatively and bounds the bundle at 4096 bytes, because
a bundle that grew is one that started importing the card.

It follows `data-theme` on `<html>`, not `window.onstorage`: `onstorage` fires in
OTHER tabs of an origin and never in the one that wrote the value, so a reader
clicking the toggle would have seen nothing until they opened a second tab.

Under System with the app never opened the badge takes the dark side — the brand
aqua, which Chrome auto-contrasts its own text against.

### The extension's ground doctrine does not change

`content/ground.ts` MEASURES the host page's painted background and the mention
underlines follow that, not the reader's theme and not `prefers-color-scheme`. A
white article stays white for a reader whose OS is dark. The card is venona's own
surface inside that page and follows the reader; the underlines are spliced into
somebody else's prose and follow the page. `ext_smoke.py` asserts the
combination: theme dark, article white, underlines unmoved.

### `--brand-wordmark` comes back, reversing its own deletion

Recorded separately because it looks like a restoration of something that was
deliberately removed. `tokens.css` deleted it with the note *"The lockup is drawn
artwork now — one image, its own colours — so there is no word for the app to
colour."* That was right about the image and wrong about the word: it has to be
white at night and near-black on paper, and the brand kit ships no lockup pairing
the aqua plate with a white wordmark — every aqua-plate variant sets the word in
`--aqua-200`, and the two that set it white whiten the plate too.

So the choice was an eleventh file maintained by hand alongside ten kit files, or
one attribute driven from the token layer. The old note's real warning — that a
floating token is "an invitation to re-colour half a lockup" — is answered by
naming all three parts of the lockup and marking two of them INVARIANT. The plate
and the monogram are artwork: the same on paper as at night, because the plate
does not change colour.

`--ink-900` arrives with it, the only value in `tokens.css` not on one of the
ramps. The monogram is `#111111`, a true neutral, and moving it onto `--grey-950`
would be re-drawing artwork to fit a palette.

*Trigger to revisit:* a third theme, or a per-surface theme. Both would break the
"absence of the attribute is System" shape, which is what makes the pre-boot
document correct with no script.
