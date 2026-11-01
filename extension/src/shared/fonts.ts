/** Self-hosted typefaces, installed into a DOCUMENT.
 *
 * The extension named `'JetBrains Mono Variable'` and `'Inter Variable'`
 * everywhere -- `card.css`'s `:host`, every `font-ui`/`font-mono`/`font-prose`
 * utility the card carries, and the options page -- and shipped neither. So
 * both fell through to `ui-monospace` and `ui-sans-serif`, and the extension has
 * never once rendered in the project's typefaces. That is the same failure
 * `web/src/main.ts` documents for `Inter var` and `Charter`, repeated on the
 * other side of the repo, and it is invisible: a fallback mono looks like a
 * deliberate choice rather than like a missing file.
 *
 * WHY THIS IS JAVASCRIPT AND NOT A STYLESHEET
 *
 * `@font-face` is document-scoped. A rule inside a shadow root is parsed and
 * then ignored -- Chrome resolves font families against the document's font
 * set, never the shadow tree's. The card lives in a shadow root on somebody
 * else's page, so the faces have to be installed into the HOST document even
 * though nothing else the extension draws is allowed to touch it. This
 * `<style>` is the only thing the extension adds outside its own shadow root,
 * which is why it declares faces and nothing else: no selector here can match a
 * host page element.
 *
 * The URLs must be absolute `chrome-extension://` -- a relative one resolves
 * against the host page's origin and 404s on every real site while the local
 * fixture happens to work -- so they are built with `chrome.runtime.getURL` at
 * runtime rather than written into CSS. `finish.mjs` copies the files to
 * `dist/fonts/` and `manifest.json` lists them in `web_accessible_resources`;
 * all three have to agree, and the assertion in `finish.mjs` is what checks it.
 *
 * `web_accessible_resources` uses `<all_urls>` rather than the host_permissions
 * list, because the card mounts on whatever site the user granted at runtime
 * and the manifest is static. It exposes four static font files, which reveals
 * nothing a host page could not already learn: the extension id is readable
 * from the page the moment the card is in the DOM.
 */

import { TOKENS } from '../../generated/tokens'

/** Latin and Latin-Extended only.
 *
 * The app takes every subset because it is served from its own origin and the
 * `unicode-range` split means a latin page fetches one file anyway. Here each
 * subset is a file copied into the bundle and listed in the manifest, and the
 * corpus is defence trade press in English -- so the six Cyrillic/Greek/
 * Vietnamese files per family would be ~120 kB of bundle that never loads.
 * Anything outside these ranges falls back to the platform face, which is the
 * correct behaviour rather than a defect.
 *
 * Ranges copied verbatim from the `@fontsource-variable` `index.css` files;
 * both families ship identical ones. The weight ranges are the real axes:
 * JetBrains Mono is 100-800, Inter 100-900, and 400/500/600 all land inside
 * both, which is the whole reason these are variable builds.
 */
const LATIN =
  'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,' +
  'U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD'

const LATIN_EXT =
  'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,' +
  'U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF'

/** The family a token's stack asks for FIRST, unquoted.
 *
 * Derived, not written down. An `@font-face` has to name a family, and a name
 * written here would be a second copy of what tokens.css already says -- which
 * is precisely how `Inter var` and `Charter` survived: the stack said one thing
 * and what was actually bundled said another, and nothing compared them. This
 * reads the same generated map `background.ts` uses for the badge colours, so
 * renaming a family in tokens.css renames it here, and `lint_tokens.py` can go
 * on refusing a typeface named anywhere outside the token layer.
 *
 * The stack's later entries are the platform fallbacks and are deliberately
 * skipped: this declares the face the extension SHIPS, and the fallbacks are
 * what the browser is already holding.
 */
function primaryFamily(token: '--font-ui' | '--font-prose'): string {
  const stack = TOKENS[token] ?? ''
  return (stack.split(',')[0] ?? '').trim().replace(/^['"]|['"]$/g, '')
}

interface Face {
  family: string
  /** Filename under `dist/fonts/`. Kept as the upstream name so the copy in
   *  `finish.mjs` is a straight `basename`, with nothing to keep in step. */
  file: string
  weight: string
  range: string
}

/** `--font-ui` and `--font-mono` resolve to the same family by design -- the
 *  chrome is fixed pitch, so there is one face for it, not two -- which is why
 *  only two tokens are read here for three utilities. */
export const FACES: readonly Face[] = [
  { family: primaryFamily('--font-ui'), file: 'jetbrains-mono-latin-wght-normal.woff2', weight: '100 800', range: LATIN },
  { family: primaryFamily('--font-ui'), file: 'jetbrains-mono-latin-ext-wght-normal.woff2', weight: '100 800', range: LATIN_EXT },
  { family: primaryFamily('--font-prose'), file: 'inter-latin-wght-normal.woff2', weight: '100 900', range: LATIN },
  { family: primaryFamily('--font-prose'), file: 'inter-latin-ext-wght-normal.woff2', weight: '100 900', range: LATIN_EXT },
]

/** The id the `<style>` carries, so a second call is a no-op rather than a
 *  second copy. The content script mounts once, but an SPA that swaps the
 *  document out from under us would otherwise accumulate these. */
const STYLE_ID = 'venona-fonts'

function faceRule(face: Face): string {
  return [
    '@font-face{',
    `font-family:'${face.family}';`, /* token-lint: ok -- generates an @font-face; the name comes from the token */
    'font-style:normal;',
    // `swap`, not `block`. The card opens on hover and has ~100ms to be useful;
    // a blocked repaint waiting on a font is a card that flashes empty. The
    // fallback stack in `--font-ui` is metrically close enough that the swap
    // reads as a weight change rather than as a reflow.
    'font-display:swap;',
    `font-weight:${face.weight};`,
    `src:url('${chrome.runtime.getURL(`fonts/${face.file}`)}') format('woff2-variations');`,
    `unicode-range:${face.range};`,
    '}',
  ].join('')
}

/**
 * Install the faces into `document`. Idempotent.
 *
 * Called from BOTH extension surfaces: `content/card.ts` before the shadow root
 * mounts, and `options/options.ts` at startup. The options page is an ordinary
 * extension document and could have used a plain stylesheet, but then the same
 * four faces would be declared in two files with two different URL schemes --
 * and the one that silently stops matching is exactly the kind of drift this
 * project keeps paying for. One source, one mechanism.
 */
export function installFonts(): void {
  if (document.getElementById(STYLE_ID)) return

  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = FACES.map(faceRule).join('')

  // `head ?? documentElement`: a content script at `document_start` can run
  // before `<head>` is parsed, and appending to `documentElement` is valid --
  // the browser hoists it. `body` would be wrong for the same reason it is
  // wrong for the card host: sites replace it.
  ;(document.head ?? document.documentElement).append(style)
}
