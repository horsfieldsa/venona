<script setup lang="ts">
/**
 * Light, Dark, System -- one button, cycling.
 *
 * A CYCLE RATHER THAN THREE CONTROLS OR A SELECT. Three radio-ish buttons in a
 * 32px status line is three targets and two more borders on the quietest row in
 * the app; a `<select>` drags in the one native control this design system has
 * no styling for. A cycle is one target and states its own position, and the
 * order puts SYSTEM LAST so a reader who simply wants the other palette gets
 * there in one press from either explicit side.
 *
 * NO `aria-pressed`. It is a boolean, and this control has three states -- a
 * pressed/unpressed reading of "system" is a lie in whichever direction it
 * lands. The accessible name carries the state instead, which is what the ARIA
 * pattern for a cycling control asks for anyway: it says where you are, what
 * pressing does, and -- under System -- what the browser actually resolved,
 * because that is the one thing the reader cannot see from the label.
 *
 * IT IS A BARE `<button>`, not `IconButton`, and the difference is deliberate.
 * `IconButton` draws a 20 or 28px square with a `--text-sm` glyph inside it;
 * this row is bare `--text-xs` text at `--text-tertiary`. A boxed glyph would
 * make the theme control the loudest thing on a row that exists to be quiet, so
 * it carries the row's treatment exactly -- same size, same rung, same hover --
 * and reads as one of its neighbours rather than a control among labels.
 *
 * No glyph for the same reason a family is never hard-coded: the obvious ones
 * (sun, moon, half-circle) are outside the Latin blocks JetBrains Mono ships, so
 * they would fall through to a platform emoji font at a different width, and on
 * some systems in colour.
 */
import { computed } from 'vue'

import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()

/** What pressing does, and where you are. Under System it also names what the
 *  browser resolved, which the word "system" does not tell anybody. */
const description = computed(() => {
  const here =
    theme.choice === 'system' ? `system (currently ${theme.systemResolves})` : theme.choice
  return `Theme: ${here}. Switch to ${theme.next}.`
})
</script>

<template>
  <button
    class="theme text-xs bg-transparent border-0 font-ui cursor-pointer text-text-tertiary hover:text-text-secondary"
    type="button"
    :title="description"
    :aria-label="description"
    @click="theme.cycle()"
  >
    theme: {{ theme.choice }}
  </button>
</template>
