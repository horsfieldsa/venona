<script setup lang="ts">
/**
 * The domain-setup wizard: describe the domain, watch the generation run,
 * review the drafts. A fresh instance is redirected here by the router guard
 * (`setup_required` on /health); a configured one reaches it from Admin in
 * refine mode — same three steps, the first prefilled.
 *
 * The run itself streams through the same store the Runs page uses, so the
 * spine, counters and log all come for free; the checklist beside it is the
 * same stage data read per document.
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

import AdminRail from '@/components/nav/AdminRail.vue'
import LiveRunPanel from '@/components/runs/LiveRunPanel.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Panel from '@/components/ui/Panel.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiGet, apiPost, errorText } from '@/api/client'
import { useConfigDocsStore } from '@/stores/configDocs'
import { useHealthStore } from '@/stores/health'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useSessionStore } from '@/stores/session'
import { useRunTypesStore } from '@/stores/runTypes'
import { useRunsStore } from '@/stores/runs'
import type { components } from '@/api/schema'

type SetupState = components['schemas']['SetupState']

const router = useRouter()
const runs = useRunsStore()
const runTypes = useRunTypesStore()
const health = useHealthStore()

/**
 * The wizard's first act is to START A RUN, so a model service is a
 * PRECONDITION of this page, not a detail of it. Without this the button was
 * live on a fresh instance, the run failed at its first call, and the failure
 * arrived as a dead run rather than as the one sentence that would have
 * explained it.
 *
 * Only `not_connected` blocks. A service that is connected and momentarily
 * unreachable recovers on its own, and refusing to let anyone start a run
 * because of a passing 529 would be a worse gate than none.
 */
const { modelUnconfigured } = useServiceHealth()

const state = ref<SetupState | null>(null)
const loading = ref(true)
const failure = ref('')
const starting = ref(false)

const slug = ref('')
const displayName = ref('')
const tagline = ref('')
const loginBlurb = ref('')
const persona = ref('')
const priorities = ref('')
const seeds = ref('')
const slugTouched = ref(false)

/** describe -> progress -> review, derived rather than stored: a reload lands
 *  on the step the server says we are at. */
const step = computed<'describe' | 'progress' | 'review'>(() => {
  if (running.value) return 'progress'
  if (state.value && !state.value.configured) return 'describe'
  if (describing.value) return 'describe'
  return state.value?.drafts?.length ? 'review' : 'describe'
})
const describing = ref(false)
const running = computed(
  () => runs.attachedKind === 'domain_setup' && runs.attachedRunId !== null && !runs.finished,
)

const setupType = computed(() => runTypes.typeFor('domain_setup'))

function slugFrom(name: string) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}

function onName(value: string | number) {
  displayName.value = String(value)
  if (!slugTouched.value) slug.value = slugFrom(displayName.value)
}

async function load() {
  loading.value = true
  try {
    state.value = await apiGet<SetupState>('/setup')
    if (state.value.configured) {
      displayName.value = state.value.display_name
      slug.value = state.value.slug
      slugTouched.value = true
    }
    if (state.value.active_run) {
      runs.attach(state.value.active_run, 0, 'domain_setup')
    }
  } catch (err) {
    failure.value = errorText(err, 'could not load setup state')
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  void load()
  // For the checklist's document titles; a wizard that cannot list them still
  // works, so the failure is swallowed and the keys fall back below.
  void useConfigDocsStore()
    .load()
    .catch(() => {})
})

/** The editor names documents by title, so the checklist does too. Falls back
 *  to the key with its underscores opened up when the list is not loadable. */
const { items: docMeta } = storeToRefs(useConfigDocsStore())
function titleFor(key: string): string {
  return docMeta.value.find((d) => d.key === key)?.title ?? key.replace(/_/g, ' ')
}

async function generate() {
  starting.value = true
  failure.value = ''
  try {
    if (!state.value?.configured || describing.value) {
      await apiPost('/setup/descriptor', {
        slug: slug.value,
        display_name: displayName.value,
        tagline: tagline.value || `${displayName.value} intelligence.`,
        login_blurb: loginBlurb.value || `Venona reads the ${displayName.value} trade press.`,
      })
    }
    const started = await apiPost<{ run_id: string }>('/setup/generate', {
      persona: persona.value,
      priorities: priorities.value,
      seeds: seeds.value,
    })
    describing.value = false
    runs.attach(started.run_id, 0, 'domain_setup')
    await health.refresh()
    await load()
  } catch (err) {
    failure.value = errorText(err, 'setup could not start')
  } finally {
    starting.value = false
  }
}

/** The review checklist: every draftable document, with whether its draft
 *  landed. Reviewing happens in the config editor — each row links there. */
const checklist = computed(() => {
  const drafted = new Set(state.value?.drafts ?? [])
  return [
    'domain',
    'sources',
    'ontology',
    'relevance_profile',
    'research_prompt',
    'brief_prompt',
    'scoring_rubric',
    'brief_sections',
    'lexicon',
    'anchors',
  ].map((key) => ({ key, drafted: drafted.has(key) }))
})

