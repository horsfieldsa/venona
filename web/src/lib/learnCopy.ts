/**
 * The learning surface's reader vocabulary, in one place.
 *
 * The wire speaks the schema's language — `gloss`, `rel`, `q`, predicate
 * constants like `PARTNERS_WITH` — and none of it may reach a reader: a
 * "gloss" is an acronym to the person studying it, and an FSRS "stability"
 * is how long a memory holds. Keeping every translation here means the study
 * card, the progress legend and the memory table cannot drift into three
 * dialects. Domain-blind by construction: entity TYPE names come from the
 * ontology and pass through untouched.
 */

/** Wire names (card namespaces, pseudo-types, card kinds) → reader words. */
const TYPE_LABELS: Record<string, string> = {
  def: 'term',
  gloss: 'acronym',
  glossary: 'acronym',
  rel: 'relationship',
  relation: 'relationship',
  q: 'quiz',
  question: 'quiz',
}

export function cardTypeLabel(type: string | null | undefined): string | null {
  if (!type) return null
  return TYPE_LABELS[type] ?? type
}

/** Re-exported so existing callers keep one import. `lib/predicate.ts` owns it. */
export { predicateLabel } from '@/lib/predicate'

/**
 * FSRS stability, in words. The number is "days until the memory needs a
 * refresh", which nobody should have to know to read the table — so the word
 * carries the meaning and the day count rides along as detail. The `strong`
 * threshold mirrors the API's `strong_stability_days` (21), so this column
 * and the progress page's coverage split agree on what strong means.
 */
export function memoryLabel(stabilityDays: number): { word: string; days: string } {
  const days = stabilityDays < 1 ? '<1d' : `~${Math.round(stabilityDays)}d`
  if (stabilityDays >= 21) return { word: 'strong', days }
  if (stabilityDays >= 7) return { word: 'fair', days }
  return { word: 'weak', days }
}

/**
 * The one-line cue above a card's face: what kind of recall is being asked
 * for. A generated question carries its own sentence, so it gets none.
 */
export function promptFor(card: { namespace: string; format: string }): string | null {
  if (card.namespace === 'q') return null
  if (card.namespace === 'rel') return 'How are these two connected?'
  if (card.format === 'cloze') return 'What fills the blank?'
  if (card.namespace === 'gloss') return 'What does it stand for?'
  if (card.format === 'choice') return 'Who or what does this describe?'
  return 'What is this?'
}
