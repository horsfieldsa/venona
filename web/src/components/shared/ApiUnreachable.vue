<script setup lang="ts">
/**
 * The API is not answering, said once for the whole app.
 *
 * THE HEADING AND THE RETRYING AFFORDANCE ARE THE SAME OUTAGE on every page, so
 * they live here. Three views had written them out longhand and the other
 * eighteen had not: those fell through to `ViewShell`'s raw error state and
 * rendered the status line, which on an HTTP/2 gateway failure is the string
 * `502:` and nothing else. A reader on the archive was shown that, under a
 * header still claiming zero briefs.
 *
 * WHAT IS PAUSED is per page and stays a slot: the graph keeps what is drawn,
 * the runs page cannot start anything, and a settings page simply cannot save.
 * The default is the sentence that is true anywhere.
 *
 * This does not decide WHETHER the API is down -- `useServiceHealth` owns that,
 * because the health poll is the only thing that knows the difference between
 * one failed request and a server that has stopped. A caller renders this when
 * `apiDown` is true.
 */
import Spinner from '@/components/ui/Spinner.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
</script>

<template>
  <StateNotice tone="high" status="offline · reconnecting" heading="The API stopped answering">
    <slot>
      Reading works from what is already loaded. This page fills in when the API comes
      back, and Venona keeps trying on its own.
    </slot>
    <template #action>
      <span class="flex items-center gap-2">
        <Spinner /><span class="font-ui text-2xs text-text-quiet">retrying</span>
      </span>
    </template>
  </StateNotice>
</template>
