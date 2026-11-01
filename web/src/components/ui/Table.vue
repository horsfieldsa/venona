<script lang="ts">
/**
 * Declared in a plain script block because `<script setup>` cannot export.
 *
 * Generic over the row so `key` is checked against the row's actual fields. The
 * default keeps every existing `Column[]` annotation compiling; consumers opt in
 * per view by writing `Column<Run>[]`.
 */
export interface Column<T = Record<string, unknown>> {
  key: keyof T & string
  label: string
  /** Right-aligned and tabular. */
  numeric?: boolean
  width?: string
  /**
   * Drop this column below the named breakpoint.
   *
   * Here rather than per view because every table in the app has the same
   * problem and was solving none of it: `docs/DESIGN.md` requires every page
   * to work at 390px, and a ten-column run history at that width is a
   * horizontal scrollbar with the answer off the right edge. A column that
   * carries detail rather than identity is the one to lose.
   */
  hideBelow?: 'sm' | 'md'
}
</script>

<script setup lang="ts" generic="T extends Record<string, unknown>">
/**
 * The one table. Replaces the hand-rolled `.runs` table in the run history and
 * the CSS-grid pseudo-table in the brief's sources footer, which had drifted
 * apart on border colour, header casing and numeric alignment.
 *
 * Columns are configured rather than hand-written because the alignment rule
 * has to live in ONE place: a numeric column is right-aligned AND tabular, and
 * a counter that reflows as it ticks is the specific thing docs/DESIGN.md
 * calls out.
 *
 * Cells default to `row[key]`; override any of them with `#cell:<key>`.
 */
/**
 * `T` rather than `Record<string, unknown>`.
 *
 * The untyped version pushed the cast onto every consumer instead of removing
 * it: `RunsView` wrote `(row as unknown as Run)` nine times in one template,
 * `MergeHistory` six, and `ArchiveView` used non-null assertions. A shared
 * primitive that makes its callers assert is not sharing the typing, it is
 * relocating the hole. `SegmentedControl` already demonstrates the pattern.
 */
withDefaults(
  defineProps<{
    columns: Column<T>[]
    rows: T[]
    rowKey: keyof T  
    /** Rows become clickable and emit `select`. */
    selectable?: boolean
    /** Marks the current row, e.g. the run being streamed. */
    activeKey?: string | null
  }>(),
  { selectable: false, activeKey: null },
)

defineEmits<{ (e: 'select', row: T): void }>()

/**
 * Cell padding steps down at narrow widths (`px-2 sm:px-3`, in ONE class
 * string so the two cannot tie on specificity).
 *
 * Padding is the largest single cost in a dense table: at `px-3` a
 * seven-column history spends 336px on air, which is most of a 390px screen
 * before a single value is drawn. `docs/DESIGN.md` requires every page to work
 * there.
 */

/**
 * `hideBelow` as literal class strings, never interpolated.
 *
 * Tailwind scans source TEXT, so a template writing `sm:table-cell` through a
 * variable generates no rule at all and the column simply stays visible --
 * a failure that looks like the prop being ignored rather than like a build
 * problem. CLAUDE.md bans the interpolated form for exactly this.
 *
 * `table-cell` rather than `block` on the way back: a `td` restored as a block
 * leaves the table layout and the row collapses.
 */
const HIDE_BELOW: Record<string, string> = {
  sm: 'hidden sm:table-cell',
  md: 'hidden md:table-cell',
}

function hideClass(column: Column<T>): string {
  return column.hideBelow ? HIDE_BELOW[column.hideBelow] : ''
}

/**
 * Declared explicitly because the slot NAMES are dynamic -- `cell:${column.key}`
 * -- and making the component generic tightened Vue's inference enough that it
 * stopped accepting them from consumers.
 *
 * Deliberately NOT a mapped type over `columns`: that would tie the slot names
 * to a prop's runtime value, which is not something the template compiler can
 * follow, and the payoff would be catching a typo in a slot name -- which is
 * already visible as a cell that renders its raw value. `row` and `value` ARE
 * typed, which is the part that removes casts.
 */
defineSlots<
  {
    empty?: () => unknown
  } & Record<`cell:${string}`, (props: { row: T; value: unknown }) => unknown>
>()
</script>

