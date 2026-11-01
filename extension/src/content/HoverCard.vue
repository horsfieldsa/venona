<script setup lang="ts">
/**
 * The hover card, on somebody else's page.
 *
 * The body is `EntityCardBody.vue` from the app, mounted unchanged. That is the
 * point of keeping the extension in this repo: the definition, the grounding
 * quote, the provenance wording and the mini-graph cannot drift from what a
 * brief shows, because they are the same component reading the same C8 payload.
 *
 * What is NOT ported from `EntityHoverCard.vue`:
 *
 *   - the `--nav-height` ceiling. There is no Venona nav on DefenseScoop; the
 *     clamp is to `GAP`.
 *   - the shared dismissable stack. One consumer here, so Escape is a listener
 *     of its own -- in the CAPTURE phase, because a host page that binds Escape
 *     would otherwise eat it before we saw it.
 *   - `Teleport to="body"`. The host is a shadow root on
 *     `document.documentElement`; some sites replace `body` outright.
 *
 * Everything else -- the flip, the viewport clamp, the ResizeObserver, the
 * capture-phase scroll listener -- is carried over as-is, because all of it is
 * about a rectangle near another rectangle and none of it knew about the app.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import Mark from '@/components/brand/Mark.vue'
import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { entityColourVar } from '@/lib/entityColour'

import { cardFor, fetchOne, invalidate, isMissing, signedOut } from './cards'
import * as hover from './hover'
import { ask } from './port'

const GAP = 8
/** Pre-measure fallback only. The width is `--card-w`, applied as a class and
 *  resolved from the `:host` block `emit-styles.mjs` generates -- the app card
 *  reads the same token the same way, so the two cannot drift to different
 *  widths again. */
const CARD_W_FALLBACK = 400

const cardEl = ref<HTMLElement | null>(null)
const position = ref({ top: 0, left: 0 })

const card = computed(() => cardFor(hover.activeId.value))

/** A 404 must not spin forever, so `missing` is part of the condition. */
const loading = computed(
  () =>
    hover.open.value &&
    hover.activeId.value !== null &&
    card.value === null &&
    !isMissing(hover.activeId.value),
)

watch(loading, (isLoading) => {
  const id = hover.activeId.value
  if (isLoading && id) void fetchOne(id)
})

function place(): void {
  const element = hover.anchor.value
  if (!element) return
  const rect = element.getBoundingClientRect()
  const height = cardEl.value?.offsetHeight ?? 260
  const width = cardEl.value?.offsetWidth || CARD_W_FALLBACK

  const below = rect.bottom + GAP
  const flip = below + height > window.innerHeight && rect.top - GAP - height > 0

  let left = rect.left + rect.width / 2 - width / 2
  left = Math.max(GAP, Math.min(left, window.innerWidth - width - GAP))

  // Placed before the definition, sources and mini-graph have landed, so it
  // grows after the fact; without the clamp the bottom runs off screen, which
  // is exactly where the mini-graph is.
  let top = flip ? rect.top - GAP - height : below
  top = Math.max(GAP, Math.min(top, window.innerHeight - height - GAP))

  position.value = { top, left }
}

watch(
  () => [hover.open.value, hover.activeId.value, card.value] as const,
  () => {
    if (hover.open.value) requestAnimationFrame(place)
  },
)

let observer: ResizeObserver | undefined
watch(cardEl, (element) => {
  observer?.disconnect()
  if (!element) return
  observer = new ResizeObserver(() => place())
  observer.observe(element)
})

function onScrollOrResize(): void {
  if (hover.open.value) place()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && hover.open.value) {
    hover.dismiss()
    event.stopPropagation()
  }
}

onMounted(() => {
  // Capture, so a nested scroller on the host page still repositions us.
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
  window.addEventListener('keydown', onKeydown, true)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
  window.removeEventListener('keydown', onKeydown, true)
})

// ── grounding an undefined term ────────────────────────────────────

type LookupState = 'idle' | 'queued' | 'running' | 'grounded' | 'rejected' | 'error'
const lookupState = ref<LookupState>('idle')
const lookupError = ref('')

/** Seeded from the card, so a term that has already been tried and failed
 * offers "Try again" rather than silently offering to spend money a second
 * time on the same question. */
watch(
  () => hover.activeId.value,
  () => {
    lookupState.value = card.value?.last_attempt_at ? 'rejected' : 'idle'
    lookupError.value = ''
  },
)

/** Per card OPEN, not per component mount or per active id: the component
 * mounts once per page, and re-opening the SAME mention leaves `activeId`
 * unchanged -- so either narrower trigger would keep serving a stale answer
 * after the worker downgraded on a 403. */
watch(
  () => hover.open.value,
  (open) => {
    if (!open) return
    void ask({ kind: 'capabilities' }).then((res) => {
      if (res.ok && res.kind === 'capabilities') gatedAvailable.value = res.data.full
    })
  },
)

