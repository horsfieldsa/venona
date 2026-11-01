/**
 * DELEGATED mention handling, shared by every surface that renders runs.
 *
 * A dense brief carries hundreds of mentions. Attaching listeners per mention
 * would mean hundreds of listeners and, worse, hundreds of Floating-UI
 * instances. Instead a container binds these six handlers and
 * `closest('[data-entity-id]')` finds the mention under the pointer. BriefBody
 * owns the body's; BriefView's masthead binds the same set now that the title
 * carries mentions too -- one behaviour, however many containers.
 *
 * Clicking a mention PINS its card rather than navigating. In a document
 * carrying several hundred dotted mentions, a click that went straight to the
 * graph would be a trapdoor -- one stray click while selecting text ejects the
 * reader out of the brief. "Open in graph →" inside the card is the deliberate
 * route through, and it carries the origin item with it.
 */
import { useHoverStore } from '@/stores/hover'

export function useMentionEvents() {
  const hover = useHoverStore()

  function mentionFrom(event: Event): HTMLElement | null {
    const target = event.target as HTMLElement | null
    return target?.closest<HTMLElement>('[data-entity-id]') ?? null
  }

  function onPointerOver(event: MouseEvent) {
    const element = mentionFrom(event)
    if (element?.dataset.entityId) hover.request(element, element.dataset.entityId)
  }

  function onPointerOut(event: MouseEvent) {
    if (mentionFrom(event)) hover.release()
  }

  function onFocusIn(event: FocusEvent) {
    const element = mentionFrom(event)
    if (element?.dataset.entityId) hover.request(element, element.dataset.entityId)
  }

  function onFocusOut() {
    hover.release()
  }

  function onClick(event: MouseEvent) {
    const element = mentionFrom(event)
    if (element?.dataset.entityId) hover.pin(element, element.dataset.entityId)
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    const element = mentionFrom(event)
    if (!element?.dataset.entityId) return
    event.preventDefault()
    hover.pin(element, element.dataset.entityId)
  }

  return { onPointerOver, onPointerOut, onFocusIn, onFocusOut, onClick, onKeydown }
}
