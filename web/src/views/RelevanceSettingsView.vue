<script setup lang="ts">
/**
 * /settings/relevance: edit what the welcome page first asked.
 *
 * Reader-owned (ADR 0134's split by owner): this page tunes THIS reader's
 * ranking and follows, never the instance's rubric -- that stays under
 * /admin/config with the rest of the operator's documents.
 */
import { ref } from 'vue'

import SettingsRail from '@/components/nav/SettingsRail.vue'
import RelevanceTailor from '@/components/relevance/RelevanceTailor.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useRelevanceStore } from '@/stores/relevance'

const relevance = useRelevanceStore()

/**
 * Reset throws away tuning the reader did by hand and cannot be undone from
 * this page, so it asks first.
 *
 * The copy is checked against what the server actually does: `reset` deletes
 * the emphasis row and nothing else, so the names being followed live in a
 * different table and genuinely survive. Saying "everything" would be easier
 * and wrong.
 */
const confirmingReset = ref(false)

async function confirmReset() {
  confirmingReset.value = false
  await relevance.reset()
}
</script>

<template>
  <ViewShell width="wide" identity="settings · relevance">
    <RailLayout>
      <template #rail>
        <SettingsRail current="relevance" />
      </template>

      <div class="flex flex-col min-w-0">
        <RelevanceTailor>
          <template #actions>
            <Button
              v-if="relevance.settings?.tailored"
              variant="text"
              icon="undo"
              label="Reset to defaults"
              :disabled="relevance.saving"
              @click="confirmingReset = true"
            >
              Reset to this domain's defaults
            </Button>
          </template>
        </RelevanceTailor>
      </div>
    </RailLayout>

    <Dialog
      v-model:open="confirmingReset"
      title="Reset how your reading is ranked?"
      confirm-label="Reset it"
      confirm-icon="undo"
      :busy="relevance.saving"
      @confirm="confirmReset"
    >
      Your own tuning goes back to this domain's defaults, and there is no undo
      for it here. The names you follow are kept.
    </Dialog>
  </ViewShell>
</template>
