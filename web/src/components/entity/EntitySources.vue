<script setup lang="ts">
/**
 * Every article this name turned up in, newest first.
 *
 * The card carries four and stops; here the list is the whole run, which is
 * what makes the page worth opening for a name you already know.
 *
 * "Load more" and no scroll sentinel, for the reason the connections list
 * above it gives: neither is the last thing on the page.
 */
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { dayLabel } from '@/lib/dates'
import { safeHref } from '@/lib/safeHref'
import type { Signal } from '@/types/brief'

defineProps<{
  items: Signal[]
  total: number
  loading: boolean
  error: string | null
  canLoadMore: boolean
}>()
defineEmits<{ (e: 'more'): void }>()
</script>

<template>
  <section class="flex flex-col gap-2 min-w-0">
    <SectionHead
      as="h2"
      :label="total ? `where it appeared · ${total}` : 'where it appeared'"
      rule
    />

    <StatusNote v-if="error" kind="error">{{ error }}</StatusNote>

    <EmptyState v-else-if="!items.length && !loading" title="No articles yet.">
      This name is known from how this domain was set up rather than from the press. It will
      collect articles as Venona reads.
    </EmptyState>

    <ul v-else class="flex flex-col m-0 p-0 list-none">
      <li
        v-for="signal in items"
        :key="signal.article_id"
        class="flex flex-col gap-(--space-half) border-b border-border-subtle py-2"
      >
        <!-- `component :is` so an article with no recorded URL renders as text
             rather than as a link that goes nowhere. -->
        <component
          :is="signal.url ? 'a' : 'span'"
          v-bind="signal.url ? { href: safeHref(signal.url), target: '_blank', rel: 'noopener noreferrer' } : {}"
          class="font-ui text-sm text-text-primary"
        >
          {{ signal.title }}
        </component>
        <span class="font-ui text-2xs text-text-quiet">
          {{ dayLabel(signal.published_at) }} · {{ signal.publisher || 'unattributed' }}
        </span>
      </li>
    </ul>

    <div v-if="canLoadMore || loading" class="flex items-center gap-2 self-start">
      <Button v-if="canLoadMore" variant="outline" label="Load more" @click="$emit('more')">
        Load more
      </Button>
      <span v-if="loading" class="flex items-center gap-2 text-text-tertiary text-xs">
        <Spinner :size="12" />loading
      </span>
    </div>
  </section>
</template>
