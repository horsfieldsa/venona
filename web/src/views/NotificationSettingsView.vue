<script setup lang="ts">
/**
 * How a reader hears that a brief has landed.
 *
 * THE READER NEVER SEES THE DATA MODEL. Behind this page a notice type, a
 * destination and a subscription are three separate rows, and an administrator
 * genuinely manages all three on `/admin/notifications`. A reader must not:
 * "create a destination, then subscribe it" is a description of the schema, not
 * of wanting your brief emailed to you. So the email destination is IMPLICIT --
 * flipping the first switch creates the row from the account's own address --
 * and what renders is sentences with switches beside them.
 *
 * NO CONTROL HERE DOES NOTHING. `AccountView.vue` refused to ship two toggles
 * for this feature on exactly that ground, and its own docstring named the
 * conditions under which they could exist. One of them still is not met: there
 * is no email transport. So when `email_available` is false the switches do not
 * render at all and the honest sentence takes their place, alongside the
 * web-address section, which does work. `Toggle` has no `disabled` prop and
 * should not grow one for this -- the component's absence is pointing at the
 * right answer.
 *
 * "WEBHOOK" IS THE READER'S WORD HERE, and this is the rare place where it and
 * ours coincide: Slack's own interface says "Incoming Webhooks", and somebody
 * setting one up will search for that. The section is titled for the
 * destination, the field is labelled "Web address", and the help line names
 * what Slack calls it. The internal noun stays internal.
 *
 * Switches apply on TOUCH, one request each. `RelevanceTailor` draws the same
 * line and gives the reason: a follow applies on touch because each chip is its
 * own resource, while emphasis applies on Save because it is one document
 * composed and committed. A subscription is a chip.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { errorText } from '@/api/client'
import SettingsRail from '@/components/nav/SettingsRail.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Input from '@/components/ui/Input.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Toggle from '@/components/ui/Toggle.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useNotificationsStore } from '@/stores/notifications'
import { useToastsStore } from '@/stores/toasts'
import { dayTimeLabel } from '@/lib/dates'

const store = useNotificationsStore()
const toasts = useToastsStore()
const { mine, myHistory, loading } = storeToRefs(store)

const failure = ref<string | null>(null)
const busy = ref<string | null>(null)

const emailDestination = computed(() => mine.value?.destinations.find((d) => d.kind === 'email'))
const webAddresses = computed(() => mine.value?.destinations.filter((d) => d.kind === 'webhook') ?? [])
const emailWorks = computed(() => mine.value?.email_available ?? false)

const identity = computed(() => {
  const count = mine.value?.destinations.length ?? 0
  return count ? `settings · notifications · ${count}` : 'settings · notifications'
})

onMounted(load)

async function load() {
  try {
    await store.loadMine()
    failure.value = null
  } catch (err) {
    failure.value = errorText(err, 'Could not load your delivery settings.')
  }
}

/**
 * Flip one switch on the email destination, creating it the first time.
 *
 * The reader never types their address: the account already has one,
 * established when they signed in. Turning the last switch off leaves the row
 * in place rather than deleting it -- it holds no preference of its own, and
 * removing it would make turning the switch back on look like adding something
 * new.
 */
async function setEmailNotice(noticeType: string, on: boolean) {
  busy.value = noticeType
  failure.value = null
  try {
    let destination = emailDestination.value
    if (!destination) {
      destination = await store.add('mine', {
        kind: 'email',
        label: 'My inbox',
        target: mine.value?.account_email ?? '',
      })
    }
    await store.setNotice('mine', destination.id, noticeType, on)
  } catch (err) {
    failure.value = errorText(err, 'Could not save that.')
  } finally {
    busy.value = null
  }
}

async function setNotice(id: string, noticeType: string, on: boolean) {
  busy.value = `${id}:${noticeType}`
  failure.value = null
  try {
    await store.setNotice('mine', id, noticeType, on)
  } catch (err) {
    failure.value = errorText(err, 'Could not save that.')
  } finally {
    busy.value = null
  }
}

// ── adding a web address ─────────────────────────────────────────────

const adding = ref(false)
const draftLabel = ref('')
const draftTarget = ref('')
const addError = ref<string | null>(null)
const saving = ref(false)

function openAdd() {
  draftLabel.value = ''
  draftTarget.value = ''
  addError.value = null
  adding.value = true
}

async function saveAddress() {
  saving.value = true
  addError.value = null
  try {
    await store.add('mine', {
      kind: 'webhook',
      label: draftLabel.value,
      target: draftTarget.value,
    })
    adding.value = false
  } catch (err) {
    // Inside the dialog, because the dialog owns the decision and a urlguard
    // refusal is a whole sentence.
    addError.value = errorText(err, 'Venona could not use that address.')
  } finally {
    saving.value = false
  }
}

// ── testing and removing ─────────────────────────────────────────────

const testResult = ref<Record<string, string>>({})

