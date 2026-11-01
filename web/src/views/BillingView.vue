<script setup lang="ts">
/**
 * The subscription: what you are on, what you will be charged, and when.
 *
 * HOLDS NO STRIPE STATE AND MAKES NO STRIPE CALL. The hub owns billing (ADR
 * 0209); this instance has four projected columns and a link out. Every button
 * here is a real navigation to `hub.example.com/billing/...`, which mints a
 * short-lived Stripe session and redirects again -- so nothing on this page can
 * be stale, and no credential ever reaches the browser.
 *
 * THREE SCREENS, and they are not the same screen. `reading` is somebody who is
 * fine. `never` has not subscribed at all and has nothing to freeze, so they
 * are offered the trial. `lapsed` has an archive they paid for and is shown it.
 * Conflating the last two is how a person who was just approved gets met with
 * an empty page.
 *
 * The questions this page answers without the reader leaving are the ones that
 * otherwise become support mail: what plan am I on, what will I be charged, and
 * on what date. Everything else is Stripe's portal, deliberately.
 */
import { computed } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import FactList, { type FactItem } from '@/components/ui/FactList.vue'
import Panel from '@/components/ui/Panel.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import SettingsRail from '@/components/nav/SettingsRail.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import * as dates from '@/lib/dates'
import {
  billingUrl,
  hasSubscription,
  noticeFor,
  planLabel,
  planTone,
  screenFor,
} from '@/lib/billing'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()

const billing = computed(() => session.info?.billing ?? null)
const screen = computed(() => screenFor(billing.value))
const notice = computed(() => noticeFor(billing.value))

/**
 * Where every button goes. The hub, always, because it is the only side that
 * holds a Stripe credential.
 *
 * `linkable` gates the panels rather than this function silently returning:
 * a button that does nothing when clicked is worse than a button that is not
 * there, and an instance with no hub has no subscription to manage at all.
 */
function go(path: 'portal' | 'checkout', params: Record<string, string> = {}): void {
  const to = billingUrl(billing.value?.manage_url, path, params)
  if (to) window.location.href = to
}

const linkable = computed(() => billingUrl(billing.value?.manage_url, 'portal') !== null)

/** When there is no route out. A half-configured instance rather than anything
 *  the reader did, so the sentence says nothing has changed and does not ask
 *  them to fix it. */
const UNREACHABLE =
  'Subscriptions are handled by our payment provider, and we cannot reach it ' +
  'from here right now. Nothing about your account has changed.'

/** Entitled is not the same as paying: an admin has access and no subscription. */
const manageable = computed(() => hasSubscription(billing.value))

const facts = computed<FactItem[]>(() => {
  // The chip above already says the plan, so repeating it here is one fact
  // twice. What is NOT said anywhere else is the amount and the date, which is
  // the question this page exists to answer without anybody leaving it.
  const rows: FactItem[] = []
  const through = billing.value?.entitled_through
  if (through) {
    // One date, two meanings, and saying the wrong one is worse than saying
    // nothing: for somebody reading it is when they will next be charged, and
    // for somebody lapsed it is the last day they were covered.
    rows.push({
      key: 'through',
      label: billing.value.entitled ? 'Next charge' : 'Covered until',
      shape: 'text',
      text: dates.longDate(through),
    })
  }
  return rows
})
</script>

