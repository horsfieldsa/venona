<script lang="ts">
/** MODULE scope, which is the point: state inside `<script setup>` compiles
 *  into `setup()` and is born fresh per mount, so it can never cache anything
 *  across visits. This block runs once when the module loads. The guides are
 *  `docs/USER-GUIDE.md` and `docs/ADMIN-GUIDE.md` rendered server-side — they
 *  change on deploy, and a deploy reloads the page — so a per-session copy is
 *  exactly as fresh as a per-mount fetch and costs each round trip once.
 *  KEYED per guide: one slot here served the reader guide's blocks to an admin
 *  who had just switched, which read as the toggle doing nothing. (Typed
 *  inline so this block needs no import statement of its own — the
 *  import-order lint reads both script blocks as one module.) */
const cachedBlocks = new Map<string, import('@/types/guide').GuideBlock[]>()
</script>

<script setup lang="ts">
/**
 * The user guide, in the app.
 *
 * `docs/USER-GUIDE.md` is the source and stays a file — read in a terminal, in
 * an editor and on GitHub as well as here — so this fetches it parsed rather
 * than duplicating it under `web/`. See `services/guide_service.py` for why it
 * does not go through the brief's `markdown_to_blocks` (tables, and the span
 * offsets that module must not have disturbed).
 *
 * NO `v-html`. The server sends structured blocks and this renders elements,
 * which is the same stance `BriefBlock` takes and the reason the reading path
 * has no XSS surface.
 *
 * The contents rail is DERIVED from the blocks rather than being a second list
 * to maintain: every `##` heading carries a slug the server computed with
 * GitHub's rule, which is the same slug the guide's own contents list links to.
 * So the in-document links work here without anything being written twice.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { apiGet, errorText, ForbiddenError } from '@/api/client'
import GuideRuns from '@/components/guide/GuideRuns.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Rail from '@/components/ui/Rail.vue'
import RailLink from '@/components/ui/RailLink.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useSessionStore } from '@/stores/session'
import type { Guide, GuideBlock } from '@/types/guide'

const received = ref<GuideBlock[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const shell = ref<{ scroller: HTMLElement | null } | null>(null)

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

/** Which guide is on screen. Two, one per audience: the reader guide, and the
 *  admin guide behind an admin-only route. Deep-linkable (`/guide?guide=admin`)
 *  so an admin can land on the right one from anywhere; the reader gate here is
 *  UX only — the server refuses the admin document on role either way. */
type Which = 'reader' | 'admin'
const GUIDE_PATH: Record<Which, string> = { reader: '/meta/guide', admin: '/meta/guide/admin' }
const OPTIONS = [
  { value: 'reader', label: 'reader guide' },
  { value: 'admin', label: 'admin guide' },
] as const

const which = ref<Which>(route.query.guide === 'admin' && session.isAdmin() ? 'admin' : 'reader')

async function load(target: Which) {
  loading.value = true
  error.value = null
  const cached = cachedBlocks.get(target)
  if (cached) {
    received.value = cached
    loading.value = false
    return
  }
  try {
    const data = await apiGet<Guide>(GUIDE_PATH[target])
    received.value = data.blocks ?? []
    cachedBlocks.set(target, received.value)
  } catch (err) {
    // `isAdmin()` deliberately fails OPEN while the session probe is in
    // flight (see the session store), so a reader can reach this fetch from a
    // shared deep link. The refusal is not an error to show them — the reader
    // guide is the page they were promised.
    if (target === 'admin' && err instanceof ForbiddenError) {
      which.value = 'reader'
      return
    }
    error.value = errorText(err, 'could not load the guide')
  } finally {
    loading.value = false
  }
}

watch(which, (next) => {
  void router.replace({ query: next === 'admin' ? { guide: 'admin' } : {} })
  shell.value?.scroller?.scrollTo({ top: 0 })
  void load(next)
})

onMounted(() => void load(which.value))

/** The slug the server computes for the guide's own `## Contents` heading. */
const CONTENTS_SLUG = 'contents'

/**
 * The document's own contents list, dropped -- the rail beside it IS that list.
 *
 * Rendered, the page showed its table of contents twice: once as a sticky rail
 * and again as the first thing in the prose, fourteen numbered links deep,
 * between the reader and the first sentence of the guide.
 *
 * Dropped HERE and not from `docs/USER-GUIDE.md`, because that file is also
 * read in a terminal, in an editor and on GitHub, where nothing supplies a rail
 * and the list is the only way to navigate. It is redundant in exactly one of
 * its four readers, so exactly one of them removes it.
 *
 * The trailing `---` goes too. Section boundaries are a single rule everywhere
 * else in this document, and dropping the section between two of them would
 * leave the doubled line that `HEADING_CLASS` below already had to solve once.
 */
