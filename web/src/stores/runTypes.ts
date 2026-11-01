import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet } from '@/api/client'
import type { RunType, RunTypeList } from '@/types/run'

/**
 * Contract C11: what kinds of run exist and how each renders.
 *
 * Fetched once on mount and never refreshed. The table is compiled into the
 * backend, so it can only change when the server restarts — and a restart drops
 * every stream and reloads the page anyway.
 *
 * This exists so the runs UI stops assuming the pipeline. `RunsView` drew a
 * six-segment spine from a hardcoded `STAGES` literal whatever the run was, so
 * a lookup — one model call — rendered as a mostly-empty pipeline with five
 * segments that never lit and counters that never moved. Segments that stay
 * dark read as stages that failed.
 */

/**
 * What an unknown kind gets. Deliberately permissive and deliberately matched
 * to the backend's `run_types._fallback`: the `runs` table outlives any one
 * release's idea of what a run is, and a history row that throws is worse than
 * one that renders plainly.
 *
 * `pipeline` rather than `single`, because assuming a run has exactly one stage
 * is the guess that renders wrongly; assuming it has several renders a table
 * with whatever the server actually reported in it.
 */
function fallback(kind: string): RunType {
  return {
    kind,
    label: kind || 'run',
    // Empty, never invented. A kind this build has never heard of cannot be
    // explained, and a made-up sentence about one is worse than none.
    description: '',
    lane: 'research',
    stages: [],
    summary: [],
    detail: 'pipeline',
    trigger: 'on_demand',
    // True, matching `run_types._fallback`'s default. An unknown kind that does
    // spend on the model would otherwise have its session hidden, and a page
    // offering an empty log is a smaller error than one hiding a full one.
    uses_model: true,
    // No phrases, matching `run_types._fallback`. An unknown kind's outcome
    // cannot be put into words nobody wrote, so `verdictFor` falls back to the
    // bare status rather than to an invented sentence.
    outcomes: {},
  }
}

export const useRunTypesStore = defineStore('runTypes', () => {
  const types = ref<Record<string, RunType>>({})
  const lanes = ref<Record<string, number>>({})
  const loaded = ref(false)

  async function load() {
    if (loaded.value) return
    try {
      const payload = await apiGet<RunTypeList>('/runs/types')
      types.value = Object.fromEntries(payload.types.map((t) => [t.kind, t]))
      lanes.value = payload.lanes
      loaded.value = true
    } catch {
      // Non-fatal. Every consumer goes through `typeFor`, so an unreachable
      // endpoint degrades to the fallback rather than blanking the Runs page.
    }
  }

  function typeFor(kind: string): RunType {
    return types.value[kind] ?? fallback(kind)
  }

  return { types, lanes, loaded, load, typeFor }
})
