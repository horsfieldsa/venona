<script setup lang="ts">
/**
 * The icon set. Two sources behind one API -- hand-drawn glyphs in `PATHS`,
 * and a curated slice of Lucide (lucide.dev, ISC licence) in `LUCIDE` -- both
 * stroked, `currentColor`, sized by the same prop, so a call site never knows
 * which it got.
 *
 * Hand-drawn: 1.25px strokes on a 16px grid. These replace the dingbats the
 * app was using — `⚙ ◀ ▶ ↺ × ‹ ›`. Those render in the system emoji/symbol
 * font, which means a different weight, a different baseline and a different
 * vertical centre from Inter beside them, and on many systems the gear renders
 * in *colour*. Nothing else you do to a dark interface survives a coloured
 * emoji sitting in the header.
 *
 * Lucide is imported ONE COMPONENT AT A TIME into `LUCIDE`: the map is the
 * allowlist, so the set stays curated and the bundle carries only what a name
 * in this file can reach. Stroke width 1.875 on Lucide's 24 grid is the same
 * ink-to-grid ratio as 1.25 on 16, so the two sources weigh the same at any
 * rendered size.
 *
 * An icon still has to earn its place against a word. Alone it rides
 * `IconButton`, whose required `label` keeps the word as the accessible name
 * and hover title; beside a word it rides `Button`'s `icon` prop. Keyboard-
 * first rows carry a `Kbd` cap instead -- never a glyph as well.
 */
import { watchEffect, type Component } from 'vue'
import {
  Ban,
  BookOpen,
  Check,
  Copy,
  Eye,
  EyeOff,
  LogIn,
  LogOut,
  Maximize2,
  Merge,
  Minimize2,
  Monitor,
  Moon,
  Pencil,
  Play,
  RefreshCw,
  Send,
  Split,
  Sun,
  Target,
  Trash2,
  UserCheck,
  UserMinus,
  UserX,
  Waypoints,
} from 'lucide-vue-next'

/* One name per semantic cluster, not per label: `check` is save AND apply AND
 * approve, so the vocabulary stays small enough to learn. `close` (below)
 * stays the chrome-dismiss glyph; a REJECTED verdict is `ban`, because
 * overloading one X for both would make "dismiss this panel" and "decline
 * this person" the same shape. */
const LUCIDE: Record<string, Component> = {
  ban: Ban,
  // Reading what is already stored, as against `search`, which is going out to
  // find more. Both are in the runs menu and one glyph for the two would make
  // the free pass over our own articles look like the web sweep beside it.
  'book-open': BookOpen,
  check: Check,
  copy: Copy,
  eye: Eye,
  'eye-off': EyeOff,
  'log-in': LogIn,
  'log-out': LogOut,
  'maximize-2': Maximize2,
  merge: Merge,
  monitor: Monitor,
  moon: Moon,
  'minimize-2': Minimize2,
  pencil: Pencil,
  play: Play,
  'refresh-cw': RefreshCw,
  send: Send,
  split: Split,
  sun: Sun,
  target: Target,
  'trash-2': Trash2,
  'user-check': UserCheck,
  'user-minus': UserMinus,
  'user-x': UserX,
  waypoints: Waypoints,
}

const PATHS: Record<string, string> = {
  // Chevrons: navigation between siblings (previous/next brief, rail collapse).
  left: 'M10 3.5 5.5 8l4.5 4.5',
  right: 'M6 3.5 10.5 8 6 12.5',
  up: 'M3.5 10 8 5.5l4.5 4.5',
  down: 'M3.5 6 8 10.5 12.5 6',
  close: 'M4 4l8 8M12 4l-8 8',
  // Restore: an arrow curling back on itself.
  undo: 'M3 8a5 5 0 1 1 1.6 3.7M3 4.5V8h3.5',
  // A real cog: eight trapezoidal teeth (outer r 6.4, inner r 4.3, half-width
  // 13 degrees, shoulder 3) plus a hub. A gear is defined by the silhouette
  // BETWEEN its teeth -- radial spokes read as a sun, and an unclosed freehand
  // outline renders at 14px as a smear.
  settings:
    'M6.56 1.76L9.44 1.76L9.19 3.87L10.08 4.24L11.39 2.57L13.43 4.61L11.76 5.92L12.13 6.81L14.24 6.56L14.24 9.44L12.13 9.19L11.76 10.08L13.43 11.39L11.39 13.43L10.08 11.76L9.19 12.13L9.44 14.24L6.56 14.24L6.81 12.13L5.92 11.76L4.61 13.43L2.57 11.39L4.24 10.08L3.87 9.19L1.76 9.44L1.76 6.56L3.87 6.81L4.24 5.92L2.57 4.61L4.61 2.57L5.92 4.24L6.81 3.87Z M10.4 8a2.4 2.4 0 1 1-4.8 0 2.4 2.4 0 0 1 4.8 0',
  search: 'M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10z M10.5 10.5 14 14',
  // A question mark, drawn rather than typeset: every other glyph here is a
  // stroked path on the same 16-unit grid, and a text `?` would carry the UI
  // font's weight and baseline into a row of icons that do not.
  help: 'M6 6a2 2 0 1 1 2.6 1.9c-.5.2-.85.65-.85 1.2v.65 M8 12.2v.3',
  external: 'M6.5 3H3v10h10V9.5M9.5 2.5H13.5V6.5M13 3l-5.5 5.5',
  // A person: head and shoulders, the account menu's glyph.
  user: 'M8 7.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2z M2.9 13.6a5.1 5.1 0 0 1 10.2 0',
  // Three bars: the phone nav drawer's toggle.
  menu: 'M2.5 4.5h11M2.5 8h11M2.5 11.5h11',
  // Add. One name for the whole cluster, per the note above `LUCIDE`: adding a
  // destination, a web address, a row. Drawn on the same 16-unit grid as its
  // neighbours rather than typeset, so it carries no font's weight into a row
  // of stroked glyphs.
  plus: 'M8 3.5v9M3.5 8h9',
}

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 14 })

// A name neither table knows renders an EMPTY `<path>`: no error, no fallback,
// just a button with a gap where its glyph should be. That is how three
// `icon="plus"` call sites shipped against a name that did not exist and were
// caught by eye rather than by anything here. `Input` warns the same way for
// the same reason.
if (import.meta.env.DEV) {
  const known = new Set([...Object.keys(LUCIDE), ...Object.keys(PATHS)])
  watchEffect(() => {
    if (props.name && !known.has(props.name)) {
      console.warn(`[Venona] <Icon name="${props.name}"> is not a known icon; nothing will render`)
    }
  })
}

</script>

<template>
  <component
    :is="LUCIDE[name]"
    v-if="LUCIDE[name]"
    class="icon block flex-none"
    :size="size"
    :stroke-width="1.875"
    aria-hidden="true"
    focusable="false"
  />
  <svg
    v-else
    class="icon block flex-none"
    :width="size"
    :height="size"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    stroke-width="1.25"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path :d="PATHS[name] ?? ''" />
  </svg>
</template>