<template>
  <!-- `wide`, like every other settings page, and that is not a preference.
       The rail is INSIDE the shell, so the shell's width decides where the rail
       starts -- and this one page being `prose` moved it a hundred and forty
       pixels right of where it sits on the other four. Switching between
       siblings re-laid out the navigation you switched with. Narrow content
       belongs in a narrow COLUMN, not a narrow shell. -->
  <ViewShell width="wide" identity="settings · subscription">
    <RailLayout>
      <template #rail>
        <SettingsRail current="billing" />
      </template>

      <div class="flex flex-col gap-6">
        <Panel>
          <SectionHead as="h2" label="your subscription" rule />

          <div class="mt-3 flex items-center gap-2">
            <Chip :tone="planTone(billing)">{{ planLabel(billing) }}</Chip>
          </div>

          <FactList v-if="facts.length" class="mt-4" :items="facts" />

          <p v-if="notice" class="mt-4 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            {{ notice.text }}
          </p>
        </Panel>

        <!-- Never started. They have nothing to freeze, so this is an offer
             rather than a paywall, and it says the price and the terms next to
             the button because a card is about to be asked for.

             NOT gated on `linkable`. When there is nowhere to send them the
             offer is still the true screen, and falling through to the panel
             below told a reader who has never subscribed that their access
             "does not come from a subscription" -- which is a sentence about
             an administrator, shown to the person we are asking to pay. -->
        <Panel v-if="screen === 'never'">
          <SectionHead as="h2" label="start reading" rule />
          <p class="mt-3 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            Three days free. We take a card now, and it becomes a subscription on
            day three unless you cancel. You can cancel any time.
          </p>
          <div v-if="linkable" class="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" @click="go('checkout', { plan: 'annual' })">
              Start with a year
            </Button>
            <Button variant="outline" @click="go('checkout', { plan: 'monthly' })">
              Start monthly
            </Button>
          </div>
          <p v-else class="mt-4 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            {{ UNREACHABLE }}
          </p>
        </Panel>

        <!-- Lapsed. The archive is theirs and stays theirs; this offers the
             rest back (ADR 0211). -->
        <Panel v-else-if="screen === 'lapsed'">
          <SectionHead as="h2" label="start again" rule />
          <p class="mt-3 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            The briefs you already had are still here. Subscribing again brings
            back new briefs, the graph, learning and your assistants.
          </p>
          <div v-if="linkable" class="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" @click="go('checkout', { plan: 'annual' })">
              Subscribe yearly
            </Button>
            <Button variant="outline" @click="go('checkout', { plan: 'monthly' })">
              Subscribe monthly
            </Button>
          </div>
          <p v-else class="mt-4 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            {{ UNREACHABLE }}
          </p>
        </Panel>

        <!-- Entitled with nothing to bill: an administrator, or a complimentary
             account. They were being shown Change plan, Cancel and Update card,
             and the hub refuses all three because there is no subscription
             behind them. Say so instead. -->
        <Panel v-else-if="!manageable">
          <SectionHead as="h2" label="nothing to pay" rule />
          <p class="mt-3 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            Your access does not come from a subscription, so there is no card on
            file and nothing to change here.
          </p>
        </Panel>

        <!-- Reading. Three links out, and cancelling is one of them rather than
             something to hunt for: a hidden cancel produces chargebacks, which
             cost more than the churn does. -->
        <Panel v-else-if="linkable">
          <SectionHead as="h2" label="manage" rule />
          <p class="mt-3 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            Payment details, invoices and cancellation are handled by our payment
            provider. These links open their secure page.
          </p>
          <div class="mt-4 flex flex-wrap gap-2">
            <Button variant="outline" @click="go('portal', { flow: 'payment_method_update' })">
              Update your card
            </Button>
            <Button variant="outline" @click="go('portal', { flow: 'subscription_update' })">
              Change plan
            </Button>
            <Button variant="text" @click="go('portal', { flow: 'subscription_cancel' })">
              Cancel
            </Button>
          </div>
        </Panel>

        <!-- Paying, with nowhere to send them. Saying so beats a panel that is
             simply absent, which reads as the subscription having vanished. -->
        <Panel v-else>
          <SectionHead as="h2" label="manage" rule />
          <p class="mt-3 max-w-hint font-prose text-sm text-text-secondary text-pretty">
            {{ UNREACHABLE }}
          </p>
        </Panel>
      </div>
    </RailLayout>
  </ViewShell>
</template>
