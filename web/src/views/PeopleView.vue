<script setup lang="ts">
/**
 * Who may reach this instance, and what has been done about it.
 *
 * `venona users` and `Venona access` on a screen, with the same rules and the
 * same refusals -- because they are the SAME rules: every control here calls a
 * route that calls `user_service.change_authority`, which holds the advisory
 * lock, the self-target refusal, the last-admin refusal and the audit write.
 * Nothing on this page is the gate. It disables a control the server would
 * refuse so the refusal is not a surprise, and then shows the server's own
 * sentence when it happens anyway.
 *
 * THE CEILING IS `reader`, AND IT IS NOT A UI CHOICE. `RoleChange.role` is
 * `Literal["reader"]` in `schemas/admin.py`, so it appears in the OpenAPI
 * document as the only accepted value and this page could not offer "make
 * admin" even if it wanted to. Promotion is `venona users role <email> admin`,
 * which needs shell access -- granting admin hands out model spend, every
 * `config/*` read including the operator's own relevance profile, and
 * `POST /api/data/purge`.
 *
 * THERE IS NO DELETE, and the absence is a decision rather than an omission.
 * The five actor columns are `ON DELETE NO ACTION`, so Postgres refuses to
 * remove anyone who has ever decided anything and permits removing everyone who
 * has not -- a feature that passes its own tests and fails on every real case.
 * Suspension ends their sessions on the next request, refuses their agent
 * tokens, and keeps the row that answers "when did I turn this off".
 *
 * THERE IS NO EMAIL. `notify.py` is one env var and one POST, operator-facing;
 * mail would mean a dependency, credentials, a template, bounce handling and an
 * offline-replay story for one message (Decision 4). So approval hands back a
 * line to send, and the users table shows "never signed in" until they arrive.
 *
 * `coverage` is up to 2000 characters written by an unauthenticated stranger and
 * is shown here IN FULL, because truncating it is what `cli.py::_access_list`
 * refuses to do -- it is the only thing distinguishing a real analyst from a
 * scraper. It renders through `{{ }}`, which Vue escapes; there is no `v-html`
 * in `web/src` and this is not the place to add the first one.
 */
import { computed, onMounted, ref, type Ref } from 'vue'
import { storeToRefs } from 'pinia'

import AdminRail from '@/components/nav/AdminRail.vue'
import StatBlock from '@/components/shared/StatBlock.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Input from '@/components/ui/Input.vue'
import Panel from '@/components/ui/Panel.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useAdminPagesStore } from '@/stores/adminPages'
import { apiGet, apiPatch, apiPost, errorText } from '@/api/client'
import { copyText } from '@/lib/clipboard'
import { agoFrom } from '@/lib/duration'
import {
  EVENT_COLUMNS,
  REQUEST_COLUMNS as SHARED_REQUEST_COLUMNS,
  REQUEST_STATUS_TONE,
  ROLE_TONE,
  subscriptionLabel,
  subscriptionTone,
  USER_STATUS_TONE,
  eventRow,
  requestRow,
  seenLabel,
} from '@/lib/people'
import { useSessionStore } from '@/stores/session'
import { useToastsStore } from '@/stores/toasts'
import type { components } from '@/api/schema'

type AdminUser = components['schemas']['AdminUser']
type AdminUserList = components['schemas']['AdminUserList']
type AccessRequest = components['schemas']['AccessRequest']
type AccessRequestList = components['schemas']['AccessRequestList']
type AuthorityEventList = components['schemas']['AuthorityEventList']

const session = useSessionStore()
const toasts = useToastsStore()

// Held in the store so a revisit paints instantly; the mount fetch below is
// a revalidation, not the render's gate.
const { users, peopleRequests: requests, peopleEvents: events } = storeToRefs(
  useAdminPagesStore(),
)
const loading = ref(false)
const error = ref<string | null>(null)

/** Pending only by default: the queue is a thing to empty, not a ledger. */
const scope = ref<'pending' | 'all'>('pending')
const SCOPES = [
  { value: 'pending', label: 'Pending' },
  { value: 'all', label: 'All' },
] as const

/** The one open confirmation, whatever kind it is. */
const pending = ref<
  | { kind: 'demote' | 'suspend' | 'reinstate'; user: AdminUser }
  | { kind: 'approve' | 'decline'; request: AccessRequest }
  | null
