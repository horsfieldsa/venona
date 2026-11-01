import { describe, expect, it } from 'vitest'

import { entityIdFromRoute, isGlossaryId, toEntityRoute } from './entityRoute'

describe('entityRoute', () => {
  it.each([
    'company:anduril-industries',
    'prog:next-generation-command-and-control',
    'gloss:mosa',
    'unit:u-s-army',
  ])('round-trips %s', (id) => {
    const route = toEntityRoute(id) as { params: Record<string, string> }
    expect(entityIdFromRoute(route.params)).toBe(id)
  })

  it('splits on the FIRST colon only', () => {
    // Not reachable from a C4 id, and that is the point: if the shape ever
    // widens, the type is still one segment and the rest is the slug.
    const route = toEntityRoute('gloss:a:b') as { params: Record<string, string> }
    expect(route.params).toEqual({ type: 'gloss', slug: 'a:b' })
  })

  it.each([
    [{ type: 'company', slug: '' }, 'an empty slug'],
    [{ type: '', slug: 'anduril' }, 'an empty type'],
    [{ type: 'company', slug: '-leading-hyphen' }, 'a slug C4 cannot mint'],
    [{ type: 'company', slug: 'Anduril' }, 'an uppercase slug'],
    [{ type: '9company', slug: 'anduril' }, 'a type starting with a digit'],
    [{ type: 'company', slug: 'a'.repeat(65) }, 'a slug past the 64-char cap'],
  ])('refuses %#: %s', (params) => {
    expect(entityIdFromRoute(params)).toBeNull()
  })

  it('knows an acronym from a name in the graph', () => {
    expect(isGlossaryId('gloss:mosa')).toBe(true)
    expect(isGlossaryId('company:anduril-industries')).toBe(false)
  })
})
