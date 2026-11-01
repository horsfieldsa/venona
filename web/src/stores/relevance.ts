import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiDelete, apiGet, apiPatch, apiPost, apiPut, errorText } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { useSessionStore } from '@/stores/session'
import type { components } from '@/api/schema'
import type { EntityHit, EntitySearchResults } from '@/types/search'

type RubricMeta = components['schemas']['RubricMeta']
type RelevanceSettings = components['schemas']['RelevanceSettings']
type FollowList = components['schemas']['FollowList']
type FollowSuggestions = components['schemas']['FollowSuggestions']
type ForYou = components['schemas']['ForYou']
type RunStarted = components['schemas']['RunStarted']

/** The same pause the glossary's filter uses: long enough to swallow a burst of
 *  typing, short enough that a deliberate pause feels answered. */
const QUERY_DEBOUNCE_MS = 200

/** How often the waiting surface asks whether the brief has landed, and for how
 *  long. One light-lane call takes a minute or two; four minutes of 5s polls
 *  outlasts a slow one without holding a spinner over a run that died. Past it
 *  the reader is told it is still coming, which is true. */
const WATCH_INTERVAL_MS = 5000
const WATCH_ATTEMPTS = 48

/**
 * The reader's personal relevance: their emphasis over what Venona scores,
 * the names they follow, and their own reading list and brief.
 *
 * Every request here is about the ACTING user -- no route takes a user id --
 * so nothing in this store can touch anyone else's state. Held in a store
 * rather than the views because the welcome page, the settings page, the
 * hover card's follow button and the brief all read the same follows.
 */
