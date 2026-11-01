import { computed, effectScope, ref, watch } from 'vue'

import { apiGet } from '@/api/client'
import { useHealthStore } from '@/stores/health'
import type { DataStats } from '@/types/data'

/**
 * Is there anything in the corpus at all?
 *
 * WHY ONE ANSWER RATHER THAN FIVE. Brief, Graph, Lookup, Curation and Archive
 * each had their own idea of "empty", derived from whatever that view happened
 * to have loaded: no briefs, no nodes, no glossary rows, no open conflicts. Four
 * of those are also true of a HEALTHY corpus -- a graph filtered to a type
 * nothing has is empty, and an empty curation queue is the goal rather than a
 * problem -- so each view had to guess, and they guessed differently. The
 * reader met five different explanations of one fact.
 *
 * `/api/data/stats` is the authoritative count and already existed for the
 * data-management screen. Asked once and shared, because five views mounting in
 * one session must not be five round trips for a number that changes only when
 * a run finishes.
 *
 * DELIBERATELY NOT POLLED. The count is re-read when a run FINISHES -- see the
 * watcher below -- because a poll would spend a request every few seconds on a
 * question whose answer changes about once a day.
 */
const total = ref<number | null>(null)
/** How many briefs exist -- the "has a run ever finished" signal `filling`
 *  needs. Null carries the same "unknown is not zero" meaning as `total`. */
const briefsCount = ref<number | null>(null)
let inFlight: Promise<void> | null = null
//: One watcher for all five views, hence module state alongside `total`.
let watching = false

async function load(force = false): Promise<void> {
  if (total.value !== null && !force) return
  if (inFlight && !force) return inFlight
  inFlight = (async () => {
    try {
      const stats = await apiGet<DataStats>('/data/stats')
      total.value = stats.total
      briefsCount.value = stats.counts?.['briefs'] ?? 0
    } catch {
      // Unknown is NOT empty. A failed count must never make a populated app
      // tell its operator to start their first run, so these stay `null` and
      // `isEmpty` is false until something answers.
      total.value = null
      briefsCount.value = null
    } finally {
      inFlight = null
    }
  })()
  return inFlight
}

/** True only when the count came back and said zero. `null` -- not yet asked, or
 *  the request failed -- reads as "not empty", which is the safe direction. */
const isEmpty = computed(() => total.value === 0)

export function useCorpusEmpty() {
  void load()
  const health = useHealthStore()

  /**
   * ⭐ RE-READ THE COUNT WHEN A RUN FINISHES.
   *
   * Without this, `refreshCorpus` has no caller anywhere in the app while the
   * docstring above claims one. `total` is module state fetched once per tab, so
   * a tab opened on a fresh install caches `0` for its lifetime and all five
   * views go on offering the first-run screen after the first run finished --
   * and through the second, and the third, until somebody reloads.
   *
   * Nothing could see it: the types are identical whether this exists or not,
   * and reproducing it by hand needs a full run.
   *
   * ON THE FALLING EDGE ONLY. A run STARTING cannot have changed the corpus, and
   * re-counting then would spend a request to learn what we already know.
   *
   * A DETACHED SCOPE, and that is the subtle part. `watch` called from inside a
   * component binds to that component and is disposed when it unmounts -- so the
   * one shared watcher would die with whichever view happened to mount first,
   * silently, and the bug would come back for every view mounted after it.
   * `effectScope(true)` is independent of any component, which is what "module
   * state" has to mean for an effect rather than a ref.
   */
  if (!watching) {
    watching = true
    effectScope(true).run(() => {
      watch(
        () => Boolean(health.data?.active_run),
        (running, wasRunning) => {
          if (wasRunning && !running) void load(true)
        },
      )
    })
  }

  /**
   * Nothing to show YET -- either nothing has been ingested, or the FIRST run
   * is still filling the corpus.
   *
   * The second half matters because the stages land at different times: `fetch`
   * writes documents within seconds, so `isEmpty` goes false long before there
   * is a single acronym or a projected node. Without it, Lookup and Graph spend
   * most of the first run telling the reader "nothing matches that filter" --
   * which is true, useless, and reads as a broken filter rather than as a run
   * in progress.
   *
   * FIRST run, established by the briefs count. `active_run` alone was the
   * gate, and "paired with the view's own 'I have nothing to show'" did not
   * hold in practice: the graph's node count is also zero while its overview
   * request is in flight, so every nightly run greeted a visit to the graph
   * with the first-run screen. A brief only exists once a run has finished,
   * so `briefsCount === 0` is precisely "no run has ever completed here" --
   * true through the whole first run, false forever after.
   */
  const filling = computed(
    () => isEmpty.value || (Boolean(health.data?.active_run) && briefsCount.value === 0),
  )

  return { isEmpty, filling, total: computed(() => total.value), refreshCorpus: () => load(true) }
}
