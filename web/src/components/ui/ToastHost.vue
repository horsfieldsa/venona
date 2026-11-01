<script setup lang="ts">
/**
 * Renders the toast queue, teleported to `body` at `--z-toast`.
 *
 * The z-index matters more than it looks: this is the one surface that survives
 * the graph's full screen, so it is how a finished run or a dropped Neo4j
 * connection reaches a user who has hidden the header.
 *
 * A TOAST CARRIES THE THING IT IS ABOUT. "Run completed" on its own is a
 * notification for its own sake -- if the reader is on Runs they can already
 * see it, and if they are not, what they want is the brief it just wrote. So
 * there is one action and it opens that. Never "OK": that is the ×.
 *
 * And a toast is for NEWS, not state. A condition of the view the reader has to
 * know while working is a `StatusNote`; a decision that needs confirming is a
 * `Dialog`; a field of the data is a `Chip`. A toast for a persistent condition
 * is wrong -- the reader dismisses it and the condition is still true with
 * nothing on screen saying so.
 */
import { useToastsStore } from '@/stores/toasts'
import Icon from '@/components/ui/Icon.vue'

const toasts = useToastsStore()

/** A LEADING RULE, not an icon: a coloured bar reads at a glance and does not
 *  import a second visual language into a system with nine glyphs. Static maps,
 *  because Tailwind scans source text and an interpolated class is never
 *  generated. */
const EDGE = {
  success: 'border-l-signal-ok',
  error: 'border-l-signal-critical',
  info: 'border-l-border-strong',
  running: 'border-l-accent-primary',
} as const

const TEXT = {
  success: 'text-signal-ok',
  error: 'text-signal-critical',
  info: 'text-text-secondary',
  running: 'text-accent-primary',
} as const

function edge(kind: string): string {
  return EDGE[kind as keyof typeof EDGE] ?? EDGE.info
}
function text(kind: string): string {
  return TEXT[kind as keyof typeof TEXT] ?? TEXT.info
}
</script>

<template>
  <Teleport to="body">
    <!-- Newest at the BOTTOM, so the one that just arrived is nearest the
         cursor and the older ones do not jump out from under it. Pointer events
         are off on the stack and back on per toast, so it never eats a click
         meant for the view behind it.

         The stack sits on the floor, because there is nothing along the bottom
         of the window to clear. The brief is where this shows: the study nudge
         and "Venona is writing your brief" are both toasts, and both fire on
         the surface whose chrome recedes. -->
    <div
      class="host fixed right-page bottom-(--space-3) z-toast flex flex-col items-end gap-2 pointer-events-none"
      role="status"
      aria-live="polite"
    >
      <div
        v-for="toast in toasts.items"
        :key="toast.id"
        class="toast flex items-start gap-3 max-w-toast px-3 py-2 border border-border-default border-l-2 rounded-sm bg-surface-overlay text-sm leading-normal pointer-events-auto"
        :class="[toast.kind, edge(toast.kind)]"
        :role="toast.kind === 'error' ? 'alert' : undefined"
      >
        <!-- `hyphens-none break-keep` on the whole sentence, not just the id: a
             caller who forgets `subject` still gets a whole id rather than one
             broken across two lines. -->
        <span class="text grow min-w-0 hyphens-none break-keep" :class="text(toast.kind)">
          {{ toast.text
          }}<template v-if="toast.subject">
            ·
            <span class="mono whitespace-nowrap">{{ toast.subject }}</span>
          </template>
        </span>

        <!-- One action, and it opens the thing the toast is about. -->
        <RouterLink
          v-if="toast.to"
          class="link flex-none text-xs whitespace-nowrap"
          :to="toast.to"
          @click="toasts.dismiss(toast.id)"
        >
          {{ toast.toLabel ?? 'open' }} →
        </RouterLink>
        <button
          class="close flex-none flex bg-transparent border-0 text-text-tertiary cursor-pointer leading-none hover:text-text-primary"
          type="button"
          aria-label="Dismiss"
          @click="toasts.dismiss(toast.id)"
        >
          <Icon name="close" :size="12" />
        </button>
      </div>
    </div>
  </Teleport>
</template>
