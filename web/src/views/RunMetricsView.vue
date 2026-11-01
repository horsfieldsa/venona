<script setup lang="ts">
/**
 * One run, measured. A SEPARATE route from `/admin/runs/:id` on purpose.
 *
 * The run detail page answers "what happened and did it degrade" — it is the
 * page you open when something looks wrong, and it is the page anyone with
 * access to a brief has a reason to read. This one answers "what did it cost
 * and where did the time go", which is an operator's question: it exposes
 * per-call latency, dollar cost, cache-hit ratios and the per-feed yield table.
 *
 * Keeping them apart means the split is a ROUTE, so gating this behind a role
 * later is one `meta` flag in `router/index.ts` and a guard — not an audit of
 * which panels on a shared page a given viewer may see. `meta.operator` is
 * already set there and is what such a guard would read.
 *
 * Everything here comes from `GET /runs/{id}/metrics`, which aggregates
 * `run_stages`, `run_metrics`, `run_source_stats` and `llm_calls`.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import LatencyBars, { type Series } from '@/components/runs/LatencyBars.vue'
import MetricFunnel from '@/components/runs/MetricFunnel.vue'
import StageTimeline from '@/components/runs/StageTimeline.vue'
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { apiGet, CancelledError, errorText } from '@/api/client'
import { useRequestScope } from '@/composables/useRequestScope'
import { usePoll } from '@/composables/usePoll'
import { humanMs as human } from '@/lib/duration'
import { predicateLabel } from '@/lib/learnCopy'
import type { Run, RunMetrics } from '@/types/run'

const route = useRoute()

const metrics = ref<RunMetrics | null>(null)

/**
 * The run row itself, for two operator details: the profile hash and the launch
 * params.
 *
 * Both earn their place. The profile hash is how you tell
 * "the brief changed because the profile changed" from "the brief changed
 * because the news did", and the params are what the run was actually asked
 * for -- both are things you want when tuning a run and neither is something a
 * reader of a brief has a question about. That is the same line this whole page
 * is drawn on.
 */
const runRow = ref<Run | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const runId = computed(() => String(route.params.id ?? ''))
const { signal } = useRequestScope()

/** The same shape as the run's own page, and for the same reason: the id is
 *  the string a reader copies, and it already begins `run:`. */
const identity = computed(() => `${runId.value} · metrics`)

async function load() {
  nowMs.value = Date.now()
  loading.value = true
  error.value = null
  try {
    metrics.value = await apiGet<RunMetrics>(`/runs/${encodeURIComponent(runId.value)}/metrics`, { signal })
    // Separate failure: the metrics ARE this page, the run row is a footnote on
    // it, so a miss here must not blank what loaded.
    void apiGet<Run>(`/runs/${encodeURIComponent(runId.value)}`, { signal })
      .then((r) => (runRow.value = r))
      .catch(() => (runRow.value = null))
  } catch (err) {
    if (err instanceof CancelledError) return
    // Unlike on the detail page, a failure here IS the page failing: there is
    // nothing else on it. A run recorded before this feature still returns 200
    // with empty collections, so an error means the run is missing or the
    // endpoint is down, and both are worth saying out loud.
    error.value = errorText(err, 'something went wrong')
    metrics.value = null
  } finally {
    loading.value = false
  }
}

/**
 * A LIVE run's numbers fill in as its stages land, so this refreshes while one
 * is going and stops the moment it is not. Polling rather than the SSE stream:
 * the stream carries events, and every panel here is an AGGREGATE over four
 * tables — recomputing them in the client from an event feed would be a second
 * implementation of `metrics_service` that could disagree with the first.
 *
 * Six seconds because that is roughly the cadence at which a funnel visibly
 * moves; `fetch` emits progress every ten documents and `analyze` once per
 * cluster, at ~45s each.
 */
const LIVE = new Set(['running', 'queued'])
/** Ticked on every refresh so a live run's elapsed time is elapsed time and not
 *  a frozen number. Only read while the run is live; a finished run's duration
 *  comes from the server, which measured it. */
const nowMs = ref(0)

// `loading` stays false on the refresh: flipping the shell back to its spinner
// every six seconds would blank the page the reader is reading.
const poll = usePoll(() => refresh(), { interval: 6000 })

watch(
  () => metrics.value?.status,
  (status) => (status && LIVE.has(status) ? poll.start() : poll.stop()),
)

