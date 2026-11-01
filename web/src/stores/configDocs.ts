import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import type { ConfigDocMeta, ConfigGroup, ConfigList } from '@/types/config'

/**
 * The editable-document list, loaded once and shared.
 *
 * Three consumers render from `GET /config` — the configuration index, the
 * editor's own list, and the ⌘K palette's document rows — and each held a
 * private copy, so one hop between admin pages cost up to three identical
 * fetches of a list that changes only when a document is saved. This is the one
 * copy; the `config` freshness stamp (stat-derived, `services/freshness.py`) is
 * what reloads it when a save, the CLI or another tab moves a file.
 *
 * LIST METADATA ONLY. Document content, drafts, dirty-tracking and ⌘S live in
 * `useConfigDocs`, which stays function-scoped for the reasons its docstring
 * gives — this store is what lets a sidebar name the documents without
 * instantiating an editor.
 */
export const useConfigDocsStore = defineStore('configDocs', () => {
  const items = ref<ConfigDocMeta[]>([])
  /** Headings, in the server's declaration order, which IS reading order. */
  const groups = ref<ConfigGroup[]>([])
  const loaded = ref(false)
  /** Coalesced: the index and the palette can ask together and must share one
   *  round trip, not race two. */
  let inflight: Promise<ConfigDocMeta[]> | null = null

  async function load(force = false): Promise<ConfigDocMeta[]> {
    if (loaded.value && !force) return items.value
    if (inflight) return inflight
    inflight = apiGet<ConfigList>('/config')
      .then((data) => {
        items.value = data.items ?? []
        groups.value = data.groups ?? []
        loaded.value = true
        return items.value
      })
      .finally(() => {
        inflight = null
      })
    return inflight
  }

  // A save in this tab forces its own reload for the immediate index marks;
  // this catches everything else -- the CLI, a setup run staging a draft, a
  // second tab. Refetching outright (rather than just dropping `loaded`) keeps
  // the has_draft badges live on whichever admin page is open.
  onFreshness('config', () => {
    if (loaded.value) void load(true)
  })

  /** The group's own title, for a surface that has only the key. */
  function groupTitle(key: string): string {
    return groups.value.find((g) => g.key === key)?.title ?? key
  }

  return { items, groups, loaded, load, groupTitle }
})
