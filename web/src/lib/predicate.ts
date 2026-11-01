/**
 * A predicate constant as a reader says it.
 *
 * `PART_OF` is the wire's word. "part of" is the reader's, and it is the only
 * one that may reach a surface -- the schema's vocabulary is banned from
 * user-facing copy, and a relationship name is the most visible schema word
 * there is.
 *
 * DISPLAY ONLY. Never compare on the result and never send it back: verdicts,
 * filters and the edge id all carry the raw constant, and the graph's `?preds=`
 * key is matched against it.
 *
 * `lib/curation.ts` and `lib/learnCopy.ts` both import it: three spellings of
 * one rule is how the merge queue and the study card come to disagree about
 * what an edge is called.
 */
export function predicateLabel(value: string): string {
  return /^[A-Z_]+$/.test(value) ? value.toLowerCase().replaceAll('_', ' ') : value
}
