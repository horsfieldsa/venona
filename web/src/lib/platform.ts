/**
 * Platform facts the UI has to render differently.
 *
 * Kept out of any component so the keyboard registry and the chrome agree on
 * one answer -- a header that says `⌘K` while the listener wants `Ctrl` is the
 * kind of drift nobody notices until someone else uses the machine.
 */

/** `navigator.platform` is deprecated; the UA string is what is left. */
export const IS_MAC = /Mac|iPhone|iPad/.test(navigator.userAgent)

/** The modifier we bind global shortcuts to, as the user should read it. */
export const MOD_LABEL = IS_MAC ? '⌘' : 'Ctrl'

/** True when a keyboard event carries the platform's command modifier. */
export function hasMod(event: KeyboardEvent): boolean {
  return IS_MAC ? event.metaKey : event.ctrlKey
}
