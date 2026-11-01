<script setup lang="ts">
/**
 * "This surface is an entity" — pick which one.
 *
 * The verdict the definitions queue could not express. Accept, reject, edit,
 * look again and choose-a-meaning all assume the answer is a different
 * DEFINITION, and for a surface like `BAE` there is no right definition: it is
 * a company, and the corpus says so in every one of the sixteen times those
 * letters appear.
 *
 * Two things this shows that a plain search box would not:
 *
 *   * **provisional entities**, via `include_provisional`. The entity a curator
 *     needs here is usually provisional — that is *why* the surface never
 *     resolved, since the alias index admits only active entities. `BAE
 *     Systems` is provisional in this corpus while two of its subsidiaries are
 *     active, so an active-only search offers the subsidiaries and hides the
 *     company.
 *   * **the status of each result**, because picking a provisional one promotes
 *     it. That is a real consequence and it is not something to discover
 *     afterwards.
 */
import { ref, watch } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiGet, errorText } from '@/api/client'
import FilterField from '@/components/ui/FilterField.vue'
import type { EntityHit as Candidate } from '@/types/search'

const props = defineProps<{ open: boolean; surface: string; busy?: boolean }>()
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'choose', entity: Candidate): void
}>()

const query = ref('')
const results = ref<Candidate[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function run() {
  const term = query.value.trim()
  if (term.length < 2) {
    results.value = []
    return
  }
  loading.value = true
  error.value = null
  try {
    const data = await apiGet<{ entities: Candidate[] }>(
      `/search?q=${encodeURIComponent(term)}&include_provisional=true&limit=20`,
    )
    // The cursor may have moved on. Same stale-response guard the meanings
    // fetch needs, for the same reason.
    if (query.value.trim() !== term) return
    results.value = data.entities ?? []
  } catch (err) {
    error.value = errorText(err, 'search failed')
    results.value = []
  } finally {
    loading.value = false
  }
}

/** Seed from the surface itself — the entity is nearly always named after it,
 *  so the first useful result is usually already on screen. */
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return
    query.value = props.surface
    void run()
  },
  { immediate: true },
)
</script>

<template>
  <Dialog :open="open" :title="`What does ${surface} name?`" :busy="busy" @update:open="emit('update:open', $event)">
    <p class="m-0 text-text-tertiary">
      The definition will be rejected and <strong class="text-text-primary">{{ surface }}</strong>
      will be underlined as this name instead. Its evidence is kept.
    </p>

    <!-- `width="full"`: a 16rem island inside a dialog reads as a stray control
         rather than the field this step is about. -->
    <div class="mt-3">
      <FilterField
        v-model="query"
        width="full"
        label="Search names to attach"
        placeholder="search names…"
        :busy="loading"
        @update:model-value="run()"
      />
    </div>

    <StatusNote v-if="error" kind="error">{{ error }}</StatusNote>

    <!-- No separate "searching…" row: the spinner rides the field, beside the
         text the request is for. -->
    <p v-if="!loading && query.trim().length >= 2 && !results.length" class="mt-3 mb-0 text-text-tertiary">
      Nothing matches. The name has to exist already; this attaches a term
      to one, it does not create one.
    </p>

    <ul v-else class="mt-3 list-none m-0 p-0 flex flex-col gap-1 max-h-(--scroll-md) overflow-y-auto">
      <li v-for="entity in results" :key="entity.id" class="flex items-baseline gap-2 flex-wrap">
        <Button variant="outline" icon="check" label="Use this" :disabled="busy" @click="emit('choose', entity)">
          use this
        </Button>
        <span class="text-sm text-text-secondary">{{ entity.name }}</span>
        <Chip size="sm">{{ entity.type }}</Chip>
        <!-- Provisional is the common case here and it is consequential:
             choosing one promotes it, because its aliases are otherwise never
             indexed and the attach would appear to do nothing. -->
        <Chip v-if="entity.status === 'provisional'" size="sm" tone="high">
          one source so far; a second confirms it
        </Chip>
      </li>
    </ul>

    <template #actions>
      <Button variant="outline" icon="close" label="Cancel" :disabled="busy" @click="emit('update:open', false)">Cancel</Button>
    </template>
  </Dialog>
</template>
