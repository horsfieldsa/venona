<script setup lang="ts">
/**
 * The MCP server, and the assistants connected to it.
 *
 * WHAT IS REAL HERE AND WHAT IS NOT. Read this before adding a control.
 * `AccountView.vue` opens the same way and for the same reason: a control that
 * looks persisted and does nothing is worse than no control.
 *
 *   server url   Derived in the BROWSER from `window.location.origin`, not
 *                served by the API. The API sits behind the reverse proxy and does not know
 *                its own scheme or host, the same fact the reverse proxy config
 *                cites for owning the HSTS header. So this states the address
 *                THIS browser used and says so, rather than printing a URL an
 *                agent on another machine may not be able to reach.
 *   tools        `GET /api/meta/mcp`, generated from `api/mcp/registry.py`.
 *                Hard-coding the list here would be a second source of truth
 *                that goes stale in silence the first time a tool is added.
 *   connections  The same endpoint. It lists what a connection is, where it
 *                came from and when it lapses, and it CANNOT list the
 *                credential: Venona stores a SHA-256, so there is nothing for
 *                this page to leak even if someone added the field.
 *   every one    NOT HERE. Every connection on the instance, whoever owns it,
 *                is `AdminAgentsView` on `/admin/agents`. This page shows a
 *                reader their own and nothing else: a section they cannot use,
 *                and beneath it a link to People they cannot open, made a
 *                reader's own settings page read as somebody else's.
 *   connecting   SIGNING IN, and that is the whole page now. A client given
 *                the address discovers the rest for itself: the 401 on
 *                `/api/mcp` carries `WWW-Authenticate: Bearer
 *                resource_metadata=...`, which leads to two documents at the
 *                site root, which lead to the consent screen. Nothing is
 *                pasted, so nothing is on a clipboard.
 *   minting      NOT HERE, AND NOT COMING. `api/security.py` opens by
 *                explaining that auth material must be neither
 *                attacker-writable nor operator-visible over HTTP, which is
 *                also why the root credential is env-only and appears on no
 *                page. A Generate button is the same door: an HTTP mint
 *                would let a compromised credential manufacture a persistent
 *                one that outlives rotation of the credential that made it.
 *                Stated on screen so it reads as a decision, not an unfinished
 *                feature.
 *   disconnect   REAL, and the asymmetry with minting is the contract rather
 *                than an inconsistency: over HTTP, access can be taken away
 *                and never handed out (C12). It is also the only revocation a
 *                person who signed in HAS -- they never saw an id, so the CLI
 *                command asks them for a value nobody showed them.
 * NOT RENDERED, and why: a per-connection scope editor (scope is decided at the
 * consent screen by what asked, and two radio buttons where one is
 * unimplementable is worse than none); per-connection spend (nothing an agent
 * can call spends, so the figure would be a permanent zero that implies the
 * opposite); a "connected now" indicator (MCP over HTTP has no session to be
 * connected in. Last-used is the honest fact, and it is what the table shows).
 *
 * AND NO CONNECTION TEST, which is worth its own line because a test button is
 * the obvious thing to add to a page like this. Anything worth testing here
 * needs a credential typed into a field, and section two of this same page
 * tells the reader that connecting involves nothing copied or pasted. A page
 * that argues one thing and offers the other teaches the offer.
 *
 * The one fault a test could genuinely find is `/.well-known/oauth-*` being
 * answered by the SPA instead of served as JSON -- invisible every other way,
 * and reaching a person only as "the client will not connect". That belongs to
 * whoever configures the proxy, not to a reader on a settings page, so it lives
 * where they already work: `docs/DEPLOYMENT.md`, under "if a discovery fetch
 * returns HTML", as two curl lines.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import SettingsRail from '@/components/nav/SettingsRail.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Panel from '@/components/ui/Panel.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useAdminPagesStore } from '@/stores/adminPages'
import { apiDelete, apiGet, errorText } from '@/api/client'
import { copyText } from '@/lib/clipboard'
import { agoFrom, untilFrom } from '@/lib/duration'
import { connectionOrigin, connectionReach } from '@/lib/agentCopy'
import { useToastsStore } from '@/stores/toasts'
import type { components } from '@/api/schema'

type McpInfo = components['schemas']['McpInfo']

// Held in the store: the MCP block is static per deployment and the connection
// table revalidates in place, so only the true first visit spins.
const { mcpInfo: info } = storeToRefs(useAdminPagesStore())
const loading = ref(false)
const error = ref<string | null>(null)
const toasts = useToastsStore()

/** The address a client should be given. Joined here, in the browser, because
 *  the API cannot name its own host. See the docblock. */
const serverUrl = computed(() => `${window.location.origin}${info.value?.path ?? '/api/mcp'}`)

const client = ref<'code' | 'desktop' | 'web'>('code')
const CLIENTS = [
  { value: 'code', label: 'Claude Code', hint: 'a terminal command' },
  { value: 'desktop', label: 'Claude Desktop', hint: 'a connector dialog' },
  { value: 'web', label: 'claude.ai', hint: 'a connector dialog' },
]

/** Rendered as LINES rather than a `<pre>`: `theme.css` is a closed Tailwind
 *  theme and betting on `whitespace-pre` surviving it is a gamble this does not
 *  need to take.
 *
 *  THE ADDRESS IS THE WHOLE CONFIGURATION. No header, no pasted secret: a client
 *  that gets a 401 here is told where the sign-in lives and finds the rest
 *  itself. That is why the second step below is a sentence rather than another
 *  line of config. */
