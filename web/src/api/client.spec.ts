/**
 * The fetch seam.
 *
 * Every one of these is a failure `vue-tsc --noEmit` cannot see and that
 * `make ui-smoke` should not have to catch: a timeout that never fires reads as
 * a spinner, and a spinner reads as "still working".
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  CancelledError,
  TimeoutError,
  apiGet,
  apiPost,
  errorText,
  setUnauthorizedHandler,
} from './client'

function respondWith(body: unknown, init: ResponseInit = {}) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
      ...init,
    }),
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  setUnauthorizedHandler(() => {})
})

describe('errorText', () => {
  it('prefers the sentence the server wrote for a person', () => {
    const err = new ApiError(503, 'The graph projection is unavailable.')
    expect(errorText(err, 'fallback')).toBe('The graph projection is unavailable.')
  })

  it('does not render the status code into the UI', () => {
    // `ApiError.message` is `${status}: ${detail}`, which is where
    // "500: Internal Server Error" came from -- rendered across the graph
    // canvas, in full, as if it were a sentence.
    const err = new ApiError(500, 'Internal Server Error')
    expect(errorText(err, 'fallback')).not.toContain('500:')
  })

  it('falls through to the caller wording for a non-Error', () => {
    expect(errorText('a string somehow', 'could not load the graph')).toBe(
      'could not load the graph',
    )
  })
})

describe('timeouts', () => {
  it('gives up rather than hanging forever', async () => {
    vi.useFakeTimers()
    // A server that accepts the connection and never answers. Before this there
    // was no AbortController anywhere in web/src, so the store's `loading` flag
    // stayed set until the browser's own timeout -- minutes.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )

    const pending = apiGet('/health')
    const assertion = expect(pending).rejects.toBeInstanceOf(TimeoutError)
    await vi.advanceTimersByTimeAsync(31_000)
    await assertion
  })

  it('does not let a caller signal replace the timeout', async () => {
    vi.useFakeTimers()
    // The bug: `...init` was spread AFTER `signal`, so a caller-supplied signal
    // silently displaced the timeout controller and the 30s ceiling stopped
    // existing for that request. Nothing passed one yet, so nothing caught it.
    const caller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )

    const pending = apiGet('/health', { signal: caller.signal })
    const assertion = expect(pending).rejects.toBeInstanceOf(TimeoutError)
    await vi.advanceTimersByTimeAsync(31_000)
    await assertion
  })

  it('reports a caller abort as cancelled, not as a timeout', async () => {
    // Both surface as `AbortError` at the fetch boundary. Conflating them is how
    // "you navigated away" would render as "the server did not respond within
    // 30s" on every route change once anything starts cancelling.
    const caller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )

    const pending = apiGet('/health', { signal: caller.signal })
    const assertion = expect(pending).rejects.toBeInstanceOf(CancelledError)
    caller.abort()
    await assertion
  })

  it('clears the timer on a normal response', async () => {
    vi.useFakeTimers()
    const clear = vi.spyOn(globalThis, 'clearTimeout')
    vi.stubGlobal('fetch', respondWith({ ok: true }))

    await apiGet('/health')

    expect(clear).toHaveBeenCalled()
  })
})

describe('401', () => {
  it('fires the unauthorized hook exactly once', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    vi.stubGlobal('fetch', respondWith({ detail: 'sign in' }, { status: 401 }))

    await expect(apiGet('/briefs')).rejects.toThrow()

    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('is still an ApiError, so existing recovery paths keep working', async () => {
    vi.stubGlobal('fetch', respondWith({ detail: 'sign in' }, { status: 401 }))

    await expect(apiGet('/briefs')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('a failure the server wrote no sentence for', () => {
  /**
   * HTTP/2 carries no reason phrase, so `response.statusText` is the empty
   * string for every response this API serves in production. The fallback used
   * to be that field, which made `ApiError.message` read `502:` and nothing
   * after it -- the whole error state of three pages.
   */
  it('says the server is not answering rather than the bare status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>bad gateway</html>', { status: 502, statusText: '' })),
    )

    await expect(apiGet('/briefs')).rejects.toThrow('502: the server is not answering')
  })

  it('prefers the server\'s own sentence when there is one', async () => {
    vi.stubGlobal('fetch', respondWith({ detail: 'the graph is rebuilding' }, { status: 503 }))

    await expect(apiGet('/briefs')).rejects.toThrow('503: the graph is rebuilding')
  })
})

describe('structured errors', () => {
  it('keeps the parsed body so a caller can recover', async () => {
    // A 409 from /glossary/{surface}/lookup carries {error, run_id, message}
    // so the caller can say WHICH run is in the way. Collapsing that to a
    // string yields "409: [object Object]".
    vi.stubGlobal(
      'fetch',
      respondWith(
        { detail: { error: 'run_active', run_id: 'run:abc', message: 'a run is in flight' } },
        { status: 409 },
      ),
    )

    try {
      await apiPost('/glossary/SDA/lookup')
      expect.unreachable('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      const body = (err as ApiError).body as { detail: { run_id: string } }
      expect(body.detail.run_id).toBe('run:abc')
      expect(errorText(err, 'x')).toBe('a run is in flight')
    }
  })

  it('survives a non-JSON error body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>502 Bad Gateway</html>', { status: 502 })),
    )

    await expect(apiGet('/briefs')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('204', () => {
  it('does not try to parse an empty body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))

    await expect(apiGet('/auth/logout')).resolves.toBeUndefined()
  })
})
