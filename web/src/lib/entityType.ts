/**
 * An ontology type as a reader says it.
 *
 * The types are the DOMAIN's words, not this repo's -- `GovernmentBody`,
 * `ResearchOrg` and `MilitaryUnit` come from `config/ontology.yaml` and change
 * per domain -- so this splits them rather than translating them. A lookup
 * table would be a domain term in a string literal, which `make lint-domain`
 * forbids and which would go stale the moment somebody adds a type.
 *
 * Every one of these is rendered uppercase with label tracking, and camel case
 * collapses under that: `GovernmentBody` reads as GOVERNMENTBODY, one long word
 * with no seam. Splitting on the case boundary is the whole job.
 *
 *   GovernmentBody -> Government body      ResearchOrg -> Research org
 *   Company        -> Company              MilitaryUnit -> Military unit
 */
export function entityTypeLabel(type: string | null | undefined): string {
  const raw = (type ?? '').trim()
  if (!raw) return ''
  // Only a lower-to-upper boundary, so an acronym-shaped type stays whole.
  const spaced = raw.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
  return spaced.charAt(0) + spaced.slice(1).toLowerCase()
}
