<script setup lang="ts">
/**
 * The Venona lockup: the drawn horizontal logo, inlined.
 *
 * WHAT THIS REPLACED, AND WHY IT IS INLINE.
 *
 * Until now this rendered the plated mark as an `<img>` beside the word
 * "Venona" TYPESET in JetBrains Mono, uppercase, at 0.32em of tracking. The
 * brand kit forbids that outright -- the wordmark is drawn artwork with custom
 * letterforms and cannot be reset in a font -- and a separate front site had
 * already moved to the drawn lockup, so the app and the site stated the same
 * name in two different shapes. Three separate places in this repo
 * (`tokens.css`, `finish.mjs`, `Mark.vue`'s docstring) already describe the
 * drawn lockup as the decided state; this component was the one that never
 * arrived. Its three utilities -- `text-brand-wordmark`, `tracking-brand` and
 * `-mr-(--tracking-brand)` -- all named tokens that had been DELETED, so in a
 * closed Tailwind theme they generated nothing and the word rendered with
 * neither brand colour nor brand tracking. Nothing caught it: an unknown
 * utility is silently not emitted.
 *
 * INLINE rather than `<img src="…svg">`, and that is the whole point. The
 * wordmark has to be WHITE on the dark theme and near-black on the light one,
 * and the kit ships no lockup pairing the aqua plate with a white wordmark --
 * every aqua-plate variant sets the word in #d2ecf9, and the two white-wordmark
 * variants whiten the plate too. An `<img>` cannot be re-coloured, so the
 * choice was a divergent eleventh file to maintain by hand, or inlining the
 * artwork and tokenising the one attribute that varies. This is the second.
 * `fill="currentColor"` on the wordmark group, `color` from
 * `--brand-wordmark`; the plate and the monogram keep their own tokens and do
 * NOT vary by theme, because they are artwork rather than interface.
 *
 * The paths are `assets/logos/venona-logo-primary.svg` verbatim, group ids and
 * transforms included, so a diff against the kit file is a straight comparison.
 * Only the generator's `featureKey` attributes are dropped -- they are SVGjs
 * bookkeeping, they are not valid SVG, and nothing reads them.
 * A separate light-variant file is unnecessary: it would differ in exactly one
 * attribute, the wordmark fill, which is what the token does.
 *
 * WHAT WAS LOST, AND IT IS WORTH NAMING. Before the kit existed this drew a
 * three-node graph fragment in `currentColor`, so it DIMMED WITH ITS CONTAINER.
 * The plate cannot: it is a fixed aqua. If a surface needs the lockup to
 * recede, dim the WHOLE thing with opacity -- the marketing footer does exactly
 * that -- and never re-colour half of it.
 *
 * The mark alone is `Mark.vue`, not a branch here. See its docstring for why
 * the extension cannot afford this file.
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** The height of the LOCKUP BOX in px; the drawn word scales with it. The
     *  mark is 104 of the box's 124 units, so `size` is ~1.19x the plate. The
     *  brand kit sets a 16px floor for the horizontal lockup and this scale
     *  puts the x-height under 5px there, so every call site passes 22 or
     *  more -- the default exists for the type, not as a recommendation. */
    size?: number
    /** The accessible name. Pass `""` where an ancestor already names it --
     *  `AppHeader`'s anchor carries "Venona, go to the latest brief", and two
     *  names on one link is read out twice. */
    label?: string
  }>(),
  { size: 16, label: 'Venona' },
)

/* The intrinsic width, so the box is correct before any stylesheet loads. The
   `w-auto` class below then hands sizing back to the viewBox ratio, which
   resolves to the same number -- the attribute is the no-CSS fallback, not a
   second source of truth. */
const width = computed(() => Math.round(((props.size * 430.36) / 124) * 100) / 100)
</script>

