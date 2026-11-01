<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, get } from '@/api/client'
import EmptyState from '@/components/ui/EmptyState.vue'
import FactList from '@/components/ui/FactList.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import StatBlock from '@/components/ui/StatBlock.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'

/**
 * The figures, and the domains.
 *
 * FIGURES RATHER THAN THE LISTS THEMSELVES. Requests, launch signups and
 * accounts all only arrive, so a front page that renders every row gets slower
 * and less readable exactly as the thing it reports on succeeds. The rail
 * carries the way through to each of them, which is why there are no buttons
 * here saying so.
 *
 * The domains table stays whole: they are registered by hand, one at a time,
 * and whether they are all still answering is the question this page exists to
 * settle at a glance.
 */
interface Domain {
  slug: string
  display_name: string
  status: string
  last_seen_at: string | null
  poll_misses: number
  version: string | null
}

const COLUMNS: Column[] = [
  { key: 'display_name', label: 'Name' },
  { key: 'slug', label: 'Short name', hideBelow: 'sm' },
  { key: 'status', label: 'Status' },
  { key: 'version', label: 'Version', hideBelow: 'sm' },
  { key: 'last_seen_at', label: 'Last seen', hideBelow: 'md' },
  { key: 'poll_misses', label: 'Missed check-ins', numeric: true },
]

/** A dash for a domain that has not said, which is not a domain running
 *  nothing. `DomainsView` carries the same rule and the same reason. */
const said = (value: unknown): string =>
  value === null || value === undefined || value === '' ? '—' : String(value)

const data = ref<{
  pending: number
  waiting: number
  accounts: number
  domains: Domain[]
  version: string
  commit: string | null
  started_at: string | null
  site_version: string | null
  site_commit: string | null
  site_built_at: string | null
  site_seen_at: string | null
} | null>(null)
const loading = ref(true)
const error = ref('')

const ownFacts = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { label: 'Version', value: d.version },
    { label: 'Build', value: said(d.commit) },
    { label: 'Started', value: stamp(d.started_at) },
    { label: 'Site version', value: said(d.site_version) },
    { label: 'Site build', value: said(d.site_commit) },
    { label: 'Site built', value: stamp(d.site_built_at) },
    { label: 'Site last read', value: stamp(d.site_seen_at) },
  ]
})

onMounted(async () => {
  try {
    data.value = await get('/overview')
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <div v-if="data" class="flex flex-col gap-4">
      <div class="figures flex flex-wrap gap-6">
        <StatBlock
          :value="data.pending"
          label="waiting on a decision"
          :tone="data.pending ? 'high' : 'neutral'"
        />
        <StatBlock :value="data.waiting" label="waiting for launch" />
        <StatBlock :value="data.accounts" label="active accounts" />
        <StatBlock :value="data.domains.length" label="domains" />
      </div>

      <!-- The glance, and a way through to the reading. The rail names Domains
           too; a row that also goes there is the app's own pattern -- its
           overview sends a pending-request row to the page that manages it. -->
      <Panel variant="bare" title="Domains">
        <Table
          :columns="COLUMNS"
          :rows="data.domains"
          row-key="slug"
          selectable
          @select="$router.push('/admin/domains')"
        >
          <!-- A dash, never a blank cell: a domain that has not answered and
               one running an unnamed build must not look the same here. -->
          <template #cell:version="{ row }">
            <span class="mono text-xs whitespace-nowrap">{{ said(row.version) }}</span>
          </template>
          <template #cell:last_seen_at="{ row }">
            <span class="age mono font-semibold text-xs whitespace-nowrap">{{
              stamp(row.last_seen_at as string)
            }}</span>
          </template>
        </Table>
        <EmptyState v-if="!data.domains.length" title="No domains registered.">
          A domain is registered from the command line, and appears here once it
          answers.
        </EmptyState>
      </Panel>

      <!-- The hub and the site are the other two things that deploy, and the
           table above cannot show either. The hub's own reading is live; the
           site's is the last one the poller got, because a static site has no
           process to ask and publishes a file instead. -->
      <Panel variant="bare" title="This hub">
        <FactList :facts="ownFacts" />
      </Panel>
    </div>
  </PaneState>
</template>