const snippet = computed<string[]>(() => {
  const url = serverUrl.value
  if (client.value === 'code') {
    return ['claude mcp add --transport http Venona \\', `  ${url} \\`, '  --scope user']
  }
  return ['Settings → Connectors → Add custom connector', `URL     ${url}`]
})

/** What to do once it is added. Separate from the snippet because it is not
 *  something to copy, and running it is the step people miss. */
const secondStep = computed(() =>
  client.value === 'code'
    ? 'Then run /mcp, choose Venona, and pick authenticate. A browser opens, you sign in, and you approve the connection here.'
    : 'Then press Connect. A browser opens, you sign in, and you approve the connection here.',
)

const CONNECTION_COLUMNS: Column<Record<string, unknown>>[] = [
  { key: 'origin', label: 'connection' },
  { key: 'reach', label: 'what it reads' },
  { key: 'used', label: 'last used' },
  { key: 'expires', label: 'ends' },
  { key: 'act', label: '' },
]

const connectionRows = computed(() => {
  const now = Date.now()
  return (info.value?.tokens ?? []).map((t) => ({
    id: t.id,
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

const TOOL_COLUMNS: Column<Record<string, unknown>>[] = [
  { key: 'name', label: 'tool' },
  { key: 'summary', label: 'what it answers' },
]
const toolRows = computed(() =>
  (info.value?.tools ?? []).map((t) => ({ name: t.name, summary: t.summary })),
)

// ── ending a connection ───────────────────────────────────────────────
/** `DELETE /meta/mcp/tokens/:id`, and not the admin endpoint beside it: this
 *  one refuses an id that is not the caller's, which is the whole difference. */
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
    const result = await apiDelete<{ ended: boolean }>(`/meta/mcp/tokens/${target.id}`)
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

async function copySnippet() {
  const ok = await copyText(snippet.value.join('\n'))
  toasts.push(
    ok
      ? { kind: 'success' as const, text: 'copied' }
      : { kind: 'error' as const, text: 'could not reach the clipboard. Select it and copy' },
  )
}

async function load() {
  const first = info.value === null
  if (first) loading.value = true
  try {
    info.value = await apiGet<McpInfo>('/meta/mcp')
  } catch (err) {
    if (first) error.value = errorText(err, 'could not read the agent access settings')
  } finally {
    if (first) loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <ViewShell width="wide" identity="settings · agents">
    <RailLayout>
      <template #rail>
        <SettingsRail current="agents" />
      </template>

      <PaneState :loading="loading" :error="error">
      <div class="flex flex-col">
        <!-- ① the server -->
        <SectionHead as="h2" label="agent access" rule />
        <p class="mt-3 font-mono text-sm text-text-body select-all">{{ serverUrl }}</p>
        <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Connect an assistant here over MCP, the protocol assistants use to reach outside
          tools, and it can read what Venona knows: look a name up, ask what a program
          connects to, and read the sentence a definition came from. This is the address
          this browser is using. An assistant on another machine needs one it can reach.
        </p>

        <StatusNote kind="info" class="mt-3">
          Agents can only read, never write.
        </StatusNote>

        <p
          v-if="info && !info.auth_required"
          class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary"
        >
          Sign-in is turned off on this copy of Venona, so anything that can reach the address
          can read everything. Only a local test copy should run this way.
        </p>

        <!-- ② connect a client -->
        <SectionHead as="h2" label="connect a client" rule class="mt-7" />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Give the client the address. It will ask you to sign in, and you approve the
          connection here. Nothing is copied or pasted.
        </p>
        <SegmentedControl
          v-model="client"
          class="mt-3"
          :options="CLIENTS"
          ariaLabel="MCP client"
        />
        <Panel variant="inset" class="mt-3 p-4">
          <p
            v-for="(line, index) in snippet"
            :key="index"
            class="font-mono text-2xs leading-normal text-text-body whitespace-normal"
          >
            {{ line }}
          </p>
          <div class="mt-3">
            <Button variant="outline" icon="copy" label="Copy" @click="copySnippet">Copy</Button>
          </div>
        </Panel>
        <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          {{ secondStep }}
        </p>
        <p
          v-if="client === 'code'"
          class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary"
        >
          <code class="text-text-body">--scope user</code> puts it in every project on this
          machine. Avoid <code class="text-text-body">--scope project</code>: that writes
          <code class="text-text-body">.mcp.json</code> into a repository, where it is shared
          with everyone who clones it.
        </p>

        <!-- ③ what it exposes -->
        <SectionHead as="h2" label="what an agent can read" rule class="mt-7" />
        <Table
          v-if="toolRows.length"
          class="mt-3"
          :columns="TOOL_COLUMNS"
          :rows="toolRows"
          row-key="name"
        />
        <StatusNote v-else kind="error" class="mt-3">
          The server did not list its tools. Reload to ask again.
        </StatusNote>

        <!-- ④ connections -->
        <SectionHead as="h2" label="connected now" rule class="mt-7" />
        <Table
          v-if="connectionRows.length"
          class="mt-3"
          :columns="CONNECTION_COLUMNS"
          :rows="connectionRows"
          row-key="id"
        >
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
        <EmptyState v-else class="mt-3" title="Nothing is connected yet.">
          Add the address above to a client and sign in. Whatever you connect shows up here.
        </EmptyState>


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
        changes, and it can connect again by signing in.
      </p>
    </Dialog>
  </ViewShell>
</template>