function refine() {
  describing.value = true
}

/* One coherent worked example threaded through every field, so an empty form
 * still shows what good looks like. Deliberately a domain no instance here
 * runs: teaching copy, not configuration -- the real domain text this form
 * collects goes to the server and lands in config/, where domain text lives. */
const EXAMPLE = {
  name: 'e.g. Offshore Wind',
  slug: 'offshore-wind',
  tagline: 'e.g. Who is building what, off which coast.',
  blurb: 'e.g. Venona reads the offshore wind trade press.',
  persona:
    'I analyse the offshore wind market for a turbine maker. I track auction ' +
    'results, projects reaching final investment decision, port and vessel ' +
    'capacity, and what the big developers are doing. My competitors are the ' +
    'other tier-one turbine suppliers.',
  priorities:
    'US east-coast auction outcomes\n' +
    'floating-platform pilots reaching FID\n' +
    'turbine reliability problems in the field',
  seeds: 'Publications I already read: Recharge, WindPower Monthly. Players I watch: Vestas, Equinor, RWE.',
}

/**
 * `from=setup` so the editor's back-link returns here.
 *
 * Reviewing a generated configuration is ten documents one after another, and
 * the checklist saying which are left is on this page. An editor that always
 * pointed back at the configuration index would drop the operator out of the
 * wizard on the first one they opened.
 */
function openDoc(key: string) {
  void router.push({ path: `/admin/config/${key}`, query: { from: 'setup' } })
}

/**
 * Two audiences, two frames. On a CONFIGURED instance this page is one more
 * admin destination — the rail lists it beside Overview, People and Data, so
 * arriving here without it read as the app losing its chrome. On a fresh
 * instance the wizard IS the app: the router gate bounces every other route
 * back here, so a rail would be a column of doors that don't open.
 *
 * From the session payload rather than `state.configured` because it is known
 * BEFORE this view's own fetch resolves — deciding off `state` would mount
 * the bare wizard and snap the rail in when the response lands.
 */
const chromed = computed(() => !useSessionStore().setupRequired)
</script>