/** Whether the credential can reach the gated routes at all. Defaults TRUE:
 * never-probed must not hide a working button while the answer is in flight,
 * and the worker downgrades on the first real 403. A paired token is always
 * annotate-scoped, so for paired readers this is simply false. */
const gatedAvailable = ref(true)

const canLookUp = computed(
  () =>
    gatedAvailable.value &&
    !!card.value &&
    !card.value.definition &&
    lookupState.value !== 'running',
)

const lookupLabel = computed(() => {
  if (lookupState.value === 'queued') return 'queued…'
  if (lookupState.value === 'running') return 'looking…'
  if (lookupState.value === 'rejected') return 'Try again'
  return 'Look this up'
})

async function lookUp(event?: MouseEvent): Promise<void> {
  // A real human gesture only. `lookUp` spends model budget, so a synthetic click
  // dispatched by page script -- the belt to the closed shadow root's braces --
  // must not reach it.
  if (event && !event.isTrusted) return
  const current = card.value
  if (!current) return
  lookupState.value = 'queued'
  lookupError.value = ''

  const res = await ask({
    kind: 'lookup',
    entityId: current.id,
    surface: current.surface ?? current.id,
    isEntity: current.kind === 'entity',
  })
  if (!res.ok || res.kind !== 'lookup') {
    lookupState.value = 'error'
    lookupError.value = res.ok ? 'unexpected reply' : res.error
    return
  }

  lookupState.value = res.data.status === 'queued' ? 'queued' : 'running'

  // Polled from HERE, not from the worker: the worker is killed after ~30s idle
  // and this can take three minutes. The port keeps it alive while we wait.
  const runId = res.data.run_id
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 1500))
    const poll = await ask({ kind: 'run', runId })
    if (!poll.ok || poll.kind !== 'run') continue
    const status = poll.data.status
    if (status === 'queued') continue
    if (status === 'running') {
      lookupState.value = 'running'
      continue
    }
    // Terminal.
    invalidate()
    await fetchOne(current.id)
    lookupState.value = cardFor(current.id)?.definition ? 'grounded' : 'rejected'
    return
  }
  lookupState.value = 'error'
  lookupError.value = 'the lookup did not finish in three minutes'
}

// ── open in Venona ─────────────────────────────────────────────────

const appUrl = ref('')
onMounted(async () => {
  const res = await ask({ kind: 'appUrl' })
  if (res.ok && res.kind === 'appUrl') appUrl.value = res.data.url
})

/** Hidden for `gloss:` ids: an acronym is not a graph node, so the graph has
 * nowhere to put it.
 *
 * `focus`, NOT `subject`. This is the one place in the project that builds a
 * graph URL by hand -- `web/src/router/graphRoute.ts` says so, and every in-app
 * producer goes through its `toGraphQuery` -- and it spelled the parameter
 * wrong. `fromGraphQuery` reads `focus`, so `subject` decoded to nothing, the
 * intent fell through to its `else`, and the link opened the ENTIRE graph
 * rather than the entity under the pointer. It looked like it worked.
 */
const graphHref = computed(() => {
  const current = card.value
  if (!current || !appUrl.value || current.id.startsWith('gloss:')) return null
  return `${appUrl.value}/graph?mode=focus&focus=${encodeURIComponent(current.id)}`
})

/**
 * The name's own page in Venona, which is the better landing place from
 * somebody else's article: everything this 308px card had to cut, at an address.
 *
 * NOT hidden for `gloss:`, unlike the graph link above. An acronym is not a
 * graph node, but it does have a page -- and for a term Venona cannot define,
 * that page is the only place listing the articles it appeared in.
 *
 * Built by hand here for the same reason the graph link is: the content script
 * shares no bundle with the app, so `web/src/router/entityRoute.ts` is not
 * reachable from inside a page's process. It is kept trivially simple for
 * exactly that reason -- the colon is a path separator and both halves of a C4
 * id are already URL-safe, so there is nothing here to spell wrong the way
 * `subject` was.
 */
const entityHref = computed(() => {
  const current = card.value
  if (!current || !appUrl.value) return null
  const [type, ...rest] = current.id.split(':')
  if (!type || !rest.length) return null
  return `${appUrl.value}/entity/${type}/${rest.join(':')}`
})

/** The footer is UNCONDITIONAL, and the reason it looks gateable is the reason
 * it must not be.
 *
 * A grounded acronym has neither button -- it needs no lookup and has no graph
 * node -- so the bar would be empty, and "an empty sticky bar reads as a control
 * that failed to load". The bar carries the Venona lockup, so it never is.
 *
 * That the grounded-acronym card is the one with no footer buttons is exactly
 * why the branding goes here: it is the card that would otherwise carry no mark
 * at all, on somebody else's page, which is the whole point of stamping it. `scripts/ext_smoke.py` asserts the lockup on that specific card.
 */
const hasActions = computed(
  () =>
    canLookUp.value ||
    graphHref.value !== null ||
    entityHref.value !== null ||
    lookupState.value === 'grounded' ||
    lookupError.value !== '',
)
</script>

