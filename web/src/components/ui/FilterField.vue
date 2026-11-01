<script setup lang="ts">
/**
 * The one field for narrowing a list.
 *
 * `Input.vue` records the divergence that produced it: five boxed fields
 * written five different ways. The same thing then happened one level up, to
 * the assembly AROUND the field -- a search glyph, a clear button, a spinner
 * for a server-backed filter, and a width. `ArchiveView` had an inline flex row
 * with a loose `Spinner` beside it; `GraphSearch` had the same row under a
 * different class name; `RelevanceTailor` had it again; `PathFinder` had it
 * twice. None of them agreed on placeholder grammar and only some had a way to
 * clear the text at all.
 *
 * So this owns the assembly, and the caller owns what the text MEANS. A
 * dropdown of results goes in the default slot, inside the positioned frame, so
 * a typeahead can keep anchoring its list to the field.
 *
 * TWO JOBS IT DOES NOT DO. It never debounces -- the stores do that, and where
 * they do not the filter is client-side and instant. And it never decides what
 * matches; `busy` is the caller telling it a request is in flight, not a
 * request it made.
 *
 * `collapsible` IS FOR THE CONTEXT BAR AND NOTHING ELSE, and it carries a
 * coupling worth stating: the expanded panel is `absolute inset-0`, which
 * resolves against `.context` because `ContextBar` is the positioned ancestor.
 * The panel deliberately spans the identity zone as well -- `#context-controls`
 * is `grow min-w-0`, so under pressure it is a sliver, and a panel sized to it
 * would be a sliver too. Everything else here works with `collapsible` off,
 * which is how the pickers use it.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type ComponentPublicInstance } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Input from '@/components/ui/Input.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useDismissable, useKeyBindings } from '@/composables/useKeyboard'

// Attributes reach the `<input>`, not the frame: a caller binding `@keydown`
// for a typeahead means the field, and a multi-root component has no single
// element for Vue to fall them through to anyway.
defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    modelValue: string
    /** The accessible name. A placeholder is not one: it leaves on the first
     *  keystroke and is not exposed as a name by every screen reader. */
    label: string
    placeholder?: string
    /** A request is in flight for the text as it stands. */
    busy?: boolean
    /**
     * Register `/` to focus this field.
     *
     * OFF by default, and read once at setup, which is what makes the
     * conditional registration below honest: the pickers must not claim `/`
     * (two of them appear twice on one screen, and one sits in a queue driven
     * by bare letters), so the opt-in belongs with the bar filters.
     *
     * PER-FIELD, never global. The keyboard layer emits no bare letters at all
     * (ADR 0070: the curation queues bind bare letters that WRITE), and `/` is
     * a character a view may legitimately want typed. Registered from the
     * component, the binding exists exactly where there is a field to focus and
     * unregisters with it -- so the `?` overlay lists the row only while the
     * field is on screen, without the view having to hide it behind a getter
     * that resolves empty.
     *
     * `allowInInput` stays off, deliberately: once focus IS in the field, `/`
     * has to insert a slash.
     */
    hotkey?: boolean
    /** Which `?` overlay section the `/` row joins. */
    hotkeyGroup?: string
    /**
     * Render as a glyph until it is asked for. See the block comment.
     *
     * At EVERY width, not just narrow ones. A filter is the least of the three
     * things the bar carries -- you arrive at a page to read it, and narrow it
     * second -- and a 16rem field sitting open on every route spends the widest
     * control in the row on the one that is idle most of the time.
     */
    collapsible?: boolean
    /**
     * `field` is one `--field-lg`, the width a control bar gives a filter.
     * `full` fills whatever the caller placed it in, for a picker inside a
     * dialog or a rail where a 16rem island would sit oddly.
     *
     * A prop rather than a class, for the reason `Input` states about its own
     * width: the frame sets a width unconditionally, so a second one from a
     * call site would tie with it and Tailwind's sort order would pick.
     */
    width?: 'field' | 'full'
  }>(),
  {
    placeholder: '',
    busy: false,
    hotkey: false,
    hotkeyGroup: 'view',
    collapsible: false,
    width: 'field',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'submit'): void
}>()

const field = ref<InstanceType<typeof Input> | null>(null)
const panel = ref<HTMLElement | null>(null)
const glyph = ref<ComponentPublicInstance | null>(null)
const expanded = ref(false)

const dismissable = useDismissable(() => collapse())
watch(expanded, (open) => (open ? dismissable.push() : dismissable.pop()))

