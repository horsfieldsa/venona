import { computed, type ComputedRef } from 'vue'

import { useHealthStore } from '@/stores/health'
import { useRunsStore } from '@/stores/runs'

/**
 * What is running, for the two places that annotate a destination with it.
 *
 * Shared rather than written twice: the header's run-status pill states it in
 * language and the admin rail's Runs row states it positionally, and a second
 * copy of `progress` would let the two disagree about what "in flight" means.
 *
 * A composable rather than a component, because the two callers draw different
 * things from the same three facts. The SPINE'S CLASS MAP stays in the
 * component that draws it: `scripts/lint_tokens.py` sweeps `*.vue` only, so a
 * Tailwind class map moved into a `.ts` silently leaves the raw-colour and
 * tier-1-primitive checks behind.
 */
export interface RunProgress {
  /** Which of the pipeline's stages is running, how many there are, and
   *  whether it is waiting for the model service rather than working. */
  progress: ComputedRef<{
    total: number
    index: number
    stage: string
    waiting: boolean
  } | null>
  /** The spine in words. Never colour and motion alone. */
  progressLabel: ComputedRef<string>
  /**
   * Light runs in flight, as a count.
   *
   * Deliberately NOT folded into the spine. The spine's whole meaning is
   * "which of the pipeline's six stages is running"; a lookup has one stage
   * and finishes in under a minute, so giving it a position on that track
   * would say something false about both. A count says the true thing: some
   * short work is happening.
   */
  lightCount: ComputedRef<number>
}

export function useRunProgress(): RunProgress {
  const health = useHealthStore()
  const runs = useRunsStore()

  const progress = computed(() => {
    const active = health.data?.active_run
    if (!active) return null
    // THE STAGES BELONG TO THE ATTACHED RUN, and the name in the header belongs
    // to the research run. There is one SSE connection and a detail page points
    // it at whatever run is on screen, so with both lanes busy -- a nightly
    // sweep running while somebody opens a live lookup -- the header would
    // report the lookup's position under the research run's name. `RunsView`
    // already applies this guard per row and says why; this is the same rule
    // for the one readout that follows a reader onto every page.
    //
    // Null falls back to the caller's own "running", which `AppHeader` already
    // renders for the moment before the stage list arrives.
    if (runs.attachedRunId !== active) return null
    const index = runs.stages.findIndex((s) => s.status === 'running')
    if (index < 0) return null
    const stage = runs.stages[index]
    return {
      total: runs.stages.length,
      index,
      stage: stage.name,
      // Carried up with the position because the header is the only in-flight
      // signal a reader has from any page, and "waiting for the service" is the
      // one thing a pulsing stage name cannot say. A run that has paused for a
      // provider looks exactly like one that is working, and for up to half an
      // hour at a time.
      waiting: stage.progress?.waiting === true,
    }
  })

  const progressLabel = computed(() =>
    progress.value
      ? `run in flight: ${progress.value.stage}, stage ${progress.value.index + 1} of ${progress.value.total}` +
        (progress.value.waiting ? ', waiting for the model service' : '')
      : '',
  )

  const lightCount = computed(() => runs.lightRuns.length)

  return { progress, progressLabel, lightCount }
}
