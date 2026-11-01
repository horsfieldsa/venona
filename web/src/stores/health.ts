import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet, errorText } from '@/api/client'
import type { Health } from '@/types/health'

/**
 * Health is polled rather than streamed: it is cheap (the CLI probe is local,
 * no model call) and a stale reading is never dangerous.
 */
export const useHealthStore = defineStore('health', () => {
  const data = ref<Health | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)

  /**
   * Coalesced: concurrent callers share one round trip. The poll loop's
   * immediate first tick, a view's mount-time `ensure()` and a config save all
   * ask within milliseconds of each other, and `/health` probes Neo4j — one
   * answer is the same answer.
   */
  let inflight: Promise<void> | null = null

  function refresh(): Promise<void> {
    if (inflight) return inflight
    loading.value = true
    inflight = apiGet<Health>('/health')
      .then((body) => {
        data.value = body
        error.value = null
      })
      .catch((err) => {
        error.value = errorText(err, 'something went wrong')
        data.value = null
      })
      .finally(() => {
        inflight = null
        loading.value = false
      })
    return inflight
  }

  /**
   * "Make sure a reading exists" — for consumers that render FROM health but
   * do not own its cadence (`useAppPolling` does). A view that forced a
   * refresh on mount was paying a round trip to re-ask a question the poll
   * answered under 30 seconds ago.
   */
  function ensure(): Promise<void> {
    if (data.value !== null) return Promise.resolve()
    return refresh()
  }

  return { data, error, loading, refresh, ensure }
})