const blocks = computed(() => {
  const kept: GuideBlock[] = []
  let inContents = false
  for (const block of received.value) {
    if (block.type === 'heading' && (block.level ?? 2) <= 2) {
      inContents = block.slug === CONTENTS_SLUG
    }
    if (inContents) continue
    // Collapse the pair of rules left either side of a removed section.
    if (block.type === 'rule' && kept[kept.length - 1]?.type === 'rule') continue
    kept.push(block)
  }
  return kept
})

/** `##` only. The guide has 28 `###` headings and a rail carrying all 45 is a
 *  second document rather than a way into this one. */
const sections = computed(() => blocks.value.filter((b) => b.type === 'heading' && b.level === 2 && b.slug))

/** Which section the reader is in, for the rail. */
const active = ref<string | null>(null)

/**
 * How far below the top of the viewport a heading counts as "arrived".
 *
 * Not zero: at zero a section becomes current only once its heading has left
 * the screen, so the rail lags a full heading behind the prose. A band near the
 * top means the highlight changes as the heading reaches the place a reader is
 * actually looking.
 */
const READING_LINE_PX = 96

/**
 * MEASURED, not an IntersectionObserver.
 *
 * The question is "which heading was the last one to pass the reading line",
 * which is a scan over positions. An observer answers "which are visible", and
 * turning that into this needs a rootMargin band tuned so that exactly one
 * heading is ever inside it -- which no band achieves for both a two-paragraph
 * section and a four-screen one, and this guide has both.
 *
 * The scroll container is `ViewShell`'s element rather than the window
 * (`scroll="page"` sets `overflow-y: auto` on it), so every offset here is
 * relative to that box and not to the document.
 */
function recomputeActive() {
  const scroller = shell.value?.scroller
  if (!scroller || !sections.value.length) return

  const top = scroller.getBoundingClientRect().top
  // The last section wins outright once there is no more scrolling to do: a
  // short final section can never reach the reading line, and a rail that
  // refuses to highlight the section you are looking at reads as broken.
  if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 2) {
    active.value = sections.value[sections.value.length - 1].slug ?? null
    return
  }

  let current = sections.value[0].slug ?? null
  for (const section of sections.value) {
    const element = section.slug ? document.getElementById(section.slug) : null
    if (!element) continue
    if (element.getBoundingClientRect().top - top > READING_LINE_PX) break
    current = section.slug ?? current
  }
  active.value = current
}

// Coalesced to one measurement per frame. A scroll listener that measures on
// every event reads layout dozens of times a frame, and this one runs over
// fourteen elements.
let queued = 0
function onScroll() {
  if (queued) return
  queued = requestAnimationFrame(() => {
    queued = 0
    recomputeActive()
  })
}

watch(
  () => shell.value?.scroller,
  (scroller, previous) => {
    previous?.removeEventListener('scroll', onScroll)
    scroller?.addEventListener('scroll', onScroll, { passive: true })
  },
  { immediate: true },
)

// The blocks arrive after mount, so the first measurement has to wait for the
// headings to exist rather than happening in `onMounted`.
watch(sections, () => requestAnimationFrame(recomputeActive), { flush: 'post' })

onBeforeUnmount(() => {
  if (queued) cancelAnimationFrame(queued)
  shell.value?.scroller?.removeEventListener('scroll', onScroll)
})

function jump(slug: string, event: Event) {
  event.preventDefault()
  // Set immediately rather than waiting for the smooth scroll to arrive: the
  // click IS the reader saying where they are, and a highlight that crawls to
  // catch up looks like lag.
  active.value = slug
  document.getElementById(slug)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  // `replaceState`, not the router: fourteen contents clicks should not put
  // fourteen entries between the reader and the page they came from.
  history.replaceState(null, '', `#${slug}`)
}

/** Heading level to element. `level` is 1–3 in this document; anything deeper
 *  renders as an h4 rather than producing an invalid tag from arithmetic. */
function headingTag(level: number | null | undefined) {
  const n = Math.min(Math.max(level ?? 2, 1), 4)
  return `h${n}`
}

/**
 * Callout frames, by tone.
 *
 * The SAME vocabulary `StatusNote` uses, and for the reason its docstring
 * gives: `info` takes the accent wash, and the two alarming tones take their
 * signal colour on a neutral ground rather than inventing `--wash-critical` for
 * one component. A reader who has learned what an amber left rule means on the
 * Runs page should not have to learn it again here.
 *
 * A static map because Tailwind scans source text -- a computed
 * `border-l-signal-${tone}` is never generated, and the failure looks like the
 * server sent no tone.
 */
