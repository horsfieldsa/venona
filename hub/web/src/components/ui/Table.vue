<script lang="ts">
/** The column config, exported from a plain `<script>` so a view can type its
 *  own list against it. `hideBelow` is how a wide table stays honest on a
 *  phone: drop the column rather than let the page scroll sideways. */
export interface Column {
  key: string
  label: string
  numeric?: boolean
  hideBelow?: 'sm' | 'md'
}
</script>

<script setup lang="ts">
withDefaults(
  defineProps<{
    columns: Column[]
    rows: Record<string, unknown>[]
    rowKey: string
    /** Rows become controls that go somewhere. */
    selectable?: boolean
  }>(),
  { selectable: false },
)
defineEmits<{ (e: 'select', row: Record<string, unknown>): void }>()

/** A static map, never an interpolated class: Tailwind scans source text, so
 *  `hidden ${tier}:table-cell` is never generated and the failure looks like a
 *  data bug. */
const HIDE = { sm: 'hidden sm:table-cell', md: 'hidden md:table-cell' } as const
function hideClass(column: Column): string {
  return column.hideBelow ? HIDE[column.hideBelow] : ''
}
</script>

<template>
  <!-- The scroll container: on a narrow screen a dense table scrolls sideways
       inside its own box instead of clipping columns or widening the page. -->
  <div class="-mx-3 overflow-x-auto">
    <table class="table w-[calc(100%_-_2*var(--space-3))] border-collapse">
      <!-- No header above nothing: column labels describing zero rows, with an
           empty state underneath, reads as a table that failed to load. -->
      <thead v-if="rows.length">
        <tr>
          <!-- `th` and `td` MUST share horizontal padding, or every
               right-aligned header sits off its own column. Symmetric, and on
               the first column too: without it the hover wash starts where the
               text does and column one reads as clipped. -->
          <th
            v-for="column in columns"
            :key="column.key"
            class="px-2 sm:px-3 pb-2 text-2xs font-medium uppercase tracking-label text-text-tertiary border-b border-b-border-subtle whitespace-nowrap"
            :class="[column.numeric ? 'num text-right' : 'text-left', hideClass(column)]"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <!-- Rows are separated by rhythm, not by a rule each. Nine hairlines
             stacked in a 300px table is a grid; 10px of air is a list. No hover
             wash: these rows are not selectable, and the app only washes rows
             that are. -->
        <!-- A selectable row carries the WHOLE affordance: a tab stop, a name
             for a screen reader, and both keys. Only when `selectable` -- a
             read-only table that collects a tab stop per row makes the rest of
             the page unreachable by tabbing.

             `.self` on the keys, because a row can hold a button: Enter on a
             nested control bubbles here, and without it one keystroke fires
             both the button and the row underneath it. -->
        <tr
          v-for="row in rows"
          :key="String(row[rowKey])"
          :class="selectable ? 'selectable cursor-pointer hover:bg-surface-inset' : ''"
          :tabindex="selectable ? 0 : undefined"
          :role="selectable ? 'button' : undefined"
          @click="selectable && $emit('select', row)"
          @keydown.enter.self.prevent="selectable && $emit('select', row)"
          @keydown.space.self.prevent="selectable && $emit('select', row)"
        >
          <td
            v-for="column in columns"
            :key="column.key"
            class="px-2 sm:px-3 py-2 text-sm text-text-secondary align-baseline"
            :class="[column.numeric ? 'num mono text-right' : '', hideClass(column)]"
          >
            <slot :name="`cell:${column.key}`" :row="row">{{ row[column.key] }}</slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
