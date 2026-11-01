<script setup lang="ts">
/**
 * A row of labelled values, of mixed kinds.
 *
 * Six copies of `<dl class="facts flex flex-wrap gap-5">` were written by hand
 * across the run detail page, the run metrics page and the account page, in two
 * incompatible treatments: value-large over label-small, and label-small beside
 * value-mono. Both are here, chosen by `emphasis`, so a page picks a treatment
 * rather than a class string.
 *
 * A NUMBER goes through `StatBlock`, which is the app's central typographic
 * motif and already owns tabular figures and label casing. Everything else is
 * rendered here, because a link, an address and a flag are not figures and
 * setting them at `--text-lg` in mono would say they were.
 */
import StatBlock from '@/components/shared/StatBlock.vue'
import { safeHref } from '@/lib/safeHref'

export type FactItem = {
  key: string
  label: string
  shape: string
  value?: unknown
  /** What to render instead of `value`. A flag's own words, a person's name. */
  text?: string | null
  href?: string | null
}

withDefaults(
  defineProps<{
    items: FactItem[]
    /** `figure` leads with the number (the counter row); `label` leads with
     *  what the thing is (a result panel, where the values are mostly words). */
    emphasis?: 'figure' | 'label'
  }>(),
  { emphasis: 'label' },
)

function shown(item: FactItem): string {
  if (item.text) return item.text
  const value = item.value
  return typeof value === 'number' || typeof value === 'string' ? String(value) : ''
}
</script>

<template>
  <dl class="facts flex flex-wrap gap-5">
    <template v-for="item in items" :key="item.key">
      <StatBlock
        v-if="emphasis === 'figure' || item.shape === 'count'"
        :value="shown(item)"
        :label="item.label"
      />
      <div v-else class="fact min-w-0">
        <dt class="text-2xs tracking-label uppercase text-text-tertiary">{{ item.label }}</dt>
        <!-- `break-all` on the value and not the label: a grounding URL is the
             one thing here with no spaces to wrap at, and it is the whole audit
             trail behind a definition (invariant 3), so it is never truncated. -->
        <dd class="mono text-sm break-all">
          <RouterLink v-if="item.shape === 'link' && item.href" :to="item.href">
            {{ shown(item) }}
          </RouterLink>
          <a
            v-else-if="item.shape === 'url'"
            :href="safeHref(shown(item))"
            target="_blank"
            rel="noreferrer noopener"
            >{{ shown(item) }}</a
          >
          <span v-else>{{ shown(item) }}</span>
        </dd>
      </div>
    </template>
  </dl>
</template>
