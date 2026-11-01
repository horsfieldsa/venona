/** Generate the extension's stylesheets and its token map from the app's, at
 * build time.
 *
 * WHY GENERATED, AND NOT WRITTEN BY HAND.
 *
 * The mentions live in the HOST PAGE, not in a shadow root. DefenseScoop's
 * `:root` carries none of venona's custom properties, so a hand copy of
 * `prose.css` would emit `border-bottom-color: var(--entity-company)`, resolve
 * to nothing, and draw an invisible underline. It would also be a second
 * implementation of the mention treatment, free to drift from the first, and a
 * committed file full of hex literals -- which is exactly what
 * `scripts/lint_tokens.py` exists to refuse.
 *
 * So: read `tokens.css` and `prose.css`, resolve every `var()` down to a
 * literal, and emit into `generated/` (git-ignored, outside `src/`, invisible
 * to the linter). Adding an entity type or restyling a mention propagates on
 * the next build with nobody touching this file.
 *
 * Four outputs, because they have different problems:
 *
 *   generated/mentions.css     page-scoped, `!important` on every declaration,
 *                              because the host page's CSS is the adversary.
 *   generated/host-tokens.css  a `:host` block carrying BOTH token tiers, for
 *                              the card's shadow root. Tier 2 resolves through
 *                              tier 1 (`--entity-slot-1:
 *                              light-dark(var(--sage-700), var(--sage-500))`)
 *                              and `theme.css`'s `@theme inline` deliberately
 *                              makes utilities emit `var(--surface-base)`
 *                              rather than a copy -- so shipping tier 2 alone
 *                              leaves every utility resolving to nothing.
 *   generated/root-tokens.css  the same block under `:root`.
 *   generated/tokens.ts        the resolved values as TypeScript.
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath, URL } from 'node:url'

const WEB = new URL('../../web/src/styles/', import.meta.url)
const OUT = new URL('../generated/', import.meta.url)

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Every `--name: value` in declaration order. Later wins, matching the cascade
 * for the single `:root` these files use. */
function readTokens(css) {
  const tokens = new Map()
  for (const [, name, value] of stripComments(css).matchAll(/(--[\w-]+)\s*:\s*([^;}]+)[;}]/g)) {
    tokens.set(name, value.trim())
  }
  return tokens
}

/** Resolve `var(--a)` transitively to a literal.
 *
 * The depth cap is not defensive padding: a token file with a cycle would
 * otherwise hang the build with no message. Ten is far past the two levels
 * `tokens.css` actually uses. */
function resolve(value, tokens, depth = 0) {
  if (depth > 10) throw new Error(`token cycle resolving: ${value}`)
  return value.replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)/g, (whole, name, fallback) => {
    const hit = tokens.get(name)
    if (hit !== undefined) return resolve(hit, tokens, depth + 1)
    if (fallback !== undefined) return resolve(fallback.trim(), tokens, depth + 1)
    // A missing token renders transparent rather than erroring, which is the
    // failure mode `lint_tokens.py` check 3 exists to catch. Fail the build.
    throw new Error(`undeclared token ${name} (in "${whole}")`)
  })
}

/** Flatten every `light-dark(a, b)` in a value to one side.
 *
 * `tokens.css` carries both palettes in one function (ADR 0133), and the outputs
 * below want that three different ways -- flattened to the measured ground for
 * mentions.css, flattened to each palette for tokens.ts, left standing in the
 * `:host` and `:root` blocks -- so this is a choice each of them makes rather
 * than something the resolver does once.
 *
 * Paren-aware, because the sides are not always simple: `--scrim` resolves to
 * `light-dark(rgb(0 0 0 / 45%), rgb(0 0 0 / 68%))` and splitting on the first
 * comma lands inside the first `rgb()`. It also loops, because the mentions
 * path hands it a whole declaration body rather than one token's value.
 */
function pickSide(value, theme) {
  const side = theme === 'light' ? 0 : 1
  let out = value
  for (;;) {
    const start = out.indexOf('light-dark(')
    if (start === -1) return out
    let depth = 0
    let split = -1
    let end = -1
    for (let i = start + 'light-dark('.length; i < out.length; i++) {
      const char = out[i]
      if (char === '(') depth++
      else if (char === ')') {
        if (depth === 0) {
          end = i
          break
        }
        depth--
      } else if (char === ',' && depth === 0 && split === -1) split = i
    }
    if (split === -1 || end === -1) throw new Error(`unbalanced light-dark() in: ${value}`)
    const sides = [out.slice(start + 'light-dark('.length, split), out.slice(split + 1, end)]
    out = out.slice(0, start) + sides[side].trim() + out.slice(end + 1)
  }
}