/**
 * A press anywhere that is not the field closes it.
 *
 * The same shape as `EntityHoverCard`'s, and for the same reasons:
 * `pointerdown` rather than `click`, because iOS does not reliably synthesize
 * clicks on non-interactive regions and closing on the press is what every
 * other popover does; capture phase, so a handler that stops propagation
 * cannot silently disable the exit.
 *
 * It cannot eat the press that OPENS the panel. That arrives as a `click` on
 * the glyph, which is after `pointerdown` has already been and gone -- and `/`
 * involves no pointer at all.
 *
 * The slotted dropdown lives inside the frame, so picking a result is a press
 * INSIDE and does not race the pick.
 */
function onPointerDownOutside(event: PointerEvent): void {
  if (!expanded.value) return
  const target = event.target instanceof Element ? event.target : null
  if (!target || panel.value?.contains(target)) return
  expanded.value = false
}

onMounted(() => document.addEventListener('pointerdown', onPointerDownOutside, true))
onUnmounted(() => document.removeEventListener('pointerdown', onPointerDownOutside, true))

async function focus(): Promise<void> {
  if (props.collapsible) expanded.value = true
  await nextTick()
  field.value?.focus()
}

/** Focus goes back to the glyph that opened the panel, or it lands on the
 *  document and the next Tab starts from the top of the page. */
function collapse(): void {
  expanded.value = false
  void nextTick(() => (glyph.value?.$el as HTMLElement | undefined)?.focus())
}

function clear(): void {
  emit('update:modelValue', '')
  void focus()
}

const frame = computed(() => {
  if (expanded.value) {
    return 'absolute inset-0 z-overlay flex items-center gap-2 pl-edge pr-page bg-surface-base'
  }
  // One branch per property, all of it here rather than split across
  // attributes: width gets exactly one value, and a call site adding a second
  // would tie on specificity with Tailwind picking the winner. An array of
  // whole literals, never an interpolated fragment -- Tailwind scans source
  // text, and a class it cannot read is a class it never emits.
  return [
    'relative flex items-center gap-2',
    props.width === 'full' ? 'w-full' : 'w-(--field-lg) max-w-full',
  ]
})

if (props.hotkey) {
  useKeyBindings([
    {
      keys: ['/'],
      label: 'Focus the filter',
      group: props.hotkeyGroup,
      run: () => void focus(),
    },
  ])
}

defineExpose({ focus })
</script>

<template>
  <!-- The glyph stays IN FLOW. The panel it opens is absolute, so without a
       sibling holding the row open the controls zone would measure zero and the
       panel would have nothing to be `inset-0` of. -->
  <!-- `active` when there is a term, and it is not decoration: closing does not
       clear, so a collapsed glyph over a filtered list is the only thing on
       screen saying the list is filtered. `IconButton`'s active state is a
       text colour rather than a fill, which keeps it out of the "no filled
       control" rule the bar's other zones live under. -->
  <IconButton
    v-if="collapsible && !expanded"
    ref="glyph"
    bordered
    :active="Boolean(modelValue)"
    :label="modelValue ? `${label} (filtered)` : label"
    @click="focus"
  >
    <Icon name="search" :size="13" />
  </IconButton>

  <div v-if="!collapsible || expanded" ref="panel" :class="frame">
    <!-- The positioned box the marks hang off, and the anchor a slotted
         dropdown resolves against. -->
    <div class="box relative grow min-w-0">
      <span
        aria-hidden="true"
        class="absolute inset-y-0 left-2 flex items-center text-text-tertiary"
      >
        <!-- Not `pointer-events-none` on the span alone: the glyph sits over the
             field's own click target, and a click on it has to reach the input
             the way a click on the padding does. -->
        <Icon name="search" :size="13" class="pointer-events-none" />
      </span>

      <Input
        ref="field"
        v-bind="$attrs"
        type="search"
        leading
        trailing
        :model-value="modelValue"
        :aria-label="label"
        :placeholder="placeholder"
        @update:model-value="(value) => emit('update:modelValue', String(value))"
        @keydown.enter="emit('submit')"
      />

      <!-- Spinner and clear share the slot: while a request is in flight the
           text cannot be stale enough to want clearing, and two marks in one
           corner is the crowding this component exists to stop. -->
      <span v-if="busy" class="absolute inset-y-0 right-2 flex items-center">
        <Spinner :size="11" />
      </span>
      <span v-else-if="modelValue" class="absolute inset-y-0 right-1 flex items-center">
        <IconButton size="sm" label="Clear the filter" @click="clear">
          <Icon name="close" :size="11" />
        </IconButton>
      </span>

      <slot />
    </div>

    <IconButton v-if="expanded" bordered label="Close the filter" @click="collapse">
      <Icon name="close" :size="13" />
    </IconButton>
  </div>
</template>
