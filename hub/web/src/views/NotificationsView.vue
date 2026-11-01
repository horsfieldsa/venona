<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, get, post } from '@/api/client'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Input from '@/components/ui/Input.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'

/**
 * Where the hub sends what it notices, and whether it arrived.
 *
 * THE HISTORY IS HALF THE PAGE, for one reason: a channel that has stopped
 * working looks exactly like a quiet week. Everything above it is intent, and
 * intent is what an operator already believes. The deliveries are the only part
 * that can contradict them.
 *
 * A DESTINATION IS NEVER SHOWN ITS ADDRESS. A webhook URL is a credential --
 * anyone holding a Slack incoming address can post to that channel -- so no
 * route returns it, and a hint renders in its place: enough to tell two apart,
 * not enough to use.
 */
interface Destination {
  id: string
  label: string
  hint: string
  status: string
  last_error: string | null
  subscribed: string[]
}

interface NoticeType {
  key: string
  label: string
  detail: string
}

interface Delivery {
  created_at: string
  notice_type: string
  label: string
  status: string
  delivered_at: string | null
  attempts: number
  last_error: string | null
}

const HISTORY: Column[] = [
  { key: 'created_at', label: 'When' },
  { key: 'notice_type', label: 'What' },
  { key: 'label', label: 'Where', hideBelow: 'sm' },
  { key: 'state', label: 'Arrived' },
]

const ON_OFF = [
  { value: 'on', label: 'On' },
  { value: 'off', label: 'Off' },
] as const

const destinations = ref<Destination[]>([])
const types = ref<NoticeType[]>([])
const history = ref<Delivery[]>([])
const loading = ref(true)
const error = ref('')
const problem = ref('')
const label = ref('')
const url = ref('')

async function load() {
  error.value = ''
  try {
    const body = await get<{
      destinations: Destination[]
      types: NoticeType[]
      history: Delivery[]
    }>('/notifications')
    destinations.value = body.destinations
    types.value = body.types
    history.value = body.history
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
}

/** Refusals from this page are the operator's own mistake to fix -- a label
 *  that is too long, an address that is not a webhook -- so they belong beside
 *  the form rather than replacing the page. */
async function act(path: string, payload: unknown) {
  problem.value = ''
  try {
    await post(path, payload)
    await load()
  } catch (err) {
    problem.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  }
}

async function add() {
  await act('/notifications/add', { label: label.value, url: url.value })
  if (!problem.value) {
    label.value = ''
    url.value = ''
  }
}

function deliveryState(row: Delivery): string {
  if (row.delivered_at) return `Arrived ${stamp(row.delivered_at)}`
  if (row.last_error) return row.last_error
  return row.attempts > 1 ? `${row.attempts} tries` : 'Not yet'
}

onMounted(load)
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <div class="flex flex-col gap-6">
      <StatusNote v-if="problem" kind="error">{{ problem }}</StatusNote>

      <Panel variant="bare" title="Where notices go">
        <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Venona posts to a webhook when somebody asks for access, or when a domain stops
          answering. Nothing here reaches readers.
        </p>

        <form class="flex flex-wrap items-end gap-3" @submit.prevent="add">
          <label class="flex flex-col gap-1">
            <span class="font-ui text-2xs uppercase tracking-label text-text-tertiary"
              >Name it</span
            >
            <span class="w-(--field-lg) max-w-full"><Input v-model="label" /></span>
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-2xs uppercase tracking-label text-text-tertiary"
              >Address</span
            >
            <span class="w-(--field-lg) max-w-full"><Input v-model="url" type="url" /></span>
          </label>
          <Button @click="add">Add</Button>
        </form>
      </Panel>

      <Panel
        v-for="destination in destinations"
        :key="destination.id"
        variant="inset"
        :title="destination.label"
      >
        <template #header>
          <div class="flex items-center gap-2">
            <Button variant="outline" @click="act('/notifications/test', { destination_id: destination.id })">
              Send a test
            </Button>
            <Button
              variant="outline"
              tone="critical"
              @click="act('/notifications/remove', { destination_id: destination.id })"
            >
              Remove
            </Button>
          </div>
        </template>

        <p class="font-ui text-xs text-text-tertiary">{{ destination.hint }}</p>

        <!-- NOT A STYLING CHOICE. A struck-out destination still passes a test
             send, because a test goes straight down the channel and never looks
             at this state. An operator who cannot see it reads a green test and
             a silent channel and has no way to connect them. -->
        <StatusNote v-if="destination.status === 'struck_out'" kind="error">
          Three sends failed in a row, so Venona stopped trying. A test will still arrive;
          real messages will not.
        </StatusNote>
        <div v-if="destination.status === 'struck_out'" class="mt-3">
          <Button variant="outline" @click="act('/notifications/clear', { destination_id: destination.id })">
            Start sending again
          </Button>
        </div>
        <p v-else-if="destination.last_error" class="mt-2 font-ui text-xs text-text-tertiary">
          last problem: {{ destination.last_error }}
        </p>

        <!-- THE NAME LEADS, then the switch, then why you would want it. A
             control above its own label reads as an unlabelled pair of words
             until the eye has travelled past it, and there is one of these per
             notice type. At the phone tier the switch drops under the name
             rather than squeezing beside it. -->
        <div class="mt-4 flex flex-col gap-4">
          <div v-for="type in types" :key="type.key" class="flex flex-col gap-2">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <span class="font-ui text-sm text-text-primary">{{ type.label }}</span>
              <SegmentedControl
                :model-value="destination.subscribed.includes(type.key) ? 'on' : 'off'"
                :name="`${destination.id}-${type.key}`"
                :options="ON_OFF"
                @update:model-value="
                  (v) =>
                    act('/notifications/switch', {
                      destination_id: destination.id,
                      notice_type: type.key,
                      on: v === 'on',
                    })
                "
              />
            </div>
            <span class="max-w-hint font-prose text-xs leading-normal text-text-tertiary">{{
              type.detail
            }}</span>
          </div>
        </div>
      </Panel>

      <EmptyState v-if="!destinations.length" title="Nothing is listening yet.">
        Add a webhook above and Venona will start telling it what it notices.
      </EmptyState>

      <Panel variant="bare" title="What was sent">
        <Table :columns="HISTORY" :rows="history" row-key="created_at">
          <template #cell:created_at="{ row }">
            <span class="age mono font-semibold text-xs whitespace-nowrap">{{
              stamp(row.created_at as string)
            }}</span>
          </template>
          <template #cell:state="{ row }">{{ deliveryState(row as unknown as Delivery) }}</template>
        </Table>
        <EmptyState v-if="!history.length" title="Nothing has been sent yet." />
      </Panel>
    </div>
  </PaneState>
</template>
