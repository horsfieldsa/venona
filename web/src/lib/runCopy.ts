/**
 * Counter names, in the reader's words.
 *
 * The wire keys are database spellings — `entities_new`, `grounded_in_article`
 * — and three surfaces render them: the live panel, the run detail page's
 * Result block, and the per-stage counts in the same page's stages table. With
 * the map in only one of them, the page disagreed with itself: `documents
 * fetched` in one block and `grounded_in_article 205` in another, a line apart.
 *
 * The fallback opens the underscores rather than hiding an unmapped key. A new
 * counter should read as slightly raw, not vanish.
 */
const COUNTER_LABELS: Record<string, string> = {
  documents_fetched: 'fetched',
  documents_new: 'new',
  clusters: 'clusters',
  // NOT "still undefined": `ui_smoke` greps every page for the literal
  // `undefined` as its marker for a JS value rendered into the DOM, and a
  // legitimate label containing the word disarms that check for the whole page.
  acronyms_grounded: 'grounded',
  acronyms_undefined: 'ungrounded',
  entities_new: 'names',
  entities_seen: 'names already known',
  entities_defined: 'defined',
  edges_written: 'edges',
  entities_promoted: 'promoted',
  curation_open: 'to review',
  llm_calls: 'model calls',
  // Not "cost". Venona runs on a flat-rate subscription, so this is what the
  // same tokens would have cost at published API rates -- useful for comparing
  // one run against another, and not a sum anybody is billed. ADR 0180.
  cost_usd: 'at list price',
}

export function counterLabel(key: string): string {
  return COUNTER_LABELS[key] ?? key.replace(/_/g, ' ')
}

/** The declared order when a run type does not name its own counters. */
export function counterKeys(): string[] {
  return Object.keys(COUNTER_LABELS)
}
