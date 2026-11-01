/**
 * What a connection IS, in the reader's words.
 *
 * The wire speaks the schema's language — a `scope` of `mcp` or `annotate`, a
 * `client_name` that is null when nobody registered one — and none of it may
 * reach a reader: "scope" is a word from `api/authz.py`, and `annotate` names a
 * pipeline stage rather than the browser extension a person actually installed.
 * `learnCopy.ts` is the same layer for the learning surface and the same
 * argument.
 *
 * This exists because the Agents page grew a Disconnect button. While the rows
 * were inert, an unlabelled one cost nothing; beside a button that ends things,
 * a reader who cannot tell their assistant from their browser extension will
 * eventually unplug the wrong one — and both are theirs, both are in the same
 * list, and nothing else on the row distinguishes them.
 */

/** Scope → what that connection can actually read. Display only. */
const REACH_LABELS: Record<string, string> = {
  mcp: 'everything Venona knows',
  annotate: 'names on pages you visit',
}

/** A connection can hold more than one scope: the wire value is the
 *  space-delimited list OAuth granted, so an unsplit lookup renders the raw
 *  `annotate mcp` straight onto the page. */
export function connectionReach(scope: string | null | undefined): string {
  if (!scope) return 'unknown'
  const labels = scope
    .split(' ')
    .filter(Boolean)
    .map((name) => REACH_LABELS[name] ?? name)
  if (!labels.length) return 'unknown'
  return labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(', ')} and ${labels.at(-1)}`
}

/**
 * Where a connection came from.
 *
 * `client_name` is the name a client registered under, so it is the true answer
 * when there is one — and it is ATTACKER-SUPPLIED: registration is
 * unauthenticated by RFC 7591, so anyone who can reach the server can put a
 * string here. Vue escapes it on the way into the DOM, which is what makes it
 * safe to render; it is trimmed and bounded here so a very long or padded name
 * cannot push the rest of the row out of the table.
 *
 * Falling back to the label rather than to a constant keeps the CLI's
 * `--label "claude-code on the laptop"` visible, which is the whole reason
 * somebody types one.
 */
export function connectionOrigin(
  clientName: string | null | undefined,
  label: string | null | undefined,
): string {
  const registered = (clientName ?? '').trim()
  if (registered) return truncate(registered)
  const typed = (label ?? '').trim()
  return typed ? truncate(typed) : 'made in a terminal'
}

/** Long enough for a real client name, short enough that no row can be pushed
 *  sideways by one. */
const MAX_NAME = 60

function truncate(value: string): string {
  return value.length > MAX_NAME ? `${value.slice(0, MAX_NAME - 1)}…` : value
}
