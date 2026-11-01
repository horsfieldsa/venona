<script setup lang="ts">
/**
 * One story: age, state, headline, attribution, the facts, then the judgement.
 *
 * The visual grammar carries the same separation the schema does. `summary` is
 * body text. `why_it_matters` is set apart and labelled, so a reader who wants
 * only what happened can skip it without reading a sentence to find out whether
 * it is fact or opinion. That was the complaint the format exists to answer.
 *
 * Attribution renders from `documents`, never from anything the model wrote, so
 * a claim cannot be hung on the wrong outlet.
 */
import AgeBadge from '@/components/shared/AgeBadge.vue'
import BriefBlockView from '@/components/brief/BriefBlock.vue'
import TextRuns from '@/components/brief/TextRuns.vue'
import type { BriefItem } from '@/types/brief'
import SectionHead from '@/components/ui/SectionHead.vue'
import { dayTimeLabel } from '@/lib/dates'

const props = defineProps<{
  item: BriefItem
  activeId?: string | null
  /** 1-based position in the brief, shown in the left margin as an eye anchor
   *  and used by the Top N list to scroll here. */
  ordinal?: number
}>()

const STATE_LABEL: Record<string, string> = { new: 'NEW', follow_up: 'FOLLOW-UP', ongoing: 'ONGOING' }

function publishedLabel(item: BriefItem): string | null {
  return dayTimeLabel(item.published_at) || null
}
</script>

<template>
  <!-- `data-item-id`, not `id`. Item ids contain colons
       (`brief:2026-08-06:<uuid7>:i03`, contract C4/C3), and
       `querySelector('#brief:…')` throws SyntaxError -- an anchor built the
       obvious way would break on the first click. -->
  <article
    class="item relative py-5 border-b border-b-border-subtle last:border-b-0 scroll-mt-4"
    :data-item-id="props.item.id"
  >
    <!-- Marginalia. The ordinal, the age and the state hang in the margin rail,
         right-aligned against the column edge, so the reading column itself is
         unbroken prose. There was already an ordinal out here at
         `left: -3.5rem` with 400px of empty black beyond it — a margin rail
         with one occupant. -->
    <div
      class="margin absolute left-[calc(-1*var(--rail-margin))] top-[calc(var(--space-5)+2px)] w-[calc(var(--rail-margin)-var(--space-3))] flex flex-col items-end gap-(--space-half) font-ui text-right max-lg:static max-lg:w-auto max-lg:flex-row max-lg:items-baseline max-lg:gap-2 max-lg:mb-1"
    >
      <span v-if="props.ordinal" class="ordinal text-xs text-border-strong mono">
        {{ String(props.ordinal).padStart(2, '0') }}
      </span>
      <AgeBadge :hours="props.item.age_hours" tone="dim" />
      <span
        class="state text-2xs tracking-label font-medium"
        :class="[
          `state--${props.item.state}`,
          props.item.state === 'follow_up' || props.item.state === 'new'
            ? 'text-text-secondary'
            : 'text-text-tertiary',
        ]"
      >
        {{ STATE_LABEL[props.item.state] || props.item.state }}
      </span>
    </div>

    <!-- `state_note` is the one field that answers "what changed since
         yesterday", so it keeps a line of its own; the pill lives in the
         margin. -->
    <p v-if="props.item.state_note" class="note font-ui text-xs text-text-tertiary mb-1">
      {{ props.item.state_note }}
    </p>

    <!-- Annotated when the server could annotate; the plain string otherwise.
         `headline_runs` concatenates back to `headline` exactly (C3), so the
         two branches render the same characters. -->
    <h3
      class="headline font-ui text-lg font-semibold leading-snug text-text-primary mt-0 mx-0 mb-1 text-balance"
    >
      <TextRuns
        v-if="props.item.headline_runs?.length"
        :runs="props.item.headline_runs"
        :active-id="activeId"
      />
      <template v-else>{{ props.item.headline }}</template>
    </h3>

    <p
      class="attribution font-ui text-sm text-text-tertiary mt-0 mx-0 mb-3 [&_a]:text-text-tertiary [&_a]:no-underline [&_a]:border-b [&_a]:border-b-border-default [&_a:hover]:text-text-secondary"
    >
      <a v-if="props.item.url" :href="props.item.url" target="_blank" rel="noopener noreferrer">
        {{ props.item.publisher || 'source' }}
      </a>
      <span v-else>{{ props.item.publisher || 'source' }}</span>
      <!-- `tabular-nums` on the stamp alone, not on the attribution line: the
           publisher beside it is words, and tabular figures do nothing for
           those while widening the digits that sit in a column down the page. -->
      <span v-if="publishedLabel(props.item)">
        · <span class="tabular-nums">{{ publishedLabel(props.item) }}</span>
      </span>
    </p>

    <div class="summary text-inherit">
      <BriefBlockView
        v-for="block in props.item.summary"
        :key="block.id"
        :block="block"
        :active-id="props.activeId"
      />
    </div>

    <div
      v-if="props.item.why?.length"
      class="why mt-4 pl-4 border-l border-l-border-default [&_p]:text-text-secondary"
    >
      <SectionHead class="why__label mb-1" label="Why it matters" />
      <BriefBlockView
        v-for="block in props.item.why"
        :key="block.id"
        :block="block"
        :active-id="props.activeId"
      />
    </div>
  </article>
</template>
