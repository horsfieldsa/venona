<script setup lang="ts">
/**
 * Which service does Venona's thinking, and what it is signed in as.
 *
 * IT LISTS THE SERVICES rather than showing only the current one. An
 * administrator cannot weigh an alternative they cannot see, and a control
 * labelled with a verb is a poor way to discover that one exists -- so
 * "Change" confirms a decision rather than revealing the options.
 *
 * NOT A SEGMENTED CONTROL, for either switch. That primitive is a radiogroup
 * for view state, placed in `ViewShell`'s `controls` slot as tabs; a control
 * that looks exactly like a tab and instead restarts what executes the
 * instance's work is a trap. Both are a button that opens a dialog stating the
 * consequence, which is the middle rung of this app's confirmation scale --
 * higher than removing a notification destination, which is graded that way
 * already and matters less.
 *
 * The word for the machinery is never on screen. A reader of this page has
 * heard of the services by name and not of anything Venona calls them
 * internally, so the vocabulary here is the service's own label and the jobs
 * it does, both supplied by the server.
 */
import { computed, onMounted, ref } from 'vue'

import AdminRail from '@/components/nav/AdminRail.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiDelete, apiGet, apiPut } from '@/api/client'
import { copyText } from '@/lib/clipboard'
import { useToastsStore } from '@/stores/toasts'
import type { components } from '@/api/schema'

/**
 * FROM THE GENERATED SCHEMA, not restated here. These were four hand-written
 * mirrors of the server's models, and a hand-written mirror does not fail when
 * the server adds a field -- it silently stops mentioning it, which is the one
 * kind of drift `make types` exists to make impossible.
 */
type ModelSettings = components['schemas']['ModelSettings']

const toasts = useToastsStore()

const data = ref<ModelSettings | null>(null)
const loading = ref(true)
const failure = ref<string | null>(null)
const busy = ref(false)

const changing = ref(false)
const target = ref<string>('')
const connecting = ref(false)
const secret = ref('')
const secretError = ref<string | null>(null)
/** WHICH service and WHICH plan is being connected. Both were implied before,
 *  and the implication was the active service on its current plan -- so the
 *  route's `service` field went unsent and a service that was not already in
 *  use could not be connected. That is a loop with no entrance: a service is
 *  only selectable once connected, and connecting only ever reached the one
 *  already selected, so a third service was unreachable by construction. The
 *  server has taken a named service all along and says so in its own comment. */
const connectTo = ref<string>('')
const connectPlan = ref<string>('subscription')
const disconnecting = ref(false)

type ModelService = components['schemas']['ModelService']
type ModelPlan = components['schemas']['ModelPlan']

/** Every service, the one in use included: connecting is not a thing you do
 *  only to the others, since a plan change is the same act on the same one. */
const allServices = computed<ModelService[]>(() => data.value?.services ?? [])

const connectService = computed<ModelService | null>(
  () => allServices.value.find((s) => s.key === connectTo.value) ?? null,
)

const connectPlans = computed<ModelPlan[]>(() => connectService.value?.plans ?? [])

const plan = computed<ModelPlan | null>(
  () => connectPlans.value.find((p) => p.mode === connectPlan.value) ?? null,
)

async function copyCommand(): Promise<void> {
  const command = plan.value?.command
  if (!command) return
  // `copyText` reports rather than throws: `navigator.clipboard` is absent over
  // plain http from anything but localhost, which is a real way to run this on
  // a LAN, and a toast claiming success over an empty clipboard is worse than
  // no button.
  const copied = await copyText(command)
  toasts.push(
    copied
      ? { kind: 'success', text: 'Copied. Run it where Venona is deployed.' }
      : { kind: 'error', text: 'Could not reach the clipboard. Select the line and copy it.' },
  )
}

function openConnect(key: string, mode?: string): void {
  connectTo.value = key
  const service = allServices.value.find((s) => s.key === key)
  const plans = service?.plans ?? []
  // The plan it already holds, else the first it can actually take a value
  // for, else the first it has. Landing on a plan with nothing to paste makes
  // the dialog open on an instruction rather than on a box, which is right
  // when that is the only truthful thing to show.
  connectPlan.value =
    mode ??
    plans.find((p) => p.connected)?.mode ??
    plans.find((p) => p.pasteable)?.mode ??
    plans[0]?.mode ??
    'subscription'
  secret.value = ''
  secretError.value = null
  connecting.value = true
}

const identity = computed(() =>
  data.value ? `admin · model · ${data.value.service_label.toLowerCase()}` : 'admin · model',
)

