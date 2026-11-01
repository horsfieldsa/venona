<script setup lang="ts">
/**
 * Who you are signed in as, and how to stop being.
 *
 * WHAT IS REAL HERE AND WHAT IS NOT — read this before adding a control.
 *
 * Four groups render: identity, display name, time zone and brief delivery.
 * Three of them have a backend. A fifth — the root credential, with a Rotate
 * button — is specified in the design bundle and deliberately absent; see
 * `api token` below.
 *
 *   identity   `GET /api/auth/session` now serves a display name, a provider and
 *              a "since" date as well as `email` and `via`, and the name is
 *              editable — `PATCH /api/auth/me`, which carries no user id, so its
 *              subject is always the caller.
 *
 *              The callback stamps `oidc_issuer`, `oidc_subject` and
 *              `display_name` via `record_oidc_identity`. `via` is
 *              `session|bearer` and is NOT a provider — `provider` is derived
 *              from `users.oidc_issuer` and is a different fact.
 *   sign out   `POST /api/auth/logout` has existed since auth shipped and had
 *              NO CALLER anywhere in the frontend. It does now. It bumps a
 *              global epoch, so it ends every session everywhere, which is the
 *              honest thing for a single-operator app and is stated on screen.
 *   time zone  `PATCH /api/preferences`, over `user_preferences`. Stored as an
 *              OVERRIDE, not a copy: null means "use whatever this browser
 *              reports", which is right for almost everybody and is the only
 *              answer available before anyone opens this page. The stored value
 *              rides back on `GET /api/auth/session` rather than a GET of its
 *              own, because the brief needs it for first paint and the session
 *              probe is the one request the SPA already makes before mount.
 *              The write resolves its subject through `learning._actor`, so it
 *              works on the auth-off local box where `actor_id` is None and
 *              `PATCH /auth/me` answers 400.
 *   delivery   ONE LINE AND A LINK. The controls live on
 *              `/settings/notifications`, which is where destinations,
 *              subscriptions and what has been sent all are; duplicating any
 *              of it here would be two screens to keep in step. This section
 *              stays because it is where a reader looks for it.
 *   subscript  ONE LINE AND A LINK, the delivery rule applied again and for the
 *              same reason: what somebody pays and when is at least as much a
 *              thing they look for on an Account page as where their brief is
 *              sent. The controls are not here and must not be -- changing a
 *              card, switching plan and cancelling all live on
 *              `/settings/billing`, which links out to the payment provider,
 *              and duplicating any of it would be two screens to keep in step.
 *
 *              Absent entirely on an instance that takes no money, matching
 *              `SettingsRail`: a line about a subscription nobody can have is
 *              noise, and the link would lead to a page with nothing on it.
 *   api token  NOT ON THIS PAGE AT ALL, and that is the decision rather than an
 *              omission. `VENONA_AUTH_API_TOKEN` is env-only and
 *              `get_auth_config` is `lru_cache`d; `api/security.py` opens with a
 *              comment explaining that auth material is deliberately NOT in
 *              `config/settings.yaml` because that file is writable over HTTP.
 *              A Rotate button would have to either serve the current token or
 *              write a new one somewhere writable, and both are the thing that
 *              comment forbids — so the page can neither show the credential nor
 *              change it. What is left to render is an environment variable on
 *              the host, which is the instance's business and not the reader's
 *              (ADR 0134), and it is the ROOT credential and a break-glass path
 *              besides: an assistant gets its own scoped credential by signing
 *              in, and the extension already does. Say nothing here.
 *              `AgentsView.vue` is where a connection is made and ended, and it
 *              carries on screen the argument this page shares: access can be
 *              taken away over HTTP and never handed out, so it has a
 *              Disconnect and no Generate.
 *
 */
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import SettingsRail from '@/components/nav/SettingsRail.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useAdminPagesStore } from '@/stores/adminPages'
import { apiGet, apiPatch, apiPost, errorText } from '@/api/client'
import {
  planAction as billingPlanAction,
  planSentence as billingPlanSentence,
} from '@/lib/billing'
import { useSessionStore } from '@/stores/session'
import type { AuthMode as Mode, SessionInfo as Session } from '@/types/auth'

