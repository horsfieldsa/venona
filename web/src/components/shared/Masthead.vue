<script setup lang="ts">
import { computed } from 'vue'

/**
 * The line at the top of a content column that says what this page is.
 *
 * `docs/DESIGN.md` bans a view TITLE, and correctly: a heading reading "Brief"
 * underneath a nav where Brief is already the active item says nothing. But the
 * rule was written as a ban on any page-level anchor, and the result was that
 * the top-left of every route — where the eye lands first — was blank, and the
 * first thing it found was a 12px uppercase panel label.
 *
 * This is the subject, not the route name: `DAILY BRIEF · 2026-08-06 · 24
 * ITEMS`. Small, mono, quiet, and above the title rather than instead of it.
 */
const props = defineProps<{ parts: (string | number | null | undefined)[] }>()

/**
 * Filtered in script, not in the `v-for`.
 *
 * `v-for="p in parts.filter(...)"` allocated a new array on every render of
 * every masthead in the app, and the separator logic keys off the index INTO
 * THE FILTERED list -- so the filter has to happen before the index is read
 * either way. Doing it here makes that explicit and does it once.
 */
const shown = computed(() => props.parts.filter((p) => p != null && p !== ''))
</script>

<template>
  <p class="masthead mono text-2xs tracking-label uppercase text-text-tertiary mb-3">
    <template v-for="(part, i) in shown" :key="i">
      <span v-if="i > 0" class="sep mx-2 text-border-strong" aria-hidden="true">·</span>{{ part }}
    </template>
  </p>
</template>