async function refresh() {
  nowMs.value = Date.now()
  try {
    metrics.value = await apiGet<RunMetrics>(`/runs/${encodeURIComponent(runId.value)}/metrics`, { signal })
  } catch {
    // A blip mid-run must not replace a page of real numbers with an error.
  }
}

onMounted(load)
watch(runId, load)

const isLive = computed(() => !!metrics.value && LIVE.has(metrics.value.status))

/**
 * Wall clock, INCLUDING a run that has not finished.
 *
 * `duration_ms` is `finished_at - started_at` and is 0 until a run ends, so the
 * page told someone watching a ten-minute run that it had taken 0ms. A live run
 * is measured against the clock instead — the one number here the server cannot
 * supply, because the run it describes has not happened yet.
 */
const wallMs = computed(() => {
  const m = metrics.value
  if (!m) return 0
  if (m.duration_ms) return m.duration_ms
  if (!isLive.value || !m.started_at) return 0
  return Math.max(0, nowMs.value - new Date(m.started_at).getTime())
})

/** Stages that recorded a funnel. A stage with only a duration has nothing to
 *  draw and a card reading "0 → 0" is worse than no card. */
const funnels = computed(() =>
  (metrics.value?.stages ?? []).filter((s) => (s.metrics?.offered ?? 0) > 0),
)

const latency = computed<Series[]>(() =>
  (metrics.value?.tasks ?? [])
    .filter((t) => t.max_ms)
    .map((t) => ({
      label: t.task,
      p50_ms: t.p50_ms ?? 0,
      p95_ms: t.p95_ms ?? 0,
      max_ms: t.max_ms ?? 0,
      calls: t.calls ?? 0,
    })),
)

const SOURCE_COLUMNS: Column[] = [
  { key: 'source_id', label: 'source' },
  { key: 'offered', label: 'offered', numeric: true },
  { key: 'kept', label: 'new', numeric: true },
  { key: 'duplicate', label: 'dupe', numeric: true },
  { key: 'stale', label: 'stale', numeric: true },
  { key: 'undated', label: 'undated', numeric: true },
  { key: 'age', label: 'median age', numeric: true },
]

/** Ordered worst-first by the server. The point of the table is to be read
 *  top-down when deciding which feed to drop, so the order is not re-sorted
 *  here. */
const sourceRows = computed(() =>
  (metrics.value?.sources ?? []).map((s) => ({
    ...s,
    age:
      s.age_p50_h == null
        ? '—'
        : s.age_p50_h < 48
          ? `${Math.round(s.age_p50_h)}h`
          : `${Math.round(s.age_p50_h / 24)}d`,
  })),
)

/** The waste number, stated in words. `stale` here means "outside the brief's
 *  own `lookback_hours`", so these are documents that were fetched, clustered
 *  and analysed and that the retrieval filter could never have selected. */
const wasteNote = computed(() => {
  const f = metrics.value?.freshness
  if (!f || !(f.fresh + f.stale)) return ''
  return (
    `${f.stale} of ${f.fresh + f.stale} dated documents (${Math.round((f.stale_ratio ?? 0) * 100)}%) ` +
    `were older than the brief's ${f.lookback_hours} hour window when they arrived. ` +
    (f.undated ? `${f.undated} more carried no date and bypass the window entirely.` : '')
  )
})

/** Nothing measurable at all — a run that predates this feature, or a lookup
 *  short enough to have no stages worth timing. Distinguished from an error,
 *  because the run itself is fine.
 *
 *  NEVER for a live run. A run thirty seconds in has no funnel, no completed
 *  call and no `finished_at`, so every test below is true of it — and telling
 *  someone watching their pipeline start that nothing was measured is the exact
 *  opposite of what is happening. */
const nothingRecorded = computed(
  () =>
    !!metrics.value &&
    !LIVE.has(metrics.value.status) &&
    !funnels.value.length &&
    !latency.value.length &&
    !sourceRows.value.length &&
    !metrics.value.duration_ms,
)
const { apiDown } = useServiceHealth()
</script>

