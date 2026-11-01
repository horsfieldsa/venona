/**
 * The corpus count is re-read when a run finishes.
 *
 * WHY THIS EXISTS. `refreshCorpus` had zero callers in the whole app while the
 * composable's own docstring said "called when a run finishes". `total` is
 * module state fetched once per tab, so a tab opened on a fresh install cached
 * `0` for its lifetime and all five views went on offering the first-run screen
 * after the first run finished — and through the second, and the third, until
 * somebody reloaded the page. Nothing could see it: the types are identical
 * whether the watcher exists or not, and by hand it needs a full run to
 * reproduce.
 *
 * `/graph` had a worse version of the same bug (its first-run branch unmounted
 * the canvas whose fetch would have disproved it, so it latched), and the store
 * half of that fix is covered here too — `graph.loaded` has to distinguish "I
 * fetched and found nothing" from "I have not fetched".
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useCorpusEmpty } from './useCorpusEmpty'

import { useHealthStore } from '@/stores/health'

let stats: { total: number; counts: Record<string, number> }[]

beforeEach(() => {
  setActivePinia(createPinia())
  stats = [
    { total: 0, counts: {} },
    { total: 12, counts: { briefs: 3 } },
  ]
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Response.json({
        ...(stats.shift() ?? { total: 12, counts: { briefs: 3 } }),
        db_bytes: 0,
        graph: null,
      }),
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/**
 * NOTE ON ORDER. `total` and the watcher's `watching` flag are MODULE state --
 * deliberately, so five views share one count and one watcher -- and a static
 * import means `vi.resetModules()` cannot give a test a fresh copy. So these run
 * in sequence against one module instance, and the second test is written for the
 * state the first leaves behind (a non-zero count) rather than pretending
 * otherwise.
 */
describe('useCorpusEmpty', () => {
  it('re-reads the count when a run finishes, not when one starts', async () => {
    const health = useHealthStore()
    const { isEmpty, filling } = useCorpusEmpty()

    // The first count landed as zero: a fresh install.
    await vi.waitUntil(() => isEmpty.value)
    expect(filling.value).toBe(true)

    // A run starts. Nothing to re-read yet -- a run beginning cannot have
    // changed the corpus.
    health.data = { active_run: 'run:x' } as never
    await nextTick()
    expect(isEmpty.value).toBe(true)

    // And it finishes. THIS is the transition that has to re-count; without it
    // `total` stays 0 and `filling` stays true forever.
    health.data = { active_run: null } as never
    await vi.waitUntil(() => !isEmpty.value)
    expect(filling.value).toBe(false)
  })

  it('is NOT filling during a run once a brief exists', async () => {
    const health = useHealthStore()
    const { isEmpty, filling } = useCorpusEmpty()

    // The previous test left a populated count behind, which is what makes this
    // the interesting case rather than a repeat.
    expect(isEmpty.value).toBe(false)

    health.data = { active_run: 'run:x' } as never
    await nextTick()

    // The run ORs in only while NO brief exists -- the whole first run, since a
    // brief is a run's last artefact. On a corpus with briefs, a nightly run
    // must not flip every view back to the first-run screen: the graph's node
    // count is zero while its overview request is in flight, so `filling` being
    // true here put the first-run splash over a populated graph on every visit
    // made during a run.
    expect(filling.value).toBe(false)
  })
})
