<script setup lang="ts">
/**
 * The tailoring form: what matters to this reader, and the names they follow.
 *
 * ONE component for both the welcome page and /settings/relevance, so the
 * two cannot drift into different vocabularies. The axis labels and
 * descriptions come from the instance's rubric over the wire -- nothing
 * domain-specific lives here.
 *
 * Two different save models, on purpose. Emphasis applies on SAVE: it is one
 * document the reader composes and commits, and half-saved emphasis would
 * re-rank their reading mid-thought. Follows apply on TOUCH: each chip is
 * its own resource, and the same toggle lives on entity cards where there is
 * no form around it.
 */
import { computed, onMounted, ref, watch } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import FilterField from '@/components/ui/FilterField.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { axisLabel } from '@/lib/relevanceCopy'
import { useRelevanceStore } from '@/stores/relevance'
import { useSessionStore } from '@/stores/session'

const props = withDefaults(
  defineProps<{
    /** The save button states its consequence, and the consequence differs:
     *  the welcome page leads to the brief, the settings page just saves. */
    saveLabel?: string
  }>(),
  { saveLabel: 'Save' },
)

const emit = defineEmits<{ (e: 'saved'): void }>()

const relevance = useRelevanceStore()
const session = useSessionStore()
const draft = ref<Record<string, string>>({})

const LEVELS = [
  { value: 'less', label: 'less' },
  { value: 'normal', label: 'normal' },
  { value: 'more', label: 'more' },
] as const

/** Re-seed the draft whenever the stored answer arrives or changes -- the
 *  settings page can be revisited with the store already warm. */
function seed() {
  const stored = relevance.settings?.emphasis ?? {}
  const next: Record<string, string> = {}
  for (const axis of relevance.rubric?.axes ?? []) {
    next[axis.id] = stored[axis.id] ?? 'normal'
  }
  draft.value = next
}
watch(() => [relevance.settings, relevance.rubric], seed, { deep: true })
onMounted(() => {
  void relevance.loadTailor().then(seed)
  // Only to know whether a brief is being written, so the warning above can
  // appear. Both screens that render this component reach them without going
  // through the reading surface, so neither had ever loaded it.
  void relevance.loadForYou()
})

const changed = computed(() => {
  const stored = relevance.settings?.emphasis ?? {}
  return Object.entries(draft.value).some(
    ([axis, level]) => (stored[axis] ?? 'normal') !== level,
  )
})

async function save() {
  // Normals are dropped so an untouched axis stores nothing -- and a first
  // save of all-normal still creates the row, which is what marks the
  // reader as having answered.
  const emphasis: Record<string, string> = {}
  for (const [axis, level] of Object.entries(draft.value)) {
    if (level !== 'normal') emphasis[axis] = level
  }
  if (await relevance.saveEmphasis(emphasis)) emit('saved')
}

function toggleFollow(id: string) {
  if (relevance.isFollowed(id)) void relevance.unfollow(id)
  else void relevance.follow(id)
}

/** Search over everything Venona has read about, debounced so the corpus is
 *  asked once per pause rather than once per keystroke. */
const query = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(query, (q) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void relevance.searchCorpus(q), 250)
})

/** Follows the picker cannot re-offer (their entity left the suggestion
 *  window) still render, so unfollowing is always possible here. */
const followedElsewhere = computed(() =>
  relevance.follows.filter(
    (f) => !relevance.suggestions.some((s) => s.id === f.entity_id || s.id === f.resolved_id),
  ),
)

/** Chips grouped under their kind, so a person and a program are never one
 *  undifferentiated pill row -- a newcomer should not have to already know
 *  who a name is to decide whether to follow it. Type names come from the
 *  ontology and pass through untouched (domain-blind). */
const suggestionGroups = computed(() => {
  const groups = new Map<string, typeof relevance.suggestions>()
  for (const s of relevance.suggestions) {
    const list = groups.get(s.type) ?? []
    list.push(s)
    groups.set(s.type, list)
  }
  return [...groups.entries()]
})
</script>