export const useRelevanceStore = defineStore('relevance', () => {
  const rubric = ref<RubricMeta | null>(null)
  const settings = ref<RelevanceSettings | null>(null)
  const follows = ref<NonNullable<FollowList['items']>>([])
  const suggestions = ref<NonNullable<FollowSuggestions['items']>>([])
  const forYou = ref<ForYou | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const generating = ref(false)
  const error = ref('')

  /** The tailor surfaces' whole read, one round of parallel requests. */
  async function loadTailor(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const [rubricData, settingsData, followsData, suggestionsData] = await Promise.all([
        rubric.value ? Promise.resolve(rubric.value) : apiGet<RubricMeta>('/meta/rubric'),
        apiGet<RelevanceSettings>('/relevance/settings'),
        apiGet<FollowList>('/relevance/follows'),
        // A dozen, not the route's cap: the picker is an invitation, and a
        // wall of chips reads as a database table.
        apiGet<FollowSuggestions>('/relevance/suggestions?limit=12'),
      ])
      rubric.value = rubricData
      settings.value = settingsData
      follows.value = followsData.items
      followsLoaded.value = true
      suggestions.value = suggestionsData.items
    } catch (err) {
      error.value = errorText(err, 'could not load your relevance settings')
    } finally {
      loading.value = false
    }
  }

  async function saveEmphasis(emphasis: Record<string, string>): Promise<boolean> {
    saving.value = true
    error.value = ''
    try {
      settings.value = await apiPatch<RelevanceSettings>('/relevance/settings', { emphasis })
      // The welcome gate reads `session.relevanceTailored`, which rides the
      // session payload -- refresh it so saving is what ends the invitation.
      await useSessionStore().load()
      return true
    } catch (err) {
      error.value = errorText(err, 'could not save')
      return false
    } finally {
      saving.value = false
    }
  }

  async function reset(): Promise<void> {
    saving.value = true
    error.value = ''
    try {
      settings.value = await apiPatch<RelevanceSettings>('/relevance/settings', { reset: true })
      await useSessionStore().load()
    } catch (err) {
      error.value = errorText(err, 'could not reset')
    } finally {
      saving.value = false
    }
  }

  /** True once `follows` reflects the server, by either load path. */
  const followsLoaded = ref(false)

  /** The hover card's read: just the follows, not the whole tailor payload. */
  async function loadFollows(): Promise<void> {
    if (followsLoaded.value) return
    followsLoaded.value = true
    try {
      follows.value = (await apiGet<FollowList>('/relevance/follows')).items
    } catch {
      // The card degrades to an un-followed state; a later card retries.
      followsLoaded.value = false
    }
  }

  function isFollowed(entityId: string): boolean {
    return follows.value.some(
      (f) => f.entity_id === entityId || f.resolved_id === entityId,
    )
  }

  async function follow(entityId: string): Promise<void> {
    try {
      follows.value = (
        await apiPut<FollowList>(`/relevance/follows/${encodeURIComponent(entityId)}`)
      ).items
    } catch (err) {
      error.value = errorText(err, 'could not follow that name')
    }
  }

  async function unfollow(entityId: string): Promise<void> {
    try {
      follows.value = (
        await apiDelete<FollowList>(`/relevance/follows/${encodeURIComponent(entityId)}`)
      ).items
    } catch (err) {
      error.value = errorText(err, 'could not stop following that name')
    }
  }

  /** The picker's search half: the suggestions are an opening offer, and
   *  anything Venona has read about is followable by finding it. */
  const searchHits = ref<EntityHit[]>([])
  const searching = ref(false)

  const searchGuard = useStaleGuard()

  /**
   * Debounced and guarded HERE rather than in the picker, so every caller
   * inherits both. Type "an" then "and": the shorter query matches more rows and
   * so answers last, and its results land over the ones that match what is
   * actually in the box.
   *
   * The debounce is the ticket, not a cancelled timer. Every call waits out the
   * pause and only the newest is still current afterwards, so three keystrokes
   * issue one request -- and each call's promise still settles, which a
   * `clearTimeout` on a promise-wrapped timer would leave hanging forever.
   */
  async function searchCorpus(q: string): Promise<void> {
    const trimmed = q.trim()
    const isCurrent = searchGuard.claim()
    if (!trimmed) {
      // Claimed on the way out too: a response already in flight must not
      // repopulate a box the reader has just cleared.
      searchHits.value = []
      searching.value = false
      return
    }
    await new Promise<void>((resolve) => setTimeout(resolve, QUERY_DEBOUNCE_MS))
    if (!isCurrent()) return

    searching.value = true
    try {
      const data = await apiGet<EntitySearchResults>(
        `/search?q=${encodeURIComponent(trimmed)}&limit=8`,
      )
      if (!isCurrent()) return
      searchHits.value = data.entities ?? []
    } catch {
      // A failed search leaves the last results; the picker is a
      // convenience, not a page that needs its own error state.
    } finally {
      if (isCurrent()) searching.value = false
    }
  }

  async function loadForYou(): Promise<void> {
    try {
      forYou.value = await apiGet<ForYou>('/briefs/for-you')
    } catch (err) {
      error.value = errorText(err, 'could not load your reading')
    }
  }

  /**
   * Read the payload and act on what it says: join a run already going, or
   * start the one the server says is due.
   *
   * One place, because three callers want the same two-branch shape -- first
   * paint, a new shared brief landing under an open tab, and a reader coming
   * back to the tab -- and a second copy of it would be a second answer to
   * "is one already running".
   */
  async function refreshForYou(): Promise<void> {
    await loadForYou()
    if (forYou.value?.generating) void watchForBrief()
    else void autoGenerate()
  }

  /**
   * New shared reading landed while the reader was sitting here. Theirs is
   * derived from it, so the payload has moved even though nothing in this tab
   * touched it, and the freshness poll already pauses on a hidden tab and
   * fires the moment it comes back.
   *
   * Guarded on having loaded once: the first read belongs to whichever surface
   * asked for it, and firing here before that would spend on a reader who has
   * not opened the reading page.
   */
  onFreshness('briefs', () => {
    if (forYou.value) void refreshForYou()
  })

  /** One deliberate spend: write (or refresh) the reader's own brief. */
  async function generate(): Promise<RunStarted | null> {
    generating.value = true
    error.value = ''
    try {
      return await apiPost<RunStarted>('/briefs/for-you/generate')
    } catch (err) {
      error.value = errorText(err, 'could not start writing your brief')
      return null
    } finally {
      generating.value = false
    }
  }

  /**
   * Start the reader's brief without being asked, when the server says one is
   * due.
   *
   * THE SERVER DECIDES, THIS ACTS. Every brake lives in `auto_due` -- the daily
   * cap, "this reading is already written", "nothing to write about", "no new
   * reading since yours" -- so this must never second-guess it with its own,
   * which would be a second copy of the rule that could disagree with the one
   * the POST enforces.
   *
   * `autoStarted` is not a brake, it is a stampede guard for one wait:
   * `auto_due` stays true until the run's row lands, so two `loadForYou` calls
   * a moment apart would both fire. It clears when the wait ends, because the
   * next edition is a new question. The launcher coalesces the duplicates onto
   * one run anyway; this saves the second request.
   */
  const autoStarted = ref(false)
  async function autoGenerate(): Promise<void> {
    if (!forYou.value?.auto_due || autoStarted.value) return
    autoStarted.value = true
    const started = await generate()
    if (!started) {
      // A refusal here is silent BY DESIGN. Nobody asked for this, so there is
      // no action to report the failure of; the reader keeps the ranked list
      // and the button that says what it will do. The manual path still speaks.
      autoStarted.value = false
      return
    }
    void watchForBrief()
  }

  /**
   * Poll until the brief lands, and report whether it did.
   *
   * IN THE STORE, NOT THE VIEW, because the run outlives the component: the
   * reader may switch to the archive, open the graph, or reload, and the wait
   * has to survive all three. A loop inside a click handler dies with the
   * component, leaving a page that looks idle over a run still going.
   *
   * Polls `/briefs/for-you` rather than the run: `GET /api/runs/{id}` is
   * admin-only, so `waitForRun` answers a reader 403. The payload is what the
   * reader wants anyway.
   */
  const watching = ref(false)
  const landed = ref<'fresh' | 'stale' | null>(null)
  async function watchForBrief(): Promise<void> {
    if (watching.value) return
    watching.value = true
    const before = forYou.value?.brief?.generated_at ?? null
    // READ BEFORE THE FIRST SLEEP. The waiting state is driven by the server's
    // `generating`, so sleeping first leaves a whole interval in which the
    // reader has pressed a button and nothing anywhere says so -- which is the
    // silence the button existed to break.
    await loadForYou()
    try {
      for (let attempt = 0; attempt < WATCH_ATTEMPTS; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, WATCH_INTERVAL_MS))
        await loadForYou()
        const after = forYou.value?.brief?.generated_at ?? null
        if (after && after !== before) {
          // Stale ON ARRIVAL means the reader changed their emphasis while this
          // was being written, so the document is already behind them. The
          // caller says so rather than announcing it as ready.
          landed.value = forYou.value?.stale ? 'stale' : 'fresh'
          return
        }
        // The run ended without writing -- refused, or nothing usable came
        // back. `generating` going false is the only signal of that a reader
        // can see, and waiting out the full timeout after it would be a
        // spinner over a finished run.
        if (!forYou.value?.generating && attempt > 0) return
      }
    } finally {
      watching.value = false
      autoStarted.value = false
    }
  }

  /** Clear the landing signal once a view has spoken it. */
  function clearLanded(): void {
    landed.value = null
  }

  return {
    rubric, settings, follows, suggestions, forYou, searchHits, searching,
    loading, saving, generating, error, watching, landed,
    loadTailor, loadFollows, saveEmphasis, reset, isFollowed, follow, unfollow,
    searchCorpus, loadForYou, refreshForYou, generate, autoGenerate, watchForBrief,
    clearLanded,
  }
})
