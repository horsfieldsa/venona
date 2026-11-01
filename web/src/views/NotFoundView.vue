<script setup lang="ts">
/**
 * The catch-all: what a typo'd or stale URL lands on.
 *
 * It NAMES the address that failed and offers one way back. Both halves are
 * load-bearing. A silent redirect to the brief answers a question the reader
 * did not ask, and they cannot tell whether they mistyped or the link they
 * followed is wrong; an empty page with the nav still up reads as a broken app.
 *
 * INSIDE `AppShell`, so the header stays and the nav is a way back. That is why
 * this route carries no `meta.public`: public routes render bare, outside the
 * shell, which is right for the login page and wrong for a reader who is
 * already signed in and one character away from where they meant to be.
 *
 * `tone="neutral"`, not `critical`. A wrong address is a fact about a link, not
 * a fault in the app, and a red screen for a typo is a screen that cries wolf.
 */
import { useRoute } from 'vue-router'

import GraphField from '@/components/shared/GraphField.vue'
import Button from '@/components/ui/Button.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import ViewShell from '@/components/ui/ViewShell.vue'

const route = useRoute()
</script>

<template>
  <ViewShell width="full" scroll="none" identity="not found">
    <!-- `relative`, because `GraphField` is absolutely positioned and needs
         this element to be the stacking context it fills. -->
    <div class="relative grid h-full place-items-center p-6">
      <GraphField />
      <StateNotice
        status="404 · no such page"
        heading="There is nothing at this address"
        class="relative w-full max-w-hint"
      >
        <!-- The path, quoted back. A reader who followed a link from somewhere
             else cannot otherwise tell which address failed, and it is the one
             fact that makes the difference between "I mistyped" and "that link
             is wrong". -->
        Nothing answers to <span class="font-mono break-all">{{ route.fullPath }}</span
        >. The link may be wrong, or the page may have moved.
        <template #action>
          <RouterLink to="/" class="inline-flex no-underline hover:no-underline">
            <Button>Back to today's brief</Button>
          </RouterLink>
        </template>
      </StateNotice>
    </div>
  </ViewShell>
</template>