const session = ref<Session | null>(null)
// `mode` is static per deployment; held in the store so only the first visit
// fetches it — with the session already in its store, a revisit renders this
// page with no spinner and no request at all.
const { authMode: mode } = storeToRefs(useAdminPagesStore())
const loading = ref(false)
const error = ref<string | null>(null)
const signingOut = ref(false)

/** Off this page's own session payload, not the store: it fetches its own so a
 *  display-name save can re-render from the response it just got back. */
const billing = computed(() => session.value?.billing ?? null)
const planSentence = computed(() => billingPlanSentence(billing.value))
const planAction = computed(() => billingPlanAction(billing.value))

/** Two letters, from the display name when there is one and from the address
 *  when there is not. `Ada Reyes` gives `AR`, and so does `a.reyes@example.com`;
 *  a single-part source gives one letter rather than a made-up second one. */
const initials = computed(() => {
  const name = session.value?.display_name?.trim()
  const source = name || (session.value?.email ?? '').split('@')[0]
  if (!source) return '—'
  const parts = source.split(/[\s._-]+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : source.slice(0, 2)).toUpperCase()
})

/** `via` says how THIS request authenticated. `provider` says who vouched for
 *  the identity, which is a different fact and the one a person recognises —
 *  so prefer it, and fall back to describing the transport. */
const how = computed(() => {
  const provider = session.value?.provider
  if (provider) return `${provider} · signed in`
  if (session.value?.via === 'bearer') return 'API token'
  if (mode.value?.google) return 'Google'
  return 'session cookie'
})

/** `users.created_at`, rendered as a date rather than a timestamp: the hour
 *  somebody's account was created is noise, and ISO-8601 in prose is a wall of
 *  digits. Absent for a principal with no row, which is honest — a bearer
 *  session is not a member. */
const since = computed(() => {
  const raw = session.value?.created_at
  if (!raw) return null
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
})

/** The draft, and whether it differs from what the server holds. `null` and `''`
 *  are the same thing to a person and different to `!==`, so both sides are
 *  normalised before comparing — otherwise Save is live on a pristine form for
 *  anyone who has never set a name. */
const draftName = ref('')
const savingName = ref(false)
const nameError = ref<string | null>(null)

const nameChanged = computed(
  () => draftName.value.trim() !== (session.value?.display_name ?? '').trim(),
)

async function saveName() {
  savingName.value = true
  nameError.value = null
  try {
    // The response IS the new session — the server normalises blank to null, so
    // echoing the draft would show a name the database does not hold.
    const saved = await apiPatch<Session>('/auth/me', { display_name: draftName.value })
    session.value = saved
    draftName.value = saved.display_name ?? ''
    // The shell shows the name too, and it was populated by the pre-mount probe
    // that will not run again until a reload.
    useSessionStore().apply(saved)
  } catch (err) {
    nameError.value = errorText(err, 'could not save that name')
  } finally {
    savingName.value = false
  }
}

/**
 * The zone every timestamp on the reading surface is rendered in.
 *
 * Detected by default and stored only as an OVERRIDE: the browser already
 * knows where the reader is, so the common case needs no row, works before
 * anyone opens this page, and follows them when they travel. The select shows
 * the detected zone preselected when nothing is stored, so a reader can see it
 * is already right rather than having to set it to find out.
 */
const detectedZone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})()

/* `Intl.supportedValuesOf` is ES2022 and in every browser this app targets,
 * but the type being present is not the runtime being present. The fallback is
 * the two zones that always make sense: where the reader is, and UTC. */
const zoneOptions = (() => {
  try {
    const all = Intl.supportedValuesOf?.('timeZone')
    if (all?.length) return all.includes(detectedZone) ? all : [detectedZone, ...all]
  } catch {
    /* fall through */
  }
  return [...new Set([detectedZone, 'UTC'])]
})()

const draftZone = ref(detectedZone)
const savingZone = ref(false)
const zoneError = ref<string | null>(null)

/** Compared against the STORED value, not the detected one: a reader whose
 *  browser already reports the zone they want should not see a live Save
 *  button on a form they have not touched. */