<template>
  <div class="tailor flex flex-col min-w-0">
    <div v-if="relevance.loading" class="flex items-center gap-2 py-6 text-text-tertiary text-sm">
      <Spinner :size="14" />
      loading…
    </div>

    <template v-else>
      <StatusNote v-if="relevance.settings?.drifted" kind="info" class="mb-4">
        Your emphasis was reset because how stories are scored here changed.
        Set it again below.
      </StatusNote>

      <!-- A brief is being written from the emphasis as it stands RIGHT NOW,
           and saving a change here lands a document that is out of date the
           moment it arrives. Said before the reader edits rather than after,
           because afterwards the only honest thing left to report is that they
           spent one of their few daily rewrites on a document nobody wanted.
           This is the likeliest collision on the whole feature: /welcome runs
           this component, and the brief starts the moment they leave it. -->
      <StatusNote v-if="relevance.forYou?.generating" kind="info" class="mb-4">
        Venona is writing your brief right now. Saving changes here means it
        needs writing again.
      </StatusNote>

      <SectionHead as="h2" label="what matters most to you" rule />
      <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        These are the kinds of stories that show up in your reading. Want
        more of one? Say more. Care less? Say less. Stories move up or down
        for you, but nothing ever disappears. Not sure yet? Normal is a
        fine answer, and it gets easier after you have read a brief or two.
      </p>
      <!-- The one person who CAN change what Venona reads and how it judges
           stories gets the path to it from the screen that renders the
           result. UX only; the config routes refuse a reader server-side
           either way. -->
      <p v-if="session.isAdmin()" class="mt-2 max-w-hint font-ui text-2xs text-text-quiet">
        You are an administrator here: what Venona reads, and the story kinds
        above, are edited under
        <RouterLink to="/admin/config/relevance_profile" class="text-text-secondary">Admin, Config</RouterLink>.
      </p>
      <div class="mt-4 flex flex-col gap-4">
        <div v-for="axis in relevance.rubric?.axes ?? []" :key="axis.id" class="min-w-0">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <span class="font-ui text-sm text-text-primary">{{ axis.label ?? axisLabel(axis.id) }}</span>
            <SegmentedControl
              v-model="draft[axis.id]"
              :options="LEVELS"
              :ariaLabel="`emphasis for ${axis.label ?? axisLabel(axis.id)}`"
            />
          </div>
          <p v-if="axis.plain" class="mt-1 max-w-hint font-prose text-2xs leading-snug text-text-quiet">
            {{ axis.plain }}
          </p>
        </div>
      </div>

      <SectionHead as="h2" label="what you want to watch" rule class="mt-7" />
      <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        Pick the programs, companies and people you want to keep an eye on.
        Any story that mentions one goes to the top of your reading. Search
        everything Venona has read about, or start from the offers below;
        you can also follow any name from its card.
      </p>
      <div class="mt-3">
        <FilterField
          v-model="query"
          label="Search for something to watch"
          placeholder="a name to watch…"
          :busy="relevance.searching"
        />
      </div>
      <div v-if="relevance.searchHits.length" class="mt-2 flex items-center flex-wrap gap-1">
        <Chip
          v-for="hit in relevance.searchHits"
          :key="hit.id"
          interactive
          :selected="relevance.isFollowed(hit.id)"
          size="sm"
          @click="toggleFollow(hit.id)"
        >
          {{ hit.name }}<template v-if="hit.type"> · {{ hit.type }}</template>
        </Chip>
      </div>
      <p
        v-else-if="query.trim() && !relevance.searching"
        class="mt-2 font-ui text-2xs text-text-quiet"
      >
        Nothing read about yet under that name.
      </p>
      <template v-for="[kind, items] in suggestionGroups" :key="kind">
        <p class="mt-3 font-ui text-2xs text-text-quiet">{{ kind }}</p>
        <div class="mt-1 flex items-center flex-wrap gap-1">
          <Chip
            v-for="s in items"
            :key="s.id"
            interactive
            :selected="relevance.isFollowed(s.id)"
            size="sm"
            @click="toggleFollow(s.id)"
          >
            {{ s.name }}
          </Chip>
        </div>
      </template>
      <template v-if="followedElsewhere.length">
        <p class="mt-3 font-ui text-2xs text-text-quiet">also following</p>
        <div class="mt-1 flex items-center flex-wrap gap-1">
          <Chip
            v-for="f in followedElsewhere"
            :key="f.entity_id"
            interactive
            selected
            size="sm"
            @click="toggleFollow(f.entity_id)"
          >
            {{ f.name ?? f.entity_id }}
          </Chip>
        </div>
      </template>

      <StatusNote v-if="relevance.error" kind="error" class="mt-4">
        {{ relevance.error }}
      </StatusNote>

      <div class="mt-5 flex items-center gap-2">
        <Button icon="check" label="Save" :disabled="relevance.saving || (!changed && !!relevance.settings?.tailored)" @click="save">
          {{ relevance.saving ? 'Saving…' : props.saveLabel }}
        </Button>
        <slot name="actions" />
      </div>
    </template>
  </div>
</template>
