/**
 * Entity colour at runtime: the ontology's slot assignments become CSS.
 *
 * `tokens.css` ships a fixed palette (`--entity-slot-1..N`) plus static
 * aliases for the defense prefixes, so the checked-in instance paints with no
 * script. A GENERATED domain's ontology declares its own types, and nothing
 * at build time knows their prefixes -- so this injects, per type:
 *
 *   :root { --entity-<prefix>: var(--entity-slot-N); }
 *   .mention--<prefix> { border-bottom-color: var(--entity-<prefix>); }
 *
 * The alias line is what every consumer already reads
 * (`var(--entity-${prefix})` via `entityColour.ts`, the Cytoscape probe, the
 * chips); the mention line is what `prose.css` carries statically for the
 * defense prefixes. Injecting both makes "add a type in the ontology editor"
 * reach every surface with no build. Idempotent: one <style> element, owned
 * and replaced wholesale.
 *
 * Prefixes are server-validated (`ontology.py::PREFIX_SHAPE`), and the
 * defensive filter here keeps a compromised payload from writing CSS anyway.
 */
import type { OntologyMeta } from '@/types/graph'

const STYLE_ID = 'venona-entity-tokens'
const PREFIX_SHAPE = /^[a-z][a-z0-9]{0,11}$/
const SLOT_SHAPE = /^--entity-slot-[0-9]{1,2}$/

export function applyEntityTokens(ontology: OntologyMeta | null): void {
  if (!ontology?.entity_types?.length) return

  const safe = ontology.entity_types.filter(
    (t) => PREFIX_SHAPE.test(t.prefix) && SLOT_SHAPE.test(t.slot_token ?? ''),
  )
  if (!safe.length) return

  const aliases = safe.map((t) => `  --entity-${t.prefix}: var(${t.slot_token});`).join('\n')
  const mentions = safe
    .map((t) => `.mention--${t.prefix} { border-bottom-color: var(--entity-${t.prefix}); }`)
    .join('\n')

  const css = `:root {\n${aliases}\n}\n${mentions}\n`
  let element = document.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (!element) {
    element = document.createElement('style')
    element.id = STYLE_ID
    document.head.appendChild(element)
  }
  element.textContent = css
}