/**
 * What the connection panel says, in one sentence per state.
 *
 * The environment case is the one worth spelling out. Both services read a
 * variable ahead of their own credential store, and every deployment sets one
 * -- so an administrator who connects an account and is not told which of the
 * two is in force will believe the wrong thing, and the sign-in check cannot
 * tell them because it reports a session as live either way.
 */
const connectionLine = computed(() => {
  const c = data.value?.connection
  if (!c) return ''
  if (c.source === 'none') return 'Nothing is connected, so Venona cannot run at all.'
  if (c.source === 'environment')
    return 'Authenticated by a credential set where Venona is deployed, not from this page.'
  if (c.source === 'inherited')
    return 'Signed in through the service\'s own settings on this machine, not from this page.'
  if (c.source === 'stored') return 'Using an API key connected here.'
  return 'Signed in from this page.'
})

/**
 * The honest reading of a sign-in check, which is not what it looks like.
 *
 * A probe reports a session as live for a credential that has expired, so the
 * only thing that knows whether calls land is whether they have been landing.
 */
const workingLine = computed(() => {
  const c = data.value?.connection
  if (!c || c.source === 'none') return null
  if (c.working === false) return 'Recent calls have been failing.'
  if (c.working === true) return 'Recent calls have been going through.'
  return 'Venona has not made a call since this was connected.'
})

const other = computed(() =>
  (data.value?.services ?? []).filter((s) => s.key !== data.value?.service),
)

const ROLE_COLUMNS: Column[] = [
  { key: 'label', label: 'job' },
  { key: 'resolved', label: 'asks for' },
  { key: 'effort', label: 'effort', hideBelow: 'sm' },
]

async function load(): Promise<void> {
  loading.value = true
  failure.value = null
  try {
    data.value = await apiGet<ModelSettings>('/admin/model')
  } catch (error) {
    failure.value = error instanceof Error ? error.message : 'could not read the model settings'
  } finally {
    loading.value = false
  }
}

function openChange(key: string): void {
  target.value = key
  changing.value = true
}

async function confirmChange(): Promise<void> {
  busy.value = true
  try {
    data.value = await apiPut<ModelSettings>('/admin/model', {
      service: target.value,
      base_version: data.value?.base_version ?? null,
    })
    changing.value = false
    toasts.push({ kind: 'success', text: `Venona now thinks with ${data.value.service_label}.` })
  } catch (error) {
    toasts.push({
      kind: 'error',
      text: error instanceof Error ? error.message : 'The service was not changed.',
    })
  } finally {
    busy.value = false
  }
}

/**
 * Whether a model process is locked to a filesystem before it runs.
 *
 * SHOWN EVEN WHEN IT WORKED, because the failure is silent in both
 * directions: the call runs whether or not the restriction took, so nothing an
 * administrator would otherwise look at distinguishes the three states. `null`
 * is not a failure -- it is a service with no filesystem tool to restrict.
 */
const reachLine = computed(() => {
  const confined = data.value?.confined
  if (confined === null || confined === undefined) {
    return 'Nothing on this machine.'
  }
  return confined
    ? 'Its own working files, and nothing else on this machine.'
    : 'Everything the server can read.'
})

const reachDetail = computed(() => {
  const confined = data.value?.confined
  if (confined === null || confined === undefined) {
    return `${data.value?.service_label ?? 'This service'} is asked to think and nothing more, so it is never given a way to open a file.`
  }
  if (confined) {
    return `This service can run code, so Venona shuts it into its own files first. It cannot read your documents, your settings, or the credential it signs in with. (${data.value?.confinement_detail ?? ''})`
  }
  return `This service can run code, and this machine cannot be told to stop it. ${data.value?.confinement_detail ?? ''} Anything the server can read, a run can read.`
})

function closeConnect(): void {
  // The field is emptied on the way OUT, not only on success. Cancelling used
  // to leave a pasted credential sitting in the input for as long as the tab
  // stayed open, which is the thing a write-only field exists to prevent.
  connecting.value = false
  secret.value = ''
  secretError.value = null
}

async function confirmConnect(): Promise<void> {
  busy.value = true
  secretError.value = null
  try {
    data.value = await apiPut<ModelSettings>('/admin/model/credential', {
      secret: secret.value,
      billing_mode: connectPlan.value,
      service: connectTo.value || undefined,
    })
    // Cleared rather than kept: a field that still holds a credential after the
    // dialog closes is a credential on a screen for as long as the tab is open.
    closeConnect()
    toasts.push({ kind: 'success', text: 'Connected.' })
  } catch (error) {
    secretError.value =
      error instanceof Error ? error.message : 'that credential was not accepted'
  } finally {
    busy.value = false
  }
}

