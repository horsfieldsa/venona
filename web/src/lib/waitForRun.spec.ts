/**
 * One failed poll is not a failed run.
 *
 * The run is on the server; a poll that fails says the QUESTION broke, not the
 * work. Aborting the wait on the first blip surfaced "no definition found" for
 * lookups that went on to succeed — an answer the reader has no way to tell
 * from a real miss, on a page that has already stopped waiting.
 *
 * `environment: 'node'` with fake timers: the whole file is a loop over a clock.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { waitForRun } from './waitForRun'

import { CancelledError } from '@/api/client'

const api = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/api/client')>()
  return { ...real, apiGet: api.get }
})

/** Advance past however many 1.5s polls the case needs. */
async function poll(times: number) {
  for (let i = 0; i < times; i += 1) await vi.advanceTimersByTimeAsync(1500)
}

beforeEach(() => {
  vi.useFakeTimers()
  api.get.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('waitForRun', () => {
  it('rides out a transient failure and reports the finished run', async () => {
    api.get
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ status: 'succeeded', error: null })

    const wait = waitForRun('run:x')
    await poll(2)

    await expect(wait).resolves.toMatchObject({ status: 'succeeded' })
  })

  it('gives up once the failures stop being transient', async () => {
    api.get.mockRejectedValue(new Error('connection reset'))

    const wait = waitForRun('run:x')
    const caught = wait.catch((err: Error) => err.message)
    await poll(5)

    await expect(caught).resolves.toBe('connection reset')
  })

  it('counts consecutive failures only, so a flap never accumulates', async () => {
    // Three failures, a success, three more failures: below the tolerance both
    // times, so the wait is still going when the run finally lands.
    api.get
      .mockRejectedValueOnce(new Error('one'))
      .mockRejectedValueOnce(new Error('two'))
      .mockRejectedValueOnce(new Error('three'))
      .mockResolvedValueOnce({ status: 'running' })
      .mockRejectedValueOnce(new Error('four'))
      .mockRejectedValueOnce(new Error('five'))
      .mockRejectedValueOnce(new Error('six'))
      .mockResolvedValueOnce({ status: 'succeeded', error: null })

    const wait = waitForRun('run:x')
    await poll(8)

    await expect(wait).resolves.toMatchObject({ status: 'succeeded' })
  })

  it('treats a cancelled poll as a cancelled wait, not a failure', async () => {
    api.get.mockRejectedValue(new CancelledError('/runs/run:x'))

    const wait = waitForRun('run:x')
    await poll(1)

    await expect(wait).resolves.toMatchObject({ status: 'cancelled' })
  })

  it('keeps waiting through queued, which is in flight', async () => {
    api.get
      .mockResolvedValueOnce({ status: 'queued' })
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({ status: 'succeeded', error: null })

    const seen: string[] = []
    const wait = waitForRun('run:x', { onStatus: (s) => seen.push(s) })
    await poll(3)

    await expect(wait).resolves.toMatchObject({ status: 'succeeded' })
    expect(seen).toEqual(['queued', 'running', 'succeeded'])
  })

  it('still stops at the deadline while inside its failure tolerance', async () => {
    api.get.mockRejectedValue(new Error('connection reset'))

    // The deadline is reached while the failure count is still under the
    // tolerance. It wins: failures burn wall-clock like any other poll, so a
    // flapping server cannot hold the caller past `timeoutMs`.
    const wait = waitForRun('run:x', { timeoutMs: 3000 })
    await poll(4)

    await expect(wait).resolves.toEqual({ status: 'failed', error: 'timed out' })
  })
})
