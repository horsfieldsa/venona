/**
 * The cells two admin screens share.
 *
 * `/admin` previews the request queue and the authority log; `/admin/people`
 * acts on them. Both build the same cells, and the cells carry judgements
 * rather than field names -- `cli` for a change no `users` row made, a raw
 * timestamp when an age cannot be computed. A spec on the mappers is what
 * keeps the preview and the page from wording one event two ways.
 *
 * It is also the only automated check on those two tables: the development
 * corpus has an empty queue and an empty log, so both render their empty
 * states in a browser and a broken mapping would look exactly like peace.
 */
import { describe, expect, it } from 'vitest'

import { EVENT_COLUMNS, REQUEST_COLUMNS, eventRow, requestRow } from './people'

import type { components } from '@/api/schema'


type AccessRequest = components['schemas']['AccessRequest']

/* Whole rows, because the mappers take whole rows: a partial cast would let a
 * field the wire stops sending pass this spec and render blank on both
 * screens. */
function aRequest(over: Partial<AccessRequest> = {}): AccessRequest {
  return {
    id: 'req_1',
    email: 'a@example.com',
    role: 'analyst',
    status: 'pending',
    coverage: 'ground autonomy',
    created_at: '2026-08-31T10:00:00Z',
    account_email: null,
    account_status: null,
    decided_at: null,
    decided_by_email: null,
    note: null,
    ...over,
  }
}

const NOW = Date.parse('2026-08-31T12:00:00Z')

describe('requestRow', () => {
  it('renders a waiting request', () => {
    const row = requestRow(aRequest(), NOW)
    expect(row).toMatchObject({
      id: 'req_1',
      email: 'a@example.com',
      role: 'analyst',
      status: 'pending',
      coverage: 'ground autonomy',
      age: '2 hours ago',
    })
  })

  it('renders a missing coverage note as blank, never as the word null', () => {
    const row = requestRow(aRequest({ coverage: null }), NOW)
    expect(row.coverage).toBe('')
  })

  it('falls back to the timestamp when an age cannot be computed', () => {
    const row = requestRow(aRequest({ created_at: 'not a date' }), NOW)
    expect(row.age).toBe('not a date')
  })
})

describe('eventRow', () => {
  it('names the shell as `cli` when no account made the change', () => {
    const row = eventRow(
      {
        id: 'ev_1',
        user_id: 'usr_2',
        user_email: 'them@example.com',
        actor_email: null,
        action: 'role_changed',
        detail: 'reader',
        created_at: '2026-08-31T11:00:00Z',
      },
      NOW,
    )
    // Not blank: a blank cell reads as a column that failed to load, and a
    // change `venona users` made is not a `users` row.
    expect(row).toMatchObject({ who: 'them@example.com', actor: 'cli', when: '1 hour ago' })
  })

  it('falls back to the user id when the account has no address', () => {
    const row = eventRow({ id: 'e', user_id: 'usr_9', user_email: null, actor_email: 'admin@example.com', action: 'suspended', detail: null, created_at: '2026-08-31T11:00:00Z' }, NOW)
    expect(row.who).toBe('usr_9')
    expect(row.detail).toBe('')
  })
})

describe('the shared columns', () => {
  it('names every key the mappers produce', () => {
    const request = requestRow(aRequest(), NOW)
    const event = eventRow({ id: 'e', user_id: 'u', user_email: null, actor_email: null, action: 'a', detail: null, created_at: '2026-08-31T10:00:00Z' }, NOW)
    for (const column of REQUEST_COLUMNS) expect(request).toHaveProperty(column.key)
    for (const column of EVENT_COLUMNS) expect(event).toHaveProperty(column.key)
  })
})