<template>
  <ViewShell
    width="wide"
    scroll="page"
    :loading="loading"
    :error="error"
    :identity="identity"
    :parent="{ label: 'runs', to: '/admin/runs' }"
  >
    <!-- The trailing edge, where the run's own page keeps `Metrics`, so the
         pair sits in the same place whichever half you are on. Going to the
         sibling changes what you are looking at rather than changing anything,
         so it is not a verb and `#actions` keeps its verbs-only rule (ADR
         0181). Both pages hang off `/admin/runs`, so the parent link is the list and
         the pair reach each other from here.

         Nothing in the centre track: how the run went is what the run's own
         page opens with, and this page is here to answer where the time went. -->
    <template #toggles>
      <Button
        variant="outline"
        icon="left"
        label="Run detail"
        @click="$router.push(`/admin/runs/${runId}`)"
      >
        Run detail
      </Button>
    </template>

    <template #error>
      <!-- Offline first, for the same reason as the run page it hangs off. -->
      <ApiUnreachable v-if="apiDown" />
      <EmptyState v-else title="No metrics for that run">
        Venona has no metrics for that run, or they could not be loaded.
        <template #action>
          <Button icon="left" label="Back to runs" @click="$router.push('/admin/runs')">Back to runs</Button>
        </template>
      </EmptyState>
    </template>

    <EmptyState v-if="nothingRecorded" title="Nothing was measured">
      This run predates step timing, or it was short enough to have nothing
      worth timing. Newer runs record their own counts, per-source yield and
      call latency.
      <template #action>
        <Button icon="right" label="Run detail" @click="$router.push(`/admin/runs/${runId}`)">Run detail</Button>
      </template>
    </EmptyState>

    <div v-if="metrics" class="flex flex-col gap-4">
      <!-- A live run that has not recorded anything yet. Inside the panel
           column rather than above it, so the space under it is the SAME
           `gap-4` that separates every other block on this page -- an `mb-*`
           here is a second spacing rule that only agrees with the first by
           coincidence, and it did not. -->
      <StatusNote v-if="isLive && !funnels.length && !sourceRows.length">
        This run is still going. Counts, per-source yield and call latency
        appear as each step lands; this page refreshes on its own.
      </StatusNote>

      <Panel v-if="metrics.duration_ms" variant="bare" title="Where the time went">
        <StageTimeline :stages="metrics.stages ?? []" :queue-ms="metrics.queue_ms" />
      </Panel>

      <Panel v-if="latency.length" variant="bare" title="Model calls">
        <LatencyBars :series="latency" />
        <dl class="facts flex flex-wrap gap-5 mt-4 pt-3 border-t border-t-border-subtle">
          <div v-for="task in metrics.tasks ?? []" :key="task.task">
            <dt class="text-2xs tracking-label uppercase text-text-tertiary">{{ task.task }}</dt>
            <dd class="mono text-sm">
              {{ human(task.total_ms ?? 0) }}
              <span class="text-text-tertiary">
                · ${{ (task.cost_usd ?? 0).toFixed(3) }}
                <!-- The cache ratio is the difference between a 45s call and a
                     several-minute one. It reads as a non-fact at 100% until
                     you see a run where it is not. -->
                · {{ Math.round((task.cache_hit_ratio ?? 0) * 100) }}% cached
              </span>
              <span v-if="task.failed" class="text-signal-critical">
                · {{ task.failed }} failed
              </span>
            </dd>
          </div>
        </dl>
      </Panel>

      <Panel v-if="funnels.length" variant="bare" title="Yield">
        <div class="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(var(--field-lg),1fr))]">
          <MetricFunnel
            v-for="stage in funnels"
            :key="stage.stage"
            :stage="stage.stage"
            :metrics="stage.metrics ?? {}"
            :reconciled="stage.reconciled ?? true"
          />
        </div>
      </Panel>

      <!-- The aggregate the warnings scroll cannot carry: which SHAPES the
           ontology refused and how often. The per-line rejections stay on the
           run detail page; the fix lives in the ontology editor, which shows
           the same shapes re-checked against the current contracts. -->
      <Panel
        v-if="metrics.rejected_shapes?.length"
        variant="bare"
        title="Refused: Venona does not record this kind of relationship"
      >
        <ul class="flex flex-col gap-1">
          <li
            v-for="shape in metrics.rejected_shapes"
            :key="`${shape.predicate}:${shape.subject_type}:${shape.object_type}`"
            class="flex items-baseline justify-between gap-3 text-xs"
          >
            <span class="mono text-text-secondary truncate">
              {{ shape.subject_type || '?' }} {{ predicateLabel(shape.predicate) }} → {{ shape.object_type || '?' }}
            </span>
            <span class="mono text-text-tertiary whitespace-nowrap">
              {{ shape.n }}× · e.g. {{ shape.example_subject }} → {{ shape.example_object }}
            </span>
          </li>
        </ul>
      </Panel>

      <Panel v-if="sourceRows.length" variant="bare" title="Sources">
        <p v-if="wasteNote" class="text-sm text-text-secondary mb-3">{{ wasteNote }}</p>
        <Table :columns="SOURCE_COLUMNS" :rows="sourceRows" row-key="source_id">
          <template #cell:source_id="{ row }">
            <span :class="row.status === 'error' ? 'text-signal-critical' : ''">
              {{ row.source_id }}
            </span>
            <!-- A feed that failed still gets a row. Omitting it makes a broken
                 source look like a quiet one. -->
            <span v-if="row.error" class="block text-2xs text-signal-critical">{{ row.error }}</span>
          </template>
        </Table>
      </Panel>

      <!-- The slowest individual items, which is only answerable because
           `llm_calls.subject` records what each call was about. -->
      <Panel v-if="metrics.slowest?.length" variant="bare" title="Slowest items">
        <ul class="flex flex-col gap-1">
          <li
            v-for="item in metrics.slowest"
            :key="`${item.subject}-${item.task}`"
            class="flex items-baseline justify-between gap-3 text-xs"
          >
            <span class="mono text-text-secondary truncate">{{ item.subject }}</span>
            <span class="mono text-text-tertiary whitespace-nowrap">
              {{ item.task }} · {{ human(item.duration_ms) }} · ${{ item.cost_usd.toFixed(3) }}
            </span>
          </li>
        </ul>
      </Panel>

      <Panel variant="bare" title="Totals">
        <dl class="facts flex flex-wrap gap-5">
          <div class="fact [&_dt]:text-lg [&_dt]:text-text-primary [&_dd]:text-xs [&_dd]:text-text-tertiary">
            <dt class="mono">{{ human(wallMs) }}</dt>
            <dd>{{ isLive ? 'elapsed so far' : 'wall clock' }}</dd>
          </div>
          <div class="fact [&_dt]:text-lg [&_dt]:text-text-primary [&_dd]:text-xs [&_dd]:text-text-tertiary">
            <dt class="mono">{{ human(metrics.queue_ms ?? 0) }}</dt>
            <dd>queued</dd>
          </div>
          <div class="fact [&_dt]:text-lg [&_dt]:text-text-primary [&_dd]:text-xs [&_dd]:text-text-tertiary">
            <dt class="mono">{{ metrics.calls_ledgered }}</dt>
            <dd>calls recorded</dd>
          </div>
          <!-- Reported ALONGSIDE the ledger count, never reconciled with it: a
               failed call does not bump the counter and a denied-permission call
               writes no ledger row at all, so the two legitimately disagree and
               neither is the attempt count. -->
          <div class="fact [&_dt]:text-lg [&_dt]:text-text-primary [&_dd]:text-xs [&_dd]:text-text-tertiary">
            <dt class="mono">{{ metrics.calls_counted }}</dt>
            <dd>calls counted</dd>
          </div>
          <div
            v-if="metrics.cost_per_kept_item"
            class="fact [&_dt]:text-lg [&_dt]:text-text-primary [&_dd]:text-xs [&_dd]:text-text-tertiary"
          >
            <dt class="mono">{{ metrics.cost_per_kept_item }}</dt>
            <dd>calls per kept item</dd>
          </div>
        </dl>

        <div
          v-if="runRow?.profile_hash || runRow?.params"
          class="rows pt-4 mt-4 border-t border-t-border-subtle text-sm text-text-secondary [&_p]:py-(--space-half) [&_.k]:inline-block [&_.k]:w-(--field-md) [&_.k]:text-text-tertiary [&_.k]:text-xs [&_.k]:uppercase [&_.k]:tracking-label"
        >
          <p v-if="runRow.profile_hash" title="identifies the interest settings this run scored against">
            <span class="k">settings fingerprint</span> <span class="mono">{{ runRow.profile_hash }}</span>
          </p>
          <p v-if="runRow.params && Object.keys(runRow.params).length">
            <span class="k">params</span>
            <span class="mono text-xs">{{ JSON.stringify(runRow.params) }}</span>
          </p>
        </div>
      </Panel>
    </div>
  </ViewShell>
</template>
