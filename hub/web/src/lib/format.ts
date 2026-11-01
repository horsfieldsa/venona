/** Rendering helpers shared by the views. */

/** `2026-08-21 15:30` from an ISO stamp, and an em dash from nothing.
 *
 * A standalone dash is typography rather than prose, which is the one place the
 * copy rules allow the character: it stands for a value that does not exist,
 * and "none" in a table cell reads as a value that does. */
export function stamp(value: string | null | undefined): string {
  return value ? value.slice(0, 16).replace('T', ' ') : '—'
}

/** The plural of a count, where the noun is regular. Written out rather than
 *  reached for from a library: two call sites, one rule. */
export function plural(count: number, noun: string): string {
  return count === 1 ? noun.replace(/s$/, '') : noun
}
