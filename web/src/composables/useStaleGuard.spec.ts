/**
 * The out-of-order typeahead bug, which `vue-tsc` cannot see and a person
 * cannot reliably reproduce by hand — it needs the SHORTER query to be slower,
 * which is exactly what happens in production and almost never on localhost.
 */
import { describe, expect, it } from 'vitest'

import { useStaleGuard } from './useStaleGuard'

describe('useStaleGuard', () => {
  it('lets a lone request through', () => {
    const isCurrent = useStaleGuard().claim()
    expect(isCurrent()).toBe(true)
  })

  it('invalidates an older claim once a newer one is made', () => {
    const guard = useStaleGuard()
    const first = guard.claim()
    const second = guard.claim()

    expect(first()).toBe(false)
    expect(second()).toBe(true)
  })

  it('survives the slow-first, fast-second race', async () => {
    // Type "an", then "and". "an" matches more rows and answers last.
    const guard = useStaleGuard()
    let shown = ''

    async function search(term: string, delay: number) {
      const isCurrent = guard.claim()
      await new Promise((r) => setTimeout(r, delay))
      if (!isCurrent()) return
      shown = term
    }

    await Promise.all([search('an', 30), search('and', 5)])

    expect(shown).toBe('and')
  })

  it('two guards are independent', () => {
    // PathFinder has two boxes: typing in "to" must not invalidate a request
    // that "from" is still waiting on.
    const from = useStaleGuard()
    const to = useStaleGuard()

    const fromClaim = from.claim()
    to.claim()
    to.claim()

    expect(fromClaim()).toBe(true)
  })
})