/** `!important` on everything.
 *
 * Not belt-and-braces. A trade-press article's own stylesheet sets `span
 * { border-bottom: none }` more often than you would hope, and it loads after
 * the manifest's `css`. Specificity is not winnable against an unknown page;
 * `!important` is. */
function important(body) {
  return body
    .split(';')
    .map((decl) => decl.trim())
    .filter(Boolean)
    .map((decl) => `  ${decl} !important;`)
    .join('\n')
}

// ── translating a dark-ground design onto a page we do not own ───────
//
// Every colour in `tokens.css` is a POSITION RELATIVE TO `--surface-base`, so a
// literal is only equivalent to the token on the ground it was measured against.
// Resolving `.mention--entity { color: var(--text-primary) }` to `color:
// #eef2f5` and painting it onto DefenseScoop's white body measures 1.13:1: the
// word vanished and only its underline showed, reported as "the annotations
// don't respect light mode", which is the right diagnosis of the wrong layer.
// ADR 0100.
//
// So colour is TRANSLATED, not resolved:
//
//   text   a text token is a rung on the app's own ramp. Emit that same rung
//          relative to whatever the host inherits, by recovering the alpha that
//          composites `--text-primary` over `--surface-base` into it. The top
//          rung becomes no declaration at all -- `all: unset` already inherits,
//          and the host's own prose colour is the single colour guaranteed to
//          read against the host's own ground.
//   hue    emitted twice: the token literal, and its light rung under
//          `[data-venona-ground='light']`, which `content/ground.ts` selects by
//          MEASURING the page. Not `prefers-color-scheme` -- that is the OS
//          preference, and a white article stays white for a user in dark mode.

/** The attribute `content/ground.ts` writes on `<html>`. Both sides of this
 * name are load-bearing; `scripts/ext_smoke.py` asserts the resulting colour,
 * so a rename that only lands on one side fails there. */
const GROUND = 'data-venona-ground'

const rgb = (colour) => [1, 3, 5].map((i) => parseInt(colour.slice(i, i + 2), 16))
const hex = (channels) =>
  '#' + channels.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')

/** A token resolved to a hex literal on one theme, or a build failure.
 *
 * The theme is REQUIRED rather than defaulted. Every caller below is making a
 * claim about which ground it is painting on, and a default would let a new one
 * inherit somebody else's answer silently. */
function literal(name, theme) {
  const value = tokens.get(name)
  if (value === undefined) throw new Error(`undeclared token ${name}`)
  const out = pickSide(resolve(value, tokens), theme).trim()
  if (!/^#[0-9a-f]{6}$/i.test(out)) {
    throw new Error(`${name} is not a hex literal on the ${theme} theme: ${out}`)
  }
  return out
}

/** Where `name` sits on the ramp from `--surface-base` to `--text-primary`, as
 * the alpha that composites one into the other. `--text-secondary` comes back
 * at 0.72 and `--text-tertiary` at 0.59 (2026-09-14) -- the app's own two steps
 * of de-emphasis, recovered rather than guessed, so a re-mapped token moves the
 * extension with it. */
function rampPosition(name) {
  // The DARK ramp, deliberately. The output is a percentage of `currentColor`,
  // which is ground-independent by construction -- so the only question is which
  // theme's two steps of de-emphasis to copy, and the answer is the one the app
  // was designed against. Copying the light ramp's steps instead is a different
  // design, not a bug fix.
  const top = rgb(literal('--text-primary', 'dark'))
  const ground = rgb(literal('--surface-base', 'dark'))
  const target = rgb(literal(name, 'dark'))
  const steps = target
    .map((value, i) => (value - ground[i]) / (top[i] - ground[i]))
    .filter((step) => Number.isFinite(step))
  if (!steps.length) throw new Error(`${name} is not on the --text-primary ramp`)
  return steps.reduce((a, b) => a + b, 0) / steps.length
}

/** The light-ground hues are NOT derived here. `tokens.css` ships a light rung
 * for every hue, targeted at 4.5:1 on the light PAGE, which comes out at 5.50:1
 * on white -- stronger than the hairline doctrine asks for. Two derivations of
 * one number is exactly the drift this file exists to prevent, so there is one
 * and it lives in the token layer: ADR 0133. `--entity-company`'s light rung,
 * #4f7060, is what `scripts/ext_smoke.py` pins.
 */