const zoneChanged = computed(() => draftZone.value !== (session.value?.timezone ?? detectedZone))

async function saveZone() {
  savingZone.value = true
  zoneError.value = null
  try {
    const saved = await apiPatch<{ timezone: string | null }>('/preferences', {
      timezone: draftZone.value,
    })
    // Write through the session store, the way `saveName` does: it is what
    // holds the copy the rest of the app reads, and it is what pushes the new
    // zone into `lib/dates.ts` so every label re-renders without a reload.
    const store = useSessionStore()
    const next = { ...(session.value as Session), timezone: saved.timezone }
    session.value = next
    store.apply(next)
    draftZone.value = saved.timezone ?? detectedZone
  } catch (err) {
    zoneError.value = errorText(err, 'could not save that time zone')
  } finally {
    savingZone.value = false
  }
}

async function load() {
  const first = mode.value === null
  if (first) loading.value = true
  try {
    // The session is the store's copy: `main.ts` fetched it before mount and
    // `saveName` writes back through `apply`, so re-fetching here re-asked the
    // one question the app answers before it paints anything. A store that
    // somehow has no payload (a bearer session predating the store, a failed
    // probe) falls back to the fetch rather than rendering an empty card.
    const store = useSessionStore()
    const sessionReady: Promise<Session | null> = store.info
      ? Promise.resolve(store.info)
      : apiGet<Session>('/auth/session').then((s) => {
          store.apply(s)
          return s
        })
    const modeReady: Promise<Mode> = mode.value
      ? Promise.resolve(mode.value)
      : apiGet<Mode>('/auth/mode')
    const [s, m] = await Promise.all([sessionReady, modeReady])
    session.value = s
    mode.value = m
    draftName.value = s?.display_name ?? ''
    draftZone.value = s?.timezone ?? detectedZone
  } catch (err) {
    if (first) error.value = errorText(err, 'could not read the session')
  } finally {
    if (first) loading.value = false
  }
}

async function signOut() {
  signingOut.value = true
  try {
    await apiPost('/auth/logout')
  } catch {
    // Either way the intent was to stop being signed in. A failed logout that
    // left the user on this page looking at their own address would be the
    // worst outcome, so the reload happens regardless — an invalid session
    // lands on /login by the normal 401 path.
  } finally {
    // A full reload, not a router push: the session cookie is gone, every store
    // in memory belongs to the session that just ended, and `main.ts` re-runs
    // its pre-mount session probe on a cold load. Through the hub's
    // end-session URL when there is one, so both doors close together.
    window.location.assign(session.value?.logout_url ?? '/login')
  }
}

onMounted(load)
</script>

