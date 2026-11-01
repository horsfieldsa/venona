import { describe, expect, it } from 'vitest'

import { safeHref } from './safeHref'

describe('safeHref', () => {
  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html;base64,PHNjcmlwdD4=',
    'vbscript:msgbox(1)',
    ' javascript:alert(1)',
    'java\nscript:alert(1)',
    'relative/path',
    '',
  ])('refuses %j', (url) => {
    expect(safeHref(url)).toBeUndefined()
  })

  it.each(['https://example.test/a?b=c#d', 'http://example.test', 'HTTPS://EXAMPLE.TEST/x'])(
    'keeps %j',
    (url) => {
      expect(safeHref(url)).toBe(url)
    },
  )

  it('returns undefined rather than the empty string', () => {
    // Vue omits the attribute for `undefined`; `href=""` is a link to the
    // current page, which looks like a working link and is not one.
    expect(safeHref(null)).toBeUndefined()
    expect(safeHref(undefined)).toBeUndefined()
  })
})
