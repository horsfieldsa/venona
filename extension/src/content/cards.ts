/** Card cache. Port of `web/src/stores/entities.ts`, minus Pinia.
 *
 * The batch is fetched once per annotate pass and every hover after that is a
 * map lookup. That is contract C8's stated reason for `cards:batch` returning a
 * map rather than a list, and it is what makes the card feel instant on a page
 * carrying two hundred mentions.
 */

import { shallowReactive, triggerRef, shallowRef } from 'vue'
import type { Card } from '../shared/types'
import { ask } from './port'

const cards = shallowReactive(new Map<string, Card>())
const missing = shallowReactive(new Set<string>())

/** Bumped whenever the map changes, so a computed reading `cards.get(id)` has
 * something to depend on -- a `shallowReactive` Map tracks `has`/`get` keys,
 * but replacing a value for a key already read needs the nudge. */
const revision = shallowRef(0)

export function cardFor(id: string | null): Card | null {
  revision.value // eslint-disable-line @typescript-eslint/no-unused-expressions
  return id ? (cards.get(id) ?? null) : null
}

export function isMissing(id: string | null): boolean {
  revision.value
  return id ? missing.has(id) : false
}

/** Whether the API refused us.
 *
 * Without this the card renders "Venona has no record of this" on a 401, which
 * is a WRONG DIAGNOSIS -- it says the corpus is empty when the truth is that we
 * never got to ask. The reader goes looking for a missing entity instead of a
 * refused token. The worker already puts a `!` on the badge; this is the same
 * fact where the reader is actually looking.
 */
export const signedOut = shallowRef(false)

/** In-flight de-dup by id. A fast traversal across a dense paragraph would
 * otherwise fire a dozen identical requests for the same mention. */
const inFlight = new Map<string, Promise<void>>()

export function fetchOne(id: string): Promise<void> {
  const existing = inFlight.get(id)
  if (existing) return existing

  const promise = (async () => {
    const res = await ask({ kind: 'cards', ids: [id] })
    if (res.ok && res.kind === 'cards') {
      signedOut.value = false
      for (const [key, card] of Object.entries(res.data.cards)) cards.set(key, card)
      for (const key of res.data.missing) missing.add(key)
    } else {
      if (!res.ok && res.status === 401) signedOut.value = true
      // Record it as missing rather than leaving the card spinning. A wrong
      // "nothing here" is recoverable by hovering again; an eternal spinner is
      // not.
      missing.add(id)
    }
    revision.value++
    inFlight.delete(id)
  })()

  inFlight.set(id, promise)
  return promise
}

export async function prefetch(refs: string[]): Promise<void> {
  const wanted = refs.filter((ref) => !cards.has(ref) && !missing.has(ref))
  if (!wanted.length) return
  const res = await ask({ kind: 'cards', ids: wanted })
  if (!res.ok || res.kind !== 'cards') {
    if (!res.ok && res.status === 401) signedOut.value = true
    return
  }
  signedOut.value = false
  for (const [id, card] of Object.entries(res.data.cards)) cards.set(id, card)
  for (const id of res.data.missing) missing.add(id)
  revision.value++
}

/** After a lookup grounds a term, or when `index_version` moves. */
export function invalidate(): void {
  cards.clear()
  missing.clear()
  revision.value++
  triggerRef(revision)
}
