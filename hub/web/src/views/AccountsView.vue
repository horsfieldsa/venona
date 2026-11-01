<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { ApiError, get } from '@/api/client'
import EmptyState from '@/components/ui/EmptyState.vue'
import Pager from '@/components/ui/Pager.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'

/**
 * Everyone who can sign in, and what each of them can reach.
 *
 * READ ONLY, deliberately. Suspending an account and changing a role stay on
 * the CLI: they are rare, they are the two that cannot be undone from a
 * browser, and a list of every account is the wrong place to put a control that
 * silently locks somebody out.
 *
 * The memberships arrive as rows rather than a rendered string, so this decides
 * how they read. The server assembling "acme:reader" would be the server
 * deciding a typographic question.
 */
interface Row {
  email: string
  display_name: string | null
  status: string
  memberships: { slug: string; role: string }[]
  last_login_at: string | null
}

const COLUMNS: Column[] = [
  { key: 'email', label: 'Email' },
  { key: 'display_name', label: 'Name', hideBelow: 'md' },
  { key: 'status', label: 'Status' },
  { key: 'memberships', label: 'Member of' },
  { key: 'last_login_at', label: 'Last sign-in', hideBelow: 'sm' },
]

const rows = ref<Row[]>([])
const page = ref(1)
const pages = ref(1)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await get<{ rows: Row[]; page: number; pages: number }>(
      `/accounts?page=${page.value}`,
    )
    rows.value = body.rows
    page.value = body.page
    pages.value = body.pages
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(page, load)
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <Panel variant="bare" title="Accounts">
      <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        People who can sign in. Suspended accounts are listed too.
      </p>

      <Table :columns="COLUMNS" :rows="rows" row-key="email">
        <template #cell:email="{ row }">
          <span class="[overflow-wrap:anywhere]">{{ row.email }}</span>
        </template>
        <template #cell:display_name="{ row }">{{ row.display_name || '—' }}</template>
        <template #cell:memberships="{ row }">
          <span v-if="!(row.memberships as Row['memberships']).length" class="text-text-tertiary"
            >no domain yet</span
          >
          <span
            v-for="member in row.memberships as Row['memberships']"
            :key="member.slug"
            class="block"
          >
            {{ member.slug }}
            <span class="text-text-tertiary">{{ member.role }}</span>
          </span>
        </template>
        <template #cell:last_login_at="{ row }">
          <span class="age mono font-semibold text-xs whitespace-nowrap">{{
            stamp(row.last_login_at as string)
          }}</span>
        </template>
      </Table>

      <EmptyState v-if="!rows.length" title="No accounts yet." />

      <Pager :page="page" :pages="pages" @go="(n) => (page = n)" />
    </Panel>
  </PaneState>
</template>
