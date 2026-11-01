import { describe, expect, it } from 'vitest'

import { entityTypeLabel } from './entityType'

describe('entityTypeLabel', () => {
  it.each([
    ['GovernmentBody', 'Government body'],
    ['MilitaryUnit', 'Military unit'],
    ['ResearchOrg', 'Research org'],
    ['Company', 'Company'],
    ['Country', 'Country'],
    ['Technology', 'Technology'],
  ])('%s reads as %s', (type, expected) => {
    expect(entityTypeLabel(type)).toBe(expected)
  })

  it('has nothing to say about nothing', () => {
    expect(entityTypeLabel(null)).toBe('')
    expect(entityTypeLabel(undefined)).toBe('')
    expect(entityTypeLabel('  ')).toBe('')
  })
})
