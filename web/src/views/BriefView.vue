<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import AgeBadge from '@/components/shared/AgeBadge.vue'
import Masthead from '@/components/shared/Masthead.vue'
import StudyNudge from '@/components/learn/StudyNudge.vue'
import BriefBody from '@/components/brief/BriefBody.vue'
import TextRuns from '@/components/brief/TextRuns.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import EntityHoverCard from '@/components/entity/EntityHoverCard.vue'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useChromeRecede } from '@/composables/useChromeRecede'
import { useKeyBindings } from '@/composables/useKeyboard'
import { useIsRailless } from '@/composables/useBreakpoint'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useSchedule } from '@/composables/useSchedule'
import { isoDayLabel, timeLabel, zoneAbbrev } from '@/lib/dates'
import { useBriefsStore } from '@/stores/briefs'
import { useEntitiesStore } from '@/stores/entities'
import { useMentionEvents } from '@/composables/useMentionEvents'
import { useHoverStore } from '@/stores/hover'
import { useRelevanceStore } from '@/stores/relevance'
import { useRunsStore } from '@/stores/runs'
import { useSessionStore } from '@/stores/session'
import { useToastsStore } from '@/stores/toasts'
import { useUiStore } from '@/stores/ui'
import { tierWord } from '@/lib/relevanceCopy'
import { safeHref } from '@/lib/safeHref'
import Chip from '@/components/ui/Chip.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Icon from '@/components/ui/Icon.vue'
import Toggle from '@/components/ui/Toggle.vue'
import SectionHead from '@/components/ui/SectionHead.vue'

const briefs = useBriefsStore()
const entities = useEntitiesStore()
const hover = useHoverStore()
const mentions = useMentionEvents()
const runs = useRunsStore()
const session = useSessionStore()
const relevance = useRelevanceStore()
const toasts = useToastsStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const shell = ref<InstanceType<typeof ViewShell> | null>(null)

const routeId = computed(() => (route.params.id as string | undefined) ?? null)

// `apiDown` is the API being unreachable, not anything about briefs;
// `useServiceHealth` says why it is neither `navigator.onLine` nor `data.offline`.
const { apiDown, modelDown } = useServiceHealth()
const { nextBriefLine } = useSchedule()

/** The first-run branch of `#error`: something failed, the API is answering, and
 *  no specific brief was asked for -- so nothing is missing, there is nothing
 *  yet. Mirrors the `v-else` chain in that slot. */
const showFirstRun = computed(() => Boolean(briefs.error) && !apiDown.value && !routeId.value)

/**
 * In-flight dedupe, keyed on the route.
 *
 * `onActivated` and `watch(routeId)` BOTH fire when the graph returns to
 * `/briefs/:id` from `/` -- the cached component activates AND `routeId` changes
 * in the same tick. Two loads means two `restoreScroll()` calls, and they are not
 * idempotent: the first consumes `briefs.returnItemId` and scrolls to the
 * paragraph, the second finds it `null` and falls through to the saved
 * `scrollTop`, undoing it. The reader lands at the top, which looks exactly like
 * the bug the return trip exists to fix.
 *
 * Only the CONCURRENT pair is collapsed. A later activation of the same route
 * must still re-run -- that is what restores the reading position after a trip
 * to the graph, and what picks up a brief rewritten while the reader was away.
 */
let inFlight: { key: string; promise: Promise<void> } | null = null

function loadForRoute(): Promise<void> {
  const key = routeId.value ?? '@latest'
  if (inFlight?.key === key) return inFlight.promise

  const promise = (async () => {
    // The index is the archive's list, and the article does not come out of it:
    // the prev/next ids ride on the brief's own payload. Awaiting it here made
    // time-to-first-word the SUM of two round trips for no reader-visible gain.
    // It stays un-awaited rather than removed because `latestId` and `pathFor`
    // read it, and both are allowed to arrive a moment late.
    void briefs.loadIndex()
    if (routeId.value) await briefs.load(routeId.value)
    else await briefs.loadLatest()
    prefetchReferences()
    restoreScroll()
  })().finally(() => {
    if (inFlight?.promise === promise) inFlight = null
  })

  inFlight = { key, promise }
  return promise
}

/** ONE `cards:batch` request for every reference in the brief, during idle time,
 *  so that hovering is a Map lookup rather than a request per mention. The
 *  single-card read is the fallback for a hover that beats it (`stores/entities.ts`);
 *  ADR 0232 is why neither route is gated on entitlement. */
