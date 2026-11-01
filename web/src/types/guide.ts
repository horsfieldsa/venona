/**
 * Aliases onto the generated schema, like `types/health.ts`.
 *
 * `make types` regenerates `api/schema.d.ts` from the real pydantic models and
 * `make lint-generated` holds it to the tree, so these cannot drift from what
 * `/api/meta/guide` actually serves.
 */

import type { components } from '@/api/schema'

type Schemas = components['schemas']

/** One formatting run: the brief's C3 shape without the entity fields. */
export type GuideRun = Schemas['GuideRun']

/** A block of the guide. `type` is the discriminator the view renders on:
 *  `heading`, `paragraph`, `list_item`, `blockquote`, `code`, `table`, `rule`. */
export type GuideBlock = Schemas['GuideBlock']

export type Guide = Schemas['Guide']