async function confirmDisconnect(): Promise<void> {
  busy.value = true
  try {
    await apiDelete('/admin/model/credential')
    disconnecting.value = false
    await load()
    toasts.push({ kind: 'success', text: 'Disconnected.' })
  } catch (error) {
    toasts.push({
      kind: 'error',
      text: error instanceof Error ? error.message : 'Nothing was disconnected.',
    })
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <ViewShell width="wide" :identity="identity">
    <template v-if="data && data.connection.source === 'none'" #notice>
      <StatusNote kind="error">
        Venona has no model service connected, so no run, brief or lookup can work.
        <template #hint>Connect one below and everything resumes.</template>
      </StatusNote>
    </template>

    <template v-else-if="data && !data.version_matches_pin" #notice>
      <StatusNote kind="info">
        {{ data.service_label }} is version {{ data.version ?? 'unknown' }}, and Venona was
        built against {{ data.pinned }}.
        <template #hint>Usually harmless. Worth knowing if results look different.</template>
      </StatusNote>
    </template>

    <RailLayout>
      <template #rail><AdminRail current="model" /></template>

      <PaneState :loading="loading" :error="failure">
        <div v-if="data" class="flex flex-col">
          <SectionHead as="h2" label="the service" rule />

          <Panel variant="bare" class="mt-3">
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <div class="min-w-0">
                <p class="text-sm text-text-primary">{{ data.service_label }}</p>
                <p class="text-2xs text-text-tertiary">
                  {{ data.version ?? 'version not reported' }} ·
                  {{ data.billing_mode === 'api' ? 'API key' : 'subscription' }}
                </p>
              </div>
              <Chip :tone="data.connection.working === false ? 'critical' : 'neutral'" dot>
                {{ data.connection.working === false ? 'not answering' : 'in use' }}
              </Chip>
            </div>
          </Panel>

          <div
            v-for="service in other"
            :key="service.key"
            class="mt-3 border-t border-edge-subtle pt-3"
          >
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <div class="min-w-0">
                <p class="text-sm text-text-primary">{{ service.label }}</p>
                <p class="text-2xs text-text-tertiary">
                  {{ service.installed ? 'available' : 'not installed here' }} ·
                  {{ service.connected ? 'connected' : 'nothing to sign in with' }}
                </p>
                <!-- WHAT IT WOULD TAKE, before the verb. A row reading
                     "nothing to sign in with" beside a disabled switch tells an
                     administrator they are stuck without telling them what to
                     do, and the two ways to pay for a service want completely
                     different things. -->
                <p class="mt-1 max-w-hint text-2xs text-text-tertiary">
                  <template v-for="(p, i) in service.plans" :key="p.mode"
                    ><template v-if="i"> · </template>{{ p.label }}:
                    {{ p.connected ? 'connected' : p.pasteable ? p.credential : 'its own sign-in' }}</template
                  >
                </p>
              </div>
              <div class="flex flex-none gap-2">
                <Button
                  v-if="service.installed"
                  variant="text"
                  @click="openConnect(service.key)"
                >
                  {{ service.connected ? 'Credentials' : 'Connect' }}
                </Button>
                <Button
                  variant="text"
                  :disabled="!service.installed || !service.connected || !!data.change_blocked"
                  :title="data.change_blocked ?? undefined"
                  @click="openChange(service.key)"
                >
                  Change to this
                </Button>
              </div>
            </div>
          </div>

          <SectionHead as="h2" label="the connection" rule class="mt-6" />

          <Panel variant="bare" class="mt-3">
            <p class="text-sm text-text-primary">{{ connectionLine }}</p>
            <p v-if="data.connection.account" class="text-2xs text-text-tertiary [overflow-wrap:anywhere]">
              {{ data.connection.account }}
            </p>
            <p v-if="data.connection.hint" class="mono text-2xs text-text-tertiary">
              {{ data.connection.hint }}
            </p>
            <p v-if="workingLine" class="mt-1 text-2xs text-text-tertiary">{{ workingLine }}</p>

            <div class="mt-3 flex flex-wrap gap-2">
              <!-- THE PLAN IN FORCE, named rather than guessed. Replacing a
                   credential means replacing the one being used, and without
                   the second argument the dialog falls through to "the first
                   plan this service can take a paste for" -- which on a
                   subscription instance is the API key. The token would then
                   be stored under `api` while every call reads `subscription`,
                   the page would say "Connected." and the next run would use
                   the dead credential. -->
              <Button variant="text" @click="openConnect(data.service, data.billing_mode)">
                {{ data.connection.source === 'none' ? 'Connect' : 'Replace credential' }}
              </Button>
              <Button
                v-if="data.connection.source === 'stored'"
                variant="text"
                tone="critical"
                @click="disconnecting = true"
              >
                Disconnect
              </Button>
            </div>
          </Panel>

          <SectionHead as="h2" label="what it can reach" rule class="mt-6" />

          <Panel variant="bare" class="mt-3">
            <p class="text-sm text-text-primary">{{ reachLine }}</p>
            <p class="mt-1 max-w-hint text-2xs text-text-tertiary">{{ reachDetail }}</p>
          </Panel>

          <SectionHead as="h2" label="what each job asks for" rule class="mt-6" />

          <Table :columns="ROLE_COLUMNS" :rows="data.roles" row-key="role" class="mt-3">
            <template #cell:effort="{ row }">
              <span class="text-text-tertiary">{{ row.effort ?? '' }}</span>
            </template>
          </Table>

          <p class="mt-3 text-2xs text-text-tertiary">
            Edit these under Configuration, in the settings document.
          </p>
        </div>
      </PaneState>
    </RailLayout>

    <Dialog
      :open="changing"
      title="Change the model service"
      confirm-label="Change it"
      :busy="busy"
      @confirm="confirmChange"
      @update:open="changing = false"
    >
      Every run, brief and lookup from now on will be done by this service instead. Work already
      in flight finishes on the one that started it.
    </Dialog>

    <!-- THE DIALOG NAMES ITS SUBJECT, because "Connect a credential" over a
         lone password box asks a question only the service can answer: what
         kind of credential, for which way of paying, and where an administrator
         is meant to get one. All three differ by service. -->
    <Dialog
      :open="connecting"
      :title="connectService ? `Connect ${connectService.label}` : 'Connect a credential'"
      :confirm-label="plan?.pasteable ? 'Verify and save' : 'Close'"
      :busy="busy"
      @confirm="plan?.pasteable ? confirmConnect() : closeConnect()"
      @update:open="closeConnect"
    >
      <!-- THE CHOICE, made explicit. A subscription and a key are not two
           routes to the same thing: one draws on a plan somebody already pays
           for, the other bills per token. The page showed which was in force
           and gave no way to move between them. -->
      <div v-if="connectPlans.length > 1" class="flex flex-col gap-2">
        <span class="font-ui text-xs uppercase tracking-label text-text-tertiary">
          How this service is paid for
        </span>
        <div class="flex flex-wrap gap-2">
          <Button
            v-for="option in connectPlans"
            :key="option.mode"
            :variant="option.mode === connectPlan ? 'outline' : 'text'"
            case="literal"
            @click="connectPlan = option.mode"
          >
            {{ option.label }}<template v-if="option.connected"> · connected</template>
          </Button>
        </div>
      </div>

      <p v-if="plan" class="mt-3 max-w-hint text-sm leading-normal text-text-body">
        {{ plan.how }}
      </p>

      <template v-if="plan?.pasteable">
        <p class="mt-3 max-w-hint text-2xs leading-normal text-text-tertiary">
          Venona puts it to work before storing it, asking the service a one word question, so
          one that does not work is refused here rather than by the next run. That takes a few
          seconds.
        </p>
        <Input
          v-model="secret"
          family="mono"
          type="password"
          class="mt-3 w-full"
          :placeholder="`Paste ${plan.credential}`"
        />
      </template>

      <!-- NO BOX when there is nothing to put in it. This service keeps this
           kind of credential in its own files and renews it there, so a field
           here would take a value nothing ever reads and report success.
           THE COMMAND INSTEAD, to be copied: whoever reads this is about to
           open a shell on another machine, and a command retyped out of a
           paragraph is the step that goes wrong. -->
      <template v-else-if="plan">
        <StatusNote kind="info" class="mt-3">
          There is nothing to paste for this one. Open a shell on the machine running Venona,
          run the command below, and sign in there. This page will see it.
        </StatusNote>
        <div
          v-if="plan.command"
          class="mt-3 flex items-center justify-between gap-3 border border-border-default rounded-md px-3 py-2"
        >
          <code class="mono min-w-0 truncate text-sm text-text-primary">{{ plan.command }}</code>
          <Button variant="text" case="literal" @click="copyCommand">Copy</Button>
        </div>
      </template>

      <StatusNote v-if="secretError" kind="error" class="mt-3">{{ secretError }}</StatusNote>
    </Dialog>

    <Dialog
      :open="disconnecting"
      title="Disconnect this credential"
      confirm-label="Disconnect"
      destructive
      :busy="busy"
      @confirm="confirmDisconnect"
      @update:open="disconnecting = false"
    >
      Venona will have nothing to sign in with unless another credential is set where it is
      deployed. Until one is connected, no run, brief rewrite or lookup can work.
    </Dialog>
  </ViewShell>
</template>
