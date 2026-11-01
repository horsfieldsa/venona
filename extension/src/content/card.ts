/** Mount the hover card into a shadow root on the host page.
 *
 * A shadow root, not a div, because the card carries the app's entire utility
 * layer and a trade-press page carries its own -- and `.text-sm` meaning two
 * different things in one document is not a bug anyone would enjoy finding.
 * Isolation goes both ways: nothing the page declares reaches in either.
 *
 * The host hangs off `document.documentElement` rather than `body`, because
 * sites replace `body` and would take the card with it.
 */

import { createApp } from 'vue'

import HoverCard from './HoverCard.vue'
import css from './card.css?inline'
import { fetchOne } from './cards'
import * as hover from './hover'
import { installFonts } from '../shared/fonts'
import { applyTheme, watchTheme } from '../shared/theme'

let mounted = false
/** Module-scoped so the click listener can ask whether an event passed through
 *  it. See the `composedPath` check in `wireHover`. */
let host: HTMLElement | null = null

export function mountCard(): void {
  if (mounted) return
  mounted = true

  // BEFORE the shadow root, and into the host document rather than into it.
  // `@font-face` is document-scoped: declared inside a shadow root it is parsed
  // and ignored, so the card's `font-ui`/`font-prose` utilities would keep
  // resolving to the platform fallbacks. See `shared/fonts.ts`.
  installFonts()

  host = document.createElement('div')
  host.id = 'venona-card-host'
  // `all: initial` first, then the positioning -- a host page rule matching
  // `div` would otherwise give this a margin, and a zero-size fixed box with a
  // margin moves the card. Zero-size because the card inside is itself fixed;
  // the host is only an anchor for the shadow root.
  //
  // `color-scheme` IS PART OF THAT STRING, and leaving it out renders the card
  // in the light palette on every page for every reader. `all: initial` resets
  // `color-scheme` to `normal`, and under `normal` `light-dark()` returns the
  // LIGHT side -- so every token in the shadow root would be present, correct,
  // and resolved to the wrong half. It has to be here rather than in a `:host`
  // rule because this declaration is INLINE, and inline outranks every author
  // rule including the ones inside the shadow root.
  //
  // `light dark` is System, which is the right thing to paint before the stored
  // preference has been read: it agrees with the reader's OS, so there is
  // nothing to flash away from.
  host.setAttribute(
    'style',
    'all: initial; color-scheme: light dark; position: fixed; top: 0; left: 0; ' +
      'width: 0; height: 0; z-index: 2147483000;',
  )
  document.documentElement.append(host)

  // The reader's explicit choice, if they made one, and for as long as the page
  // lives. `watchTheme` schedules its own first read -- nothing here awaits,
  // because the line above already renders System correctly.
  watchTheme((choice) => {
    if (host) applyTheme(host, choice)
  })

  // Closed, so page script cannot reach the card through
  // `host.shadowRoot`. The card carries a button that spends model budget, and an
  // open root lets a script on a granted origin drive a synthetic click on it.
  // Nothing here reads `host.shadowRoot`; internal code holds `shadow` directly.
  const shadow = host.attachShadow({ mode: 'closed' })
  const style = document.createElement('style')
  style.textContent = css
  shadow.append(style)

  const slot = document.createElement('div')
  shadow.append(slot)
  createApp(HoverCard).mount(slot)
}

/** Wire the delegated listeners. Called once per page.
 *
 * On `document`, not on the article root: an SPA can replace the article root
 * wholesale, and listeners attached to it would go with it. `closest()` costs
 * nothing and works from anywhere in the tree.
 */
export function wireHover(): void {
  hover.setPrefetch((id) => void fetchOne(id))

  // A tap fires `mouseover` once and then never clears it, so hover intent on a
  // touch device leaves a card stuck open with no way to dismiss it. Tap-to-pin
  // is the whole interaction there.
  const coarse = window.matchMedia('(hover: none)').matches

  const mentionFrom = (event: Event): HTMLElement | null => {
    const target = event.target
    if (!(target instanceof Element)) return null
    return target.closest<HTMLElement>('[data-venona-ref]')
  }
  const refOf = (element: HTMLElement | null): string | null =>
    element?.getAttribute('data-venona-ref') ?? null

  if (!coarse) {
    document.addEventListener('mouseover', (event) => {
      const element = mentionFrom(event)
      const ref = refOf(element)
      if (element && ref) {
        mountCard()
        hover.request(element, ref)
      }
    })
    document.addEventListener('mouseout', (event) => {
      if (mentionFrom(event)) hover.release()
    })
    document.addEventListener('focusin', (event) => {
      const element = mentionFrom(event)
      const ref = refOf(element)
      if (element && ref) {
        mountCard()
        hover.request(element, ref)
      }
    })
    document.addEventListener('focusout', (event) => {
      if (mentionFrom(event)) hover.release()
    })
  }

  document.addEventListener('click', (event) => {
    const element = mentionFrom(event)
    const ref = refOf(element)
    if (element && ref) {
      event.preventDefault()
      mountCard()
      // A second tap on the pinned mention closes it; on a coarse pointer that
      // is the only way back.
      if (hover.pinned.value && hover.activeId.value === ref) hover.dismiss()
      else hover.pin(element, ref)
      return
    }
    // A click anywhere else dismisses a pinned card -- but a click INSIDE the
    // card is not "anywhere else". `click` is composed, so it crosses the shadow
    // boundary and arrives here retargeted to the host, where `closest` finds no
    // mention and the dismiss below fires. Clicking "Look this up" therefore
    // started a paid tier-3 run and the card vanished on the same click: no
    // spinner, no result, no error, money spent. On a coarse pointer that is the
    // only interaction path there is.
    //
    // `composedPath` is the retargeting-proof question -- it lists the real
    // nodes the event travelled through, host included.
    if (host && event.composedPath().includes(host)) return
    if (hover.pinned.value) hover.dismiss()
  })

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    const element = mentionFrom(event)
    const ref = refOf(element)
    if (!element || !ref) return
    event.preventDefault()
    mountCard()
    hover.pin(element, ref)
  })
}
