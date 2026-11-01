<script setup lang="ts">
/**
 * Every assistant connected to this instance, whoever it belongs to.
 *
 * `GET /api/meta/mcp` is filtered to the calling user by design (`authz.py`
 * says so), which is right and leaves a hole behind it: the person who owns the
 * box could not see what connections existed on it, so a leaked one was
 * invisible to the only person who could end it. This page is that view, over a
 * SECOND endpoint rather than a widened first one.
 *
 * IT IS HERE AND NOT ON `/settings/agents`, which is where it started. A reader
 * never reaches an admin page, so a section they cannot use — and beneath it a
 * link to People they cannot open — made a reader's own settings page read as
 * somebody else's. `SettingsRail`'s docblock carries the general rule: Settings
 * is the reader's own, and what belongs to the instance is on `AdminRail`.
 *
 * NO MINTING, and that asymmetry is the contract rather than an omission: over
 * HTTP, access can be taken away and never handed out (C12). `api/security.py`
 * has the reasoning — an HTTP mint would let a compromised credential
 * manufacture a persistent one that outlives rotation of the credential that
 * made it.
 *
 * The table also CANNOT show a credential: Venona stores a SHA-256, so there is
 * nothing here to leak even if someone added the field.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import AdminRail from '@/components/nav/AdminRail.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import PaneState from '@/components/ui/PaneState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useAdminPagesStore } from '@/stores/adminPages'
import { apiDelete, apiGet, errorText } from '@/api/client'
import { agoFrom, untilFrom } from '@/lib/duration'
import { connectionOrigin, connectionReach } from '@/lib/agentCopy'
import { useToastsStore } from '@/stores/toasts'
import type { components } from '@/api/schema'

type AdminTokenList = components['schemas']['AdminTokenList']

// Held in the store, so hopping between admin pages revalidates in place rather
// than spinning: only the true first visit has nothing to draw.
const { instanceTokens } = storeToRefs(useAdminPagesStore())
const loading = ref(false)
const error = ref<string | null>(null)
const toasts = useToastsStore()

const identity = computed(
  () => `admin · agents · ${instanceTokens.value?.items.length ?? 0} connected`,
)

const rows = computed(() => {
  const now = Date.now()
  return (instanceTokens.value?.items ?? []).map((t) => ({
    id: t.id,
    // A connection whose `user_id` is NULL predates identity existing. The row
    // is shown — one belonging to nobody is the one most worth looking at — and
    // it says so rather than leaving the cell blank, which reads as a failed
    // load.
    owner: t.user_email ?? '',
    origin: connectionOrigin(t.client_name, t.label),
    reach: connectionReach(t.scope),
    // `—`, never blank: "never used" and "used just now" must not render the
    // same way on a page whose whole job is telling you which is which.
    used: t.last_used_at ? agoFrom(t.last_used_at, now) || t.last_used_at : '—',
    // A connection made at a terminal can have no end date at all, and saying
    // so is the point: it is the one kind that does not lapse on its own.
    expires: t.expires_at ? untilFrom(t.expires_at, now) || t.expires_at : 'does not end',
    act: '',
  }))
})

const COLUMNS: Column<(typeof rows.value)[number]>[] = [
  { key: 'owner', label: 'whose' },
  { key: 'origin', label: 'connection' },
  { key: 'reach', label: 'what it reads' },
  { key: 'used', label: 'last used' },
  { key: 'expires', label: 'ends' },
  { key: 'act', label: '' },
]

const pending = ref<{ id: string; label: string } | null>(null)
const ending = ref(false)

function askToEnd(id: string, label: string) {
  pending.value = { id, label }
}

async function endConnection() {
  const target = pending.value
  if (!target) return
  ending.value = true
  try {
    const result = await apiDelete<{ ended: boolean }>(`/admin/tokens/${target.id}`)
    // `ended: false` is not a failure. It means the list this click came from
    // was stale — ended in another tab, or already lapsed — and saying "already
    // ended" is more useful than a success message that implies this click did
    // it.
    toasts.push({
      kind: 'success' as const,
      text: result.ended ? 'connection ended' : 'that connection had already ended',
    })
    pending.value = null
    await load()
  } catch (err) {
    toasts.push({ kind: 'error' as const, text: errorText(err, 'could not end that connection') })
  } finally {
    ending.value = false
  }
}

/** No `isAdmin()` guard on the fetch, deliberately: this route carries
 *  `meta.operator` and `router/index.ts`'s guard redirects before the component
 *  mounts, so the check is unreachable. `AdminRail` makes the same call for the
 *  same reason. */
async function load() {
  const first = instanceTokens.value === null
  if (first) loading.value = true
  try {
    instanceTokens.value = await apiGet<AdminTokenList>('/admin/tokens')
  } catch (err) {
    error.value = errorText(err, 'could not read the list of connections')
  } finally {
    if (first) loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <ViewShell width="wide" scroll="page" :identity="identity">
    <RailLayout>
      <template #rail>
        <AdminRail current="agents" />
      </template>

      <PaneState :loading="loading" :error="error">
      <div class="flex flex-col">
        <SectionHead as="h2" label="everyone's connections" rule />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          These are all the assistants connected to this domain, whoever they belong to.
        </p>

        <Table v-if="rows.length" class="mt-3" :columns="COLUMNS" :rows="rows" row-key="id">
          <template #cell:owner="{ row }">
            <span v-if="row.owner" class="text-text-primary">{{ row.owner }}</span>
            <span v-else class="text-text-quiet">nobody</span>
          </template>
          <template #cell:act="{ row }">
            <Button
              variant="outline"
              tone="critical"
              @click="askToEnd(String(row.id), String(row.origin))"
            >
              Disconnect
            </Button>
          </template>
        </Table>
        <EmptyState v-else class="mt-3" title="Nothing is connected">
          Nobody has connected an assistant to this domain yet.
        </EmptyState>

        <StatusNote kind="info" class="mt-3">
          Suspending someone on
          <RouterLink to="/admin/people" class="text-accent-primary">People</RouterLink>
          stops their connections from working. Disconnecting one here removes it permanently.
        </StatusNote>
      </div>
      </PaneState>
    </RailLayout>

    <Dialog
      :open="pending !== null"
      title="End this connection?"
      confirm-label="Disconnect"
      destructive
      :busy="ending"
      @update:open="pending = null"
      @confirm="endConnection"
    >
      <p class="font-prose text-base leading-normal text-text-secondary">
        {{ pending?.label }} stops being able to read anything from Venona. Nothing else
        changes, and whoever owns it can connect again by signing in.
      </p>
    </Dialog>
  </ViewShell>
</template>
