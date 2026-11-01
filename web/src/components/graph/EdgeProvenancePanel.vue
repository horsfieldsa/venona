<script setup lang="ts">
import Chip from '@/components/ui/Chip.vue'
import type { EdgeProvenance } from '@/types/graph'
import SectionHead from '@/components/ui/SectionHead.vue'
import { isoDayLabel, timeLabel } from '@/lib/dates'
import { predicateLabel } from '@/lib/learnCopy'

/**
 * Why an edge is believable.
 *
 * This is a first-class surface, not a debug view: an intelligence graph whose
 * edges cannot be audited is a rumour mill. The reason to trust
 * `PEO C3N —MANAGES→ NGC2` is being able to read the sentence that asserted it
 * and click through to the article.
 */
defineProps<{ provenance: EdgeProvenance }>()

/** In the reader's zone, ISO-shaped so the `mono` column stays aligned. An
 *  assertion date sliced off UTC beside a brief time in the reader's zone was
 *  two clocks in one panel, five lines apart. */
function shortDate(value?: string | null) {
  return isoDayLabel(value)
}

/** How each claim was made, in the reader's words. Display only, like the
 *  provenance map on the entity card; the wire value never renders raw. */
const METHOD_LABEL: Record<string, string> = {
  in_article: 'from the article',
  in_corpus: "from Venona's reading",
  web_grounded: 'from the web',
  manual: 'entered by hand',
}

function methodLabel(value?: string | null): string {
  if (!value) return ''
  return METHOD_LABEL[value] ?? value.replace(/_/g, ' ')
}
</script>

<template>
  <div class="panel flex flex-col min-h-0 h-full">
    <header class="head pb-3 border-b border-b-border-subtle">
      <div class="flex items-baseline gap-2 flex-wrap">
        <span class="entity font-semibold text-md">{{ provenance.source.name }}</span>
        <Chip tone="accent">{{ predicateLabel(provenance.predicate) }}</Chip>
        <span class="text-text-tertiary">→</span>
        <span class="entity font-semibold text-md">{{ provenance.target.name }}</span>
      </div>
      <div class="meta flex items-center gap-2 mt-2">
        <Chip :tone="provenance.single_source ? 'high' : 'ok'" dot>
          {{ provenance.source_count }}
          {{ provenance.source_count === 1 ? 'source' : 'sources' }}
        </Chip>
        <span class="text-text-tertiary text-xs">
          {{ provenance.assertion_count }}
          {{ provenance.assertion_count === 1 ? 'claim' : 'claims' }}
        </span>
      </div>
      <!-- Matches the dashed edge on the canvas: epistemic weakness is shown
           the same way in both places. -->
      <p
        v-if="provenance.single_source"
        class="warn font-prose text-xs mt-2 tracking-tight text-pretty text-signal-high"
      >
        One source only; treat as unconfirmed.
      </p>
    </header>

    <section class="assertions overflow-y-auto flex-1 min-h-0 pt-3">
      <article
        v-for="a in provenance.assertions"
        :key="a.assertion_id"
        class="assertion pb-3 mb-3 border-b border-b-border-subtle [&_blockquote]:pl-3 [&_blockquote]:border-l-2 [&_blockquote]:border-l-border-default [&_blockquote]:font-prose [&_blockquote]:text-text-secondary [&_blockquote]:mb-2"
      >
        <blockquote>{{ a.quote }}</blockquote>
        <div class="flex items-center gap-2 flex-wrap text-xs text-text-tertiary">
          <span class="mono">{{ shortDate(a.published_at) }}</span>
          <span>{{ a.publisher || 'unknown source' }}</span>
          <span>{{ methodLabel(a.method) }}</span>
          <span class="mono">confidence {{ a.confidence?.toFixed(2) }}</span>
        </div>
        <a v-if="a.url" class="text-xs" :href="a.url" target="_blank" rel="noopener noreferrer">
          {{ a.title || a.url }}
        </a>
      </article>
    </section>

    <footer v-if="provenance.briefs?.length" class="foot pt-3 border-t border-t-border-subtle">
      <SectionHead as="h4" class="mb-2" label="cited in" />
      <!-- Time as well as date: a day can hold several briefs, and without it
           two entries here read as the same one listed twice. -->
      <div v-for="b in provenance.briefs" :key="b.id" class="text-xs text-text-secondary">
        {{ b.date }}{{ b.generated_at ? ` ${timeLabel(b.generated_at)}` : '' }} ·
        {{ b.title }}
      </div>
    </footer>
  </div>
</template>