>(null)
const note = ref('')
const busy = ref(false)
const failure = ref('')

/** The line to send by hand, because nothing sends it for us. */
const invite = ref<string | null>(null)

const identity = computed(() => `admin · people · ${users.value?.total ?? 0}`)

const lastAdmin = computed(() => (users.value?.active_admins ?? 0) <= 1)

const userRows = computed(() =>
  (users.value?.items ?? []).map((u) => ({
    id: u.id,
    email: u.email,
    name: u.display_name ?? '',
    role: u.role,
    status: u.status,
    seen: seenLabel(u.never_signed_in, agoFrom(u.last_seen_at, Date.now()) || '—'),
    subscription: u.entitlement_status,
    user: u,
  })),
)

/**
 * The subscription column exists only where there ARE subscriptions.
 *
 * On an instance that takes no money every row reads "not started", which is a
 * column of noise about a thing that does not happen here -- and this screen is
 * already dense. There are no ACTIONS on it either: the instance holds a
 * projection and cannot change a subscription, so anything to be done is done
 * on the hub, where the data is.
 */
const billingOn = computed(() => Boolean(session.info?.billing?.enforced))

const USER_COLUMNS = computed<Column<(typeof userRows.value)[number]>[]>(() => [
  { key: 'email', label: 'address' },
  { key: 'role', label: 'role' },
  { key: 'status', label: 'status' },
  ...(billingOn.value
    ? [{ key: 'subscription' as const, label: 'subscription', hideBelow: 'md' as const }]
    : []),
  { key: 'seen', label: 'last seen' },
  { key: 'id', label: '' },
])

/* The row the preview on /admin builds, plus the request itself: this is the
 * screen that can approve one, and the actions take the whole object. */
const requestRows = computed(() =>
  (requests.value?.items ?? []).map((r) => ({ ...requestRow(r), request: r })),
)

const REQUEST_COLUMNS: Column<(typeof requestRows.value)[number]>[] = [
  ...SHARED_REQUEST_COLUMNS,
  { key: 'status', label: '' },
]

const eventRows = computed(() => (events.value?.items ?? []).map((e) => eventRow(e)))

/** Wire enums for the screen: a snake_case status never renders raw. */
function enumLabel(value: string): string {
  return value.replace(/_/g, ' ')
}

/**
 * Why a control is disabled, as a sentence, or `''` when it is not.
 *
 * A disabled control with no explanation reads as a broken page. Both rules are
 * the server's -- `change_authority` refuses the same two -- and both are
 * re-checked there inside the lock, so this is the explanation and never the
 * enforcement.
 */
function refusal(user: AdminUser, kind: 'demote' | 'suspend'): string {
  if (session.userId && user.id === session.userId) {
    return kind === 'demote'
      ? 'you cannot change your own role'
      : 'you cannot suspend your own account'
  }
  if (user.role === 'admin' && user.status === 'active' && lastAdmin.value) {
    return 'this is the only active administrator'
  }
  return ''
}

function ask(entry: NonNullable<typeof pending.value>) {
  note.value = ''
  failure.value = ''
  invite.value = null
  pending.value = entry
}

const title = computed(() => {
  const entry = pending.value
  if (!entry) return ''
  // `'user' in entry` rather than a `kind` test: the two members share no
  // fields, so this is the narrowing TypeScript can follow and a `kind` check
  // is one it cannot.
  if ('user' in entry) {
    if (entry.kind === 'demote') return `Make ${entry.user.email} a reader`
    if (entry.kind === 'suspend') return `Suspend ${entry.user.email}`
    return `Reinstate ${entry.user.email}`
  }
  return entry.kind === 'approve'
    ? `Approve ${entry.request.email}`
    : `Decline ${entry.request.email}`
})

