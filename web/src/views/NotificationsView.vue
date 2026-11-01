<script setup lang="ts">
/**
 * Where this instance's notices go.
 *
 * CARDS, NOT A MATRIX. The natural shape for "destinations across notice types"
 * is a grid, and a grid cannot work at 390px -- which is the floor every view
 * clears (ADR 0152). One card per destination with its switches stacked inside
 * it is the shape `docs/DESIGN.md` prescribes and it reads better on a laptop
 * too: what an operator asks is "what does the ops channel get told about",
 * which is one column of a matrix and the whole of a card.
 *
 * THE STRUCK-OUT BANNER IS THE POINT OF THIS PAGE. `SourcesHealth`'s docstring
 * states the same failure for feeds: an auto-disabled source is silent by
 * design, so without a surface "the brief got thin" has no visible cause
 * anywhere in the product. A struck-out destination is worse, because the thing
 * that broke is the telling -- nothing else in the app would ever mention it.
 * It renders in `#notice`, which is the full-width band between the bar and the
 * body, and the Admin rail carries a mark so somebody who is not on this page
 * still sees it.
 *
 * NO CONTROL SHOWS A STORED ADDRESS. `Replace` opens an EMPTY field; there is
 * no Edit, because an edit control pre-filled from a response is how a webhook
 * URL -- which is a bearer token in URL clothing -- would leak to anyone holding
 * a session. `api/security.py` makes the same argument about the root token and
 * `AccountView.vue` refuses to render it for the same reason.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { errorText } from '@/api/client'
import AdminRail from '@/components/nav/AdminRail.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Input from '@/components/ui/Input.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import Toggle from '@/components/ui/Toggle.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { dayTimeLabel } from '@/lib/dates'
import { useNotificationsStore } from '@/stores/notifications'
import { useToastsStore } from '@/stores/toasts'

const store = useNotificationsStore()
const toasts = useToastsStore()
const { instance, instanceHistory, loading } = storeToRefs(store)

const failure = ref<string | null>(null)
const busy = ref<string | null>(null)

const destinations = computed(() => instance.value?.destinations ?? [])
const struckOut = computed(() => destinations.value.filter((d) => d.status === 'struck_out'))

const identity = computed(() => {
  const count = destinations.value.length
  if (!count) return 'admin · notifications'
  return `admin · notifications · ${count} ${count === 1 ? 'destination' : 'destinations'}`
})

type Row = {
  id: string
  notice_label: string
  destination: string
  status: string
  when: string
  last_error: string
}

// A column that carries DETAIL rather than identity is the one to lose on a
// phone, which is `RunsView`'s rule and its worked example.
const columns: Column<Row>[] = [
  { key: 'notice_label', label: 'what' },
  { key: 'destination', label: 'where' },
  { key: 'status', label: 'result' },
  { key: 'when', label: 'when', hideBelow: 'sm' as const },
  { key: 'last_error', label: 'detail', hideBelow: 'sm' as const },
]

const rows = computed<Row[]>(() =>
  (instanceHistory.value?.items ?? []).map((item) => ({
    id: item.id,
    notice_label: item.notice_label,
    destination: item.destination,
    status: item.status,
    when: dayTimeLabel(item.delivered_at ?? item.created_at),
    last_error: item.last_error ?? '',
  })),
)

onMounted(load)

async function load() {
  try {
    await store.loadInstance()
    failure.value = null
  } catch (err) {
    failure.value = errorText(err, 'Could not load the notification settings.')
  }
}

async function setNotice(id: string, noticeType: string, on: boolean) {
  busy.value = `${id}:${noticeType}`
  failure.value = null
  try {
    await store.setNotice('instance', id, noticeType, on)
  } catch (err) {
    failure.value = errorText(err, 'Could not save that.')
  } finally {
    busy.value = null
  }
}

// ── adding ───────────────────────────────────────────────────────────

const adding = ref(false)
const draftKind = ref<'webhook' | 'email'>('webhook')
const draftLabel = ref('')
const draftTarget = ref('')
const dialogError = ref<string | null>(null)
const saving = ref(false)

// `SegmentedControl` rather than a `Select`: the app has two kinds and no
// `Select` primitive, and `docs/DESIGN.md` lists that one as deliberately
// unbuilt until something needs it. Two options do not need it.
const kinds = [
  { value: 'webhook', label: 'Web address' },
  { value: 'email', label: 'Email' },
]

function openAdd() {
  draftKind.value = 'webhook'
  draftLabel.value = ''
  draftTarget.value = ''
  dialogError.value = null
  adding.value = true
}

async function saveDestination() {
  saving.value = true
  dialogError.value = null
  try {
    await store.add('instance', {
      kind: draftKind.value,
      label: draftLabel.value,
      target: draftTarget.value,
    })
    adding.value = false
  } catch (err) {
    dialogError.value = errorText(err, 'Venona could not use that.')
  } finally {
    saving.value = false
  }
}

// ── replacing an address ─────────────────────────────────────────────

const replacing = ref<{ id: string; label: string } | null>(null)

function openReplace(id: string, label: string) {
  // Deliberately blank. See the docstring: there is no route that reads a
  // stored address back, so there is nothing to pre-fill it with and nothing
  // for this page to leak.
  draftTarget.value = ''
  dialogError.value = null
  replacing.value = { id, label }
}

async function saveReplacement() {
  if (!replacing.value) return
  saving.value = true
  dialogError.value = null
  try {
    await store.replaceTarget(replacing.value.id, draftTarget.value)
    replacing.value = null
    toasts.push({ kind: 'success', text: 'Address replaced.' })
  } catch (err) {
    dialogError.value = errorText(err, 'Venona could not use that address.')
  } finally {
    saving.value = false
  }
}

// ── testing, clearing, removing ──────────────────────────────────────

const testResult = ref<Record<string, string>>({})

async function test(id: string) {
  busy.value = id
  try {
    const result = await store.sendTest('instance', id)
    testResult.value = {
      ...testResult.value,
      [id]: result.delivered ? 'Sent. Check that channel.' : (result.detail ?? 'It did not go.'),
    }
  } catch (err) {
    testResult.value = { ...testResult.value, [id]: errorText(err, 'It did not go.') }
  } finally {
    busy.value = null
  }
}

async function clearStrikes(id: string) {
  busy.value = id
  try {
    await store.clearStrikes(id)
    toasts.push({ kind: 'success', text: 'Venona will try that one again.' })
  } catch (err) {
    failure.value = errorText(err, 'Could not start sending there again.')
  } finally {
    busy.value = null
  }
}

const removing = ref<{ id: string; label: string } | null>(null)

async function confirmRemove() {
  if (!removing.value) return
  const { label } = removing.value
  saving.value = true
  try {
    await store.remove('instance', removing.value.id)
    removing.value = null
    toasts.push({ kind: 'success', text: `Venona will stop sending to ${label}.` })
  } catch (err) {
    dialogError.value = errorText(err, 'Could not remove that.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ViewShell width="wide" :identity="identity">
    <template #actions>
      <Button icon="plus" @click="openAdd">Add a destination</Button>
    </template>

    <!-- Full width, between the bar and the body, because it is a banner with
         a consequence and not a chip. Never colour alone: the sentence says
         what stopped and the last error says why. -->
    <template v-if="struckOut.length" #notice>
      <StatusNote kind="error">
        Venona has stopped sending to
        {{ struckOut.map((d) => d.label).join(', ') }} after repeated failures, so nothing is
        reaching {{ struckOut.length === 1 ? 'it' : 'them' }}.
        <template #hint>{{ struckOut[0].last_error }}</template>
      </StatusNote>
    </template>

    <RailLayout>
      <template #rail><AdminRail current="notifications" /></template>

      <PaneState :loading="loading" :error="failure">
        <div class="flex flex-col">
          <SectionHead as="h2" label="where notices go" rule />

          <EmptyState v-if="!destinations.length" title="Venona has nowhere to send an alert" class="mt-3">
            A run that fails, a publication that stops answering, or a model credential that has
            expired will only ever show up here in the app. Add a destination and Venona will
            tell you when it happens.
            <template #action>
              <Button icon="plus" @click="openAdd">Add a destination</Button>
            </template>
          </EmptyState>

          <div
            v-for="destination in destinations"
            :key="destination.id"
            class="mt-3 border-t border-edge-subtle pt-3"
          >
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <div class="min-w-0">
                <p class="text-sm text-text-primary">{{ destination.label }}</p>
                <p class="mono text-2xs text-text-tertiary [overflow-wrap:anywhere]">
                  {{ destination.hint }}
                </p>
              </div>
              <div class="flex flex-none flex-wrap gap-2">
                <Button
                  variant="text"
                  :disabled="busy === destination.id"
                  :title="
                    destination.kind === 'email' && !instance?.email_available
                      ? instance?.email_unavailable_reason ?? undefined
                      : undefined
                  "
                  @click="test(destination.id)"
                >
                  Send a test
                </Button>
                <Button variant="text" @click="openReplace(destination.id, destination.label)">
                  Replace address
                </Button>
                <Button
                  variant="text"
                  tone="critical"
                  @click="removing = { id: destination.id, label: destination.label }"
                >
                  Remove
                </Button>
              </div>
            </div>

            <StatusNote v-if="destination.status === 'struck_out'" kind="error" class="mt-2">
              Stopped after {{ destination.failures }} failures in a row.
              <template #hint>{{ destination.last_error }}</template>
            </StatusNote>
            <Button
              v-if="destination.status === 'struck_out'"
              variant="outline"
              class="mt-2"
              :disabled="busy === destination.id"
              @click="clearStrikes(destination.id)"
            >
              Try this one again
            </Button>

            <SectionHead as="h3" label="what this gets told about" class="mt-3" />
            <div class="mt-2 flex flex-col gap-2">
              <div v-for="notice in destination.notices" :key="notice.key">
                <Toggle
                  :model-value="notice.on"
                  @update:model-value="(on) => setNotice(destination.id, notice.key, on)"
                >
                  {{ notice.label }}
                </Toggle>
                <p class="mt-1 text-2xs text-text-tertiary">{{ notice.detail }}</p>
              </div>
            </div>

            <StatusNote v-if="testResult[destination.id]" kind="info" class="mt-2">
              {{ testResult[destination.id] }}
            </StatusNote>
          </div>

          <!-- email -->
          <SectionHead as="h2" label="email" rule class="mt-7" />
          <StatusNote v-if="!instance?.email_available" kind="info" class="mt-3">
            {{ instance?.email_unavailable_reason }}
            <template #hint>
              An email destination can still be added and switched on. Venona holds what it would
              have sent, and sends it once there is a way to.
            </template>
          </StatusNote>

          <!-- what was sent -->
          <SectionHead as="h2" label="recently sent" rule class="mt-7" />
          <Table :columns="columns" :rows="rows" row-key="id" class="mt-3">
            <template #empty>
              <EmptyState title="Nothing sent yet">
                Once a destination is set up, everything Venona sends shows up here with whether
                it arrived.
              </EmptyState>
            </template>
          </Table>
          <p v-if="rows.length" class="mt-2 text-2xs text-text-tertiary">
            Showing {{ instanceHistory?.items.length ?? 0 }} of {{ instanceHistory?.total ?? 0 }}.
          </p>
          <Button
            v-if="store.moreInstanceHistory"
            variant="text"
            icon="down"
            @click="store.loadMoreInstanceHistory()"
          >
            Show more
          </Button>

          <StatusNote v-if="failure" kind="error" class="mt-3">{{ failure }}</StatusNote>
        </div>
      </PaneState>
    </RailLayout>

    <Dialog
      :open="adding"
      title="Add a destination"
      confirm-label="Add"
      :busy="saving"
      @update:open="adding = false"
      @confirm="saveDestination"
    >
      <SegmentedControl
        :model-value="draftKind"
        :options="kinds"
        ariaLabel="How to reach you"
        @update:model-value="(value) => (draftKind = value === 'email' ? 'email' : 'webhook')"
      />
      <div class="mt-3 flex flex-col gap-2">
        <Input v-model="draftLabel" aria-label="Name" placeholder="What is it, e.g. Ops channel" />
        <Input
          v-model="draftTarget"
          family="mono"
          aria-label="Address"
          :placeholder="draftKind === 'email' ? 'ops@example.com' : 'https://…'"
        />
      </div>
      <p class="mt-2 text-2xs text-text-tertiary">
        For Slack this is an incoming webhook. Venona will not show this address again once it is
        saved, so keep your own copy if you need it.
      </p>
      <StatusNote v-if="dialogError" kind="error" class="mt-3">{{ dialogError }}</StatusNote>
    </Dialog>

    <Dialog
      :open="replacing !== null"
      title="Replace the address"
      confirm-label="Replace"
      :busy="saving"
      @update:open="replacing = null"
      @confirm="saveReplacement"
    >
      <p class="text-xs text-text-secondary">
        Paste the new address for {{ replacing?.label }}. Venona does not show the current one:
        it is a credential, and a page that could display it could leak it.
      </p>
      <Input v-model="draftTarget" family="mono" aria-label="New address" class="mt-3" />
      <StatusNote v-if="dialogError" kind="error" class="mt-3">{{ dialogError }}</StatusNote>
    </Dialog>

    <Dialog
      :open="removing !== null"
      title="Stop sending here?"
      confirm-label="Remove"
      destructive
      :busy="saving"
      @update:open="removing = null"
      @confirm="confirmRemove"
    >
      <p class="text-xs text-text-secondary">
        Venona will stop sending to {{ removing?.label }}, and anything still waiting to go there
        is dropped.
      </p>
      <StatusNote v-if="dialogError" kind="error" class="mt-3">{{ dialogError }}</StatusNote>
    </Dialog>
  </ViewShell>
</template>
