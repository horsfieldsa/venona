/**
 * Render an `unknown` for a reader, without ever writing `[object Object]`.
 *
 * `String(x)` on a value typed `unknown` is a promise that it is a scalar, and
 * three sites were making it about the same value: a run's `params`, which
 * crosses the wire as an opaque JSON blob and is typed `Record<string, unknown>`
 * on purpose (contract C11 lets each run kind carry its own shape). Every one of
 * those three renders into the UI -- the Runs table's subject column, the run
 * detail header, and the live stream's terminal status.
 *
 * `client.ts`'s `ApiError` docstring records what this looks like when it
 * escapes: "Collapsing that to `String(detail)` yields `409: [object Object]`
 * and silently destroys every such recovery path." Same failure, different blob.
 *
 * An object renders as EMPTY rather than as its stringification, because on all
 * three surfaces a blank subject is legible ("this run has no target") while
 * `[object Object]` is not, and because a nested object in `params` means the
 * caller reached for the wrong key -- which is a bug to notice, not to print.
 */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  // Arrays, plain objects, functions, symbols. Nothing a label should contain.
  return ''
}
