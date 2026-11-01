<script setup lang="ts">
/**
 * The instance, as its administrator. Counts, the queue, the work, the log.
 *
 * WHAT THIS PAGE IS SCOPED AGAINST is restating things that already have a
 * surface. `/api/health` carries neo4j, the `claude` circuit, run state, source
 * strikes and freshness stamps, and the surface each outage breaks reports it
 * -- the graph explorer for neo4j, `/admin/runs` and every launch control for the
 * model -- so a health card here would be chrome. Curation carries its own
 * row on the rail beside this page; run history is one row further down at
 * `/admin/runs` and is richer there. Every panel below answers a question that
 * today has either a CLI command or no answer at all:
 *
 *   pending requests   `Venona access list`, and a funnel that fills silently
 *   users              "who can reach this instance" — unanswerable in a browser
 *   agent tokens       `GET /api/meta/mcp` is filtered to the CALLING user, so
 *                      an admin cannot see what credentials exist on their own
 *                      instance and a leaked one is invisible
 *   work, 30 days      `repositories/metrics.py` is entirely `*_by_run_id`, so
 *                      "how busy has this month been" has no other answer
 *   authority changes  what makes the HTTP user surface reviewable, and
 *                      therefore what justifies it existing
 *
 * NO ACTIONS HERE, deliberately. Every row links out to `/admin/people`,
 * which is where a decision is made and where the confirmations live. A queue
 * that can be approved from two screens is two places to get the refusals right.
 *
 * `coverage` is 2000 characters of attacker-authored text (`schemas/access.py`),
 * read by an administrator before deciding. It is rendered through `{{ }}`,
 * which Vue escapes, and there is no `v-html` anywhere in `web/src`. Keep it
 * that way.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import AdminRail from '@/components/nav/AdminRail.vue'
import WorkBars from '@/components/admin/WorkBars.vue'
import StatBlock from '@/components/shared/StatBlock.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Panel from '@/components/ui/Panel.vue'
import Table from '@/components/ui/Table.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiGet, errorText } from '@/api/client'
import { agoFrom } from '@/lib/duration'
import { useAdminPagesStore } from '@/stores/adminPages'
import {
  EVENT_COLUMNS,
  REQUEST_COLUMNS,
  REQUEST_STATUS_TONE,
  eventRow,
  requestRow,
} from '@/lib/people'
import type { components } from '@/api/schema'

type AdminOverview = components['schemas']['AdminOverview']
type AccessRequestList = components['schemas']['AccessRequestList']
type AuthorityEventList = components['schemas']['AuthorityEventList']

// The payloads live in the store so a revisit paints them instantly; this
// mount's fetch below is a revalidation, not the render's gate.
const { overview, overviewRequests: requests, overviewEvents: events } = storeToRefs(
  useAdminPagesStore(),
)
const loading = ref(false)
const error = ref<string | null>(null)

/** The last few, not the whole log. The full trail is on `/admin/people`. */
const EVENT_PREVIEW = 8

/* `admin · <section> · <detail>`, the shape every page under /admin and
 * /settings shares. Without the section in the middle, three admin pages all
 * opened with the word `admin` and a number, and none of them said which page
 * you were on -- which is the one job this line has. */
const identity = computed(() =>
  overview.value
    ? `admin · overview · ${overview.value.users_total} ${overview.value.users_total === 1 ? 'user' : 'users'}`
    : 'admin · overview',
)

const pendingRows = computed(() => (requests.value?.items ?? []).map((r) => requestRow(r)))

/** The whole-set total, not `pendingRows.length`: the listing is capped at 200
 *  and the panel's title is a claim about how many people are waiting. */
const pendingCount = computed(() => requests.value?.pending ?? 0)

const eventRows = computed(() =>
  (events.value?.items ?? []).slice(0, EVENT_PREVIEW).map((e) => eventRow(e)),
)

async function load() {
  // The spinner is for the FIRST load only. With held data this fetch is a
  // silent revalidation — flipping `loading` would blank the page (rail
  // included) to re-answer questions already on screen, which read as "the
  // sidebar reloads on every hop".
  const first = overview.value === null
  if (first) loading.value = true
  error.value = null
  try {
    // In parallel: three independent reads, and the page is worth nothing until
    // all three land. `Promise.all` rather than `allSettled` on purpose -- a
    // partial dashboard is a dashboard that quietly understates.
    const [o, r, e] = await Promise.all([
      apiGet<AdminOverview>('/admin/overview'),
      apiGet<AccessRequestList>('/access/requests'),
      apiGet<AuthorityEventList>('/admin/events?limit=20'),
    ])
    overview.value = o
    requests.value = r
    events.value = e
  } catch (err) {
    // A failed revalidate keeps the held page: stale-but-real beats an error
    // state over data that was fine a moment ago.
    if (first) error.value = errorText(err, "could not read this domain's details")
  } finally {
    if (first) loading.value = false
  }
}

