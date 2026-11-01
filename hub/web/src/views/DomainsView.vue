<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, get } from '@/api/client'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FactList from '@/components/ui/FactList.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { stamp } from '@/lib/format'

/**
 * Every domain the hub knows, and everything it knows about each.
 *
 * A COMPLETE READOUT, not a triage screen. Every field the poll returns is on
 * the card at the same weight, in a fixed order, and nothing is promoted into a
 * "what is wrong" block -- an operator reading this is looking something up, and
 * a page that decides which four fields matter is a page that hides the fifth.
 * The one thing rendered as a state rather than as a value is `status`, because
 * live and offline are not two readings of a number.
 *
 * TWO SOURCES, SHOWN APART. `What the hub decided` is its own columns; `What
 * this domain says` is that domain's own answer, stored verbatim at the last
 * poll. They are not reconciled: a version, a name or a paywall setting that
 * disagrees across the two is the reason to look at this page at all, and
 * merging them into one figure would be the page deciding which to believe.
 *
 * FIVE MINUTES STALE BY CONSTRUCTION, and it says so. The alternative is
 * fanning out to every instance on load, which takes the slowest one's timeout
 * as the page's own and shows a blank screen exactly when a domain is down.
 */
interface Setup {
  configured: boolean | null
  has_dossier: boolean | null
  drafts: string[]
  active_run: string | null
}

interface Domain {
  id: string
  slug: string
  display_name: string
  tagline: string | null
  public_url: string
  status: string
  last_seen_at: string | null
  poll_misses: number
  registered_at: string | null
  settings_version: number
  settings_pushed_version: number
  version: string | null
  commit: string | null
  started_at: string | null
  reported_slug: string | null
  reported_name: string | null
  setup: Setup | null
  documents: number | null
  entities: number | null
  relationships: number | null
  admins: number | null
  readers: number | null
  billing_enforced: boolean | null
  latest_brief_at: string | null
  login_blurb: string | null
  unreadable: boolean
}

const domains = ref<Domain[]>([])
const polledAt = ref<string | null>(null)
const loading = ref(true)
const error = ref('')

/** A static map, never an interpolated class. */
const STATUS_TONE: Record<string, 'ok' | 'critical' | 'high' | 'neutral'> = {
  live: 'ok',
  offline: 'critical',
  provisioning: 'high',
  retired: 'neutral',
}

/** Nothing reported reads as a dash, and a dash is not a zero. An instance a
 *  version behind reports a shorter body, so an absent field means "this domain
 *  did not say", which is different from "it said none".
 *
 *  An EMPTY STRING takes the dash too. `login_blurb` defaults to `''` in the
 *  schema that produces it, so a domain that has not redeployed and one with
 *  nothing to say are already indistinguishable here -- and rendering the
 *  difference as a blank cell next to a label reads as a broken row rather than
 *  as an absent value. */
const said = (value: unknown): string =>
  value === null || value === undefined || value === '' ? '—' : String(value)

const yesNo = (value: boolean | null | undefined): string =>
  value === null || value === undefined ? '—' : value ? 'yes' : 'no'

const count = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : value.toLocaleString()

function hubFacts(d: Domain) {
  return [
    { label: 'Short name', value: d.slug },
    { label: 'Address', value: d.public_url },
    { label: 'Tagline', value: said(d.tagline) },
    { label: 'Last seen', value: stamp(d.last_seen_at) },
    { label: 'Missed check-ins', value: String(d.poll_misses) },
    { label: 'Registered', value: stamp(d.registered_at) },
    { label: 'Settings version', value: String(d.settings_version) },
    { label: 'Delivered version', value: String(d.settings_pushed_version) },
  ]
}

function saidFacts(d: Domain) {
  return [
    { label: 'Version', value: said(d.version) },
    // The release is the same number on every domain until someone bumps it,
    // so this is the field a disagreement actually shows up in.
    { label: 'Build', value: said(d.commit) },
    { label: 'Started', value: stamp(d.started_at) },
    { label: 'Calls itself', value: said(d.reported_name) },
    { label: 'Short name', value: said(d.reported_slug) },
    { label: 'Set up', value: yesNo(d.setup?.configured) },
    { label: 'Has a dossier', value: yesNo(d.setup?.has_dossier) },
    { label: 'Unsaved drafts', value: d.setup ? String(d.setup.drafts.length) : '—' },
    { label: 'Run in flight', value: d.setup?.active_run ? 'yes' : d.setup ? 'no' : '—' },
    { label: 'Documents', value: count(d.documents) },
    { label: 'Names', value: count(d.entities) },
    { label: 'Relationships', value: count(d.relationships) },
    { label: 'Admins', value: count(d.admins) },
    { label: 'Readers', value: count(d.readers) },
    { label: 'Paying to read', value: yesNo(d.billing_enforced) },
    { label: 'Last brief', value: stamp(d.latest_brief_at) },
    { label: 'Describes itself as', value: said(d.login_blurb) },
  ]
}

const freshness = computed(() =>
  polledAt.value
    ? `Last answered ${stamp(polledAt.value)}. The hub asks every five minutes, so everything here is up to that old.`
    : 'No domain has answered yet, so there is nothing to report.',
)

onMounted(async () => {
  try {
    const body = await get<{ domains: Domain[]; polled_at: string | null }>('/domains')
    domains.value = body.domains
    polledAt.value = body.polled_at
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <div class="flex flex-col gap-6">
      <p class="max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        {{ freshness }}
      </p>

      <!-- The name goes through the header slot rather than `title`, because
           `SectionHead` uppercases and a domain's display name is DATA, not a
           label: "Acme Watch" is the name its operator chose and "ACME WATCH"
           is not. `Button` carries a `case="literal"` prop for exactly this
           distinction and gives the reason -- an entity's own name is not a
           thing to shout. -->
      <Panel v-for="d in domains" :key="d.id" variant="inset">
        <template #header>
          <h2 class="m-0 font-ui text-md font-medium text-text-primary [overflow-wrap:anywhere]">
            {{ d.display_name }}
          </h2>
          <Chip size="sm" :tone="STATUS_TONE[d.status] ?? 'neutral'" dot>{{ d.status }}</Chip>
        </template>

        <!-- A domain that answered with something this hub could not read is
             not the same as one that has never answered, and the columns below
             would show only the hub's own half without saying which. -->
        <StatusNote v-if="d.unreadable" kind="error">
          This domain answered its last check with something Venona could not read, so
          everything under "What this domain says" is missing rather than empty.
        </StatusNote>

        <div class="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
          <section class="min-w-0">
            <h3
              class="mb-3 font-ui text-2xs font-medium uppercase tracking-label text-text-tertiary"
            >
              What the hub decided
            </h3>
            <FactList :facts="hubFacts(d)" />
          </section>

          <section class="min-w-0">
            <h3
              class="mb-3 font-ui text-2xs font-medium uppercase tracking-label text-text-tertiary"
            >
              What this domain says
            </h3>
            <FactList :facts="saidFacts(d)" />
          </section>
        </div>
      </Panel>

      <EmptyState v-if="!domains.length" title="No domains registered.">
        A domain is registered from the command line, and appears here once it
        answers.
      </EmptyState>
    </div>
  </PaneState>
</template>