<template>
  <!-- `:loading` is the first-run wizard's only. With the rail on, the
       spinner lives in the content column below instead — ViewShell's swap
       replaces the whole slot, which is exactly the rail-vanishes-per-visit
       artifact the admin pages just shed. -->
  <ViewShell
    :width="chromed ? 'wide' : 'prose'"
    :loading="chromed ? false : loading"
    :error="null"
    identity="setup"
  >
    <!-- NOT `RailLayout`, and this is the one page that cannot be.
         The wizard runs twice over: once as the first-boot page a fresh
         instance redirects its admins to, where there is no rail because there
         is nothing yet to navigate to, and once as an admin page among the
         others. `RailLayout` is a two-column grid whose second track holds the
         content, so rendering it without a rail would put the wizard in the
         16rem column; and swapping the component out would mean writing the
         wizard's body twice. The grid is therefore a class that is present or
         absent, which is exactly what `chromed` says. -->
    <div :class="chromed ? 'grid grid-cols-1 md:grid-cols-[var(--field-lg)_minmax(0,1fr)] gap-6 pt-4' : ''">
      <AdminRail v-if="chromed" current="setup" />

      <!-- The wizard column keeps its prose measure inside the wide admin
           frame: these are forms to read and fill, not a dashboard. -->
      <div :class="chromed ? 'flex min-w-0 max-w-measure flex-col gap-6 pb-8' : 'flex flex-col gap-6 pt-4 pb-8'">
      <SectionHead as="h2" label="domain setup" rule />

      <div v-if="chromed && loading" class="flex items-center gap-2">
        <Spinner /><span class="text-text-secondary">loading</span>
      </div>
      <template v-else>
      <StatusNote v-if="failure" kind="error">{{ failure }}</StatusNote>

      <template v-if="step === 'describe'">
        <StateNotice
          v-if="modelUnconfigured"
          tone="high"
          status="nothing connected"
          heading="Venona has nothing to think with"
          class="mb-4"
        >
          Researching a domain is a long piece of reading and writing, and every part of it
          is done by a model. Connect one and this page picks up where it left off.
          <template #action>
            <Button icon="right" @click="router.push({ name: 'admin-model' })">
              Choose a model
            </Button>
          </template>
        </StateNotice>

        <Panel class="flex flex-col gap-4 p-5">
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">Domain name</span>
            <Input
              :model-value="displayName"
              :placeholder="EXAMPLE.name"
              @update:model-value="onName"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">Short name for URLs, set once</span>
            <Input
              :model-value="slug"
              family="mono"
              :placeholder="EXAMPLE.slug"
              :disabled="state?.configured && !describing"
              @update:model-value="
                (v) => {
                  slug = String(v)
                  slugTouched = true
                }
              "
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">Tagline: one public line, optional</span>
            <Input
              :model-value="tagline"
              :placeholder="EXAMPLE.tagline"
              @update:model-value="(v) => (tagline = String(v))"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">
              The sentence on the sign-in page, optional
            </span>
            <Input
              :model-value="loginBlurb"
              :placeholder="EXAMPLE.blurb"
              @update:model-value="(v) => (loginBlurb = String(v))"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">
              About you, the reader: what you do, what you track, what this should help
              you decide. The more specific this is, the better the drafts.
            </span>
            <textarea
              v-model="persona"
              rows="6"
              :placeholder="EXAMPLE.persona"
              class="w-full p-2 resize-none bg-surface-inset border border-border-default rounded-sm font-ui text-sm leading-normal text-text-body"
             />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">
              What matters to you right now: one per line, optional
            </span>
            <textarea
              v-model="priorities"
              rows="3"
              :placeholder="EXAMPLE.priorities"
              class="w-full p-2 resize-none bg-surface-inset border border-border-default rounded-sm font-ui text-sm leading-normal text-text-body"
             />
          </label>
          <label class="flex flex-col gap-1">
            <span class="font-ui text-xs text-text-tertiary">
              Starting points: publications you already read, players you already watch,
              optional
            </span>
            <textarea
              v-model="seeds"
              rows="3"
              :placeholder="EXAMPLE.seeds"
              class="w-full p-2 resize-none bg-surface-inset border border-border-default rounded-sm font-ui text-sm leading-normal text-text-body"
             />
          </label>

          <div class="mt-2 flex items-center gap-3">
            <Button
              icon="play"
              :disabled="starting || !displayName || !slug || modelUnconfigured"
              @click="generate"
            >
              {{ starting ? 'Starting…' : 'Research this domain' }}
            </Button>
            <span class="font-ui text-xs text-text-quiet">Takes tens of minutes.</span>
          </div>
        </Panel>
      </template>

      <template v-else-if="step === 'progress'">
        <p class="max-w-hint font-prose text-base leading-normal text-text-secondary">
          Researching {{ state?.display_name }}. Each document appears below as it is written,
          and you can start reviewing the early ones while the rest are still being drafted.
        </p>
        <LiveRunPanel
          :title="setupType.label"
          :detail="setupType.detail"
          :summary="[...setupType.summary]"
        />
        <Panel class="p-5">
          <SectionHead as="h2" label="documents" rule />
          <ul class="mt-3 flex flex-col gap-1">
            <li v-for="row in checklist" :key="row.key" class="flex items-center gap-3">
              <span class="font-mono text-xs" :class="row.drafted ? 'text-signal-ok' : 'text-text-quiet'">
                {{ row.drafted ? '✓' : '·' }}
              </span>
              <span class="font-ui text-sm text-text-body">{{ titleFor(row.key) }}</span>
              <Button v-if="row.drafted" variant="text" icon="right" @click="openDoc(row.key)">Review</Button>
            </li>
          </ul>
        </Panel>
      </template>

      <template v-else>
        <p class="max-w-hint font-prose text-base leading-normal text-text-secondary">
          The drafts are ready. Open each one in the editor and press Save to apply it. Save
          Ontology before Anchors, and once both are saved, press Apply on the Anchors document
          to load the anchors into the graph.
        </p>
        <!-- Both numbers are by design, and both read as failures when nothing
             says so: the first live instance's operator saw 51 deferred
             definitions and ~70 conflicts and reasonably asked what broke. -->
        <p class="max-w-hint font-prose text-base leading-normal text-text-secondary">
          Then expect the first research run to look alarming. Venona meets every name and
          acronym at once on its first reading, so some definitions are held over to later
          runs, and dozens of name conflicts land in one day. Both are by design:
          definitions fill in over the following runs, and the conflicts wait for you on
          the Curation page.
        </p>
        <Panel class="p-5">
          <SectionHead as="h2" label="review" rule />
          <ul class="mt-3 flex flex-col gap-1">
            <li v-for="row in checklist" :key="row.key" class="flex items-center gap-3">
              <span class="font-mono text-xs" :class="row.drafted ? 'text-accent-primary' : 'text-text-quiet'">
                {{ row.drafted ? '◆' : '·' }}
              </span>
              <span class="font-ui text-sm text-text-body">{{ titleFor(row.key) }}</span>
              <Button v-if="row.drafted" variant="text" icon="right" @click="openDoc(row.key)">Review</Button>
            </li>
          </ul>
          <div class="mt-4 flex items-center gap-3">
            <Button variant="outline" icon="pencil" @click="refine">Refine the description</Button>
            <span class="font-ui text-xs text-text-quiet">
              Re-running a document reuses the stored research notes.
            </span>
          </div>
        </Panel>
      </template>
      </template>
      </div>
    </div>
  </ViewShell>
</template>
