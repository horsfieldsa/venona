<script setup lang="ts">
/**
 * The pages a lookup read, and why none of them counted.
 *
 * On a run that landed a definition this is a footnote. On one that landed
 * nothing it is the whole content of the page: it is the only thing that tells
 * a reader whether to try again now, look somewhere else, or accept that the
 * term is genuinely undocumented. That is a different question per reason, so
 * the reasons are stated rather than serialised.
 *
 * `reason` is the wire vocabulary from `pipeline/web_grounding.py::REASON_STAGE`
 * and every value of it is a database word. A reader has never met
 * `no_extractable_text`.
 */
import { computed } from 'vue'

import { asText } from '@/lib/text'

const props = defineProps<{ rejected: unknown[] }>()

/**
 * Each wire reason in the reader's words, and which part of the work it
 * happened in.
 *
 * `stage` matters as much as the sentence: a wall of `could not be reached` is
 * a network to fix, and a wall of `does not define it` is a term the web has
 * genuinely not written down. Grouping them is what makes that visible at a
 * glance instead of by counting lines.
 */
const SAID: Record<string, { text: string; stage: string }> = {
  subject_too_long: { text: 'the term is too long to look up', stage: 'searching' },
  subject_empty_after_scrub: { text: 'the term is empty', stage: 'searching' },
  search_failed: { text: 'the search failed', stage: 'searching' },
  search_failed_twice: { text: 'the search failed twice', stage: 'searching' },
  no_candidates: { text: 'nothing was suggested to read', stage: 'searching' },
  retry_skipped: { text: 'the budget for this run ran out', stage: 'searching' },
  retry_search_failed: { text: 'the second search failed', stage: 'searching' },
  retry_proposed_nothing_new: { text: 'nothing further was suggested', stage: 'searching' },
  fetch_error: { text: 'could not be reached', stage: 'reaching the page' },
  fetch_status: { text: 'refused the request', stage: 'reaching the page' },
  no_extractable_text: { text: 'published nothing readable', stage: 'reaching the page' },
  read_failed: { text: 'could not be read', stage: 'reading the page' },
  not_defined_in_page: { text: 'does not define it', stage: 'reading the page' },
  not_described_in_page: { text: 'does not describe it', stage: 'reading the page' },
  incomplete: { text: 'says too little to be sure', stage: 'reading the page' },
  quote_not_in_page: { text: 'the quote was not on the page', stage: 'checking the quote' },
  expansion_not_in_quote: { text: 'the quote does not contain it', stage: 'checking the quote' },
  acronym_not_in_quote: { text: 'the quote does not contain it', stage: 'checking the quote' },
  entity_not_in_quote: { text: 'the quote does not name it', stage: 'checking the quote' },
  initials_mismatch: { text: 'the initials do not match', stage: 'checking the quote' },
  off_domain: { text: 'our own articles disagree', stage: 'checking the quote' },
  adjudicated: { text: 'accepted on a second look', stage: 'checking the quote' },
}

interface Candidate {
  host: string
  url: string
  said: string
  stage: string
  detail: string
}

/** `https://www.centcom.mil/ABOUT-US-CENTCOM/` reads as its host; the full
 *  address is the link and the title. */
function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

const candidates = computed<Candidate[]>(() =>
  props.rejected.flatMap((entry) => {
    if (typeof entry !== 'object' || entry === null) return []
    const row = entry as Record<string, unknown>
    // `asText`, never `String(...)`: `rejected` crosses the wire as opaque JSON
    // and a nested object under `reason` would render as `[object Object]` at a
    // reader. See `lib/text.ts`.
    const reason = asText(row.reason)
    const known = SAID[reason]
    const url = typeof row.url === 'string' ? row.url : ''
    return [
      {
        host: url ? hostOf(url) : 'no address',
        url,
        // An unmapped reason opens its underscores rather than vanishing: a new
        // gate should read as slightly raw, not silently drop its rows.
        said: known?.text ?? reason.replace(/_/g, ' '),
        stage: known?.stage ?? 'checking',
        // An HTTP status is the one detail worth carrying: 403 and 404 mean
        // different things to whoever decides what to do next.
        detail: typeof row.detail === 'number' ? String(row.detail) : '',
      },
    ]
  }),
)

/** Grouped in the order the work happens, so the shape of the failure is the
 *  shape of the list. */
const ORDER = ['searching', 'reaching the page', 'reading the page', 'checking the quote']

const groups = computed(() => {
  const byStage = new Map<string, Candidate[]>()
  for (const candidate of candidates.value) {
    const bucket = byStage.get(candidate.stage)
    if (bucket) bucket.push(candidate)
    else byStage.set(candidate.stage, [candidate])
  }
  return [...byStage.entries()]
    .sort((a, b) => ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]))
    .map(([stage, rows]) => ({ stage, rows }))
})
</script>

<template>
  <div class="candidates flex flex-col gap-4">
    <div v-for="group in groups" :key="group.stage">
      <p class="text-2xs tracking-label uppercase text-text-tertiary">
        {{ group.stage }} · {{ group.rows.length }}
      </p>
      <p
        v-for="(row, i) in group.rows"
        :key="i"
        class="flex flex-wrap items-baseline gap-2 py-(--space-half) text-sm"
      >
        <a
          v-if="row.url"
          class="mono text-xs text-text-secondary underline"
          :href="row.url"
          :title="row.url"
          target="_blank"
          rel="noreferrer noopener"
          >{{ row.host }}</a
        >
        <span v-else class="mono text-xs text-text-tertiary">{{ row.host }}</span>
        <span class="text-text-tertiary text-xs">{{ row.said }}</span>
        <span v-if="row.detail" class="mono text-2xs text-text-tertiary">{{ row.detail }}</span>
      </p>
    </div>
  </div>
</template>
