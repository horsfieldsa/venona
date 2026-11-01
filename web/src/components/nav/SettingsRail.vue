<script setup lang="ts">
/**
 * The settings sidebar: what belongs to the READER. Their account, what they
 * want read to them, and what they have connected.
 *
 * Data, People and the config documents are the INSTANCE's settings, not the
 * reader's, and live on `AdminRail`. Behind a `v-if="session.isAdmin()"` here
 * they would make Settings a page most of whose destinations a reader cannot
 * see, with their own address underneath seventeen documents they may not read.
 *
 * NO FETCH, NO SESSION READ, and that is a consequence rather than tidiness:
 * listing the documents means asking `GET /api/config` on mount, which is admin
 * INCLUDING the reads, so the call needs an `isAdmin()` guard or a 403 fires the
 * global forbidden toast. With nothing to ask for, the guard goes too.
 */
import Rail from '@/components/ui/Rail.vue'
import RailLink from '@/components/ui/RailLink.vue'
import { useSessionStore } from '@/stores/session'

defineProps<{
  current: 'account' | 'relevance' | 'notifications' | 'agents' | 'billing'
}>()

/**
 * Subscription is hidden on an instance that takes no money, which is every
 * local box and every self-hosted one. A rail entry leading to a page that can
 * only say "billing is not configured here" is worse than no entry.
 *
 * This IS a session read, which the docblock above says the rail deliberately
 * avoids -- but the reason it avoids one does not apply: that argument is about
 * FETCHING, and about `isAdmin()` guards around admin reads that would 403.
 * The session store is already loaded before mount and asking it costs nothing.
 */
const session = useSessionStore()
</script>

<template>
  <Rail label="Settings" name="settings">
    <RailLink to="/settings/account" :active="current === 'account'">Account</RailLink>
    <RailLink to="/settings/relevance" :active="current === 'relevance'">Relevance</RailLink>
    <RailLink to="/settings/notifications" :active="current === 'notifications'">
      Notifications
    </RailLink>
    <RailLink to="/settings/agents" :active="current === 'agents'">Agents</RailLink>
    <RailLink
      v-if="session.info?.billing?.enforced"
      to="/settings/billing"
      :active="current === 'billing'"
    >
      Subscription
    </RailLink>
  </Rail>
</template>
