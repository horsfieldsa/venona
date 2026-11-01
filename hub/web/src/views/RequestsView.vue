<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, get, post } from '@/api/client'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Pager from '@/components/ui/Pager.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'

/**
 * Access requests: people who signed in, found no domain of their own, and
 * asked for one.
 *
 * GRANT PICKS A DOMAIN AND A ROLE; dismiss closes the request without one.
 * Both settle the request row, which is why neither is the machine surface's
 * `POST /admin/grants` -- that creates the membership and leaves the row
 * pending, so composing the two from here would be two calls that can half
 * fail.
 *
 * The note is shown in full. It is the only thing telling a real analyst from
 * somebody sweeping the form, and truncating it here would mean opening
 * something else to read it.
 */
interface Row {
  id: string
  email: string
  note: string
  wanted_slug: string | null
  created_at: string
}

const COLUMNS: Column[] = [
  { key: 'email', label: 'Who' },
  { key: 'wanted_slug', label: 'Wants' },
  { key: 'created_at', label: 'Asked', hideBelow: 'sm' },
  { key: 'grant', label: 'Grant' },
  { key: 'settle', label: '' },
]

const rows = ref<Row[]>([])
const domains = ref<string[]>([])
const page = ref(1)
const pages = ref(1)
const loading = ref(true)
const error = ref('')
const busy = ref('')

/** The choice per row, seeded from what the person asked for. A grant form
 *  that defaults to the first domain in the list quietly grants the wrong one
 *  on a page where most requests name a domain. */
const choice = ref<Record<string, { slug: string; role: string }>>({})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await get<{ rows: Row[]; page: number; pages: number; domains: string[] }>(
      `/requests?page=${page.value}`,
    )
    rows.value = body.rows
    domains.value = body.domains
    page.value = body.page
    pages.value = body.pages
    choice.value = Object.fromEntries(
      body.rows.map((row) => [
        row.id,
        { slug: row.wanted_slug ?? body.domains[0] ?? '', role: 'reader' },
      ]),
    )
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
}

async function settle(id: string, action: 'grant' | 'dismiss') {
  busy.value = id
  try {
    await post(`/requests/${id}/${action}`, action === 'grant' ? choice.value[id] : {})
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    busy.value = ''
  }
}

const title = computed(() => (rows.value.length ? `${rows.value.length} waiting` : 'Access requests'))

onMounted(load)
watch(page, load)
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <Panel variant="bare" :title="title">
      <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        Each of these signed in, found no domain of their own, and asked for one.
        Granting creates the account and the membership.
      </p>

      <Table :columns="COLUMNS" :rows="rows" row-key="id">
        <template #cell:email="{ row }">
          <span class="[overflow-wrap:anywhere]">{{ row.email }}</span>
          <span v-if="row.note" class="block text-text-tertiary">{{ row.note }}</span>
        </template>
        <template #cell:wanted_slug="{ row }">{{ row.wanted_slug || 'any' }}</template>
        <template #cell:created_at="{ row }">
          <span class="age mono font-semibold text-xs whitespace-nowrap">{{
            stamp(row.created_at as string)
          }}</span>
        </template>
        <template #cell:grant="{ row }">
          <div class="flex flex-wrap items-center gap-2">
            <select
              v-model="choice[row.id as string].slug"
              aria-label="Domain"
              class="h-control max-xs:h-(--control-h-touch) px-2 bg-surface-inset border border-border-default rounded-sm font-ui text-sm text-text-primary"
            >
              <option v-for="slug in domains" :key="slug" :value="slug">{{ slug }}</option>
            </select>
            <select
              v-model="choice[row.id as string].role"
              aria-label="Role"
              class="h-control max-xs:h-(--control-h-touch) px-2 bg-surface-inset border border-border-default rounded-sm font-ui text-sm text-text-primary"
            >
              <option value="reader">reader</option>
              <option value="admin">admin</option>
            </select>
            <Button :disabled="busy === row.id" @click="settle(row.id as string, 'grant')">
              Grant
            </Button>
          </div>
        </template>
        <template #cell:settle="{ row }">
          <Button
            variant="outline"
            tone="critical"
            :disabled="busy === row.id"
            @click="settle(row.id as string, 'dismiss')"
          >
            Dismiss
          </Button>
        </template>
      </Table>

      <EmptyState v-if="!rows.length" title="Nobody is waiting.">
        A request arrives when somebody signs in and has no domain of their own.
      </EmptyState>

      <Pager :page="page" :pages="pages" @go="(n) => (page = n)" />
    </Panel>
  </PaneState>
</template>
