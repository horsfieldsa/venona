/** The capability classifier: one probe status, one answer.
 *
 * `GET /runs/types` is an admin-only read. What each status means is pinned
 * here because every misread has the same silent symptom -- a button that
 * should not exist, or one that vanished for a credential that could use it.
 */

import { describe, expect, it } from 'vitest'

import { classifyProbe } from './client'

describe('classifyProbe', () => {
  it('200 is an unrestricted admin credential', () => {
    expect(classifyProbe(200)).toBe('full')
  })

  it('403 is a reach or role block — both mean the gated buttons fail', () => {
    expect(classifyProbe(403)).toBe('limited')
  })

  it('401 is not a capability at all', () => {
    expect(classifyProbe(401)).toBe('invalid')
  })
})