async function confirm() {
  const entry = pending.value
  if (!entry) return
  busy.value = true
  failure.value = ''
  const body = { note: note.value.trim() || null }
  try {
    if ('user' in entry) {
      if (entry.kind === 'demote') {
        await apiPatch(`/admin/users/${entry.user.id}/role`, { role: 'reader' })
      } else if (entry.kind === 'suspend') {
        await apiPost(`/admin/users/${entry.user.id}/suspend`, body)
      } else {
        await apiPost(`/admin/users/${entry.user.id}/reinstate`, body)
      }
    } else if (entry.kind === 'approve') {
      const decided = await apiPost<AccessRequest>(`/access/${entry.request.id}/approve`, body)
      // The only thing that tells them, and nothing else will: see the docblock.
      invite.value =
        `You now have access to Venona. Sign in at ${window.location.origin}/login ` +
        `with ${decided.account_email ?? decided.email}.`
    } else {
      await apiPost(`/access/${entry.request.id}/decline`, body)
    }
    if (!invite.value) pending.value = null
    await load()
  } catch (err) {
    // A 409 here is a REFUSAL, not a permission failure: the caller is an
    // administrator and is allowed to be here — what they asked for is the thing
    // that cannot happen, and the server's sentence says which rule stopped it.
    failure.value = errorText(err, 'that change did not go through')
  } finally {
    busy.value = false
  }
}

async function copyInvite() {
  if (!invite.value) return
  // `copyText` reports rather than throws: `navigator.clipboard` is absent over
  // plain http from anything but localhost, which is a real way to run this on a
  // LAN. A toast claiming success over an empty clipboard is the failure it
  // exists to prevent.
  const copied = await copyText(invite.value)
  toasts.push(
    copied
      ? { kind: 'success', text: 'Copied. Venona does not send email, so send it yourself.' }
      : { kind: 'error', text: 'Could not reach the clipboard. Select the line and copy it.' },
  )
}

async function load() {
  // First load only: with held data this is a silent revalidation, and every
  // post-action `load()` in this file stops blanking the page too.
  const first = users.value === null
  if (first) loading.value = true
  error.value = null
  try {
    const [u, r, e] = await Promise.all([
      apiGet<AdminUserList>(`/admin/users?limit=${PAGE}`),
      apiGet<AccessRequestList>(`${requestsPath()}&limit=${PAGE}`),
      apiGet<AuthorityEventList>(`/admin/events?limit=${PAGE}`),
    ])
    users.value = u
    requests.value = r
    events.value = e
  } catch (err) {
    // A failed revalidate keeps the held page rather than replacing real rows
    // with an error state.
    if (first) error.value = errorText(err, 'could not read the user list')
  } finally {
    if (first) loading.value = false
  }
}

async function reloadRequests() {
  requests.value = await apiGet<AccessRequestList>(
    `${requestsPath()}&limit=${PAGE}`,
  )
}

/** One page for each of the three lists. They are read together and scrolled
 *  together, so a single size keeps "load more" meaning the same thing. */
const PAGE = 100

/** Always a `?` form, so callers can append `&limit=` without knowing whether a
 *  filter is already on the query string. */
function requestsPath(): string {
  return `/access/requests?all=${scope.value === 'all'}`
}

/** Append the next window in place.
 *
 *  These lists live in `adminPages` as whole payloads, which is what lets a
 *  revisit render without a spinner -- so growing one means extending the held
 *  payload rather than swapping in a new list and losing that.
 */
async function more<T extends { items?: unknown[] | null; total?: number }>(
  held: Ref<T | null>,
  path: string,
): Promise<void> {
  const current = held.value
  if (!current) return
  const offset = current.items?.length ?? 0
  loadingMore.value = true
  try {
    const next = await apiGet<T>(`${path}&offset=${offset}`)
    held.value = { ...current, ...next, items: [...(current.items ?? []), ...(next.items ?? [])] }
  } catch (err) {
    error.value = errorText(err, 'could not load more')
  } finally {
    loadingMore.value = false
  }
}

// Named wrappers rather than passing the refs from the template: Vue unwraps a
// ref on the way through a binding, so the template would hand over the value
// and there would be nothing left to assign the longer list back to.
const moreUsersNow = () => more(users, `/admin/users?limit=${PAGE}`)
const moreRequestsNow = () => more(requests, `${requestsPath()}&limit=${PAGE}`)
const moreEventsNow = () => more(events, `/admin/events?limit=${PAGE}`)

const loadingMore = ref(false)
const moreUsers = computed(() => (users.value?.items.length ?? 0) < (users.value?.total ?? 0))
const moreRequests = computed(
  () => (requests.value?.items.length ?? 0) < (requests.value?.total ?? 0),
)
const moreEvents = computed(() => (events.value?.items.length ?? 0) < (events.value?.total ?? 0))

onMounted(load)
</script>