async function test(id: string) {
  busy.value = id
  try {
    const result = await store.sendTest('mine', id)
    testResult.value = {
      ...testResult.value,
      [id]: result.delivered ? 'Sent. Check that app.' : (result.detail ?? 'It did not go.'),
    }
  } catch (err) {
    testResult.value = { ...testResult.value, [id]: errorText(err, 'It did not go.') }
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
    await store.remove('mine', removing.value.id)
    removing.value = null
    toasts.push({ kind: 'success', text: `Venona will stop sending to ${label}.` })
  } catch (err) {
    addError.value = errorText(err, 'Could not remove that.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ViewShell width="wide" :identity="identity">
    <RailLayout>
      <template #rail><SettingsRail current="notifications" /></template>

      <PaneState :loading="loading" :error="failure">
        <div class="flex flex-col">
          <!-- brief delivery -->
          <SectionHead as="h2" label="brief delivery" rule />

          <template v-if="emailWorks">
            <p class="mt-3 text-xs text-text-secondary">
              Venona will write to {{ mine?.account_email }}, the address you sign in with.
            </p>
            <div class="mt-3 flex flex-col gap-2">
              <Toggle
                v-for="notice in emailDestination?.notices ?? []"
                :key="notice.key"
                :model-value="notice.on"
                @update:model-value="(on) => setEmailNotice(notice.key, on)"
              >
                {{ notice.label }}
              </Toggle>
            </div>
            <p
              v-for="notice in emailDestination?.notices ?? []"
              :key="`${notice.key}-detail`"
              class="mt-1 text-2xs text-text-tertiary"
            >
              {{ notice.label }}. {{ notice.detail }}
            </p>
          </template>

          <!-- The switches are absent rather than disabled. See the docstring:
               a control that looks persisted and does nothing is worse than no
               control, and this page is where that decision was first made. -->
          <StatusNote v-else kind="info" class="mt-3">
            {{ mine?.email_unavailable_reason }}
            <template #hint>
              Whoever runs this domain can turn it on. You can still be sent a message in Slack
              or another app that accepts a web address, below.
            </template>
          </StatusNote>

          <!-- slack and other apps -->
          <SectionHead as="h2" label="slack and other apps" rule class="mt-7">
            <Button variant="text" icon="plus" @click="openAdd">Add a web address</Button>
          </SectionHead>

          <EmptyState v-if="!webAddresses.length" title="Nothing set up" class="mt-3">
            Paste the web address an app gave you and Venona will send a message there when your
            reading is ready. Slack calls it an incoming webhook.
          </EmptyState>

          <div
            v-for="destination in webAddresses"
            :key="destination.id"
            class="mt-3 border-t border-edge-subtle pt-3"
          >
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <div class="min-w-0">
                <p class="text-sm text-text-primary">{{ destination.label }}</p>
                <!-- An address is an unbreakable word: without this it widens
                     the page instead of wrapping (DESIGN.md "Responsive"). -->
                <p class="mono text-2xs text-text-tertiary [overflow-wrap:anywhere]">
                  {{ destination.hint }}
                </p>
              </div>
              <div class="flex flex-none gap-2">
                <Button variant="text" :disabled="busy === destination.id" @click="test(destination.id)">
                  Send a test
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
              Venona stopped sending here after {{ destination.failures }} failures in a row.
              <template #hint>{{ destination.last_error }}</template>
            </StatusNote>

            <div class="mt-2 flex flex-col gap-2">
              <Toggle
                v-for="notice in destination.notices"
                :key="notice.key"
                :model-value="notice.on"
                @update:model-value="(on) => setNotice(destination.id, notice.key, on)"
              >
                {{ notice.label }}
              </Toggle>
            </div>

            <!-- Inline, not a toast: a refusal from the endpoint is longer than
                 a toast should hold and is the half a person can act on. -->
            <StatusNote v-if="testResult[destination.id]" kind="info" class="mt-2">
              {{ testResult[destination.id] }}
            </StatusNote>
          </div>

          <!-- what has been sent -->
          <SectionHead as="h2" label="recently sent" rule class="mt-7" />
          <EmptyState v-if="!myHistory?.items.length" title="Nothing sent yet" class="mt-3">
            Once you turn something on above, what Venona sends you shows up here.
          </EmptyState>
          <ul v-else class="mt-3 flex flex-col gap-2">
            <li
              v-for="item in myHistory.items"
              :key="item.id"
              class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-edge-subtle pt-2"
            >
              <span class="text-xs text-text-primary">{{ item.notice_label }}</span>
              <span class="text-2xs text-text-tertiary">
                {{ item.destination }} · {{ dayTimeLabel(item.delivered_at ?? item.created_at) }}
              </span>
            </li>
          </ul>
          <p v-if="myHistory?.items.length" class="mt-2 text-2xs text-text-tertiary">
            Showing {{ myHistory.items.length }} of {{ myHistory.total }}.
          </p>
          <Button
            v-if="store.moreMyHistory"
            variant="text"
            icon="down"
            @click="store.loadMoreMyHistory()"
          >
            Show more
          </Button>

          <StatusNote v-if="failure" kind="error" class="mt-3">{{ failure }}</StatusNote>
        </div>
      </PaneState>
    </RailLayout>

    <Dialog
      :open="adding"
      title="Add a web address"
      confirm-label="Add"
      :busy="saving"
      @update:open="adding = false"
      @confirm="saveAddress"
    >
      <p class="text-xs text-text-secondary">
        In Slack this is an incoming webhook. Other apps may call it a hook or a callback address.
        Venona sends a short message to it, never the whole brief.
      </p>
      <div class="mt-3 flex flex-col gap-2">
        <Input v-model="draftLabel" aria-label="Name" placeholder="What is it, e.g. My Slack" />
        <Input
          v-model="draftTarget"
          family="mono"
          aria-label="Web address"
          placeholder="https://…"
        />
      </div>
      <StatusNote v-if="addError" kind="error" class="mt-3">{{ addError }}</StatusNote>
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
        Venona will stop sending to {{ removing?.label }}. Anything waiting to go there is dropped.
      </p>
    </Dialog>
  </ViewShell>
</template>