const CALLOUT_CLASS: Record<string, string> = {
  info: 'bg-accent-wash border-l-accent-muted text-text-secondary',
  warn: 'bg-surface-inset border-l-signal-high text-text-secondary',
  stop: 'bg-surface-inset border-l-signal-critical text-text-secondary',
}

/** The type scale, by level. Written as a lookup rather than a computed class
 *  string because Tailwind scans source text — an interpolated `text-${size}`
 *  is never generated and the failure looks like a data bug.
 *
 *  ACCENT throughout, and the SIZE is what carries hierarchy. A reader scanning
 *  for a section is looking for its heading, so the headings are the one thing
 *  on the page that should be findable without reading — which is the same job
 *  the accent does in the rail beside them. */
const HEADING_CLASS: Record<number, string> = {
  1: 'text-2xl text-accent-primary mt-0 mb-4',
  // No top border: the guide separates its own sections with `---`, which
  // renders as a rule, and the two together drew a doubled line with a
  // band of dead space between them on every one of 14 sections.
  2: 'text-lg text-accent-primary mt-6 mb-3',
  3: 'text-md text-accent-primary mt-5 mb-2',
  4: 'text-base text-accent-primary mt-4 mb-2',
}
</script>

<template>
  <ViewShell ref="shell" width="wide" :loading="loading" :error="error" identity="guide">
    <!-- Readers never see the switch: the admin guide describes controls a
         reader cannot reach, and a segmented control with one reachable
         segment is a question the page cannot answer. -->
    <template v-if="session.isAdmin()" #controls>
      <SegmentedControl v-model="which" :options="OPTIONS" ariaLabel="which guide" />
    </template>

    <EmptyState v-if="!loading && !error && !blocks.length" title="The guide is not installed.">
      The guide has not been installed here. The app is unaffected.
    </EmptyState>

    <div v-else class="flex gap-6 items-start">
      <!-- The rail is the way IN. `sticky` so it survives a 200-block scroll,
           and hidden below the wide breakpoint where it would push the prose
           measure below readable.

           SAME FRAME AND SAME ROWS AS THE ADMIN AND SETTINGS RAILS: an inset
           panel of `border-l-2` rows, the current one carrying the accent edge
           and the selected ground. "Where am I" is answered one way across the
           app; all three compose `Rail` and `RailLink`, and what is local here
           is only the placement -- a sticky fixed-width column that leaves
           below `lg`, where the guide is one column of prose. -->
      <Rail
        v-if="sections.length"
        label="Guide contents"
        name="guide"
        class="flex-none w-(--rail-width) sticky top-0 max-lg:hidden"
      >
        <template #header>
          <span class="font-ui text-2xs uppercase tracking-label text-text-quiet px-3 py-2">contents</span>
        </template>
        <!-- Anchors, not buttons. A contents entry IS a link to a place in this
             document -- it should carry an href a reader can copy, open in a
             new tab, or middle-click, and `components/ui/Button.vue` is for
             actions. `jump` only takes over the scroll so it is smooth and so
             the hash replaces rather than pushes. -->
        <!-- `RailLink`, the same row the admin and settings rails are built
             from -- the accent edge is what "you are here" means everywhere
             else in this app, and this rail restated all six of its utilities
             to say so. It renders an `<a>` rather than a `RouterLink` here,
             which is why the primitive takes `href` as well as `to`. -->
        <RailLink
          v-for="s in sections"
          :key="s.slug!"
          :href="`#${s.slug}`"
          :active="active === s.slug"
          @click="jump(s.slug!, $event)"
          >{{ s.text }}</RailLink
        >
      </Rail>

      <article class="guide min-w-0 flex-1 text-text-body">
        <template v-for="(block, i) in blocks" :key="i">
          <component
            :is="headingTag(block.level)"
            v-if="block.type === 'heading'"
            :id="block.slug ?? undefined"
            :class="HEADING_CLASS[Math.min(Math.max(block.level ?? 2, 1), 4)]"
          >
            <GuideRuns :runs="block.runs ?? []" />
          </component>

          <!-- `font-prose`. The guide is the longest continuous reading in the
               app after the brief, and it was set in the chrome face: mono for
               the instrument, Inter for the argument, and several hundred words
               of explanation are an argument. -->
          <p
            v-else-if="block.type === 'paragraph'"
            class="my-3 font-prose text-sm leading-prose tracking-tight text-pretty"
          >
            <GuideRuns :runs="block.runs ?? []" />
          </p>

          <!-- Depth as an indent step, not a nested <ul>. The server sends a
               flat list with a depth, so rendering a real tree would mean
               rebuilding one here for no gain a reader can see. -->
          <div
            v-else-if="block.type === 'list_item'"
            class="my-1 font-prose text-sm leading-prose tracking-tight text-pretty flex gap-2"
            :class="(block.depth ?? 1) > 1 ? 'ml-6' : 'ml-2'"
          >
            <!-- The marker is the item's own number when it has one. A
                 numbered list rendered as bullets loses the cross-reference,
                 and this document refers to its sections by number. -->
            <span class="flex-none text-text-quiet select-none mono text-xs">{{
              block.ordered && block.number ? `${block.number}.` : '·'
            }}</span>
            <span class="min-w-0"><GuideRuns :runs="block.runs ?? []" /></span>
          </div>

          <!-- A CALLOUT, not a quotation. The guide quotes nobody: every
               blockquote in it is an aside the reader must not skim past, which
               is why the source marks them with GitHub alert syntax and the
               server sends a tone.

               The left edge is DASHED, borrowed from the dashed underline the
               app puts under an acronym it cannot ground — the same visual verb
               for "read this before you act on it" in both places.

               `first`/`last` draw ONE frame around a multi-paragraph note. The
               padding is split across them and the ground is on every block, so
               two paragraphs read as one box rather than two with a seam. -->
          <blockquote
            v-else-if="block.type === 'blockquote'"
            class="px-4 border-l-2 border-dashed text-sm"
            :class="[
              CALLOUT_CLASS[block.tone ?? 'info'],
              block.first ? 'mt-4 pt-3 rounded-t-sm' : '',
              block.last ? 'mb-4 pb-3 rounded-b-sm' : '',
              !block.first && !block.last ? 'py-1' : '',
            ]"
          >
            <GuideRuns :runs="block.runs ?? []" />
          </blockquote>

          <pre
            v-else-if="block.type === 'code'"
            class="my-3 p-3 rounded-md bg-surface-inset overflow-x-auto mono text-xs text-text-secondary"
            >{{ block.text }}</pre>

          <!-- A FRAMED table. Nine tables carry most of this document's real
               content -- the conflict kinds, the key bindings, what each page
               answers -- and unframed they ran edge to edge into the prose, so
               a 12-row table read as more body copy. The frame is what says
               "this is a reference you scan", and the inset header row is what
               separates the column names from the first answer.

               `overflow-x-auto` on the wrapper, not the table: a wide table must
               scroll inside its own box rather than making the page scroll
               sideways. -->
          <div
            v-else-if="block.type === 'table'"
            class="my-4 overflow-x-auto border border-border-default rounded-md bg-surface-raised"
          >
            <table class="w-full border-collapse text-sm">
              <thead>
                <tr v-for="(row, r) in block.head ?? []" :key="`h${r}`">
                  <th
                    v-for="(cell, c) in row"
                    :key="c"
                    class="text-left align-top font-ui text-2xs uppercase tracking-label text-text-tertiary bg-surface-inset border-b border-b-border-default px-3 py-2 whitespace-nowrap"
                  >
                    <GuideRuns :runs="cell" />
                  </th>
                </tr>
              </thead>
              <tbody>
                <!-- `last:border-b-0`: the wrapper already draws the bottom
                     edge, and a row rule under it doubled the line. -->
                <tr
                  v-for="(row, r) in block.rows ?? []"
                  :key="r"
                  class="border-b border-b-border-subtle last:border-b-0"
                >
                  <!-- The first column is the term being defined, so it takes
                       the primary rung and the rest stay secondary.

                       Stays in the chrome face: a reference table is an
                       instrument, and its cells are looked up rather than read
                       through. `leading-relaxed` was a dead class -- `theme.css`
                       clears `--leading-*` and redefines four rungs, and that is
                       not one of them, so this cell has been inheriting the body
                       leading all along. -->
                  <td
                    v-for="(cell, c) in row"
                    :key="c"
                    class="align-top px-3 py-2 leading-normal"
                    :class="c === 0 ? 'text-text-primary' : 'text-text-secondary'"
                  >
                    <GuideRuns :runs="cell" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <hr v-else-if="block.type === 'rule'" class="my-6 border-0 border-t border-t-border-subtle" />
        </template>
      </article>
    </div>
  </ViewShell>
</template>
