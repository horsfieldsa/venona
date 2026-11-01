<script setup lang="ts">
/**
 * The one-time invitation: tailor your reading before the first brief.
 *
 * Skippable, and skipping stores nothing server-side -- the dismissal is
 * per-device, and until the reader answers, Venona ranks with the instance
 * defaults exactly as it always has. The same form lives at
 * /settings/relevance for every later edit.
 */
import { useRouter } from 'vue-router'

import RelevanceTailor from '@/components/relevance/RelevanceTailor.vue'
import Button from '@/components/ui/Button.vue'
import ViewShell from '@/components/ui/ViewShell.vue'

const router = useRouter()

function dismiss() {
  try {
    localStorage.setItem('venona.welcome.1', '1')
  } catch {
    /* private mode; the invitation simply offers again next visit */
  }
}

function skip() {
  dismiss()
  void router.push('/')
}

function done() {
  dismiss()
  void router.push('/')
}
</script>

<template>
  <ViewShell width="prose" identity="welcome">
    <div class="flex flex-col pt-4 pb-8">
      <h1 class="m-0 font-ui text-lg font-semibold text-text-primary">
        Make Venona's reading yours
      </h1>
      <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-secondary">
        Venona reads the press every morning and writes you a brief. Tell it
        what you care about, in two parts: how much of each kind of story,
        and the names you want to watch. Skip if you like, everything works
        without it, and you can change your answers in Settings whenever you
        want.
      </p>

      <RelevanceTailor class="mt-6" save-label="Save and start reading" @saved="done">
        <template #actions>
          <Button variant="text" icon="right" @click="skip">Skip for now</Button>
        </template>
      </RelevanceTailor>
    </div>
  </ViewShell>
</template>
