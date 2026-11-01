<script setup lang="ts">
/**
 * Every configuration document, grouped by what you would be trying to change.
 *
 * A rail can give a document a row and a truncated title. This page gives it
 * the sentence `config_service.EDITABLE` carries, when it last changed, and
 * whether there is unsaved work in it, which is what makes twenty documents
 * a library rather than a list.
 *
 * THE ROW IS THE LINK, not a row with a button on the end. A document is a
 * destination with exactly one action, so a separate control would be a second
 * tab stop to the same place and twenty more outlined rectangles on one page --
 * which `docs/DESIGN.md` records as the failure that made `/runs` unreadable.
 * As a link the row also clears the 44px tap rung without `Button` having to.
 *
 * The groups, their order and their blurbs come from the server, so a document
 * added to `EDITABLE` appears here with no change to this file.
 */
import { computed, onMounted, ref } from 'vue'

import AdminRail from '@/components/nav/AdminRail.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FilterField from '@/components/ui/FilterField.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useConfigDocs } from '@/composables/useConfigDocs'
import { agoFrom } from '@/lib/duration'
import { useConfigDocsStore } from '@/stores/configDocs'
import type { ConfigDocMeta } from '@/types/config'

const store = useConfigDocsStore()
const { drafts, staleDrafts, loadList } = useConfigDocs()

const loading = ref(true)
const error = ref<string | null>(null)
const query = ref('')

/** Ages are read once per visit. This page is a library, not a monitor: a
 *  ticker would rewrite twenty rows a minute to move "3 days" to "3 days". */
const nowMs = Date.now()

const identity = computed(
  () => `admin · configuration · ${store.items.length} documents`,
)

function matches(doc: ConfigDocMeta, needle: string): boolean {
  if (!needle) return true
  return [doc.title, doc.description, store.groupTitle(doc.group)]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

/** Server order throughout: `GROUPS` declaration order is reading order, and
 *  the documents arrive in the order `EDITABLE` declares them. */
const shown = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return store.groups
    .map((group) => ({
      ...group,
      items: store.items.filter((d) => d.group === group.key && matches(d, needle)),
    }))
    .filter((group) => group.items.length > 0)
})

/** `updated_at` is epoch seconds on the wire; `agoFrom` reads an instant. */
function changed(doc: ConfigDocMeta): string {
  if (typeof doc.updated_at !== 'number') return ''
  return agoFrom(new Date(doc.updated_at * 1000).toISOString(), nowMs)
}

onMounted(async () => {
  try {
    await loadList()
  } catch {
    error.value = 'could not load the configuration documents'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <ViewShell width="wide" scroll="page" :identity="identity">
    <template #search>
      <FilterField v-model="query" label="Filter documents" placeholder="Filter" hotkey hotkey-group="configuration" />
    </template>

    <RailLayout>
      <template #rail>
        <AdminRail current="config" />
      </template>

      <PaneState :loading="loading" :error="error">
        <!-- ONE ELEMENT, not a run of sections. `RailLayout` wraps neither
             slot and `PaneState` renders its content branch as a fragment, so
             without this every group becomes a grid child of the rail's own
             two-column track and the list lays itself out down both columns. -->
        <div class="pane min-w-0">
          <EmptyState
            v-if="!shown.length && !loading"
            :title="query.trim() ? 'Nothing matches that' : 'No documents to show'"
          >
            {{
              query.trim()
                ? 'Clear the filter to see every document.'
                : 'Venona could not list its configuration. The documents are still on disk.'
            }}
          </EmptyState>

          <section v-for="group in shown" :key="group.key" class="group-block mb-6">
            <SectionHead as="h2" :label="group.title" rule />
            <p class="blurb mt-2 mb-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
              {{ group.blurb }}
            </p>

            <ul class="docs m-0 p-0 list-none">
            <li v-for="doc in group.items" :key="doc.key" class="border-b border-border-subtle">
              <RouterLink
                :to="`/admin/config/${doc.key}`"
                :data-doc="doc.key"
                class="row flex flex-col gap-1 py-3 px-2 no-underline hover:no-underline hover:bg-surface-base"
              >
                <span class="head flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span class="name font-ui text-sm text-text-body">{{ doc.title }}</span>
                  <!-- One branch per chip: a utility and its override in two
                       attributes tie on specificity and Tailwind's sort order
                       decides. Words, not coloured dots -- `Chip.vue` records
                       that meaning is never carried by colour alone, and a
                       `title=` tooltip is unreachable on a phone. -->
                  <Chip v-if="staleDrafts.has(doc.key)" tone="critical" size="sm" data-mark="stale">
                    unsaved, older version
                  </Chip>
                  <Chip v-else-if="drafts.has(doc.key)" tone="high" size="sm" data-mark="unsaved">
                    unsaved
                  </Chip>
                  <Chip v-else-if="doc.has_draft" tone="accent" size="sm" data-mark="draft">
                    draft to review
                  </Chip>
                  <Chip v-if="doc.readonly" tone="neutral" size="sm" data-mark="readonly">
                    read only
                  </Chip>
                  <span class="go ml-auto flex-none font-ui text-xs text-text-secondary">
                    {{ doc.readonly ? 'Read' : 'Edit' }} ›
                  </span>
                </span>
                <span
                  class="about flex flex-wrap items-baseline gap-x-3 gap-y-1 font-prose text-sm leading-normal text-text-tertiary"
                >
                  <span class="max-w-hint">{{ doc.description }}</span>
                  <span v-if="changed(doc)" class="when ml-auto flex-none text-xs text-text-quiet">
                    changed {{ changed(doc) }}
                  </span>
                </span>
              </RouterLink>
            </li>
            </ul>
          </section>
        </div>
      </PaneState>
    </RailLayout>
  </ViewShell>
</template>
