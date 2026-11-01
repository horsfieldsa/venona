<script setup lang="ts">
import { useRoute } from 'vue-router'

import AppShell from '@/components/ui/AppShell.vue'

/**
 * A public route renders BARE — outside `AppShell` entirely.
 *
 * `AppShell` is not a passive frame. It mounts the header, the context bar,
 * the footer, the command palette and `useAppPolling`/`useRunStream`
 * — all of which call authenticated endpoints. Rendering `/login` inside it
 * would show a signed-out visitor the whole chrome of an app they cannot use,
 * and start a poll loop that 401s forever into the login rate limiter.
 *
 * `useAppPolling` also guards its own tick on `meta.public`. That is deliberate
 * belt-and-braces: this branch is the one that matters today, and the guard
 * there is what holds if a public route is ever added inside the shell.
 */
const route = useRoute()
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <AppShell v-else />
</template>
