/**
 * The one place an id becomes a colour.
 *
 * `tokens.css` names the entity hues after the C4 id prefix so a consumer can
 * write `var(--entity-${id.split(':')[0]})` with no lookup table -- and that
 * derivation is right for every ontology prefix and WRONG for the one id
 * namespace the ontology does not own:
 *
 *   `company:thales` -> `company` -> `--entity-company`   ✓
 *   `gloss:mosa`     -> `gloss`   -> `--entity-gloss`     ✗  it is `--entity-glossary`
 *
 * A custom property that does not exist is not a CSS error: it resolves to
 * nothing and the element renders with no colour at all. `lint_tokens.py`'s
 * third check exists for exactly that and cannot see this one, because the name
 * is assembled at runtime.
 *
 * Every site that turns an id into a hue comes through here -- the card, the
 * mini graph, the focus panel and the graph search dot. That is the point of
 * the file: the mapping is one line of data, and a second copy of the
 * derivation is a second place for `gloss:` to render colourless.
 */
import type { Card } from '@/types/brief'

/** Prefixes whose token name is not the prefix. */
const TOKEN_FOR_PREFIX: Record<string, string> = { gloss: 'glossary' }

/**
 * A `var(--entity-*)` reference for anything with a C4 id.
 *
 * Keyed on `kind` before `id`, because kind is the thing being coloured: an
 * ungrounded acronym is `undefined` whatever its id says, and that is the state
 * the reader is being told about.
 */
export function entityColourVar(card: Pick<Card, 'id' | 'kind'>): string {
  if (card.kind === 'undefined') return 'var(--entity-undefined)'
  const prefix = card.id.split(':')[0]
  return `var(--entity-${TOKEN_FOR_PREFIX[prefix] ?? prefix})`
}
