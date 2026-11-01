<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, get, post } from '@/api/client'
import EmptyState from '@/components/ui/EmptyState.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { stamp } from '@/lib/format'
import { offerLabel, offerReason, priceLabel } from '@/lib/offer'

/**
 * What it takes to read, and how new people get in.
 *
 * THREE COLUMNS AND THE THIRD IS THE POINT. A page that shows only what was
 * decided cannot tell a delivered switch from one sitting in a queue behind a
 * domain that has been down since Tuesday. `What it reports` comes from that
 * domain's own status reply, so the two disagreeing is visible rather than
 * assumed away.
 *
 * ONE PAGE, TWO SCOPES, deliberately. Paying to read is per domain; new
 * accounts is one switch for the whole hub; the front page is neither, and has
 * no control at all. Three groups with a sentence each read better together
 * than as three pages, and the day a fourth arrives is the day to split them.
 */
interface Domain {
  id: string
  slug: string
  display_name: string
  billing_enforced: boolean
  reported: boolean | null
  last_seen_at: string | null
  blockers: string[]
  price_monthly_amount: number | null
  price_currency: string | null
}

const COLUMNS: Column[] = [
  { key: 'display_name', label: 'Domain' },
  { key: 'billing_enforced', label: 'Paying to read' },
  // Dropped in order of what a phone can spare. `What a new reader sees`
  // carries no `hideBelow` on purpose: somebody checking why a domain is not
  // selling is often doing it from a phone, and that is the column they came
  // for. The table still scrolls sideways inside its own box at 390px, which is
  // what `Table` is built to do with more columns than a phone can hold.
  { key: 'reported', label: 'What it reports', hideBelow: 'sm' },
  { key: 'offer', label: 'What a new reader sees' },
  { key: 'last_seen_at', label: 'Last check-in', hideBelow: 'md' },
]

const ON_OFF = [
  { value: 'on', label: 'On' },
  { value: 'off', label: 'Off' },
] as const

const ACCESS = [
  { value: 'on', label: 'They pick a domain and get it' },
  { value: 'off', label: 'They ask, and I decide' },
] as const

const domains = ref<Domain[]>([])
const selfServe = ref('off')
const takesPayment = ref(true)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await get<{
      domains: Domain[]
      self_serve: boolean
      takes_payment: boolean
    }>('/settings')
    domains.value = body.domains
    selfServe.value = body.self_serve ? 'on' : 'off'
    takesPayment.value = body.takes_payment
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  } finally {
    loading.value = false
  }
}

async function saveBilling(domain: Domain, value: string) {
  try {
    await post('/settings/billing', { instance_id: domain.id, enforced: value === 'on' })
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  }
}

async function saveAccess(value: string) {
  selfServe.value = value
  try {
    await post('/settings/accounts', { self_serve: value === 'on' })
    // RELOAD, because this switch is one of the three things `What a new reader
    // sees` is computed from: without it the column keeps its old answer at the
    // exact moment somebody changed it, which is the confusion this column was
    // added to end.
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'The hub did not answer.'
  }
}

onMounted(load)
</script>

<template>
  <PaneState :loading="loading" :error="error">
    <div class="flex flex-col gap-6">
      <Panel variant="bare" title="Paying to read">
        <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          On, a reader needs a subscription. Somebody whose subscription has ended can
          still sign in and open the briefs they already had, and nothing else. Off,
          everybody who can sign in reads everything, which is the right answer for a
          domain that takes no money.
        </p>

        <Table :columns="COLUMNS" :rows="domains" row-key="id">
          <template #cell:display_name="{ row }">
            {{ row.display_name }}
            <span class="block text-text-tertiary">{{ row.slug }}</span>
          </template>
          <template #cell:billing_enforced="{ row }">
            <SegmentedControl
              :model-value="row.billing_enforced ? 'on' : 'off'"
              :name="`billing-${row.id}`"
              :options="ON_OFF"
              @update:model-value="(v) => saveBilling(row as unknown as Domain, v)"
            />
          </template>
          <template #cell:reported="{ row }">
            <span v-if="row.reported === null" class="text-text-tertiary">not reported yet</span>
            <span v-else-if="row.reported === row.billing_enforced">{{
              row.reported ? 'on' : 'off'
            }}</span>
            <span v-else>
              <strong>{{ row.reported ? 'on' : 'off' }}</strong>
              <span class="block text-text-tertiary">
                saved, but this domain has not confirmed it yet. It may be offline, or it
                may be set to ignore this switch.
              </span>
            </span>
          </template>
          <template #cell:offer="{ row }">
            {{ offerLabel(row.blockers as string[]) }}
            <span
              v-if="priceLabel(row.price_monthly_amount as number | null, row.price_currency as string | null)"
              class="block text-text-tertiary"
            >
              {{
                priceLabel(
                  row.price_monthly_amount as number | null,
                  row.price_currency as string | null,
                )
              }}
              a month
            </span>
            <!-- Bounded, because this is the one cell holding a sentence and
                 the table scrolls sideways rather than wrapping: unbounded, one
                 domain with three reasons sets the width of every column after
                 it. -->
            <span
              v-if="offerReason(row.blockers as string[])"
              class="block max-w-hint font-prose leading-normal text-text-tertiary"
            >
              {{ offerReason(row.blockers as string[]) }}
            </span>
          </template>
          <template #cell:last_seen_at="{ row }">
            <span class="age mono font-semibold text-xs whitespace-nowrap">{{
              stamp(row.last_seen_at as string)
            }}</span>
          </template>
        </Table>

        <EmptyState v-if="!domains.length" title="No domains registered." />
      </Panel>

      <Panel variant="bare" title="New accounts">
        <p class="mb-4 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          What happens when somebody signs in and has no domain yet.
        </p>
        <!-- `stack`, because these two options are sentences: side by side they
             are wider than a phone, and every segment is nowrap. -->
        <SegmentedControl
          :model-value="selfServe"
          name="self-serve"
          :options="ACCESS"
          stack
          @update:model-value="saveAccess"
        />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Letting people in on their own is safe only while paying to read is on above:
          the card is then what stands between a stranger and the cost of writing them a
          brief.
        </p>
        <!-- Stated here rather than only per row, because with no domains
             registered there is no row to carry it, and this is the switch it
             makes no difference to throw. -->
        <p
          v-if="!takesPayment"
          class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-secondary"
        >
          This hub has no payment details, so nobody can subscribe however this is set.
          Whoever set up this hub adds them; until then every domain shows people a
          request form instead of a price.
        </p>
      </Panel>

      <Panel variant="bare" title="The front page">
        <p class="max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Whether the front page shows the holding page or the real one is decided when
          that site is built, so it is not a switch here.
        </p>
      </Panel>
    </div>
  </PaneState>
</template>
