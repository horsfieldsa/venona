<script setup lang="ts">
/**
 * What a name means, and how we know.
 *
 * Split out of `EntityCardBody` so the 400px card and the full page render one
 * definition rather than two that drift. Invariant 3 lives in the difference
 * between a QUOTATION and a SYNTHESIS -- `in_corpus` is written from several
 * sentences and is never shown as a quote -- and that distinction is exactly
 * the kind that survives in one component and rots in two.
 *
 * It depends on `components/ui/` and nothing else: the extension's shadow root
 * globs `brand`, `entity` and `ui` only, so a reach into `components/shared/`
 * would silently lose its styling on somebody else's page.
 *
 * WHAT SITS BELOW IT IS THE CALLER'S JOB, and `provenance` says "below" out
 * loud, so a caller that puts something else there makes this component lie.
 * Both callers put the sources there.
 */
import { computed } from 'vue'

import SectionHead from '@/components/ui/SectionHead.vue'
import type { Card } from '@/types/brief'

const props = withDefaults(defineProps<{ card: Card; compact?: boolean }>(), { compact: false })

/**
 * How we know, in the reader's words rather than the pipeline's.
 *
 * The method is the difference between a sentence someone wrote in an article
 * and a sentence Venona assembled, and the card must never blur the two --
 * `in_corpus` is a synthesis and is never shown as a quotation.
 */
const PROVENANCE: Record<string, string> = {
  in_article: 'quoted from the article below',
  in_corpus: 'written from the articles below',
  web_grounded: 'quoted from the source below',
  manual: 'entered by hand, with a source',
}

const provenance = computed(() => PROVENANCE[props.card.method ?? ''] ?? '')

/** A quotation only when there IS a quote. Tier 2 has article ids and no quote,
 *  and rendering it as a blockquote would present a synthesis as a source. */
const quote = computed(() => props.card.grounding?.quote ?? null)
</script>

<template>
  <section class="section definition flex flex-col gap-1 min-w-0">
    <SectionHead as="h4" label="definition" />
    <template v-if="card.definition">
      <!-- `font-prose`. A definition is the one thing on this card that is a
           SENTENCE rather than a reading of the corpus, and it is what the
           card exists to deliver. The rest of the card -- ids, aliases,
           scores, stamps -- stays in the chrome face around it. -->
      <p class="m-0 font-prose text-sm leading-normal tracking-tight text-pretty text-text-secondary">
        {{ card.definition }}
      </p>
      <!-- The quote is the evidence, so it is shown as one. Only when there
           is one: a written definition carries articles, not a sentence.
           `compact` suppresses it because the merge queue puts two of these
           side by side and a blockquote each stops them fitting on screen. -->
      <blockquote
        v-if="quote && quote !== card.definition && !compact"
        class="mt-1 pl-3 border-l-2 border-l-border-default font-prose text-sm text-text-secondary"
      >
        {{ quote }}
      </blockquote>
      <p v-if="provenance" class="text-2xs text-text-quiet mono">
        {{ provenance }}
      </p>
    </template>
    <!-- Honest about the gap, in the same words the acronym card has always
         used. An ungrounded definition reads exactly like a grounded one,
         which is what makes guessing dangerous. -->
    <p v-else class="m-0 font-prose text-sm leading-normal tracking-tight text-pretty text-text-tertiary">
      No sourced definition yet. Venona will not write one it cannot trace to an article.
    </p>
  </section>
</template>
