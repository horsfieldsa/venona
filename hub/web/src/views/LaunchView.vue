<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { ApiError, get, post } from '@/api/client'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Pager from '@/components/ui/Pager.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'

/**
 * Addresses left on the front page and nothing else.
 *
 * THERE IS NOTHING TO GRANT HERE, and the copy says so where the control would
 * be. These people were promised one message when it opens and have not said
 * which domain they want, so a Grant button would be asking the operator to
 * invent an answer on their behalf.
 */
interface Row {
  id: string
  email: string
  created_at: string
}

const COLUMNS: Column[] = [
  { key: 'email', label: 'Who' },
  { key: 'created_at', label: 'Left it', hideBelow: 'sm' },
  { key: 'settle', label: '' },
]

const rows = ref<Row[]>([])
const page = ref(1)
const pages = ref(1)
const total = ref(0)
const loading = ref(true)
const error = ref('')
const busy = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await get<{ rows: Row[]; page: number; pages: number; total: number }>(
      `/launch?page=${page.value}`,
    )
    rows.value = body.rows
    page.value = body.page
    pages.value = body.pages
    total.value = body.total
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
}

async function remove(id: string) {
  busy.value = id
  try {
    await post(`/launch/${id}/remove`)
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    busy.value = ''
  }
}

onMounted(load)
watch(page, load)
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <Panel variant="bare" title="Waiting for launch">
      <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        Left on the front page and nothing else: they were promised one message when it
        opens, and have not said which domain they want. There is nothing to grant here.
      </p>

      <!-- A real link, not a fetch. The file arrives as an attachment with a
           filename, which is the whole point of it; a download assembled in the
           page would lose both. -->
      <Button variant="outline" href="/admin/launch.csv">Download the list</Button>

      <div class="mt-4">
        <Table :columns="COLUMNS" :rows="rows" row-key="id">
          <template #cell:email="{ row }">
            <span class="[overflow-wrap:anywhere]">{{ row.email }}</span>
          </template>
          <template #cell:created_at="{ row }">
            <span class="age mono font-semibold text-xs whitespace-nowrap">{{
              stamp(row.created_at as string)
            }}</span>
          </template>
          <template #cell:settle="{ row }">
            <Button
              variant="outline"
              tone="critical"
              :disabled="busy === row.id"
              @click="remove(row.id as string)"
            >
              Remove
            </Button>
          </template>
        </Table>

        <EmptyState v-if="!rows.length" title="Nobody is waiting for launch.">
          The front page adds an address here when somebody asks to be told when Venona
          opens.
        </EmptyState>

        <Pager :page="page" :pages="pages" @go="(n) => (page = n)" />
      </div>
    </Panel>
  </PaneState>
</template>