<template>
  <ViewShell width="wide" scroll="page" :identity="identity">
    <RailLayout>
      <template #rail>
        <AdminRail current="people" />
      </template>

      <PaneState :loading="loading" :error="error">
      <div class="flex flex-col gap-4">
        <div v-if="users" class="figures flex flex-wrap gap-6">
          <StatBlock :value="users.total ?? 0" label="users" />
          <StatBlock :value="users.active_admins" label="admins" />
          <StatBlock
            :value="requests?.pending ?? 0"
            label="pending requests"
            :tone="requests?.pending ? 'high' : 'neutral'"
          />
        </div>

        <Panel variant="bare" title="People">
          <Table :columns="USER_COLUMNS" :rows="userRows" row-key="id">
            <template #cell:email="{ row }">
              <span class="text-text-primary">{{ row.email }}</span>
              <span v-if="row.name" class="text-text-tertiary"> · {{ row.name }}</span>
              <span v-if="session.userId === row.id" class="text-text-quiet"> · you</span>
            </template>
            <template #cell:role="{ row }">
              <Chip size="sm" variant="bare" :tone="ROLE_TONE[row.role] ?? 'neutral'">
                {{ row.role }}
              </Chip>
            </template>
            <template #cell:status="{ row }">
              <Chip size="sm" variant="bare" dot :tone="USER_STATUS_TONE[row.status] ?? 'neutral'">
                {{ enumLabel(row.status) }}
              </Chip>
            </template>
            <template #cell:subscription="{ row }">
              <Chip
                size="sm"
                variant="bare"
                :tone="subscriptionTone(row.subscription)"
              >
                {{ subscriptionLabel(row.subscription) }}
              </Chip>
            </template>
            <template #cell:id="{ row }">
              <span class="flex justify-end gap-2">
                <Button
                  v-if="row.role === 'admin'"
                  variant="text"
                  icon="user-minus"
                  label="Make reader"
                  :disabled="!!refusal(row.user, 'demote')"
                  :title="refusal(row.user, 'demote')"
                  @click="ask({ kind: 'demote', user: row.user })"
                >
                  Make reader
                </Button>
                <Button
                  v-if="row.status === 'active'"
                  variant="text"
                  icon="user-x"
                  label="Suspend"
                  tone="critical"
                  :disabled="!!refusal(row.user, 'suspend')"
                  :title="refusal(row.user, 'suspend')"
                  @click="ask({ kind: 'suspend', user: row.user })"
                >
                  Suspend
                </Button>
                <Button
                  v-else
                  variant="text"
                  icon="user-check"
                  label="Reinstate"
                  @click="ask({ kind: 'reinstate', user: row.user })"
                >
                  Reinstate
                </Button>
              </span>
            </template>
            <template #empty>
              <EmptyState title="Nobody has an account yet">
                The first administrator is created with
                <span class="font-mono">venona users add</span>. Everyone after that arrives
                through the queue below.
              </EmptyState>
            </template>
          </Table>
          <Button
            v-if="moreUsers"
            variant="text"
            icon="down"
            :disabled="loadingMore"
            @click="moreUsersNow"
          >
            Show more people
          </Button>
          <p class="mt-3 max-w-hint font-prose text-xs leading-normal text-text-tertiary">
            There is no delete. Suspending someone signs them out, and any assistant they have
            connected stops working. Making someone an administrator needs a terminal on the
            server: <span class="font-mono">venona users role</span>.
          </p>
        </Panel>

        <Panel variant="bare" title="Access requests">
          <!-- `#header`, which is the slot Panel has -- the filter belongs on
               the panel's own header line, beside its title. -->
          <template #header>
            <SegmentedControl
              v-model="scope"
              :options="SCOPES"
              ariaLabel="access queue"
              @update:model-value="reloadRequests"
            />
          </template>
          <Table :columns="REQUEST_COLUMNS" :rows="requestRows" row-key="id">
            <template #cell:coverage="{ row }">
              <!-- In full, and escaped by Vue. See the docblock. -->
              <span class="whitespace-pre-line text-text-tertiary">{{ row.coverage || '—' }}</span>
            </template>
            <template #cell:status="{ row }">
              <span v-if="row.status === 'pending'" class="flex justify-end gap-2">
                <Button variant="text" icon="check" label="Approve" @click="ask({ kind: 'approve', request: row.request })">
                  Approve
                </Button>
                <Button
                  variant="text"
                  icon="ban"
                  label="Decline"
                  tone="critical"
                  @click="ask({ kind: 'decline', request: row.request })"
                >
                  Decline
                </Button>
              </span>
              <Chip
                v-else
                size="sm"
                variant="bare"
                dot
                :tone="REQUEST_STATUS_TONE[row.status] ?? 'neutral'"
              >
                {{ enumLabel(row.status) }}
              </Chip>
            </template>
            <template #empty>
              <EmptyState title="The queue is empty">
                Approving a request creates a reader account and gives you an invite line to
                send. Venona does not send email.
              </EmptyState>
            </template>
          </Table>
          <Button
            v-if="moreRequests"
            variant="text"
            icon="down"
            :disabled="loadingMore"
            @click="moreRequestsNow"
          >
            Show more requests
          </Button>
        </Panel>

        <Panel variant="bare" title="Account changes">
          <Table :columns="EVENT_COLUMNS" :rows="eventRows" row-key="id">
            <template #cell:action="{ row }">
              <span class="text-text-primary">{{ row.action }}</span>
              <span v-if="row.detail" class="text-text-tertiary"> · {{ row.detail }}</span>
            </template>
            <template #empty>
              <EmptyState title="Nothing has changed yet">
                Role changes, suspensions and reinstatements are recorded here.
              </EmptyState>
            </template>
          </Table>
          <Button
            v-if="moreEvents"
            variant="text"
            icon="down"
            :disabled="loadingMore"
            @click="moreEventsNow"
          >
            Show more changes
          </Button>
        </Panel>
      </div>
      </PaneState>
    </RailLayout>

    <Dialog
      :open="pending !== null"
      :title="title"
      :destructive="pending?.kind === 'suspend' || pending?.kind === 'decline'"
      :busy="busy"
      @update:open="pending = null"
    >
      <p v-if="pending?.kind === 'demote'" class="font-prose text-base leading-normal text-text-secondary">
        They keep their account and stay signed in. They lose Curation, Runs and Admin, and can
        no longer change anything. Making them an administrator again needs a terminal on the
        server.
      </p>
      <p v-else-if="pending?.kind === 'suspend'" class="font-prose text-base leading-normal text-text-secondary">
        They are signed out, and any assistant they have connected stops working. Nothing is
        deleted, and you can reinstate them from this page.
      </p>
      <p v-else-if="pending?.kind === 'reinstate'" class="font-prose text-base leading-normal text-text-secondary">
        They can sign in again, with the role they had. The assistants they connected start
        working again too. Disconnect any you do not recognise first.
      </p>
      <p v-else-if="pending?.kind === 'approve'" class="font-prose text-base leading-normal text-text-secondary">
        Creates a reader account for
        <span class="font-mono text-text-primary">{{ pending.request.email }}</span> and closes
        any other requests from them. If that address already has an account, nothing about it
        changes.
      </p>
      <p v-else-if="pending?.kind === 'decline'" class="font-prose text-base leading-normal text-text-secondary">
        Closes the request. Nothing is sent, and they can ask again.
      </p>

      <StatusNote v-if="failure" kind="error" class="mt-3">{{ failure }}</StatusNote>

      <StatusNote v-if="invite" kind="success" class="mt-3">
        The account is ready. Venona does not send email, so send this line to them yourself.
        <template #hint>
          <span class="font-mono">{{ invite }}</span>
        </template>
      </StatusNote>

      <template v-if="!invite">
        <label
          for="people-note"
          class="mt-4 block font-ui text-2xs uppercase tracking-label text-text-tertiary"
        >
          Note <span class="normal-case tracking-normal">(optional, for you)</span>
        </label>
        <Input id="people-note" v-model="note" class="mt-2" autocomplete="off" />
      </template>

      <template #actions>
        <Button v-if="invite" variant="outline" icon="copy" @click="copyInvite">Copy</Button>
        <Button v-if="invite" icon="check" @click="pending = null">Done</Button>
        <template v-else>
          <Button variant="outline" icon="close" :disabled="busy" @click="pending = null">Cancel</Button>
          <Button
            icon="check"
            :tone="pending?.kind === 'suspend' || pending?.kind === 'decline' ? 'critical' : 'default'"
            :disabled="busy"
            @click="confirm"
          >
            {{ busy ? 'Working…' : 'Confirm' }}
          </Button>
        </template>
      </template>
    </Dialog>
  </ViewShell>
</template>
