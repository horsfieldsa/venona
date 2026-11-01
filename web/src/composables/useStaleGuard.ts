/**
 * A monotonic sequence guard for anything that ASSIGNS AFTER AWAITING.
 *
 * THE BUG IT PREVENTS, in its clearest form. Type "an", then "and". Two
 * requests are in flight. If the first is slower than the second — which is
 * exactly what happens when the shorter query matches more rows — its results
 * arrive last and overwrite the ones that match what is actually in the box.
 * The list then shows entities for a query the user has already moved past, and
 * it looks like the search is simply wrong.
 *
 * Typeahead is where it is easiest to see, but it is not the shape of the rule.
 * Any `x.value = await f()` where `f` can be called again before it resolves has
 * it. `stores/graph.ts::loadProvenance` is the same bug with worse consequences
 * and no text box in sight: click edge A then edge B, and A's sentences land
 * under B's heading — a provenance panel attributing the wrong evidence to the
 * wrong relationship.
 *
 * Non-optional by construction: `claim()` hands back a function that answers
 * "is this still the newest?", and there is no way to use this without calling
 * it. A guard you can forget is the one you forget.
 *
 * THE DELIBERATE EXCEPTION. `stores/briefs.ts::runSearch` does NOT use this, and
 * should not be migrated to it. It discriminates on the `query` the server
 * echoes back rather than on a claim ticket, which is strictly stronger here:
 * two callers issuing the SAME query do not invalidate each other, and the
 * discriminator survives the store being recreated. `briefs.spec.ts` states the
 * reason as "the server tells us what it answered".
 */
export function useStaleGuard() {
  let latest = 0

  /** Call before awaiting. The returned predicate is false once superseded. */
  function claim(): () => boolean {
    const mine = ++latest
    return () => mine === latest
  }

  return { claim }
}
