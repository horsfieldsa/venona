/**
 * Relevance, in words. The wire speaks the rubric's own language and a reader
 * has never heard it, so the word is what renders and the wire value stays on
 * the wire. Shared, so the brief and the tailor cannot drift into two
 * vocabularies for one scale.
 */

/** A cluster tier on the wire → the same three words. The wire speaks the
 *  rubric's language (`must_read`), which no reader has heard. */
export function tierWord(tier: string): string {
  if (tier === 'must_read') return 'high'
  if (tier === 'worth_knowing') return 'medium'
  return 'low'
}

/** Axis id → reader words: `product_overlap` renders as `product overlap`.
 *  Display only — never compare on it. Domain-blind: the words come from the
 *  instance's own rubric ids. */
export function axisLabel(id: string): string {
  return id.replaceAll('_', ' ')
}
