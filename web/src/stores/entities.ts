import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError, apiGet, apiPost } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import type { Card, CardBatchResponse } from '@/types/brief'

const TTL_MS = 10 * 60 * 1000
/**
 * How many cards the cache holds.
 *
 * It grew without limit: every hover on every brief, graph node and lookup row
 * added a card and never removed one, so a long reading session accumulated the
 * whole corpus a card at a time. 500 is comfortably more than any one brief's
 * distinct references (typically 20-60), so the working set survives eviction.
 */
const CARD_LIMIT = 500

/**
 * Card cache.
 *
 * The batch prefetch is the single highest-leverage performance decision in the
 * reader: BriefView collects every distinct reference on mount (typically
 * 20-60) and fetches them in ONE request during idle time. After that, hovering
 * is a Map lookup and the card paints instantly.
 *
 * In-flight promise de-duplication matters because a fast mouse traversal
 * across a dense paragraph can otherwise fire a dozen identical requests.
 */
export const useEntitiesStore = defineStore('entities', () => {
  const cards = ref(new Map<string, Card>())
  const missing = ref(new Set<string>())
  const fetchedAt = ref(new Map<string, number>())
  const inflight = new Map<string, Promise<Card | null>>()

  function isFresh(id: string): boolean {
    const at = fetchedAt.value.get(id)
    return at !== undefined && Date.now() - at < TTL_MS
  }

  function get(id: string): Card | null {
    return cards.value.get(id) ?? null
  }

  function store(id: string, card: Card) {
    // Deleted before set so a refetched card moves to the END of the Map.
    // Insertion order is then fetch order, which is what eviction walks --
    // and it has no ties, where `fetchedAt` does: a batch of sixty cards is
    // stored inside one millisecond and shares a single timestamp, so sorting
    // on it would evict members of the batch that was just fetched.
    cards.value.delete(id)
    fetchedAt.value.delete(id)
    cards.value.set(id, card)
    fetchedAt.value.set(id, Date.now())
    missing.value.delete(id)
    evictOldest()
  }

  /**
   * Oldest fetch first, so the working set of the open brief -- fetched
   * together in one batch -- survives as a group.
   *
   * Never evicts an id with a promise in `inflight`: that promise re-inserts on
   * resolve, so evicting it would produce a ghost. `missing` is untouched,
   * being a separate lifecycle that `invalidate` clears.
   */
  function evictOldest() {
    if (cards.value.size <= CARD_LIMIT) return
    for (const id of [...cards.value.keys()]) {
      if (cards.value.size <= CARD_LIMIT) break
      if (inflight.has(id)) continue
      cards.value.delete(id)
      fetchedAt.value.delete(id)
    }
  }

  /** Fetch many at once. Called on brief mount; safe to call repeatedly. */
  async function prefetch(ids: string[]): Promise<void> {
    const wanted = ids.filter((id) => !isFresh(id) && !missing.value.has(id))
    if (wanted.length === 0) return

    // The endpoint caps at 100 ids per request.
    //
    // Guarded, unlike `fetchOne` beneath it, which always was. This is called
    // from the brief's `onMounted`: an unhandled rejection there propagates out
    // of the awaiting caller and can take the whole mount with it, so a failed
    // hover-card prefetch would blank the brief -- the reading surface -- over
    // a decoration. A batch that fails simply leaves those cards unfetched, and
    // `fetchOne` picks them up on hover.
    for (let i = 0; i < wanted.length; i += 100) {
      const slice = wanted.slice(i, i + 100)
      try {
        const response = await apiPost<CardBatchResponse>('/entities/cards:batch', {
          ids: slice,
        })
        for (const [id, card] of Object.entries(response.cards)) store(id, card)
        for (const id of response.missing ?? []) missing.value.add(id)
      } catch {
        // Deliberately silent: nothing on screen depends on this having
        // succeeded, and a toast about a prefetch is noise.
      }
    }
  }

  /** Single fetch, used when a hover beats the prefetch. */
  async function fetchOne(id: string): Promise<Card | null> {
    if (isFresh(id)) return get(id)
    if (missing.value.has(id)) return null

    const existing = inflight.get(id)
    if (existing) return existing

    const request = apiGet<Card>(`/entities/${encodeURIComponent(id)}/card`)
      .then((card) => {
        store(id, card)
        return card
      })
      .catch((err: unknown) => {
        // Only a 404 means "this id has no card". A network blip or a 500
        // cached here as `missing` made the card unfetchable for the rest of
        // the session -- the curation pair rendered an eternal spinner off
        // one dropped request.
        if (err instanceof ApiError && err.status === 404) missing.value.add(id)
        return null
      })
      .finally(() => inflight.delete(id))

    inflight.set(id, request)
    return request
  }

  /**
   * The graph moved under us -- a run wrote entities, a merge landed, a
   * lookup finished.
   *
   * STALE-WHILE-REVALIDATE, not a wipe. This fires on every `entities`
   * freshness tick, and during a run that is every few seconds -- clearing
   * `cards` made every card on screen vanish into its loading state at that
   * cadence, which on the curation queue read as "the cards just disappear".
   * Dropping only the freshness marks means `get()` keeps serving the stale
   * card while the next `fetchOne`/`prefetch` replaces it in place.
   */
  function invalidate() {
    fetchedAt.value.clear()
    missing.value.clear()
  }

  /**
   * Entities changed elsewhere — a run minting them, a merge, a promotion
   * crossing the corroboration gate.
   *
   * Dropping the cache rather than refetching it: this store is a lazy
   * card cache with a TTL, so there is no "current" set to reload. The next
   * hover fetches the new truth and everything not being looked at costs
   * nothing.
   */
  onFreshness('entities', invalidate)

  return { cards, missing, get, prefetch, fetchOne, invalidate, isFresh }
})