<template>
  <div
    v-if="hover.open.value"
    ref="cardEl"
    class="fixed z-[2147483000] w-(--card-w) max-h-(--scroll-xl) overflow-y-auto px-4 pt-3 pb-0
           bg-surface-overlay border border-border-default rounded-md
           font-ui text-base leading-normal text-text-body"
    :style="{
      top: `${position.top}px`,
      left: `${position.left}px`,
      borderColor: card ? entityColourVar(card) : undefined,
    }"
    @mouseenter="hover.hold()"
    @mouseleave="hover.release()"
  >
    <p v-if="loading" class="m-0 py-4 text-sm text-text-tertiary">looking that up…</p>

    <!-- Order matters: signed out is checked FIRST. "No record of this" is a
         claim about the corpus, and making it when the API refused the request
         sends the reader looking for a missing entity instead of a refused
         token. -->
    <p v-else-if="signedOut" class="m-0 py-4 text-sm text-text-tertiary">
      Venona is signed out; reconnect in the extension's options.
    </p>

    <p v-else-if="!card" class="m-0 py-4 text-sm text-text-tertiary">
      Venona has no record of this.
    </p>

    <template v-else>
      <!-- No `@open` handler: there is no router in a shadow root on somebody
           else's page, so the browser follows the href, which is what
           `titleNewTab` is for. -->
      <EntityCardBody
        :card="card"
        :graph-width="308"
        :title-href="entityHref ?? ''"
        title-new-tab
      />

      <!-- Sticky, and the negative margin is deliberate: the card is a scroll
           container with px-4, so without it the opaque background stops short
           of the card's own edges and text slides visibly through the gap.
           `pb-0` above is the other half of the same fix. -->
      <!-- UNCONDITIONAL, and it has to stay that way. The card sets `pb-0`
           because this strip is what supplies its bottom padding, so gating the
           strip on `hasActions` leaves a grounded acronym's card with its last
           line of text flush against the border. That was tried; see the note
           on `hasActions` for why the footer stopped being conditional in the
           first place.

           `mr-auto` on the lockup pushes the ACTIONS right rather than the mark
           left, so the mark keeps its corner on a card with no actions at all --
           which is precisely the card that would otherwise carry no Venona mark
           on somebody else's page. -->
      <div
        class="sticky bottom-0 -mx-4 px-4 py-2 mt-3 flex items-center gap-2
               bg-surface-overlay border-t border-t-border-subtle"
      >
        <!-- `Mark`, the mark without the word. At 14px in the corner of somebody
             else's article it says which tool drew the underlines, which is the
             whole job; the word was ~82px of branding for the same sentence.

             This was `Wordmark`'s `mark-only` branch until the wordmark became
             the drawn lockup. `Wordmark` now INLINES ~4.5KB of path data --
             because the word has to take `currentColor` and no kit file pairs
             the aqua plate with a white wordmark -- so depending on it here
             would ship the whole drawing into the content bundle to render
             none of it.

             A component, not a hand-inlined SVG: the asset is 499 bytes, under
             Vite's 4096-byte `assetsInlineLimit`, so it bundles as a `data:` URI
             and needs no `web_accessible_resources` -- an `<img>` resolving
             against a file path would resolve against the HOST page's origin and
             render broken. -->
        <Mark :size="14" class="mr-auto shrink-0 opacity-70" />

        <!-- `IconButton`, the app card footer's vocabulary: the same two
             actions on the same card must not read as two different tools.
             Each `label` doubles as the hover title, so the word -- the busy
             wordings included -- still reaches the pointer. The graph link
             stays a real `<a>` (new tab, another origin) through `as="a"`:
             middle-click and open-in-new-window ride on `href`. -->
        <template v-if="hasActions">
        <IconButton
          v-if="canLookUp"
          :label="lookupLabel"
          :disabled="lookupState === 'queued' || lookupState === 'running'"
          bordered
          @click="lookUp($event)"
        >
          <Icon name="search" :size="12" />
        </IconButton>

        <!-- The name's page leads, and the graph keeps its own button.
             Repointing "Open in Venona" would have silently deleted the
             `ext_smoke` check that asserts `mode=focus` -- a check that exists
             because the parameter was once spelled `subject` and still rendered
             a graph, which is why it survived so long. -->
        <IconButton
          v-if="entityHref"
          as="a"
          :href="entityHref"
          label="Open in Venona"
          bordered
          target="_blank"
          rel="noopener noreferrer"
        >
          <Icon name="external" :size="12" />
        </IconButton>

        <IconButton
          v-if="graphHref"
          as="a"
          :href="graphHref"
          label="Open in the graph"
          bordered
          target="_blank"
          rel="noopener noreferrer"
        >
          <Icon name="waypoints" :size="12" />
        </IconButton>

        <span v-if="lookupState === 'grounded'" class="text-2xs mono text-accent-primary">
          grounded
        </span>
        <span v-else-if="lookupError" class="text-2xs mono text-signal-critical">
          {{ lookupError }}
        </span>
        </template>
      </div>
    </template>
  </div>
</template>
