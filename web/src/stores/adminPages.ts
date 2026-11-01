import { defineStore } from 'pinia'
import { ref } from 'vue'

import type { components } from '@/api/schema'
import type { AuthMode } from '@/types/auth'
import type { DataStats } from '@/types/data'

type AdminOverview = components['schemas']['AdminOverview']
type AccessRequestList = components['schemas']['AccessRequestList']
type AuthorityEventList = components['schemas']['AuthorityEventList']
type AdminUserList = components['schemas']['AdminUserList']
type McpInfo = components['schemas']['McpInfo']
type AdminTokenList = components['schemas']['AdminTokenList']

/**
 * The admin and settings pages' LAST GOOD data, held across navigation.
 *
 * These views held their payloads in per-mount refs, so every hop between
 * Overview, People and Data tore the whole page — rail included — down to a
 * `ViewShell` spinner while three fast requests re-answered questions
 * answered seconds ago. The refs live here so a revisit renders instantly;
 * the views still fetch on every mount, but as a REVALIDATION that updates
 * these in place, flipping their spinner only when a ref is empty (the true
 * first load). No freshness stamp covers the user/event tables, and the
 * per-mount revalidate is what stands in for one — do not add a loaded-guard
 * here without adding the stamp.
 *
 * `overviewRequests` and `peopleRequests` are the SAME endpoint kept twice,
 * deliberately: People fetches it with a `?all=true` scope toggle, and one
 * shared ref would have the Overview panel silently switch to whichever
 * scope People asked for last.
 */
export const useAdminPagesStore = defineStore('adminPages', () => {
  const overview = ref<AdminOverview | null>(null)
  const overviewRequests = ref<AccessRequestList | null>(null)
  const overviewEvents = ref<AuthorityEventList | null>(null)

  const users = ref<AdminUserList | null>(null)
  const peopleRequests = ref<AccessRequestList | null>(null)
  const peopleEvents = ref<AuthorityEventList | null>(null)

  const dataStats = ref<DataStats | null>(null)

  /** Static per deployment (which providers are configured), so unlike the
   *  tables above it never needs revalidating within a session. */
  const authMode = ref<AuthMode | null>(null)

  const mcpInfo = ref<McpInfo | null>(null)
  const instanceTokens = ref<AdminTokenList | null>(null)

  return {
    overview,
    overviewRequests,
    overviewEvents,
    users,
    peopleRequests,
    peopleEvents,
    dataStats,
    authMode,
    mcpInfo,
    instanceTokens,
  }
})
