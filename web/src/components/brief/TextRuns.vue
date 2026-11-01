<script setup lang="ts">
/**
 * A flat run list, rendered inline -- the one implementation of "text with
 * mentions in it". `BriefBlock` wraps it in a block tag; titles and headlines
 * drop it straight inside their own heading element.
 *
 * Note what is NOT here: no markdown parsing, no v-html, no substring
 * arithmetic. The server already split the text at every span and mark
 * boundary, so rendering is a single v-for and there is no XSS surface.
 * Interactivity is delegated: the mention spans carry `data-entity-id` and
 * an ancestor owns the listeners (`useMentionEvents`), so a 500-mention
 * brief creates zero per-span listeners.
 */
import type { TextRun } from '@/types/brief'

const props = defineProps<{ runs: TextRun[]; activeId?: string | null }>()

/** C4 id prefix drives the colour token: org, prog, tech, plat, person. */
function refPrefix(run: TextRun): string {
  return run.r ? run.r.split(':')[0] : ''
}

function classesFor(run: TextRun): string[] {
  if (!run.r) return []
  const classes = ['mention']
  if (run.k === 'entity') {
    classes.push('mention--entity', `mention--${refPrefix(run)}`)
  } else {
    classes.push(`mention--${run.k}`)
  }
  // A modifier, not a kind: it stacks on `mention--org` so the entity keeps its
  // own hue and only the underline changes. C3's `k` partition is untouched.
  if (run.u) classes.push('mention--uncorroborated')
  if ((run.c ?? 1) < 0.7) classes.push('mention--low')
  if (run.r === props.activeId) classes.push('is-active')
  return classes
}

function titleFor(run: TextRun): string | undefined {
  if (run.k === 'undefined') return 'No grounded definition found'
  // Said in words as well as in the underline: the treatment tells you there is
  // something to know, the title tells you what.
  if (run.u) return 'Named by one source; not yet corroborated'
  return undefined
}
</script>

<template>
  <template v-for="(run, i) in runs" :key="i">
    <span
      v-if="run.r"
      :class="classesFor(run)"
      :data-entity-id="run.r"
      :title="titleFor(run)"
      role="button"
      tabindex="0"
      ><b v-if="run.b">{{ run.t }}</b
      ><i v-else-if="run.i">{{ run.t }}</i
      ><code v-else-if="run.code">{{ run.t }}</code
      ><template v-else>{{ run.t }}</template></span
    >

    <a v-else-if="run.href" :href="run.href" target="_blank" rel="noopener noreferrer">{{
      run.t
    }}</a>

    <b v-else-if="run.b">{{ run.t }}</b>
    <i v-else-if="run.i">{{ run.t }}</i>
    <code v-else-if="run.code">{{ run.t }}</code>
    <template v-else>{{ run.t }}</template>
  </template>
</template>
