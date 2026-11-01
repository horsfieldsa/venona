/**
 * The down/up derivations that every notice and disabled control reads.
 *
 * The two cases that earn a spec: `claude.ok === true` with the breaker open
 * must still read as down (an OAuth token can be present and expired at once,
 * so `ok` alone lies), and `VENONA_OFFLINE` replay mode -- which reports
 * `ok: false` from a healthy server -- must not.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useServiceHealth } from './useServiceHealth'

import { useHealthStore } from '@/stores/health'
import type { Health } from '@/types/health'

function healthy(overrides: Partial<Health> = {}): Health {
  return {
    api: 'ok',
    offline: false,
    neo4j: { ok: true },
    claude: { ok: true },
    ...overrides,
  } as Health
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('useServiceHealth', () => {
  it('reads everything as up when nothing has answered yet', () => {
    const { apiDown, neo4jDown, modelDown } = useServiceHealth()
    expect(apiDown.value).toBe(false)
    expect(neo4jDown.value).toBe(false)
    expect(modelDown.value).toBe(false)
  })

  it('apiDown follows the store error, and the service flags stay quiet on null data', () => {
    const health = useHealthStore()
    health.error = 'could not reach the server'
    health.data = null

    const { apiDown, neo4jDown, modelDown } = useServiceHealth()
    expect(apiDown.value).toBe(true)
    // With no payload there is nothing to blame on a single service; the API
    // notice is the one that should render.
    expect(neo4jDown.value).toBe(false)
    expect(modelDown.value).toBe(false)
  })

  it('neo4jDown surfaces the probe error', () => {
    const health = useHealthStore()
    health.data = healthy({ neo4j: { ok: false, error: 'connection refused' } })

    const { neo4jDown, neo4jDetail, modelDown } = useServiceHealth()
    expect(neo4jDown.value).toBe(true)
    expect(neo4jDetail.value).toBe('connection refused')
    expect(modelDown.value).toBe(false)
  })

  it('modelDown on ok: false', () => {
    const health = useHealthStore()
    health.data = healthy({ claude: { ok: false, error: 'not logged in' } })

    const { modelDown, modelDetail } = useServiceHealth()
    expect(modelDown.value).toBe(true)
    expect(modelDetail.value).toBe('not logged in')
  })

  it('modelDown when ok is green but the breaker is open', () => {
    const health = useHealthStore()
    health.data = healthy({
      claude: {
        ok: true,
        circuit: { open: true, consecutive_failures: 3, last_error: 'invalid api key' },
      },
    })

    const { modelDown, modelDetail } = useServiceHealth()
    expect(modelDown.value).toBe(true)
    expect(modelDetail.value).toBe('invalid api key')
  })

  it('replay mode is a healthy server, not a claude outage', () => {
    const health = useHealthStore()
    health.data = healthy({
      offline: true,
      claude: { ok: false, detail: 'replay backend (offline)' },
    })

    const { modelDown } = useServiceHealth()
    expect(modelDown.value).toBe(false)
  })

  it('tolerates an absent circuit and falls back through the detail chain', () => {
    const health = useHealthStore()
    health.data = healthy({ claude: { ok: false, detail: 'replay backend (offline)' } })

    const { modelDown, modelDetail } = useServiceHealth()
    expect(modelDown.value).toBe(true)
    expect(modelDetail.value).toBe('replay backend (offline)')
  })

  it('modelWaiting only when the open circuit is a provider outage being waited out', () => {
    const health = useHealthStore()
    health.data = healthy({
      claude: {
        ok: true,
        circuit: { open: true, consecutive_failures: 6, last_error: '529 overloaded', transient: true },
      },
    })

    const { modelDown, modelWaiting } = useServiceHealth()
    expect(modelDown.value).toBe(true)
    expect(modelWaiting.value).toBe(true)

    // A dead token is down and NOT waiting: nothing improves on its own.
    health.data = healthy({
      claude: {
        ok: true,
        circuit: { open: true, consecutive_failures: 6, last_error: '401 invalid', transient: false },
      },
    })
    expect(modelWaiting.value).toBe(false)
    expect(modelDown.value).toBe(true)

    // A closed circuit is neither, whatever `transient` last read.
    health.data = healthy({ claude: { ok: true, circuit: { open: false, consecutive_failures: 0 } } })
    expect(modelWaiting.value).toBe(false)
  })

  it('prefers the model block and falls back to the old key for one release', () => {
    // A tab open across the deploy polls this every thirty seconds. The block
    // itself was read unchained, so a response carrying only the new name
    // would have thrown inside a computed during render until somebody
    // reloaded -- which is why both are read and both are chained.
    const health = useHealthStore()
    health.data = healthy({
      claude: undefined,
      model: { ok: false, error: 'not signed in' },
    })
    const { modelDown } = useServiceHealth()
    expect(modelDown.value).toBe(true)
  })

  it('a service nobody connected is not an outage', () => {
    // `down` promises Venona keeps checking. Nothing here improves until a
    // person acts, so the two must not render as one.
    const health = useHealthStore()
    health.data = healthy({
      claude: { ok: false, reason: 'not_connected', error: 'not signed in' },
    })
    const { modelDown, modelUnconfigured, modelWaiting } = useServiceHealth()
    expect(modelDown.value).toBe(true)
    expect(modelUnconfigured.value).toBe(true)
    expect(modelWaiting.value).toBe(false)
  })
})