/**
 * Which build this is, in one line.
 *
 * `local` rather than a blank when there is no commit: it means nobody
 * deployed this, which on a developer's machine is the answer and on a
 * production instance is a fault worth seeing. The release alone would not
 * distinguish them.
 */
const build = computed(() => {
  const o = overview.value
  if (!o) return ''
  const parts = [`Version ${o.version}`, `build ${o.commit ?? 'local'}`]
  const since = agoFrom(o.started_at, Date.now())
  // `agoFrom` phrases a past instant; this line is about an uptime, so the
  // elapsed span is reused and the 'ago' dropped. Under a minute it reads as a
  // restart, which is what it is.
  if (since) {
    parts.push(since === 'just now' ? 'just restarted' : `running for ${since.replace(' ago', '')}`)
  }
  return parts.join(' · ')
})

onMounted(load)
</script>

<template>
  <ViewShell width="wide" scroll="page" :identity="identity">

    <!-- The SAME layout as every other admin and settings page: an inset rail
         in a `--field-lg` column beside the content, on the page's own padding.
         `pt-4` moved up here off the figures row, so the rail's first item and
         the first stat sit on one line rather than 16px apart. -->
    <RailLayout>
      <template #rail>
        <AdminRail current="overview" />
      </template>

      <!-- The state swap is the PANE's, so the rail above never blanks with
           it: the first load of a session spins here, beside a rail already
           rendered from the store. -->
      <PaneState :loading="loading" :error="error">
      <div v-if="overview" class="admin flex flex-col gap-4">
        <!-- The figures first, as one row. `RunsView` is the precedent; a stat
             per card would be five bordered boxes, which is the register
             docs/DESIGN.md rejects. -->
        <div class="figures flex flex-wrap gap-6">
          <StatBlock
            :value="overview.pending_requests"
            label="pending requests"
            :tone="overview.pending_requests ? 'high' : 'neutral'"
          />
          <StatBlock :value="overview.users_total" label="users" />
          <StatBlock :value="overview.active_admins" label="admins" />
          <StatBlock
            :value="overview.users_suspended"
            label="suspended"
            :tone="overview.users_suspended ? 'critical' : 'neutral'"
          />
          <StatBlock :value="overview.users_never_signed_in" label="never signed in" />
          <StatBlock :value="overview.tokens_live" label="agent connections" />
        </div>

        <!-- Only when somebody IS waiting, and titled with how many.
             The figures row above already reports `pending requests`, so at
             zero this panel was a heading and an empty state restating a number
             standing two lines above them. `RunsView`'s History panel stands
             down on the same rule: nothing to say, and something else on the
             page already saying it.

             The count comes from `pending` rather than the row count -- the
             listing is capped at 200 and a title that quietly says 200 when 340
             are waiting is worse than no title. -->
        <Panel
          v-if="pendingCount"
          variant="bare"
          :title="`${pendingCount} waiting on a decision`"
        >
          <Table
            :columns="REQUEST_COLUMNS"
            :rows="pendingRows"
            row-key="id"
            selectable
            @select="$router.push('/admin/people')"
          >
            <template #cell:role="{ row }">
              <Chip size="sm" variant="bare" :tone="REQUEST_STATUS_TONE[row.status] ?? 'neutral'" dot>
                {{ row.role }}
              </Chip>
            </template>
            <!-- Truncated HERE and nowhere else: `cli.py::_access_list` refuses to
                 truncate the note on the grounds that it is the only thing telling
                 a real analyst from a scraper, and `/admin/people` shows it in
                 full for exactly that reason. This is the glance, not the read. -->
            <template #cell:coverage="{ row }">
              <span class="line-clamp-2 text-text-tertiary">{{ row.coverage || '—' }}</span>
            </template>
          </Table>
        </Panel>

        <Panel variant="bare" :title="`Model work, last ${overview.spend_days} days`">
          <WorkBars :days="overview.spend ?? []" :span="overview.spend_days ?? 30" />
          <!-- Uncaptioned on purpose: a count of calls raises no question about
               what it is costing, and the limit that does bind is on the Runs
               page rather than here. -->
        </Panel>

        <Panel variant="bare" title="Recent account changes">
          <Table :columns="EVENT_COLUMNS" :rows="eventRows" row-key="id">
            <template #cell:action="{ row }">
              <span class="text-text-primary">{{ row.action }}</span>
              <span v-if="row.detail" class="text-text-tertiary"> · {{ row.detail }}</span>
            </template>
            <template #empty>
              <EmptyState title="No account changes yet">
                Role changes, suspensions and reinstatements are recorded here. Changes made from
                a terminal on the server show as <span class="font-mono">cli</span>.
              </EmptyState>
            </template>
          </Table>
        </Panel>

        <!-- Last, and the only string on a page of figures. It is here because
             it has no other surface: the hub has always been able to read this
             instance's build, and the person running the instance could not. -->
        <Panel variant="bare" title="This Venona">
          <p class="text-text-tertiary">{{ build }}</p>
        </Panel>
      </div>
    </PaneState>
    </RailLayout>
  </ViewShell>
</template>