/** Rewrite one mention rule's declarations for a ground we cannot see. */
function forHostPage(body) {
  return body
    .split(';')
    .map((decl) => decl.trim())
    .filter(Boolean)
    .map((decl) => {
      const property = decl.slice(0, decl.indexOf(':')).trim()
      const value = decl.slice(decl.indexOf(':') + 1).trim()
      const token = property === 'color' && value.match(/^var\(\s*(--text-[\w-]+)\s*\)$/)
      if (!token) return decl
      const rung = rampPosition(token[1])
      // The top rung is the host's own prose colour, and the way to say that is
      // to say nothing.
      if (rung > 0.99) return null
      return `color: color-mix(in srgb, currentColor ${Math.round(rung * 100)}%, transparent)`
    })
    .filter(Boolean)
    .join('; ')
}

/** Prefix EVERY selector in a comma list, not just the first -- `[x] a, b`
 * leaves `b` unqualified, which would apply a light-ground hue on a dark one. */
const underLightGround = (selector) =>
  selector
    .split(',')
    .map((one) => `[${GROUND}='light'] ${one.trim()}`)
    .join(',\n')

const tokens = readTokens(await readFile(new URL('tokens.css', WEB), 'utf8'))
const prose = stripComments(await readFile(new URL('prose.css', WEB), 'utf8'))

// ── mentions.css ────────────────────────────────────────────────────
const parsed = [...prose.matchAll(/((?:^|\s)\.mention[^{}]*?)\{([^{}]*)\}/gm)]

if (parsed.length < 8) {
  // A parser that silently matches two of `prose.css`'s `.mention` rules ships a
  // page with no underlines and no error, which is the class of failure
  // `make ui-smoke` exists for. Counted on what was PARSED, not what is emitted:
  // a rule whose every declaration translates away -- `.mention--entity` is
  // exactly that -- is a correct outcome, not a parse failure.
  throw new Error(`expected >=8 .mention rules in prose.css, parsed ${parsed.length}`)
}

const rules = []
const lightRules = []
for (const [, selector, body] of parsed) {
  const scoped = selector
    .trim()
    .replace(/\.mention/g, '.venona-mention')
    .replace(/\.is-active/g, '.venona-active')

  // FLATTENED, both times. `light-dark()` must not survive into this file: these
  // rules run in the HOST page, whose `color-scheme` is the article author's and
  // has nothing to do with the ground, which is MEASURED (`content/ground.ts`).
  const resolved = resolve(forHostPage(body), tokens)
  const declarations = pickSide(resolved, 'dark')
  if (!declarations) continue
  rules.push(`${scoped} {\n${important(declarations)}\n}`)

  // Only the declarations that actually MOVE between the two sides are repeated,
  // so the generated file states the difference rather than burying it in a
  // second copy of the rule. Everything that survives `forHostPage` as
  // `currentColor` is identical on both and drops out here for free.
  const lightSide = pickSide(resolved, 'light').split(';').map((decl) => decl.trim())
  const light = declarations
    .split(';')
    .map((decl) => decl.trim())
    .filter(Boolean)
    .map((decl, i) => [decl, lightSide[i]])
    .filter(([dark, lit]) => dark !== lit)
    .map(([, lit]) => lit)
  if (light.length) {
    lightRules.push(`${underLightGround(scoped)} {\n${important(light.join('; '))}\n}`)
  }
}

const header = `/* GENERATED by extension/scripts/emit-styles.mjs -- do not edit.
   Source: web/src/styles/{tokens,prose}.css */\n\n`

await mkdir(fileURLToPath(OUT), { recursive: true })
await writeFile(
  new URL('mentions.css', OUT),
  header +
    `.venona-mention {\n  all: unset !important;\n}\n\n` +
    rules.join('\n\n') +
    `\n\n/* Light ground. Selected by measurement, not by \`prefers-color-scheme\`:\n` +
    `   the page's background is the ground, and a white article stays white for\n` +
    `   a reader whose OS is in dark mode. Written by content/ground.ts. */\n\n` +
    lightRules.join('\n\n') +
    '\n',
)

// ── host-tokens.css ─────────────────────────────────────────────────
//
// `light-dark()` SURVIVES here, unlike in mentions.css, and the difference is
// the ground. The card is a shadow root the extension owns end to end, so it can
// declare its own `color-scheme` and let the browser pick -- which is what makes
// it theme for free rather than needing a second generated palette.
//
// `color-scheme` has to be declared explicitly and this is not optional:
// `card.css` opens with `:host { all: initial }`, and `color-scheme`'s initial
// value is `normal`, under which `light-dark()` returns the LIGHT side. Without
// this the card would render light on every page for every reader, silently.
const declarations = [...tokens.entries()]
  .map(([name, value]) => `  ${name}: ${resolve(value, tokens)};`)
  .join('\n')

