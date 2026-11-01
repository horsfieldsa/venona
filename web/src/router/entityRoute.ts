import type { RouteLocationRaw, RouteParams } from 'vue-router'

/**
 * The /entity route's address, built and parsed in one place.
 *
 * A name's id is `<prefix>:<slug>` and minted once (contract C4), so the slug
 * this route needs already exists and must never be recomputed -- a rename adds
 * an alias and leaves the id alone, which is exactly what makes the address
 * stable enough to send to somebody.
 *
 * The colon becomes a path separator rather than travelling in one segment, so
 * `/entity/company/anduril-industries` needs no percent-encoding and survives
 * being pasted into a chat window, where `company%3A…` does not.
 *
 * Two rules, the same two `graphRoute.ts` holds:
 *
 *  1. `toEntityRoute` and `entityIdFromRoute` are INVERSES.
 *  2. Nothing else builds this URL by hand.
 *
 * The C4 slug charset is `[a-z0-9-]`, so neither half needs escaping. A param
 * that does not match that shape is not a name this build can ask about, and
 * `entityIdFromRoute` returns null rather than sending the server a guess.
 */

/** C4's id shape, one segment at a time. The prefix set is per-domain and comes
 *  from the ontology, so it is matched by SHAPE here rather than by a list this
 *  file would have to keep in step with `config/ontology.yaml`. */
const TYPE_SHAPE = /^[a-z][a-z0-9]{0,15}$/
const SLUG_SHAPE = /^[a-z0-9][a-z0-9-]{0,63}$/

export function toEntityRoute(id: string): RouteLocationRaw {
  const [type, ...rest] = id.split(':')
  return { name: 'entity', params: { type, slug: rest.join(':') } }
}

export function entityIdFromRoute(params: RouteParams): string | null {
  const type = typeof params.type === 'string' ? params.type : ''
  const slug = typeof params.slug === 'string' ? params.slug : ''
  if (!TYPE_SHAPE.test(type) || !SLUG_SHAPE.test(slug)) return null
  return `${type}:${slug}`
}

/** An acronym has a page, but no connections and no articles of its own: it is
 *  a term rather than a name in the graph. Every caller that fetches those two
 *  lists asks this first, so the page says why they are absent instead of
 *  rendering two empty boxes. */
export function isGlossaryId(id: string): boolean {
  return id.startsWith('gloss:')
}
