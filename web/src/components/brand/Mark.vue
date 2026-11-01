<script setup lang="ts">
/**
 * The Venona mark alone: the plated monogram, no wordmark.
 *
 * SPLIT OUT OF `Wordmark.vue`, and the reason is the extension. The hover card
 * renders the mark and nothing else -- at 14px in the corner of somebody else's
 * article, the word is ~82px of branding for the same sentence. Reaching it
 * through a `markOnly` prop on `Wordmark` would not do: `Wordmark` INLINES the
 * drawn lockup, ~4.5KB of path data, so the branch would ship the whole drawing
 * into the content bundle for a component that renders none of it.
 *
 * This file has one asset, 499 bytes, comfortably under that limit, so it bundles
 * as a `data:` URI and needs no `web_accessible_resources`. An `<img>` resolving
 * against a file path would resolve against the HOST page's origin and render
 * broken on every real site while the local fixture still looked fine.
 *
 * A fixed `#1891ac` plate, so this cannot dim with its container the way a
 * `currentColor` drawing would. Dim the whole thing with opacity if a surface
 * needs it -- `HoverCard` does exactly that.
 */
withDefaults(
  defineProps<{
    /** Edge length in px. The asset is square. */
    size?: number
  }>(),
  { size: 16 },
)
</script>

<template>
  <!-- `rounded-md`, not the asset's own `rx`: the file is drawn at 104px where
       `rx="10"` is ~9.6% of the side, and rendered at 14-26px that same ratio is
       a 1.5-2.5px corner -- visibly softer than every other control on the same
       row. Clipping to the container's radius instead puts the mark on the app's
       shape scale, which is what `--radius-md` is for. -->
  <img
    class="mark block flex-none rounded-md select-none"
    src="@/assets/icons/venona-mark-aqua.svg"
    :width="size"
    :height="size"
    alt=""
    aria-hidden="true"
    draggable="false"
  />
</template>