// SYSTEM is all this block declares, and the two explicit choices are set
// INLINE by `shared/theme.ts::applyTheme`. The card's host element carries
// `style="all: initial; position: fixed; ..."` in the OUTER tree, so that a host
// page's `div { margin: … }` cannot move a card anchored to it -- and an inline
// declaration outranks every author rule, `:host([data-venona-theme='dark'])`
// included, so an attribute-driven override here would be written, matched, and
// lost. One mechanism, not two that disagree.
await writeFile(
  new URL('host-tokens.css', OUT),
  `${header}:host {\n  color-scheme: light dark;\n${declarations}\n}\n`,
)

// The same tokens under `:root`, for the OPTIONS page. It renders on a blank
// tab with no shadow root, so `:host` matches nothing there.
await writeFile(
  new URL('root-tokens.css', OUT),
  `${header}:root {\n  color-scheme: light dark;\n${declarations}\n}\n`,
)

console.log(`styles -> generated/mentions.css (${rules.length} rules, +${lightRules.length} light-ground)`)
console.log(`styles -> generated/host-tokens.css (${tokens.size} tokens)`)
// ── tokens.ts ───────────────────────────────────────────────────────
//
// JSON.stringify for both halves: font stacks carry single quotes
// (`'JetBrains Mono Variable', ui-monospace, ...`), which end the string
// literal early and emit a file that does not parse.
// `.ts`, not `.js`: an untyped module import is an implicit `any` and
// `vue-tsc` refuses it, so a `.js` file would need a hand-written `.d.ts`
// beside it -- two things to keep in step rather than one.
//
// THREE MAPS, and the split is the type system doing work.
//
// `TOKENS` carries only the tokens that are IDENTICAL on both themes -- the
// type scale, the geometry, the font stacks. Anything theme-dependent is
// absent, so a consumer that reaches for `--surface-base` through it is a
// compile error rather than a value that is right half the time.
// `shared/fonts.ts` is the whole reason: it asks for `--font-ui` and
// `--font-prose`, which are invariant, and it must go on being able to.
//
// `TOKENS_LIGHT` / `TOKENS_DARK` are complete, for `background.ts`, which paints
// the toolbar badge. That is the one surface in the extension that cannot take a
// custom property -- `setBadgeBackgroundColor` wants a literal -- and it runs in
// a service worker, which has no DOM and no `matchMedia`, so it cannot ask the
// browser to pick for it.
//
// No `Record<string, string>` on any of them. With the annotation a stale key
// typechecks and returns `undefined` at runtime -- `fonts.ts` does
// `TOKENS[token] ?? ''`, and a silent miss there renders the card in the
// platform's mono, which is the exact failure that file exists to prevent.
// `as const` makes the same mistake a `vue-tsc` error.
const invariant = [...tokens.entries()].filter(
  ([, value]) => !resolve(value, tokens).includes('light-dark('),
)
const varying = [...tokens.entries()].filter(([, value]) =>
  resolve(value, tokens).includes('light-dark('),
)
const entries = (pairs, theme) =>
  pairs
    .map(
      ([name, value]) =>
        `  ${JSON.stringify(name)}: ${JSON.stringify(pickSide(resolve(value, tokens), theme))},`,
    )
    .join('\n')

await writeFile(
  new URL('tokens.ts', OUT),
  header +
    `/** Tokens that are the same on both themes. A theme-dependent name is\n` +
    ` *  deliberately NOT here: reaching for one through this map is a type error. */\n` +
    `export const TOKENS = {\n${entries(invariant, 'dark')}\n} as const\n\n` +
    `/** Every token, on the light palette. */\n` +
    `export const TOKENS_LIGHT = {\n${entries([...tokens.entries()], 'light')}\n} as const\n\n` +
    `/** Every token, on the dark palette. */\n` +
    `export const TOKENS_DARK = {\n${entries([...tokens.entries()], 'dark')}\n} as const\n`,
)

console.log(`styles -> generated/root-tokens.css (${tokens.size} tokens)`)
console.log(
  `styles -> generated/tokens.ts (${invariant.length} invariant, ${varying.length} per theme)`,
)
