import { computed, getCurrentInstance, onBeforeUnmount, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { ApiError, apiDelete, apiGet, apiPut, errorText } from '@/api/client'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { useConfigDocsStore } from '@/stores/configDocs'
import { useToastsStore } from '@/stores/toasts'
import type {
  ConfigConflict,
  ConfigDoc,
  ConfigDocMeta,
  ConfigSnapshotFull,
  SaveResponse,
  ServerDraft,
  StoredDraft,
} from '@/types/config'

/**
 * Loading, dirty-tracking and saving for the config editor.
 *
 * Two shipped bugs live here and both are easy to reintroduce: opening a
 * document must not mark it unsaved, and mutating a `Set` is not reactive. The
 * comments on `syncDraftFlag` and `restoreDraftFlags` are the record of those.
 *
 * **Function-scoped on purpose, not a module singleton.** `useKeyBindings`
 * installs its teardown only when there is a current component instance, so a
 * singleton would register `mod+s` once at import and never remove it -- and
 * ⌘S would save a config document from anywhere in the app.
 */
export function useConfigDocs() {
  const toasts = useToastsStore()

  /** The list is the shared store's: the index and the ⌘K palette render the
   *  same rows, and a private copy here would duplicate `GET /config` on every
   *  editor mount. Everything below the list stays function-scoped. */
  const listStore = useConfigDocsStore()
  const { items: docs } = storeToRefs(listStore)
  const active = ref<string | null>(null)
  const doc = ref<ConfigDoc | null>(null)
  const draft = ref('')
  const saving = ref(false)
  const loading = ref(false)
  /** The on-disk content when the file changed underneath us (409). */
  const conflict = ref<string | null>(null)
  /**
   * A validation failure, or something the server wants to say about what did
   * NOT take effect. Deliberately not a toast: `stores/toasts.ts` draws that
   * line itself -- fire-and-forget only -- and every sentence set here asks the
   * reader to do something. A refused save and a failed draft backup are both
   * lost work if they leave the screen on a timer.
   */
  const notice = ref<string | null>(null)

  /** Ordered across keys, not per key: the loser is whoever the user left. */
  const selectGuard = useStaleGuard()

  const readonly = computed(() => doc.value?.readonly === true)
  const dirty = computed(
    () => doc.value !== null && !doc.value.readonly && draft.value !== doc.value.content,
  )

  /** Which documents have an unsaved change, reactively. */
  const drafts = ref(new Set<string>())
  /**
   * Of those, the ones whose draft was taken against a version of the file that
   * has since been replaced -- the file moved underneath the buffer.
   *
   * Never auto-discarded: it may be the only copy of something you typed. But
   * it is not the same state as "unsaved", it is the dangerous one (Save writes
   * this buffer over whatever the file says), so it must be visible.
   */
  const staleDrafts = ref(new Set<string>())
  /** The disk version the ACTIVE document's buffer diverged from. */
  const activeBase = ref<string | null>(null)
  /** The saved version currently in the buffer, when one was restored. */
  const restoredFrom = ref<ConfigSnapshotFull | null>(null)
  /** What the buffer held before that restore, so it can be handed back. */
  const priorBuffer = ref<string | null>(null)
  /** And the version it was taken against, which is what makes it stale. */
  const priorBase = ref<string | null>(null)
  /**
   * A server-side generated draft for the ACTIVE document (the setup run's
   * output, staged for review). Loaded into the buffer so the editor's
   * ordinary dirty/diff machinery renders the review; Save IS apply. A local
   * draft the user typed still wins the buffer -- their work, not ours -- but
   * never silently: when both exist the view shows the choice, because a
   * shadowed draft reads as "the regeneration didn't work".
   */
  const serverDraft = ref<ServerDraft | null>(null)
  /**
   * The buffer was seeded from the staged draft, so edits to it are
   * amendments to the draft ("fix the buffer, or discard") rather than a
   * rival copy. A session flag, not a content comparison: comparing would
   * flip the review banner into the two-copies choice on the first keystroke
   * of a fix.
   */
  const reviewing = ref(false)
  /**
   * A draft is staged but the editor is showing something else -- the state
   * the view must never leave unnamed, because the pane looks exactly like
   * "the regeneration produced nothing".
   */
  const serverDraftShadowed = computed(() => serverDraft.value !== null && !reviewing.value)

  function draftKey(key: string) {
    return `venona.draft.${key}`
  }

  /**
   * `version` is optional on `ConfigDocMeta`: an assembled prompt's list entry
   * carries none, because it is rendered only when opened. An absent version
   * reads as `''`, the same value a pre-versioned draft gets, and unknown on
   * both sides compares equal -- which does not manufacture a divergence
   * warning out of a field the server simply did not send.
   */
  function versionOf(document: { version?: string | null } | null): string {
    return document?.version ?? ''
  }

  /**
   * Drafts written before they carried a base version are bare strings. They
   * are read as `base: ''`, which no real version matches -- so a legacy draft
   * reports as diverged and gets the discard affordance rather than being
   * silently binned or silently trusted.
   */
  function readStoredDraft(key: string): StoredDraft | null {
    const raw = localStorage.getItem(draftKey(key))
    if (raw === null) return null
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed.content === 'string' && typeof parsed.base === 'string') {
        return parsed as StoredDraft
      }
    } catch {
      /* not JSON: a pre-versioned draft, handled below */
    }
    return { content: raw, base: '' }
  }

  /**
   * localStorage writes are debounced; the unsaved marker is not.
   *
   * `syncDraftFlag` runs on every keystroke. Writing there meant a synchronous
   * main-thread write of the WHOLE document per character — `relevance_profile.md`
   * is several KB and `config_service.MAX_BYTES` allows 256KB — and a
   * QuotaExceededError thrown from inside a Vue watcher, which aborts the rest
   * of the handler and silently stops the unsaved mark from updating. The flag
   * is what the UI reads, so it stays immediate; the backup can wait 400ms.
   */
  const DRAFT_FLUSH_MS = 400
  /** key -> draft, or null to remove. Coalesced, so N keystrokes are 1 write. */
  const queued = new Map<string, StoredDraft | null>()
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  function flushDrafts() {
    if (flushTimer !== null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    for (const [key, value] of queued) {
      try {
        if (value === null) localStorage.removeItem(draftKey(key))
        else localStorage.setItem(draftKey(key), JSON.stringify(value))
      } catch {
        // Out of quota, or storage disabled. The editor still holds the text —
        // only the reload-survives-it guarantee is gone, and saying so is the
        // difference between losing work and choosing to.
        notice.value =
          'Local draft backup failed. The editor still has your text, ' +
          'but a reload would lose it. Save now.'
      }
    }
    queued.clear()
  }

  function queueDraftWrite(key: string, value: StoredDraft | null) {
    queued.set(key, value)
    if (flushTimer === null) flushTimer = setTimeout(flushDrafts, DRAFT_FLUSH_MS)
  }

  /**
   * `base` is threaded through rather than read from the loaded document,
   * because a draft must keep the version it ORIGINALLY diverged from. Stamping
   * it with the current version on every keystroke would erase the divergence
   * the moment the user touched a stale buffer -- which is precisely when they
   * need to be told.
   */
  function syncDraftFlag(key: string, value: string, saved: string, base: string) {
    if (value === saved) {
      queueDraftWrite(key, null)
      drafts.value.delete(key)
      staleDrafts.value.delete(key)
    } else if (serverDraft.value?.key === key && serverDraft.value.content === value) {
      // The buffer IS the staged draft, verbatim. The server holds that copy,
      // so persisting it here would let it outlive its own discard -- and
      // shadow the regenerated draft the next time this document opens, which
      // is how "the repair didn't work" gets reported against a repair that
      // worked. The index marks this state "draft to review", not "unsaved".
      queueDraftWrite(key, null)
      drafts.value.delete(key)
      staleDrafts.value.delete(key)
    } else {
      queueDraftWrite(key, { content: value, base })
      drafts.value.add(key)
    }
    // Reassign so the template sees the change; Set mutation is not reactive.
    drafts.value = new Set(drafts.value)
    staleDrafts.value = new Set(staleDrafts.value)
  }

  // A reload inside the debounce window would otherwise lose the last few
  // keystrokes, which is exactly what the draft exists to prevent.
  if (getCurrentInstance()) {
    window.addEventListener('pagehide', flushDrafts)
    onBeforeUnmount(() => {
      window.removeEventListener('pagehide', flushDrafts)
      flushDrafts()
    })
  }

  /**
   * Reconcile stored drafts against what is actually on disk.
   *
   * A stored draft cannot be trusted to BE a change: a browser can hold one
   * left by a build that wrote a draft on every open, byte-identical to the
   * file, and a marker on a document nobody edited is noise. Rather than trust
   * them (wrong) or bin them (throws away real work), fetch and compare.
   */
  async function restoreDraftFlags(items: ConfigDocMeta[]) {
    flushDrafts() // a queued write is a draft too; reconcile against all of them
    const stored = items.filter(
      (d) => !d.readonly && localStorage.getItem(draftKey(d.key)) !== null,
    )
    // UTF-8, because `ConfigSummary.bytes` is the size on disk, not the JS
    // string length — the two disagree on any non-ASCII character, and a
    // length comparison would then claim divergence a byte count refutes.
    const utf8 = new TextEncoder()
    const real = await Promise.all(
      stored.map(async (d) => {
        const kept = readStoredDraft(d.key)
        if (kept === null) return null
        // Verified against the LIST METADATA first, fetching a body only for
        // the one genuinely ambiguous case. A stored draft answers "is this a
        // real change?" from `version` and `bytes` alone almost always:
        //   - base ≠ current version: the file moved under it — kept, stale.
        //     (A draft that happens to match the NEWER disk content keeps its
        //     marker where a body fetch would have binned it; the discard
        //     affordance covers that oddity more cheaply than a fetch per
        //     draft per admin-page visit covered everything.)
        //   - same base, different byte count: a real edit — kept, current.
        //   - same base, same byte count: identical or a same-length edit;
        //     only here does the body settle it.
        if (kept.base !== versionOf(d)) return { key: d.key, stale: true }
        if (typeof d.bytes === 'number' && utf8.encode(kept.content).length !== d.bytes) {
          return { key: d.key, stale: false }
        }
        try {
          const full = await apiGet<ConfigDoc>(`/config/${d.key}`)
          if (kept.content === full.content) {
            localStorage.removeItem(draftKey(d.key))
            return null
          }
          return { key: d.key, stale: kept.base !== full.version }
        } catch {
          // Cannot verify; leave the draft and its marker alone rather than
          // silently discarding something the user typed.
          return { key: d.key, stale: false }
        }
      }),
    )
    const live = real.filter((r): r is { key: string; stale: boolean } => r !== null)
    drafts.value = new Set(live.map((r) => r.key))
    staleDrafts.value = new Set(live.filter((r) => r.stale).map((r) => r.key))
  }

  async function loadList(force = false): Promise<ConfigDocMeta[]> {
    const items = await listStore.load(force)
    await restoreDraftFlags(items)
    return items
  }

  /**
   * No confirmation on switching documents: the draft is written to
   * localStorage on every keystroke and read back here, so switching away and
   * back returns exactly what was typed. The index marks unsaved documents in
   * words, and this page counts the others -- information, not an interruption.
   */
  async function select(key: string) {
    // `ConfigView` reaches here from both its mount and its route watcher, so
    // one key can arrive twice. Without this they double-fetch, and the second
    // response can clobber a keystroke made between them.
    if (key === active.value && doc.value !== null) return

    // The buffer is read back from localStorage below, so anything still
    // queued has to land before we switch away from it.
    flushDrafts()

    // Roll the selection back on a failed fetch. Leaving `active` on the new
    // key while `doc` still holds the previous document leaves the URL and tab
    // title naming one file while the editor shows another, and Save would PUT
    // the stale content to the new key's endpoint.
    const previous = { key: active.value, doc: doc.value, draft: draft.value }

    // Every assignment below happens after an await, so a slow response for a
    // document the user has already left must not land: it would pair one key's
    // buffer with another key's `active`, and Save PUTs the buffer to `active`.
    const isCurrent = selectGuard.claim()

    active.value = key
    conflict.value = null
    notice.value = null
    loading.value = true
    try {
      const meta = docs.value.find((d) => d.key === key)
      const path = meta?.readonly ? `/config/assembled/${key}` : `/config/${key}`
      const loaded = await apiGet<ConfigDoc>(path)
      if (!isCurrent()) return
      doc.value = loaded

      // Cleared for EVERY document, the readonly ones included -- otherwise
      // the previous document's staged draft keeps its banner over a file it
      // does not belong to. A restore banner is the same hazard and clears
      // with it.
      serverDraft.value = null
      reviewing.value = false
      restoredFrom.value = null
      priorBuffer.value = null
      priorBase.value = null

      if (loaded.readonly) {
        draft.value = loaded.content
        activeBase.value = null
        return
      }
      const kept = readStoredDraft(key)
      const restoring = kept !== null && kept.content !== loaded.content

      if (meta?.has_draft) {
        try {
          const staged = await apiGet<ServerDraft>(`/config/${key}/draft`)
          if (!isCurrent()) return
          serverDraft.value = staged
        } catch {
          /* listed a moment ago but gone now -- applied or discarded elsewhere */
        }
      }

      if (!restoring && serverDraft.value) {
        // Review = the generated content in the buffer, diffed against disk by
        // the machinery that already exists. Not written to localStorage: the
        // server holds it, and a localStorage copy would survive its discard.
        draft.value = serverDraft.value.content
        activeBase.value = versionOf(loaded)
        reviewing.value = true
        return
      }
      draft.value = restoring ? kept.content : loaded.content
      // A kept copy that is byte-identical to the staged draft is a review,
      // not a rival: syncDraftFlag below hands the copy back to the server.
      reviewing.value = serverDraft.value !== null && draft.value === serverDraft.value.content
      // Preserve the version the restored draft diverged from; a buffer that
      // matches disk starts fresh from what is on disk now.
      activeBase.value = restoring ? kept.base : versionOf(loaded)
      syncDraftFlag(key, draft.value, loaded.content, activeBase.value ?? versionOf(loaded))
    } catch {
      // A superseded failure must not roll back: the document it would restore
      // is the one the user just successfully opened.
      if (!isCurrent()) return
      active.value = previous.key
      doc.value = previous.doc
      draft.value = previous.draft
      toasts.push({ kind: 'error', text: `could not open ${key}` })
    } finally {
      // The spinner belongs to whichever request is still wanted.
      if (isCurrent()) loading.value = false
    }
  }

  /** A tab reload would lose the buffer, so keep a local draft per document. */
  function onDraftChanged(value: string) {
    if (active.value && doc.value && !doc.value.readonly) {
      syncDraftFlag(active.value, value, doc.value.content, activeBase.value ?? versionOf(doc.value))
    }
  }

  /**
   * Throw the buffer away and take what is on disk.
   *
   * A draft is kept forever unless it matches the file byte for byte -- correct,
   * since binning it would throw away real work -- so without this a stale
   * buffer marks a document unsaved indefinitely and the only other button is
   * Save, which writes that buffer over the file. Deliberately not a
   * confirmation dialog: the draft is by definition different from disk, the
   * pane says so and shows where, and the discard is one click to reproduce by
   * retyping. A modal on every discard is the thing you learn to click through.
   */
  function discardDraft() {
    const current = doc.value
    if (!current || !active.value || current.readonly) return
    const key = active.value
    draft.value = current.content
    activeBase.value = versionOf(current)
    restoredFrom.value = null
    priorBuffer.value = null
    priorBase.value = null
    queueDraftWrite(key, null)
    flushDrafts()
    drafts.value.delete(key)
    staleDrafts.value.delete(key)
    drafts.value = new Set(drafts.value)
    staleDrafts.value = new Set(staleDrafts.value)
    notice.value = null
  }

  /**
   * The active buffer was taken against a version that has since been replaced.
   * `''` is a pre-versioned draft, which no real version matches -- unknown
   * provenance is reported as diverged rather than assumed safe.
   */
  const draftDiverged = computed(
    () =>
      dirty.value &&
      doc.value !== null &&
      activeBase.value !== null &&
      activeBase.value !== versionOf(doc.value),
  )

  async function save() {
    const current = doc.value
    if (!current || !active.value || !dirty.value) return
    saving.value = true
    conflict.value = null
    notice.value = null
    const key = active.value
    // Decided BEFORE the write, while the buffer still says which copy it is.
    // Saving applies the staged draft only when the buffer holds the draft or
    // an amendment of it; saving local edits that were merely coexisting with
    // a staged draft must leave the draft staged, or the save quietly destroys
    // a document the user has never seen.
    const consumesDraft =
      serverDraft.value?.key === key &&
      (reviewing.value || draft.value === serverDraft.value.content)
    try {
      // Through `apiPut`, like everything else. A raw `fetch` here buys nothing
      // -- `ApiError.body` carries the whole parsed 409 body, the recovery shape
      // `client.spec.ts` covers for the glossary's lookup -- and costs the 30s
      // timeout, the `accept` header and the 401 hook: a session expiring with
      // this editor open renders `save failed (401)` and sits there instead of
      // bouncing to /login with somewhere to come back to.
      const saved = await apiPut<SaveResponse>(`/config/${key}`, {
        content: draft.value,
        base_version: current.version,
      })

      doc.value = saved
      draft.value = saved.content
      // The buffer now IS the file, so the next edit diverges from this. A
      // restored version that has landed is the file too, so the banner
      // describing it as a restore in progress goes with it.
      activeBase.value = versionOf(saved)
      restoredFrom.value = null
      priorBuffer.value = null
      priorBase.value = null
      drafts.value.delete(key)
      staleDrafts.value.delete(key)
      drafts.value = new Set(drafts.value)
      staleDrafts.value = new Set(staleDrafts.value)
      queueDraftWrite(key, null)
      flushDrafts()
      if (consumesDraft) {
        // The reviewed content just landed through the one write path; the
        // staged copy is spent. Best-effort: a failed delete leaves a stale
        // "generated" badge, not a wrong document.
        try {
          await apiDelete(`/config/${key}/draft`)
        } catch {
          /* the badge clears on the next successful list refresh */
        }
        serverDraft.value = null
        reviewing.value = false
      }
      toasts.push({ kind: 'success', text: 'saved', subject: saved.title })

      if (saved.restart_required?.length) {
        // Sentences from the server, not field names: it reports what the save
        // did not reach, which is a fact about the running process rather than
        // about the document.
        notice.value =
          `Saved, but not all of it is in use yet: ${saved.restart_required.join('; ')}. ` +
          `Restarting the server applies the rest.`
      }
      // Refreshing the list is cosmetic and must not be able to report a
      // completed save as a failed one.
      try {
        await loadList(true)
      } catch {
        /* the index keeps the marks and ages it already had */
      }
    } catch (err) {
      // THE RECOVERY PATH. A 409 means the file moved under us, and its body
      // carries the version and content on disk -- which is the only way back.
      // `ApiError.body` is the WHOLE parsed body, so the detail is one level
      // deeper than it looks; reading `err.body` directly here would silently
      // yield `undefined` and lose the disk version, which is precisely the
      // failure that makes every later save 409 forever.
      if (err instanceof ApiError && err.status === 409) {
        const detail = (err.body as ConfigConflict | null)?.detail
        conflict.value = detail?.current_content ?? ''
        notice.value = detail?.message ?? 'changed on disk'
        // Adopt the version on disk, or the recovery path never recovers:
        // `base_version` would stay the hash from before the file moved under
        // us, so every subsequent save — including one that takes the disk
        // content wholesale — 409s again until the page is reloaded.
        if (typeof detail?.current_version === 'string') {
          doc.value = {
            ...current,
            version: detail.current_version,
            content: detail.current_content ?? current.content,
          }
        }
        return
      }
      // A validation failure names a source id, an axis, a predicate. It is
      // read and acted on, so it stays on screen until the next save.
      if (err instanceof ApiError) {
        notice.value = errorText(err, `save failed (${err.status})`)
        return
      }
      // The API restarting is the likeliest cause, and it is what the
      // `restart_required` notice just told the user to do. Without this the
      // rejection escaped through `run: () => void save()` as an unhandled
      // promise and the Save button simply re-enabled, looking like a no-op.
      notice.value = 'Could not reach the server, so nothing was saved. Your text is still here.'
    } finally {
      saving.value = false
    }
  }

  /**
   * Reject the generated draft: delete it server-side and put the disk content
   * back in the buffer. Distinct from `discardDraft` (the localStorage one)
   * because the copies live in different places and only this one is shared
   * state another browser could see.
   */
  async function discardServerDraft() {
    const current = doc.value
    if (!current || !active.value || !serverDraft.value) return
    const key = active.value
    try {
      await apiDelete(`/config/${key}/draft`)
    } catch (err) {
      notice.value = errorText(err, 'could not discard the draft')
      return
    }
    serverDraft.value = null
    // The buffer goes back to disk only when it was showing the draft. When
    // the discard was "keep my edits" in the two-copies choice, the edits are
    // the thing being kept -- resetting the buffer would discard both.
    if (reviewing.value) {
      draft.value = current.content
      activeBase.value = versionOf(current)
      reviewing.value = false
    }
    try {
      await loadList(true)
    } catch {
      /* cosmetic refresh; the index keeps what it had */
    }
  }

  /**
   * The other half of the two-copies choice: put the staged draft in the
   * buffer and let the kept local edits go. One click and no dialog, the same
   * reasoning as `discardDraft` -- the banner has already said which copy dies.
   */
  function reviewServerDraft() {
    const current = doc.value
    const staged = serverDraft.value
    if (!current || !active.value || current.readonly || !staged) return
    const key = active.value
    draft.value = staged.content
    activeBase.value = versionOf(current)
    reviewing.value = true
    queueDraftWrite(key, null)
    flushDrafts()
    drafts.value.delete(key)
    staleDrafts.value.delete(key)
    drafts.value = new Set(drafts.value)
    staleDrafts.value = new Set(staleDrafts.value)
  }

  /**
   * Put a saved version in the buffer.
   *
   * IT LIVES HERE AND NOT IN THE VIEW, because `activeBase` does not leave this
   * module and the whole correctness of a restore is in that one line. Set
   * `draft` alone and the buffer is a snapshot taken against whatever base the
   * previous draft carried: `draftDiverged` then reads true and the pane warns
   * that "saving would overwrite the current file with what is in the editor"
   * about a restore the reader just chose, while the watcher on `draft` writes
   * the snapshot to localStorage as if it were typed work -- which survives a
   * reload and marks the document unsaved indefinitely.
   *
   * Anchored to the version ON DISK, so Save carries a `base_version` the
   * server accepts and the restore is an ordinary edit: validated, snapshotted,
   * conflict-checked.
   */
  function restoreSnapshot(snapshot: ConfigSnapshotFull) {
    const current = doc.value
    if (!current || !active.value || current.readonly) return
    // ONLY ON THE FIRST RESTORE. A second one would capture the first
    // snapshot's text as "the reader's edits", so the way back would hand them
    // a version they never typed and the real work would be gone with no undo.
    if (restoredFrom.value === null) {
      priorBuffer.value = draft.value === current.content ? null : draft.value
      // The base travels with the buffer. Without it a stale draft comes back
      // anchored to the CURRENT version, which erases the one warning that
      // stands between it and overwriting a file it has never seen.
      priorBase.value = activeBase.value
    }
    restoredFrom.value = snapshot
    draft.value = snapshot.content
    activeBase.value = versionOf(current)
    // The buffer holds the snapshot, not the staged draft, so the banner
    // announcing that the editor shows that draft would be false. The draft
    // itself is untouched on the server and returns if this is discarded.
    reviewing.value = false
    // AND IT IS PERSISTED, unlike a reviewed server draft. That one is not
    // written because discarding it server-side would leave a local copy
    // shadowing the next regeneration; a saved version cannot be discarded, so
    // the hazard does not exist here and a reader who restores, walks away and
    // reloads should find what they chose still on screen.
  }

  /** Undo a restore: back to the buffer, and the base, that it replaced. */
  function keepMyEdits() {
    const prior = priorBuffer.value
    const base = priorBase.value
    restoredFrom.value = null
    priorBuffer.value = null
    priorBase.value = null
    if (prior !== null) {
      draft.value = prior
      activeBase.value = base
    } else if (doc.value) {
      draft.value = doc.value.content
      activeBase.value = versionOf(doc.value)
    }
  }

  return {
    docs,
    active,
    doc,
    draft,
    restoredFrom,
    restoreSnapshot,
    keepMyEdits,
    dirty,
    drafts,
    staleDrafts,
    draftDiverged,
    saving,
    loading,
    conflict,
    notice,
    readonly,
    loadList,
    select,
    onDraftChanged,
    save,
    discardDraft,
    serverDraft,
    serverDraftShadowed,
    discardServerDraft,
    reviewServerDraft,
  }
}
