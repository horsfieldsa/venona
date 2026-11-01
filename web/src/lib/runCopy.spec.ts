/**
 * One map, three surfaces.
 *
 * The live panel, the Result block and the per-stage counts all render the same
 * counters, and while each carried its own spelling the page contradicted
 * itself a line apart. A spec on the map is what keeps a fourth surface from
 * open-coding a fifth spelling.
 */
import { describe, expect, it } from 'vitest'

import { counterKeys, counterLabel } from './runCopy'

describe('counterLabel', () => {
  it('renders a wire key in the reader\'s words', () => {
    expect(counterLabel('entities_new')).toBe('names')
    expect(counterLabel('llm_calls')).toBe('model calls')
    expect(counterLabel('curation_open')).toBe('to review')
  })

  it('opens the underscores on a key it does not know', () => {
    // Slightly raw beats absent: a counter a new stage invents should still
    // appear, and look like something nobody has named yet.
    expect(counterLabel('grounded_in_article')).toBe('grounded in article')
  })

  it('never labels a counter with the word ui_smoke greps for', () => {
    // `ui_smoke` treats the literal `undefined` anywhere on a page as a JS value
    // rendered into the DOM. A label containing it disarms that check for the
    // whole page, which is why `acronyms_undefined` reads `ungrounded`.
    for (const key of counterKeys()) {
      expect(counterLabel(key)).not.toContain('undefined')
    }
  })

  it('offers a stable default order', () => {
    expect(counterKeys()[0]).toBe('documents_fetched')
    expect(counterKeys()).toContain('cost_usd')
  })
})
