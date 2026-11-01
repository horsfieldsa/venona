import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiPost, errorText } from '@/api/client'
import { awaitLookup, noteLookupLaunched, pushLookupStarted } from '@/lib/lookupRun'
import { useEntitiesStore } from '@/stores/entities'
import { useToastsStore } from '@/stores/toasts'

/**
 * Card lookups in flight, keyed by the card's id.
 *
 * A store, not component state: a ref inside the hover card dies with the
 * card session, so closing a card mid-lookup would lose the spinner while
 * the run kept executing, and reopening would offer "Look this up" as
 * though nothing were happening. Held here, the run's progress belongs to
 * the TERM, not to whichever card happens to be open -- any card asking
 * about the same id sees the same state, for as long as the run takes.
 *
 * Terminal successes clear their entry -- the server row (`definition`,
 * `last_attempt_at`) is the durable record from there. A failure keeps its
 * entry, so the error survives a close-and-reopen until the next attempt
 * replaces it.
 */

export type CardLookup =
  | { status: 'queued' | 'running'; runId: string }
  | { status: 'error'; runId: string | null; error: string }

export const useLookupsStore = defineStore('lookups', () => {
  const pending = ref(new Map<string, CardLookup>())

  function stateFor(id: string | null): CardLookup | null {
    return id ? (pending.value.get(id) ?? null) : null
  }

  /** POST the lookup and follow its run to the end. `surface` is the word the
   *  toasts name -- the reader may be three paragraphs past the card. */
  async function start(id: string, path: string, surface: string): Promise<void> {
    const existing = pending.value.get(id)
    if (existing && existing.status !== 'error') return
    const toasts = useToastsStore()

    let runId: string
    try {
      const { run_id, status } = await apiPost<{ run_id: string; status?: string }>(path)
      noteLookupLaunched()
      runId = run_id
      pending.value.set(id, {
        status: status === 'queued' ? 'queued' : 'running',
        runId,
      })
      pushLookupStarted(surface, status)
    } catch (err) {
      pending.value.set(id, { status: 'error', runId: null, error: errorText(err, 'lookup failed') })
      return
    }

    const entities = useEntitiesStore()
    const result = await awaitLookup(runId, surface, (s) => {
      const current = pending.value.get(id)
      if (current && current.status === 'queued' && s === 'running') {
        pending.value.set(id, { status: 'running', runId })
      }
    })
    // The error is already on screen as a toast; it is kept on the TERM as
    // well, so a card closed and reopened still says what happened.
    if (result.error !== undefined) {
      pending.value.set(id, { status: 'error', runId, error: result.error })
      return
    }

    pending.value.delete(id)
    try {
      // The definition, if there is one, is now stored. Drop the cache so an
      // open card repaints from the server rather than from what it knew.
      entities.invalidate()
      const refreshed = await entities.fetchOne(id)
      // The card may be long closed by now; the toast is the feedback that
      // reaches the reader wherever they are.
      toasts.push(
        refreshed?.definition
          ? { kind: 'success', text: 'definition found', subject: surface }
          : { kind: 'info', text: 'nothing found to quote', subject: surface },
      )
    } catch (err) {
      pending.value.set(id, { status: 'error', runId, error: errorText(err, 'lookup failed') })
      toasts.push({ kind: 'error', text: errorText(err, 'lookup failed'), subject: surface })
    }
  }

  return { pending, stateFor, start }
})