function prefetchReferences() {
  const brief = briefs.current
  if (!brief) return
  const ids = [...(brief.entities ?? []), ...(brief.glossary ?? [])]
  const run = () => void entities.prefetch(ids)
  if ('requestIdleCallback' in window) {
    ;(window as unknown as { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(run)
  } else {
    setTimeout(run, 0)
  }
}

/** The round trip this view exists inside: read, click a mention, look at the
 *  name in the graph, come back. `<KeepAlive>` keeps the component but not the
 *  scroller's position, and a reader who left from a specific item lands back on
 *  that item rather than on an offset that may since mean something else. */
function restoreScroll() {
  const brief = briefs.current
  const el = shell.value?.scroller
  if (!brief || !el) return

  const itemId = briefs.returnItemId
  briefs.returnItemId = null
  requestAnimationFrame(() => {
    if (itemId) {
      // `[data-item-id="…"]`, never `#id`: item ids contain colons and would
      // throw SyntaxError as a CSS id selector.
      const target = el.querySelector<HTMLElement>(`[data-item-id="${CSS.escape(itemId)}"]`)
      if (target) {
        target.scrollIntoView({ block: 'start' })
        return
      }
    }
    // Only when there IS a position to go back to. `?? 0` looks harmless and is
    // not: the brief loads after the view mounts, so this lands a frame or two
    // after arrival -- by which time a reader may already be scrolling, and it
    // yanks them back to the top. Nothing saved means nothing to restore.
    const saved = briefs.scrollTops[brief.id]
    if (saved !== undefined) el.scrollTop = saved
  })
}

function rememberScroll() {
  const brief = briefs.current
  const el = shell.value?.scroller
  if (brief && el) briefs.rememberScroll(brief.id, el.scrollTop)
}

onActivated(() => void loadForRoute())
onDeactivated(() => {
  rememberScroll()
  // Leaving must not hand the next route a mode it did not ask for: one flag,
  // two callers, and whoever set it clears it. `GraphView` does the same.
  ui.exitFullscreen()
})
watch(routeId, () => void loadForRoute())

/**
 * A run that finishes while the reader is SITTING on this view.
 *
 * Neither other trigger fires in that case: `onActivated` needs a deactivation
 * first, `routeId` stays `null` on `/`, and `<KeepAlive include="BriefView,…">`
 * never rebuilds the component -- so the run completes, a new brief lands, and
 * the screen keeps showing the previous one until the reader navigates or
 * reloads. That was the reported bug.
 *
 * Index FIRST and both calls FORCED. `loadLatest` early-returns while
 * `latestId` is null, and `latestId` is `index[0]?.id` (`stores/briefs.ts`), so
 * against a stale or empty index it silently renders stale content.
 */
watch(
  () => runs.finished,
  (done) => {
    if (!done || routeId.value !== null) return
    void (async () => {
      await briefs.loadIndex(true)
      await briefs.loadLatest(true)
      prefetchReferences()
    })()
  },
)

/**
 * The reader's own surface, beside the shared one.
 *
 * `view` picks which document the article renders on `/` -- the archive
 * (`routeId` set) is always the shared record. Auto-switches to `personal`
 * ONCE, when the reader's document first arrives, and never after they touch
 * the toggle: a control that keeps snapping back is worse than no control.
 */
const view = ref<'personal' | 'shared'>('shared')
const touchedView = ref(false)
watch(view, () => {
  touchedView.value = true
})

const personalOffered = computed(() => session.relevanceTailored && !routeId.value)

/**
 * WHICH document was already written when this page loaded, not merely whether
 * one was. It decides whether the toggle may move on its own: arriving to a
 * finished brief and being shown it is the point, having the page swapped under
 * you mid-paragraph is not. A brief that lands mid-session routinely lands OVER
 * one that was here on arrival, because the reader's brief is rewritten for each
 * new edition, so the id is the only thing telling the two apart.
 *
 * `undefined` means the question is unanswered; `null` means it was answered and
 * there was no document.
 */
const arrivalBriefId = ref<string | null | undefined>(undefined)

/**
 * `flush: 'sync'` and declared FIRST, both load-bearing.
 *
 * The brief-id watcher below reads this flag, and Vue flushes watchers in a job
 * queued the moment `forYou` is assigned -- a microtask earlier than anything
 * chained off the loading promise, so setting the flag in a `.then` sets it
 * after its reader has already run and seen `null`. Synchronous, in creation
 * order, is the only arrangement where "was it there when we arrived" is
 * answered before "did it arrive".
 */
watch(
  () => relevance.forYou,
  (payload) => {
    if (payload && arrivalBriefId.value === undefined)
      arrivalBriefId.value = payload.brief?.id ?? null
  },
  { immediate: true, flush: 'sync' },
)

watch(
  () => session.relevanceTailored,
  (tailored) => {
    if (!tailored) return
    // Written for them before they got here, or waiting on a run that outlived
    // the last page: either way the reader is not asked to press anything.
    void relevance.refreshForYou()
  },
  { immediate: true },
)

watch(
  () => relevance.forYou?.brief?.id,
  () => {
    const doc = relevance.forYou?.brief
    if (!doc) return
    // Only THIS document, the one that was already there. A brief that lands
    // mid-session is announced by the toast below and waits behind the toggle;
    // it never takes the page away from whatever is being read.
    if (doc.id === arrivalBriefId.value && !touchedView.value && !routeId.value)
      view.value = 'personal'
    // Same idle-time prefetch the shared brief gets, so hovering the
    // reader's own document is a Map lookup too.
    void entities.prefetch([...(doc.entities ?? []), ...(doc.glossary ?? [])])
  },
)

/** The brief arrived while they were here. Say so once, and let them choose
 *  when to switch. Stale on arrival gets its own sentence: it means they
 *  changed what matters while it was being written, so calling it ready would
 *  be wrong. */
watch(
  () => relevance.landed,
  (how) => {
    if (!how) return
    toasts.push(
      how === 'stale'
        ? {
            kind: 'info',
            text: 'Your brief is ready, but you changed what matters while it was being written. Rewrite brings it up to date.',
          }
        : { kind: 'success', text: 'Your brief is ready.' },
    )
    relevance.clearLanded()
  },
)

/** Venona is writing this reader's brief right now, on `/`. */
const writingNow = computed(() => Boolean(relevance.forYou?.generating) && !routeId.value)

const showTailorNudge = computed(() => !showFirstRun.value && !session.relevanceTailored)

/**
 * The steer, while the brief is being written.
 *
 * A TOAST RATHER THAN AN IN-FLOW NOTICE, and the difference is whose space it
 * takes. The reading column belongs to the document; a banner above it pushes
 * the brief the reader came for down the page to describe work happening
 * elsewhere. The toast stack carries work in flight on every view and width, so
 * the signal follows a reader who wanders to the graph or the archive.
 *
 * Sticky because it names a CONDITION rather than an event, and dismissed here
 * rather than by the reader: it stops being true on its own, and a status that
 * outlives its truth is worse than no status.
 */
const writingToastId = ref<number | null>(null)
watch(writingNow, (writing) => {
  if (writing && writingToastId.value === null) {
    writingToastId.value = toasts.push({
      kind: 'running',
      sticky: true,
      text: 'Venona is writing your brief for today. The shared brief is ready to read now.',
    })
    return
  }
  if (!writing && writingToastId.value !== null) {
    toasts.dismiss(writingToastId.value)
    writingToastId.value = null
  }
})
onBeforeUnmount(() => {
  if (writingToastId.value !== null) toasts.dismiss(writingToastId.value)
})

/** What the article renders: the reader's document on `/` in personal view,
 *  the shared record everywhere else. */
const shownDoc = computed(() =>
  view.value === 'personal' && !routeId.value ? (relevance.forYou?.brief ?? null) : briefs.current,
)

/** A run is under way, whichever page started it. The server's answer rather
 *  than a local flag, so a reload during generation shows the truth instead of
 *  an idle page with a button that silently joins the run already going. */
const writing = computed(() => Boolean(relevance.forYou?.generating) || relevance.watching)

/**
 * Whether rewriting would DO anything.
 *
 * Two ways it would: the reader's inputs have moved (`stale`), or reading has
 * landed that their document was written before (`outdated`). With neither, the
 * run reaches `already_current` and returns having written nothing and called no
 * model -- but the daily allowance counts RUNS, so pressing it spends one of the
 * three (`llm.personal_briefs_per_day`) to accomplish precisely nothing. A
 * control that charges for a no-op is worse than an absent one.
 *
 * `outdated` is also the reader's way back when a generation fails: the
 * automatic path tries once per edition, so nothing else would offer them a
 * second attempt at the same reading.
 */
const canRewrite = computed(() =>
  Boolean(relevance.forYou?.stale || relevance.forYou?.outdated),
)

/** One deliberate spend. The waiting is the store's: the run outlives this
 *  component, and a loop here died with it. */
async function writeBrief() {
  const started = await relevance.generate()
  if (!started) {
    // The store knows why and nothing on this page renders it, so a refused
    // rewrite looked exactly like a button that does nothing.
    toasts.push({
      kind: 'error',
      text: relevance.error || 'could not start writing your brief',
    })
    return
  }
  await relevance.watchForBrief()
  // Running out of attempts is not the same as failing, and saying nothing is
  // the worst of the three: the spinner stops, the page is unchanged, and the
  // reader cannot tell whether it broke or is still going. It is still going.
  // `landed` is cleared by the watcher that speaks it, so an unset one here
  // means the poll gave up rather than saw the document.
  if (!relevance.forYou?.brief) {
    toasts.push({
      kind: 'info',
      text: 'Your brief is taking longer than usual. It appears here when it lands.',
    })
  }
}

const rewriteTitle = computed(() => {
  if (modelDown.value) return 'the model is not answering; try again soon'
  if ((relevance.forYou?.generations_left ?? 0) === 0)
    return 'your brief has been rewritten enough times today; the allowance resets tomorrow'
  return 'writes your brief again from the latest stories and your latest changes'
})

/** `brief · 2026-08-22 01:39 GMT+12 · 19 items`.
 *
 *  THE ONLY PLACE THIS DOCUMENT NAMES ITSELF. A second dateline in the article
 *  disagreed with this one the moment times became local: the bar showed the UTC
 *  editorial date, the dateline the instant in the reader's zone, and for a
 *  reader far enough east those are different days. One identity, and the day in
 *  it is the day the time beside it belongs to -- so it can sit one day off
 *  `briefs.id` and the archive's date column, which are the editorial label.
 *
 *  The time earns its place: several briefs can share a day, and without it
 *  prev/next between two of them looks like it did nothing. */
const identityStamp = computed(() => {
  const b = shownDoc.value
  if (!b?.generated_at) return b?.date ?? relevance.forYou?.date ?? ''
  const named = zoneAbbrev(b.generated_at)
  return `${isoDayLabel(b.generated_at)} ${timeLabel(b.generated_at)}${named ? ` ${named}` : ''}`
})

const identity = computed(() => {
  const at = identityStamp.value
  if (view.value === 'personal' && !routeId.value) {
    if (!at) return 'your brief'
    return itemCount.value ? `your brief · ${at} · ${itemCount.value} items` : `your brief · ${at}`
  }
  return briefs.current ? `brief · ${at} · ${itemCount.value} items` : 'brief'
})

const refCount = computed(
  () => (shownDoc.value?.entities?.length ?? 0) + (shownDoc.value?.glossary?.length ?? 0),
)

/** What the "no sourced definition yet" line counts. The shared document carries
 *  the model's unmatched names; the personal one carries none, because per-reader
 *  output must not feed the shared review queue (`services/personal_brief.py`),
 *  so its count is the dashed underlines themselves: distinct references still
 *  rendering as undefined across the document's runs. */
const undefinedCount = computed(() => {
  const doc = shownDoc.value
  if (!doc) return 0
  if (doc.unresolved_mentions?.length) return doc.unresolved_mentions.length
  const seen = new Set<string>()
  for (const section of doc.sections) {
    const blocks = [
      ...(section.blocks ?? []),
      ...(section.items ?? []).flatMap((i) => [...(i.summary ?? []), ...(i.why ?? [])]),
    ]
    const runs = [
      ...blocks.flatMap((b) => b.runs ?? []),
      ...(section.items ?? []).flatMap((i) => i.headline_runs ?? []),
    ]
    for (const run of runs) if (run.k === 'undefined' && run.r) seen.add(run.r)
  }
  return seen.size
})
const itemCount = computed(
  () => shownDoc.value?.sections.reduce((n, s) => n + (s.items?.length ?? 0), 0) ?? 0,
)

/** The counts behind the sentence, or null when the document names no sources
 *  at all -- which is what keeps the whole block off the rail. */
const freshness = computed(() => {
  const f = shownDoc.value?.freshness
  return f?.total ? f : null
})

/**
 * How recent the reporting is, as a sentence. `3 24h · 3 1-3d · 0 older` is a
 * log line: three abbreviations to decode for one thing the reader could simply
 * be told. Led by the freshest fact, because the question underneath is always
 * "is this current", and the answer wanted first is yes or no.
 */
const freshnessLine = computed(() => {
  const f = freshness.value
  if (!f) return ''
  const total = f.total
  const sources = total === 1 ? '1 source' : `${total} sources`
  const recent = f.under_24h + f.one_to_three_days
  if (f.older === 0 && f.one_to_three_days === 0) {
    return total === 1
      ? 'The one source is from the last day.'
      : `All ${total} sources are from the last day.`
  }
  if (f.older === 0) {
    return `${sources}, all from the last three days.`
  }
  if (recent === 0) {
    return `${sources}, none from the last three days.`
  }
  return `${sources}. ${recent} from the last three days, ${f.older} older.`
})

/**
 * What a reader can look up here, and how much of it Venona can actually
 * explain. A count of everything linked answers a question nobody asked; what
 * they want to know is whether the words they do not know will be explained, so
 * the count is the one with a traceable definition behind it, against the total.
 */
const definedLine = computed(() => {
  const total = refCount.value
  const undefined_ = undefinedCount.value
  if (!total) return ''
  if (!undefined_) {
    return total === 1
      ? 'One name here is explained from a source.'
      : `All ${total} names here are explained from a source.`
  }
  return `${total - undefined_} of ${total} names here are explained from a source.`
})

function goto(id: string | null) {
  // `pathFor`, not the id route: arrowing back to the newest brief belongs on
  // `/`, which is where the reader's own brief and the next-brief line live.
  if (id) void router.push(briefs.pathFor(id))
}

function jumpToItem(itemId: string) {
  const el = shell.value?.scroller
  const target = el?.querySelector<HTMLElement>(`[data-item-id="${CSS.escape(itemId)}"]`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/**
 * The chrome gets out of the way as the reading goes down the page.
 *
 * Installed here rather than in `ViewShell`, because it is not true of every
 * route: `ArchiveView` grows its own `scrollHeight` as it pages in more rows, so
 * the rule keyed on how much there is to scroll would flip state under a reader
 * who did nothing. This is a view that is READ THROUGH, and that is the whole of
 * the qualification.
 */
useChromeRecede(() => shell.value?.scroller ?? null)

/**
 * Focus: the chrome stays gone, and the rail folds with it.
 *
 * Offered from `lg` up only, and not as a lesser phone experience -- below that
 * breakpoint there is no rail to fold, so the switch would buy the chrome's 86px
 * (`--nav-height` plus `--subnav-height`) that reading down the page already
 * buys for free, and it would have no way out: a phone has no `Esc` and no top
 * edge to reach for. Simpler, not parity: ADR 0152.
 */
const railless = useIsRailless()
const canFocus = computed(() => !railless.value)
const focusMode = computed({
  get: () => ui.fullscreen,
  set: (on: boolean) => (on ? ui.toggleFullscreen() : ui.exitFullscreen()),
})

useKeyBindings([
  { keys: ['arrowleft'], label: 'Older brief', group: 'brief', run: () => goto(briefs.neighbours.older) },
  { keys: ['arrowright'], label: 'Newer brief', group: 'brief', run: () => goto(briefs.neighbours.newer) },
  { keys: ['a'], label: 'Archive', group: 'brief', run: () => void router.push('/briefs') },
  {
    keys: ['f'],
    label: 'Focus',
    group: 'brief',
    run: () => {
      if (canFocus.value) ui.toggleFullscreen()
    },
  },
])
</script>

<template>
  <!-- `wide` for the first-run screen, `prose` for a brief. The screen is a
       page, not an article, and `prose` would set it in the reading column --
       narrower than the `wide` column `EmptyCorpus` is drawn for. -->
  <ViewShell
    ref="shell"
    :width="showFirstRun ? 'wide' : 'prose'"
    scroll="page"
    :loading="briefs.loading"
    :error="briefs.error"
    :identity="identity"
  >
    <template v-if="showTailorNudge" #notice>
      <!-- The invitation, degraded to a nudge: the reader skipped the welcome
           page on this device, so it never gates again -- it just offers. -->
      <RouterLink
        to="/welcome"
        class="nudge flex items-center gap-2 rounded-md border border-accent-muted bg-surface-raised px-3 py-2 text-sm text-text-secondary hover:text-text-primary no-underline hover:no-underline"
      >
        <span>Tell Venona what matters to you and this reading gets ranked for you</span>
        <span aria-hidden="true" class="text-text-tertiary">→</span>
      </RouterLink>
    </template>

    <!-- Reading the brief is where the terms come from, so this is where "you
         could hold on to them" belongs, and nowhere on the first-run screen,
         where there is nothing to study yet. Renderless: it speaks through the
         toast stack, so it reaches every width and never takes the column. -->
    <StudyNudge v-if="!showFirstRun" />

    <!-- Which brief: the reader's own, or the shared record. Only offered on
         `/` -- the archive is always the shared document. -->
    <template v-if="personalOffered" #controls>
      <SegmentedControl
        v-model="view"
        :options="[
          {
            value: 'personal',
            label: 'for you',
            busy: writingNow,
            hint: writingNow ? 'Venona is writing your brief now' : undefined,
          },
          { value: 'shared', label: 'shared' },
        ]"
        ariaLabel="which brief"
      />
    </template>

    <!-- A switch, so it sits with the toggles at the trailing edge and not among
         the verbs: it stays on screen showing its state and it is not one of the
         things you are choosing between. That also keeps it clear of the actions
         zone, which older, newer and Rewrite already fill. -->
    <template v-if="canFocus" #toggles>
      <Toggle v-model="focusMode">Focus</Toggle>
    </template>

    <template #actions>
      <!-- Everything that acts on THIS brief sits together here, and none of it
           is filled: `actions` is where the filled vocabulary is ALLOWED, not
           where it is required.

           `bordered` because they sit among boxed controls rather than in a
           table row. The real `disabled` attribute, not a class that dims them:
           `IconButton` lets it fall through and styles it, and an unreachable
           control has to leave the tab order rather than sit in it doing nothing
           when pressed. -->
      <IconButton
        label="Older brief"
        bordered
        :disabled="!briefs.neighbours.older"
        @click="goto(briefs.neighbours.older)"
      >
        <Icon name="left" :size="12" />
      </IconButton>
      <IconButton
        label="Newer brief"
        bordered
        :disabled="!briefs.neighbours.newer"
        @click="goto(briefs.neighbours.newer)"
      >
        <Icon name="right" :size="12" />
      </IconButton>

      <!-- No Archive button here: it is the one thing in this bar that would not
           act on THIS brief, and `AppHeader`'s nav carries Archive on every view
           -- a positional target rather than a fourth button competing with the
           two that move between briefs. `a` goes there from the keyboard. -->

      <!-- The rewrite belongs to the reader's OWN brief: it writes their document
           again from today's stories and their latest emphasis and follows. The
           shared brief is the instance's record and has no rewrite control on the
           reading surface for anyone.

           ONLY WHEN IT WOULD CHANGE SOMETHING (`canRewrite`). Venona writes this
           brief unasked, so what the reader still decides is whether a document
           written before their latest changes should be caught up. Disabled
           rather than hidden once it IS offered, with the reason in the title,
           because it spends one of a small daily allowance and a mute disabled
           button reads as broken. -->
      <Button
        v-if="view === 'personal' && !routeId && shownDoc && canRewrite"
        variant="outline"
        icon="play"
        label="Rewrite"
        :disabled="writing || modelDown || (relevance.forYou?.generations_left ?? 0) === 0"
        :title="rewriteTitle"
        @click="writeBrief"
      >
        {{ writing ? 'Rewriting…' : 'Rewrite' }}
      </Button>
    </template>

    <!-- THREE STATES, not one, because the three want opposite answers: nothing
         read yet is a first run and wants a run started, a missing id is a bad
         link and wants the latest brief, and an unreachable API is not about
         briefs at all and wants nothing but an explanation. A single "No brief to
         show" covered all three and read as "the app is broken" in each. -->
    <template #error>
      <!-- Offline. Checked first: when the API is unreachable every other
           diagnosis here is a guess about a server that is not answering. -->
      <ApiUnreachable v-if="apiDown" class="mt-4">
        Reading works from what is already loaded; review and runs are paused until it
        comes back. Venona keeps trying on its own.
      </ApiUnreachable>

      <!-- A specific brief that is not there. Only when an id was asked for --
           without one there is nothing to be missing, which is the case below. -->
      <StateNotice
        v-else-if="routeId"
        status="no such brief"
        heading="There is no brief at that address"
        class="mt-4"
      >
        The link may be from a brief that was deleted, or a run may still be in progress.
        <template #action>
          <RouterLink to="/" class="inline-flex no-underline hover:no-underline"
            ><Button variant="outline" icon="right" label="Latest brief">Latest brief</Button></RouterLink
          >
        </template>
      </StateNotice>

      <!-- Nothing ingested. The first run. -->
      <EmptyCorpus v-else steps />
    </template>

    <!-- The side rail orients a reader without interrupting them. Every block
         reads the SHOWN document, so the reader's own brief carries the same rail
         as the shared one: its items link the same names and definitions.

         The contents list leads the rail, and below `lg` it leads the ARTICLE --
         it is the one part of the rail that is navigation rather than
         orientation, and a phone reader who scrolls past a meter, a contents list
         and a paragraph about references to reach the first sentence has been
         shown two screens of orientation with nothing yet to orient. -->
    <template v-if="shownDoc" #side-lead>
      <!-- Focus folds the rail to a strip, and the strip is the WAY OUT. A mode
           whose only exits are a key and a control it has itself put out of reach
           is a trap (ADR 0243). Here the way out is the thing that took the rail
           away, sitting in its space. -->
      <div v-if="ui.fullscreen" class="strip flex justify-start pt-1">
        <IconButton label="Show the contents" @click="ui.exitFullscreen()">
          <Icon name="minimize-2" :size="12" />
        </IconButton>
      </div>

      <!-- The Top N, unboxed and in the rail. It is a contents list, so it is
           shaped like one and stays with the reader instead of scrolling away; a
           filled, bordered, rounded callout of body text in the reading column is
           a docs-site move in an app meant to read as an instrument. -->
      <nav
        v-if="shownDoc.top?.length && !ui.fullscreen"
        class="rail-block py-4 border-t border-t-border-subtle first:border-t-0 first:pt-1"
      >
        <SectionHead as="h2" class="mb-3" :label="`Top ${shownDoc.top?.length}`" />
        <button
          v-for="(entry, i) in shownDoc.top"
          :key="entry.item_id"
          class="toc grid grid-cols-[2rem_1fr] gap-2 w-full text-left py-2 bg-transparent border-0 cursor-pointer font-ui text-sm leading-normal text-text-secondary hover:text-text-primary [&:hover_.toc\_\_n]:text-accent-primary"
          type="button"
          @click="jumpToItem(entry.item_id)"
        >
          <!-- Ordinal and age stack inside the gutter cell: as siblings of the
               line they fall into a third grid cell and wrap onto their own row. -->
          <span class="toc__n flex flex-col items-end gap-(--space-half) text-xs text-text-tertiary">
            <span class="mono">{{ String(i + 1).padStart(2, '0') }}</span>
            <AgeBadge :hours="entry.age_hours" tone="dim" />
          </span>
          <span class="toc__line line-clamp-3">{{ entry.line }}</span>
        </button>
      </nav>
    </template>

    <template v-if="shownDoc" #side>
      <!-- No `first:` exemption on the top rule, unlike the blocks around it.
           This block is `#side`'s first child but the contents list sits above
           it in the rail, so a `:first-child` rule would drop exactly the
           separator that divides them. Below `lg` the two are on opposite sides
           of the article and the rule reads as the appendix beginning. -->
      <section
        v-if="freshness && !ui.fullscreen"
        class="rail-block py-4 border-t border-t-border-subtle"
      >
        <SectionHead as="h2" class="mb-3" label="How recent" />
        <!-- NO METER. A proportion bar answers "how are these distributed",
             which is a question about a handful of sources that the sentence
             answers outright and in fewer marks. Three segments over six items
             is a chart drawn for a sample too small to have a shape. -->
        <p class="legend text-xs leading-normal text-text-tertiary">
          {{ freshnessLine }}
        </p>
      </section>

      <section
        v-if="refCount && !ui.fullscreen"
        class="rail-block py-4 border-t border-t-border-subtle first:border-t-0 first:pt-1"
      >
        <SectionHead as="h2" class="mb-3" label="References" />
        <p class="rail-note text-xs leading-normal text-text-tertiary">
          {{ definedLine }} Hover one to read it.
        </p>
        <!-- AGAINST THE TOTAL, never alone: "7 undefined" is a scold about the
             reading, "76 of 83 explained" is the same fact as coverage, and that
             is the half a reader wanted. NO MENTION OF THE UNDERLINE either --
             every name carries one and only the style differs, so naming it as if
             it marked these out reads as a claim about the others. -->
        <p
          v-if="undefinedCount"
          class="rail-note unresolved mt-2 text-xs leading-normal text-text-tertiary"
        >
          Venona can go and look the rest up.
        </p>
      </section>
    </template>

    <!-- The personal view before any document exists: the reader's list,
         instantly and for free, with the one spend offered above it. A
         degraded personal surface, never a blank one. -->
    <section v-if="view === 'personal' && !shownDoc && relevance.forYou" class="pb-8">
      <Masthead :parts="['your reading', 'ordered for you']" />
      <!-- Two sentences for two different situations, and they must not be
           merged: one says work is under way, the other says nothing is. The
           single "has not written yet" line covered both and read as a refusal
           on the very morning Venona was busy writing it. -->
      <p
        v-if="writingNow"
        class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-secondary"
      >
        Venona is writing your brief for today. It usually takes a minute or
        two, and it appears here when it lands. The shared brief is under the
        toggle above. Below is your reading in the meantime, ordered by what you
        said matters.
      </p>
      <p v-else class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-secondary">
        Venona has not written your brief yet today. This is your reading,
        ordered by what you said matters, with names you follow first. The
        shared daily brief is under the toggle above.
      </p>
      <!-- No button while a run is going: pressing it would coalesce onto that
           run and look like nothing happened. The line above is the status. -->
      <div v-if="!writingNow && (relevance.forYou.generations_left ?? 0) > 0" class="mt-3">
        <Button
          icon="play"
          :disabled="modelDown"
          :title="modelDown ? 'the model is not answering; try again soon' : undefined"
          @click="writeBrief"
        >
          Write my brief
        </Button>
      </div>
      <div v-else-if="writingNow" class="mt-3 flex items-center gap-2">
        <Spinner :size="12" />
        <span class="font-ui text-2xs uppercase tracking-label text-text-quiet">writing</span>
      </div>
      <div class="mt-5 flex flex-col">
        <article
          v-for="item in relevance.forYou.items ?? []"
          :key="item.cluster_id"
          class="py-3 border-t border-t-border-subtle first:border-t-0"
        >
          <div class="flex items-center gap-1 flex-wrap">
            <Chip size="sm" :tone="item.tier === 'must_read' ? 'accent' : 'neutral'">
              {{ tierWord(item.tier ?? 'background') }}
            </Chip>
            <Chip v-for="name in item.follows ?? []" :key="name" size="sm" tone="info">
              {{ name }}
            </Chip>
          </div>
          <h3 class="m-0 mt-1 font-ui text-md font-normal leading-snug">
            <a
              :href="item.url ?? undefined"
              target="_blank"
              rel="noopener noreferrer"
              class="text-text-primary no-underline hover:underline"
              >{{ item.headline }}</a
            >
          </h3>
          <p class="m-0 mt-1 mono font-ui text-2xs text-text-quiet">
            {{ [item.publisher, isoDayLabel(item.published_at)].filter(Boolean).join(' · ') }}
          </p>
          <p v-if="item.why" class="m-0 mt-2 font-prose text-sm leading-normal text-text-secondary">
            {{ item.why }}
          </p>
        </article>
      </div>
      <p v-if="!(relevance.forYou.items ?? []).length" class="mt-6 font-prose text-sm text-text-tertiary">
        Nothing in today's window yet. The shared brief may still have
        yesterday's reading.
      </p>
    </section>

    <!-- Their inputs moved since this was written; say so instead of presenting
         it as current, and say how it resolves -- a notice with no way out reads
         as a permanent condition. The action is the bar's Rewrite, not a second
         button here. -->
    <div
      v-if="view === 'personal' && shownDoc && relevance.forYou?.stale"
      class="mt-4 mb-6 flex items-center gap-3 flex-wrap"
    >
      <StatusNote kind="info" class="grow">
        Written before your latest changes.
        {{
          (relevance.forYou?.generations_left ?? 0) > 0
            ? 'Rewrite refreshes it now.'
            : "It refreshes with tomorrow's brief."
        }}
      </StatusNote>
    </div>

    <!-- `data-brief-id` for the same reason `BriefItem` carries `data-item-id`:
         ids contain colons and cannot be `#id` selectors. It also names the
         document on screen without anyone splitting a brief id back out of an
         item id; C3 makes both opaque. -->
    <article v-if="shownDoc" class="brief pb-8" :data-brief-id="shownDoc.id">
      <!-- The header binds the same delegated mention handlers as the body:
           the title's runs render `[data-entity-id]` spans, and this container
           is outside `BriefBody`'s. -->
      <header
        class="head mb-6"
        @mouseover="mentions.onPointerOver"
        @mouseout="mentions.onPointerOut"
        @focusin="mentions.onFocusIn"
        @focusout="mentions.onFocusOut"
        @click="mentions.onClick"
        @keydown="mentions.onKeydown"
      >
        <!-- `font-ui`, the mono face, like every other heading and like
             `BriefItem`'s headlines below it: a title set in a different family
             from the items it introduces reads as two documents one scroll apart,
             whatever the case for the prose face on an editorial title. Headings
             are structure; the dek underneath is the sentence, and keeps Inter. -->
        <h1 class="title font-ui text-xl font-normal leading-snug text-text-primary text-balance">
          <TextRuns
            v-if="shownDoc.title_runs?.length"
            :runs="shownDoc.title_runs"
            :active-id="hover.activeId"
          />
          <template v-else>{{ shownDoc.title }}</template>
        </h1>
        <p v-if="shownDoc.dek" class="dek mt-3 font-prose text-lg leading-normal text-text-secondary">
          {{ shownDoc.dek }}
        </p>
      </header>

      <BriefBody :sections="shownDoc.sections" :active-id="hover.activeId" />

      <footer
        v-if="shownDoc.sources?.length"
        class="sources mt-8 pt-4 border-t border-t-border-subtle font-ui"
      >
        <SectionHead as="h2" class="mb-3" label="Sources" />
        <!-- `component :is` rather than an href that may be absent: a source
             whose URL did not survive the scheme check must render as the
             citation it is, not as a link that goes nowhere. -->
        <component
          :is="safeHref(source.url) ? 'a' : 'div'"
          v-for="source in shownDoc.sources"
          :key="source.article_id"
          class="source grid grid-cols-[5.5rem_8rem_1fr] max-sm:grid-cols-1 max-sm:gap-0 gap-3 items-baseline py-1 text-text-secondary text-sm no-underline hover:text-text-primary hover:no-underline"
          :href="safeHref(source.url)"
          :target="safeHref(source.url) ? '_blank' : undefined"
          :rel="safeHref(source.url) ? 'noopener noreferrer' : undefined"
        >
          <span class="mono source__date text-xs text-text-tertiary">{{
            isoDayLabel(source.published_at)
          }}</span>
          <span class="source__pub text-xs text-text-tertiary">{{ source.publisher }}</span>
          <span class="source__title">{{ source.title }}</span>
        </component>
      </footer>
    </article>

    <!-- When the next one lands. Below the document, because a reader asks that
         having finished this one, and only on `/` -- a countdown under an
         archived brief from three weeks ago answers nothing anyone asked. -->
    <p
      v-if="!routeId && nextBriefLine"
      class="next-brief mt-6 pt-3 border-t border-t-border-subtle font-ui text-2xs text-text-quiet"
    >
      {{ nextBriefLine }}
    </p>

    <EntityHoverCard />
  </ViewShell>
</template>