<template>
  <!-- The scroll container: on a narrow screen a dense table scrolls sideways
       inside its own box instead of clipping columns or stretching the page.
       The `-mx-3` lives HERE so the scroll box owns the overhang; overflow to
       the LEFT of a scroll container's padding box is unreachable by
       scrolling, so a negative margin on the table itself would clip the first
       column's wash the moment this wrapper existed. -->
  <div class="-mx-3 overflow-x-auto">
  <!-- The width cancels the wrapper's two 12px overhangs, so the table box and
       the wash land exactly where `w-full -mx-3` on the table itself put them:
       column one's TEXT on the page's left line, the wash 12px past it on both
       sides, the right edge pulled in 12px rather than widened. On a table,
       `width` is a floor, not a cap -- content that needs more room widens it
       and the wrapper scrolls. -->
  <table class="table w-[calc(100%_-_2*var(--space-3))] border-collapse">
    <!-- No header above nothing. Four column labels describing zero rows, with
         an empty state underneath them, reads as a table that failed to load. -->
    <thead v-if="rows.length">
      <tr>
        <!-- `th` and `td` MUST share horizontal padding. They did not: `th` had
             none and numeric `td` had 8px on the right, so every right-aligned
             header sat 8px off its own column -- four visible misalignments on
             the app's densest surface.

             SYMMETRIC, and on the first column too. It was `pr-3 last:pr-0`, so
             a cell had air on its right and none on its left -- invisible until
             a row is hovered or selected, at which point the wash starts exactly
             where the text does and the first column reads as clipped. -->
        <th
          v-for="column in columns"
          :key="column.key"
          class="px-2 sm:px-3 pb-2 text-2xs font-medium uppercase tracking-label text-text-tertiary border-b border-b-border-subtle whitespace-nowrap"
          :class="[column.numeric ? 'num text-right' : 'text-left', hideClass(column)]"
          :style="column.width ? { width: column.width } : undefined"
        >
          {{ column.label }}
        </th>
      </tr>
    </thead>
    <tbody>
      <!-- Rows are separated by rhythm and hover, not by a rule each. Nine
           hairlines stacked in a 300px table is a grid; 10px of air and a hover
           wash is a list.

           Row actions appear on hover: a `×` on every row is nine pieces of
           permanent noise for an occasional action. `focus-within` keeps it
           reachable by keyboard, which `opacity: 0` alone would not.

           A selectable row is a control and carries the whole affordance: a tab
           stop, a name for the screen reader, and both keys. Only when
           `selectable` -- a read-only table that collects a tab stop per row
           makes the rest of the page unreachable by tabbing.

           `.self` on the keys, because a row can hold a button: Enter on a
           nested IconButton bubbles here, and without it one keystroke fires
           both the button and the row underneath it. -->
      <tr
        v-for="row in rows"
        :key="String(row[rowKey])"
        class="[&_.icon-btn]:opacity-0 [&_.icon-btn]:transition-opacity [&_.icon-btn]:duration-fast [&_.icon-btn]:ease-standard hover:[&_.icon-btn]:opacity-100 focus-within:[&_.icon-btn]:opacity-100"
        :class="[
          selectable ? 'selectable cursor-pointer hover:bg-surface-inset' : '',
          activeKey != null && row[rowKey] === activeKey ? 'on bg-surface-selected' : '',
        ]"
        :tabindex="selectable ? 0 : undefined"
        :role="selectable ? 'button' : undefined"
        :aria-pressed="selectable ? row[rowKey] === activeKey : undefined"
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
          <!-- Branch on whether the slot EXISTS rather than using `<slot>`
               fallback content. Vue treats a provided slot that renders nothing
               -- a `v-if` that is false -- as empty and falls back, so a cell
               deliberately blanked by its consumer silently reverted to the raw
               value. A `warnings: []` cell rendered the literal string `[]`. -->
          <slot
            v-if="$slots[`cell:${column.key}`]"
            :name="`cell:${column.key}`"
            :row="row"
            :value="row[column.key]"
          />
          <template v-else>{{ row[column.key] ?? '' }}</template>
        </td>
      </tr>
      <tr v-if="!rows.length" class="empty">
        <td :colspan="columns.length" class="py-4 border-b-0">
          <slot name="empty" />
        </td>
      </tr>
    </tbody>
  </table>
  </div>
</template>