<template>
  <!-- `w-auto` is also the canary `extension/scripts/finish.mjs` looks for: it
       is the one utility the `components/brand` @source glob uniquely produces,
       so its absence from the built card bundle is what proves the glob stopped
       matching. See the comment there before moving or removing it.

       `text-brand-wordmark` colours drawn artwork through `currentColor`
       rather than setting a font. -->
  <svg
    class="lockup block w-auto flex-none select-none text-brand-wordmark"
    :height="size"
    :width="width"
    viewBox="0 0 430.36 124"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    focusable="false"
  >
    <g transform="translate(10, 10)">
      <!-- The plate. Fixed aqua, and the kit's own `rx="10"` is kept: at 104px
           that is 9.6% of the side, which is the drawing. `Mark.vue` overrides
           it with `rounded-md` because a standalone mark sits in a row of
           controls and has to be on the app's shape scale; a lockup does not. -->
      <g
        id="SvgjsG1078"
        transform="matrix(1,0,0,1,0,0)"
        class="fill-brand-plate"
      >
        <rect width="104" height="104" rx="10" ry="10"></rect>
      </g>
      <!-- The monogram, on the plate. Also fixed: it is the kit's neutral black,
           not the app's cool near-black, and it never inverts -- the plate it
           sits on does not change between themes. -->
      <g
        id="SvgjsG1079"
        transform="matrix(4.388109809494211,0,0,4.388109809494211,11.515327382532368,-3.611983846947986)"
        class="fill-brand-monogram"
      >
        <path
          d="M12.832 13.3594 l-3.75 6.6797 l-4.7168 0 l-0.69336 -6.6797 l-0.89844 0 l-0.83984 -7.9785 l5.1074 0 l0.5957 7.041 c1.3184 -2.3145 2.5098 -4.7363 3.8184 -7.041 l4.9805 0 l-4.4824 7.9785 l0.87891 0 z"
        ></path>
      </g>
      <!-- The word. The ONE group that varies: `currentColor`, taking
           `--brand-wordmark` off the `text-brand-wordmark` above. -->
      <g
        id="SvgjsG1080"
        transform="matrix(2.914589875278122,0,0,2.914589875278122,120.21406303187945,14.745035992407297)"
        fill="currentColor"
      >
        <path
          d="M11.631 13.6328 l-3.6523 6.3574 l-4.5313 0 l-0.72266 -6.3574 l-0.89844 0 l-0.52734 -4.6094 l4.7852 0 l0.29297 4.3457 c0.77148 -1.4258 1.5039 -2.9199 2.3047 -4.3457 l4.7168 0 l-2.6563 4.6094 l0.88867 0 z M14.0527 14.5703 c0.078125 -0.33203 0.18555 -0.625 0.2832 -0.9375 l-1.084 0 c2.3438 -6.377 12.383 -7.0117 11.084 0 l1.0938 0 c-0.019531 0.11719 -0.019531 0.23438 -0.048828 0.36133 l-0.41992 1.6309 l-6.4648 0 c-0.33203 2.4121 4.248 1.6797 4.6973 0.81055 l1.4941 0 l-0.71289 2.7832 c-3.8574 2.2168 -11.357 1.6602 -9.9219 -4.6484 z M20.976599999999998 12.685500000000001 c0.21484 -1.0645 -2.1289 -1.6699 -3.2324 0 c0.0097656 0 3.2324 0.0097656 3.2324 0 z M37.597484375 13.6719 l-1.4746 6.3672 l-4.6094 0 l1.2598 -5.4492 c0.039063 -0.21484 0.078125 -0.40039 0.10742 -0.60547 c0.019531 -0.097656 0.039063 -0.20508 0.058594 -0.3125 l-0.98633 0 c0.0097656 -0.087891 0.019531 -0.16602 0.039063 -0.26367 c0.019531 -0.17578 0.039063 -0.32227 0.058594 -0.43945 c0.0097656 -0.14648 0.0097656 -0.23438 0 -0.2832 l0 0.029297 c-0.14648 -0.56641 -1.0156 -0.46875 -1.2305 -0.029297 l-0.22461 0.98633 l0.9668 0 l-1.4551 6.3672 l-4.5996 0 l1.4648 -6.3672 l-0.98633 0 l1.0645 -4.6387 l4.5898 0 l-0.078125 0.41992 c1.5234 -0.72266 3.7402 -1.3965 4.9414 0.40039 c0.48828 0.72266 0.58594 1.7676 0.25391 3.1836 l-0.14648 0.63477 l0.98633 0 z M50.458984375 14.541 c-0.81055 3.5547 -3.4082 5.8203 -7.0996 5.8203 c-3.6035 0 -5.2734 -2.168 -4.4336 -5.8203 c0.068359 -0.29297 0.14648 -0.5957 0.26367 -0.86914 l-1.0059 0 c1.0449 -3.0957 3.5156 -4.9707 6.875 -4.9707 c3.457 0 4.9316 2.0215 4.5703 4.9707 l0.98633 0 c-0.029297 0.27344 -0.087891 0.57617 -0.15625 0.86914 z M42.861284375 13.6719 l0.97656 0 c-0.55664 1.4648 -0.66406 3.3789 0.30273 3.3789 c0.88867 0 1.6797 -1.9141 1.7969 -3.3789 l-0.9668 0 c0.078125 -0.91797 -0.10742 -1.6602 -0.68359 -1.6602 c-0.53711 0 -1.0742 0.74219 -1.4258 1.6602 z M62.62678125 13.6719 l-1.4746 6.3672 l-4.6094 0 l1.2598 -5.4492 c0.039063 -0.21484 0.078125 -0.40039 0.10742 -0.60547 c0.019531 -0.097656 0.039063 -0.20508 0.058594 -0.3125 l-0.98633 0 c0.0097656 -0.087891 0.019531 -0.16602 0.039063 -0.26367 c0.019531 -0.17578 0.039063 -0.32227 0.058594 -0.43945 c0.0097656 -0.14648 0.0097656 -0.23438 0 -0.2832 l0 0.029297 c-0.14648 -0.56641 -1.0156 -0.46875 -1.2305 -0.029297 l-0.22461 0.98633 l0.9668 0 l-1.4551 6.3672 l-4.5996 0 l1.4648 -6.3672 l-0.98633 0 l1.0645 -4.6387 l4.5898 0 l-0.078125 0.41992 c1.5234 -0.72266 3.7402 -1.3965 4.9414 0.40039 c0.48828 0.72266 0.58594 1.7676 0.25391 3.1836 l-0.14648 0.63477 l0.98633 0 z M69.29688125 17.0605 c0.15625 -0.67383 0.2832 -1.3281 0.41016 -2.0117 c-3.1543 0.42969 -1.9434 3.1055 -0.41016 2.0117 z M73.21328125 20 l-4.5605 0 l0.039063 -0.20508 l-0.019531 0 c-2.3242 0.70313 -6.0352 0.18555 -5.2539 -3.1836 c0.32227 -1.3574 1.0156 -2.2949 2.1484 -2.9785 l-1.1035 0 c0.20508 -0.11719 0.46875 -0.24414 0.71289 -0.3418 c1.1914 -0.57617 2.6367 -0.71289 3.9551 -0.81055 c0.53711 -1.2402 -2.2363 -1.2695 -4.3555 0.20508 l-0.75195 0 l0.75195 -3.2715 c2.959 -0.98633 10.205 -1.8262 9.043 3.2715 l-0.22461 0.94727 l1.1035 0 c-0.49805 2.1289 -0.97656 4.248 -1.4844 6.3672 z M80.2831625 16.6699 l-0.77148 3.3301 l-3.3887 0 l0.76172 -3.3301 l3.3984 0 z M87.03125625 17.0605 c0.15625 -0.67383 0.2832 -1.3281 0.41016 -2.0117 c-3.1543 0.42969 -1.9434 3.1055 -0.41016 2.0117 z M90.94765625 20 l-4.5605 0 l0.039063 -0.20508 l-0.019531 0 c-2.3242 0.70313 -6.0352 0.18555 -5.2539 -3.1836 c0.32227 -1.3574 1.0156 -2.2949 2.1484 -2.9785 l-1.1035 0 c0.20508 -0.11719 0.46875 -0.24414 0.71289 -0.3418 c1.1914 -0.57617 2.6367 -0.71289 3.9551 -0.81055 c0.53711 -1.2402 -2.2363 -1.2695 -4.3555 0.20508 l-0.75195 0 l0.75195 -3.2715 c2.959 -0.98633 10.205 -1.8262 9.043 3.2715 l-0.22461 0.94727 l1.1035 0 c-0.49805 2.1289 -0.97656 4.248 -1.4844 6.3672 z M93.8672375 13.789100000000001 l-1.0254 0 l0.84961 -3.7793 l4.6582 0 l-0.84961 3.7793 l1.0352 0 l-1.377 6.2109 l-4.6582 0 z M99.5507375 5.234 l-0.81055 3.5352 l-4.8242 0 l0.81055 -3.5352 l4.8242 0 z"
        ></path>
      </g>
    </g>
  </svg>
</template>
