<script setup lang="ts">
/**
 * A short status message: an error, a hint, or an empty state.
 *
 * The primitive owns TYPE and COLOUR; the view owns POSITION. That split is why
 * one component can serve both an absolutely-positioned canvas overlay and a
 * full-page load failure — the caller supplies the layout class and this
 * supplies the semantics.
 *
 * The bug that motivated it: `BriefView` rendered load errors in ordinary grey
 * `--text-secondary`, indistinguishable from body copy, on the app's primary
 * reading surface. A failed brief looked like a quiet day.
 *
 * `role="alert"` on errors only — announcing every empty state would make the
 * live region useless.
 */
withDefaults(defineProps<{ kind?: 'error' | 'info' | 'success' }>(), { kind: 'info' })

/** Body colour per kind. A static map: Tailwind scans source text, so a
 *  computed `text-${...}` class name is never generated. */
const BODY_TONE = {
  info: 'text-text-secondary',
  error: 'text-signal-critical',
  success: 'text-signal-ok',
} as const

/**
 * The FRAME, which this component did not have.
 *
 * `.note` and `.note--info` were class names with no CSS anywhere behind them --
 * not in a scoped block in this SFC (there is none, and per CLAUDE.md there may
 * not be one), not in `base.css`, not in `theme.css`. A StatusNote rendered as
 * bare paragraphs:
 * no ground, no rule, nothing marking it off from the copy around it. The
 * original bug this component was written for -- "a failed brief looked like a
 * quiet day" -- was only half fixed, because the colour landed and the frame
 * never existed.
 *
 * The design bundle draws it as a tinted ground with a 2px left rule, and that
 * is what these are. A left rule rather than a full border on purpose: a border
 * means "this floats, or you can press it" (docs/DESIGN.md) and a status note is
 * neither -- it is an annotation on the thing above it.
 *
 * `info` takes `--accent-wash` and `--accent-muted`, exactly as the bundle
 * specifies. `error` and `success` take their signal colour on `--surface-inset`
 * rather than a wash of their own: there are only two wash tokens and both are
 * the accent's, so inventing `--wash-critical` here would add tier-1 values for
 * one component when a neutral ground carries the same structure.
 */
const FRAME = {
  info: 'bg-accent-wash border-l-accent-muted',
  error: 'bg-surface-inset border-l-signal-critical',
  success: 'bg-surface-inset border-l-signal-ok',
} as const
</script>

<template>
  <div class="note border-l-2 px-3 py-2" :class="FRAME[kind]" :role="kind === 'error' ? 'alert' : undefined">
    <!-- The body is always a sentence -- every caller passes one -- so it takes
         the prose face. The hint under it stays mono: it carries paths, commands
         and run ids, which is exactly what fixed pitch is for. -->
    <p
      class="note__body font-prose text-sm leading-normal tracking-tight text-pretty"
      :class="BODY_TONE[kind]"
    >
      <slot />
    </p>
    <p v-if="$slots.hint" class="note__hint mt-1 text-text-tertiary text-xs mono">
      <slot name="hint" />
    </p>
  </div>
</template>