<template>
  <ViewShell width="wide" identity="settings · account">
    <RailLayout>
      <template #rail>
        <SettingsRail current="account" />
      </template>

      <PaneState :loading="loading" :error="error">
      <div class="flex flex-col">
        <!-- signed in -->
        <SectionHead as="h2" label="signed in" rule />
        <div class="mt-3 flex items-center gap-3">
          <img
            v-if="session?.picture_url"
            :src="session.picture_url"
            alt=""
            class="avatar w-(--control-h) h-(--control-h) rounded-md border border-accent-deep object-cover"
            referrerpolicy="no-referrer"
            aria-hidden="true"
          />
          <span
            v-else
            class="avatar grid place-items-center w-(--control-h) h-(--control-h) rounded-md border border-accent-deep bg-surface-selected font-mono text-2xs text-accent-primary"
            aria-hidden="true"
          >
            {{ initials }}
          </span>
          <div class="min-w-0 grow">
            <p class="font-ui text-sm text-text-primary truncate">
              {{ session?.display_name || session?.email || 'not signed in' }}
            </p>
            <p class="font-ui text-2xs text-text-quiet truncate">
              <template v-if="session?.display_name">{{ session.email }} · </template>{{ how }}
            </p>
          </div>
          <Button variant="outline" icon="log-out" label="Sign out" :disabled="signingOut" @click="signOut">
            {{ signingOut ? 'Signing out…' : 'Sign out' }}
          </Button>
        </div>

        <dl class="facts mt-3 flex flex-wrap gap-5 [&_dt]:text-2xs [&_dt]:text-text-quiet [&_dd]:font-ui [&_dd]:text-sm [&_dd]:text-text-body">
          <div v-if="since">
            <dt>member since</dt>
            <dd>{{ since }}</dd>
          </div>
          <div>
            <dt>role</dt>
            <dd>{{ session?.role ?? 'unknown' }}</dd>
          </div>
          <div v-if="session?.provider">
            <dt>signed in with</dt>
            <dd>{{ session.provider }}</dd>
          </div>
        </dl>
        <p
          v-if="session?.role === 'reader'"
          class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary"
        >
          A reader account. You can read the brief, the graph, the glossary and search.
        </p>

        <p class="mt-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Signing out ends every session, on every device.
        </p>

        <!-- display name -->
        <SectionHead as="h2" label="display name" rule class="mt-7" />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          What this account is called on screen. Set from your Google profile at first sign-in,
          and yours to change. Your role is not.
        </p>
        <div class="mt-3 flex items-center gap-2">
          <Input
            id="display-name"
            v-model="draftName"
            aria-label="Display name"
            placeholder="Not set"
            class="w-(--field-lg)"
          />
          <Button icon="check" label="Save" :disabled="!nameChanged || savingName" @click="saveName">
            {{ savingName ? 'Saving…' : 'Save' }}
          </Button>
        </div>
        <StatusNote v-if="nameError" kind="error" class="mt-3">{{ nameError }}</StatusNote>

        <!-- time zone -->
        <SectionHead as="h2" label="time zone" rule class="mt-7" />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Times on your brief are shown in this zone. Venona reads it from your browser, so it
          is usually right already and follows you when you travel. Set it here if you would
          rather pin it.
        </p>
        <div class="mt-3 flex items-center gap-2">
          <!-- A bare select, and deliberately NOT a `components/ui` primitive:
               `Button.vue` justified one with thirteen copies of which five had
               diverged, and the same argument is why there is no `Textarea`.
               One picker is not that. It carries `Input.vue`'s metrics by hand
               so it cannot drift from the field above it.
               TRIGGER: a second select anywhere promotes this to
               `components/ui/Select.vue`. -->
          <select
            id="time-zone"
            v-model="draftZone"
            aria-label="Time zone"
            class="w-(--field-lg) h-control max-xs:h-(--control-h-touch) px-2 bg-surface-inset border border-border-default rounded-sm font-ui text-sm text-text-primary"
          >
            <option v-for="tz in zoneOptions" :key="tz" :value="tz">{{ tz }}</option>
          </select>
          <Button
            icon="check"
            label="Save"
            :disabled="!zoneChanged || savingZone"
            @click="saveZone"
          >
            {{ savingZone ? 'Saving…' : 'Save' }}
          </Button>
        </div>
        <StatusNote v-if="zoneError" kind="error" class="mt-3">{{ zoneError }}</StatusNote>

        <!-- brief delivery -->
        <SectionHead as="h2" label="brief delivery" rule class="mt-7" />
        <p class="mt-3 text-xs text-text-secondary">
          Venona writes your brief on this domain's schedule and you read it here. You can also
          ask to be told when it lands, by email or in Slack.
          <!-- A LINK THIS TIME, and the difference is who owns the page. The
               note that stood here pointed at `config/settings.yaml`, which is
               admin-only including the read, so the link was an invitation the
               router guard bounced. `/settings/notifications` is the reader's
               own, beside this one on the same rail. -->
          <RouterLink to="/settings/notifications" class="text-accent-primary hover:underline">
            Notifications
          </RouterLink>
          is where.
        </p>

        <!-- subscription. Rendered only where there is one to have; see the
             docblock. -->
        <template v-if="billing?.enforced">
          <SectionHead as="h2" label="subscription" rule class="mt-7" />
          <p class="mt-3 text-xs text-text-secondary">
            {{ planSentence }}
            <RouterLink to="/settings/billing" class="text-accent-primary hover:underline">
              Subscription
            </RouterLink>
            {{ planAction }}
          </p>
        </template>
      </div>
      </PaneState>
    </RailLayout>
  </ViewShell>
</template>
