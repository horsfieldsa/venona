/** Which way round the host page is.
 *
 * The mentions live in the page, so their legibility is a property of the
 * page's background, and nothing in the annotation pipeline knows it. The app's
 * tokens are all positions relative to `--surface-base`; `emit-styles.mjs`
 * translates the ones that can be expressed relative to inherited colour, but a
 * hue is a hue, and a green tuned for a near-black surface is a 2.8:1 hairline
 * on newsprint white.
 *
 * So measure. Not `prefers-color-scheme` -- that is the reader's OS setting,
 * and DefenseScoop is white whatever it says. The one thing that decides
 * whether a dark hue or a light one reads is the pixel actually behind the
 * word.
 */

/** Read by the `[data-venona-ground='light']` rules in `generated/mentions.css`,
 * which `extension/scripts/emit-styles.mjs` writes under the same name. */
export const GROUND = 'data-venona-ground'

/** Relative luminance at which white and black text tie for contrast. Above it
 * a page is "light" in the only sense that matters here: dark ink wins. */
const CROSSOVER = 0.1791

type Rgba = [number, number, number, number]

/** `getComputedStyle` hands back `rgb(r, g, b)` or `rgba(r, g, b, a)` in every
 * Chrome this extension supports, but it serialises a page's own `color(srgb
 * ...)` or `lab(...)` verbatim. Those parse to null and the walk continues,
 * which reads one layer further out rather than inventing a number. */
function parse(colour: string): Rgba | null {
  const inside = colour.match(/^rgba?\(([^)]+)\)$/)
  if (!inside) return null
  const parts = inside[1].split(/[,/\s]+/).filter(Boolean).map(Number)
  if (parts.length < 3 || parts.slice(0, 3).some((n) => !Number.isFinite(n))) return null
  const alpha = parts.length > 3 && Number.isFinite(parts[3]) ? parts[3] : 1
  return [parts[0], parts[1], parts[2], alpha]
}

function luminance([r, g, b]: Rgba): number {
  const channel = (c: number): number => {
    const srgb = c / 255
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** The first ancestor that actually paints something, `root` included.
 *
 * Half-transparent backgrounds are treated as not painting: compositing them
 * properly would mean walking back down with an accumulator, and a layer that
 * faint does not decide the answer anyway.
 */
export function ground(root: Element): 'light' | 'dark' {
  for (let el: Element | null = root; el; el = el.parentElement) {
    const background = parse(getComputedStyle(el).backgroundColor)
    if (background && background[3] > 0.5) {
      return luminance(background) >= CROSSOVER ? 'light' : 'dark'
    }
  }
  // Nothing opaque the whole way up, so the canvas shows through. That is white
  // unless the page opted into `color-scheme: dark`, which is not readable from
  // here -- so ask the prose instead. Light text is only ever set on a dark
  // ground, and it is the same author's decision either way.
  const text = parse(getComputedStyle(root).color)
  return text && luminance(text) > CROSSOVER ? 'dark' : 'light'
}

/** Stamp the answer where the stylesheet can see it.
 *
 * On `<html>` rather than the article: the card's shadow root is mounted at
 * `<body>` level, and a rule scoped to the article would not reach a mention
 * inside a pull-quote that sits outside it.
 */
export function stampGround(root: Element): void {
  document.documentElement.setAttribute(GROUND, ground(root))
}
