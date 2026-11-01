/**
 * Where the graph's nodes were last time, per scope, across sessions.
 *
 * The cache this replaces was one `sessionStorage` slot (`venona.graph.positions`)
 * holding one snapshot, and all three of its properties were wrong for the job:
 *
 *  - it died with the tab, so the arrival that most wants it -- opening the app
 *    in the morning -- never had one;
 *  - it was a single global slot, so the whole graph, a brief's subgraph and a
 *    filtered view overwrote each other, and a snapshot of one scope was tested
 *    against the canvas of another;
 *  - 10,000 nodes is ~400KB of JSON, which is most of a 5MB `sessionStorage`
 *    budget shared with everything else the app stores.
 *
 * IndexedDB answers all three: it survives the tab, it is keyed, and its quota
 * is measured in hundreds of megabytes.
 *
 * NOTHING HERE IS ALLOWED TO FAIL LOUDLY. Positions are derived data -- a miss
 * is a slower render, never a wrong one -- so every operation resolves to a
 * null-ish value rather than rejecting, and a browser with IndexedDB blocked or
 * a quota exhausted simply always lays out cold. That is also why positions are
 * NOT persisted server-side: node coordinates are not a fact about the corpus,
 * and SQLite is the system of record for facts about the corpus.
 */

export type Positions = Record<string, { x: number; y: number }>

interface Entry {
  key: string
  positions: Positions
  saved_at: number
  /**
   * How many positions this snapshot holds, so eviction can weigh what it
   * costs to rebuild rather than only how old it is.
   *
   * Age alone evicts the whole-graph snapshot -- the most expensive one in the
   * cache and the one every cold start needs -- as soon as a few filtered
   * scopes are visited after it. Rebuilding a filtered scope is a small
   * layout; rebuilding the whole graph is the full cold fcose the cache exists
   * to avoid.
   *
   * OPTIONAL, and not as a convenience: rows already sitting in a reader's
   * IndexedDB were written without this field, and `DB_VERSION` is unchanged
   * because a bump to add one number would throw their positions away. A row
   * with no weight sorts as 0, so it is evicted before anything measured --
   * which is right, since it is also the oldest.
   */
  weight?: number
}

const DB_NAME = 'venona.graph'
const DB_VERSION = 1
const STORE = 'positions'

/**
 * How many scopes to keep.
 *
 * A scope is a mode plus its filter state, so a session that toggles two
 * predicate chips and a type has already produced several. Eight covers the
 * shapes anyone returns to -- the whole graph, the whole graph with the
 * unconnected shown, today's brief, a filtered view or two -- at roughly 400KB
 * each in the worst case this was measured against (2026-08-16).
 */
const MAX_SCOPES = 8

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') {
      resolve(null)
      return
    }
    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION)
      request.onupgradeneeded = () => {
        const db = request.result
        if (db.objectStoreNames.contains(STORE)) return
        // Keyed by scope, indexed by age. Eviction reads every row back rather
        // than walking the index, because it has to weigh rebuild cost as well
        // as age -- at MAX_SCOPES rows that is a handful of records.
        db.createObjectStore(STORE, { keyPath: 'key' }).createIndex('saved_at', 'saved_at')
      }
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => resolve(null)
      // A version change from another tab. Not an error worth reporting; this
      // is a cache.
      request.onblocked = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
  return dbPromise
}

export async function readPositions(key: string): Promise<Positions | null> {
  const db = await openDb()
  if (!db) return null
  return new Promise((resolve) => {
    try {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(key)
      request.onsuccess = () => resolve((request.result as Entry | undefined)?.positions ?? null)
      request.onerror = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

export async function writePositions(key: string, positions: Positions): Promise<void> {
  const db = await openDb()
  if (!db) return
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, 'readwrite')
      const store = tx.objectStore(STORE)
      store.put({
        key,
        positions,
        saved_at: Date.now(),
        weight: Object.keys(positions).length,
      } satisfies Entry)

      // Evict oldest-first among the rows that are NOT the most expensive one,
      // in the same transaction as the write that caused the overflow -- a
      // separate one could be interleaved with another tab's and delete the
      // entry it had just written.
      //
      // Every row is read back rather than walked by the age index. At
      // MAX_SCOPES rows that is a handful of records, and it is the only way to
      // know which row is the expensive one; the index stays for reads.
      const all = store.getAll()
      all.onsuccess = () => {
        const rows = (all.result as Entry[] | undefined) ?? []
        let excess = rows.length - MAX_SCOPES
        if (excess <= 0) return
        const heaviest = Math.max(...rows.map((row) => row.weight ?? 0))
        const dropped = new Set<string>()
        const candidates = rows
          .filter((row) => (row.weight ?? 0) < heaviest)
          .sort((a, b) => a.saved_at - b.saved_at)
        for (const row of candidates) {
          if (excess <= 0) break
          store.delete(row.key)
          dropped.add(row.key)
          excess -= 1
        }
        // Not enough rows outside the protected weight to cover the overflow --
        // either every row ties (one scope, or all the same size) or the light
        // ones ran out. Age decides from here, and `dropped` is why the walk
        // skips what the pass above already removed: a repeat `delete` on a
        // key that is gone succeeds and still spends one of the evictions, so
        // without it the store stays over capacity and the cache silently
        // stops being bounded at all.
        if (excess > 0) {
          const byAge = [...rows]
            .filter((row) => !dropped.has(row.key))
            .sort((a, b) => a.saved_at - b.saved_at)
          for (const row of byAge) {
            if (excess <= 0) break
            store.delete(row.key)
            excess -= 1
          }
        }
      }

      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    } catch {
      resolve()
    }
  })
}
