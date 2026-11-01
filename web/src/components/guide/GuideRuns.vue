<script setup lang="ts">
/**
 * The guide's inline formatting, in real elements.
 *
 * Same run vocabulary as `BriefBlock` and for the same reason: no markdown
 * parsing in the browser, no `v-html`, no substring arithmetic. The server sends
 * runs that already carry their flags and the template picks an element.
 *
 * The one difference from the brief is LINK HANDLING. The guide's contents list
 * links to its own headings (`#3-reading-the-brief`), and `BriefBlock` opens
 * every href in a new tab — correct for a citation to an outside article, wrong
 * for a table of contents, which would open fourteen copies of the app. So an
 * in-document anchor scrolls this page and everything else keeps the brief's
 * `target="_blank"` with `rel="noopener noreferrer"`.
 */
import Kbd from '@/components/ui/Kbd.vue'
import type { GuideRun } from '@/types/guide'

defineProps<{ runs: GuideRun[] }>()

/** `#section` is ours; anything else leaves the app. */
function isAnchor(href: string) {
  return href.startsWith('#')
}

/** Keys with a name rather than a glyph, plus the modifier spellings this
 *  document uses. `⌘K` and `Ctrl K` are the same binding written for two
 *  platforms, and both appear. */
const NAMED_KEYS = /^(Esc|Escape|Enter|Tab|Space|Shift|Del|Backspace)$/i
const MODIFIED_KEY = /^([⌘⌥⇧⌃]|Ctrl|Alt|Shift)[ ]?[\w,./[\]]$/i
/** A bare key: one uppercase letter, one digit 1-9, or a key that is a glyph. */
const BARE_KEY = /^([A-Z]|[1-9]|[←→↑↓?.,[\]/])$/

/**
 * Is this inline code a KEY the reader is meant to press?
 *
 * Markdown has no `<kbd>`, so the source spells every key as inline code and
 * the whole keyboard reference -- four tables and a dozen run-on lines --
 * rendered as if it were code. A key looks nothing like a code span, and this
 * document is largely about which keys to press.
 *
 * An ALLOWLIST rather than a shape heuristic, because the same file also writes
 * `NGC2`, `CPE`, `failed`, `0`, `—` and `…` as code, and none of those is a
 * key. Anything not listed stays a code span, which is the safe direction: a
 * key that renders as code is a missed flourish, where a value that renders as
 * a key cap is a false instruction.
 */
function isKey(text: string) {
  const t = text.trim()
  return NAMED_KEYS.test(t) || MODIFIED_KEY.test(t) || BARE_KEY.test(t)
}

function scrollToAnchor(href: string, event: Event) {
  event.preventDefault()
  const target = document.getElementById(href.slice(1))
  if (!target) return
  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  // The hash is updated WITHOUT the router, so this does not push a history
  // entry per contents click — back should leave the guide, not walk up
  // fourteen sections of it.
  history.replaceState(null, '', href)
}
</script>

<template>
  <template v-for="(run, i) in runs" :key="i">
    <a
      v-if="run.href && isAnchor(run.href)"
      :href="run.href"
      class="text-accent-primary no-underline hover:underline"
      @click="scrollToAnchor(run.href, $event)"
      >{{ run.t }}</a
    >
    <a
      v-else-if="run.href"
      :href="run.href"
      target="_blank"
      rel="noopener noreferrer"
      class="text-accent-primary"
      >{{ run.t }}</a
    >
    <b v-else-if="run.b" class="text-text-primary">{{ run.t }}</b>
    <i v-else-if="run.i">{{ run.t }}</i>
    <!-- A key cap, not a code span. `Kbd` is the app's own primitive and the
         one the brief and every view already use, so a key in the guide looks
         like the same key on the page it documents. -->
    <Kbd v-else-if="run.code && isKey(run.t)">{{ run.t.trim() }}</Kbd>
    <code v-else-if="run.code" class="mono text-xs text-text-secondary bg-surface-inset rounded-sm px-1">{{ run.t }}</code>
    <s v-else-if="run.s">{{ run.t }}</s>
    <template v-else>{{ run.t }}</template>
  </template>
</template>
