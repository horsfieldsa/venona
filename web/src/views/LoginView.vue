<script setup lang="ts">
/**
 * Sign in.
 *
 * The Google control is a raw `<a>`, not `<Button>`, and that is deliberate:
 * `/api/auth/login` answers with a 302 to accounts.google.com, so it needs a
 * top-level NAVIGATION. A `<button @click>` — the natural reflex, and what the
 * new `<button>`-outside-ui lint would otherwise push you toward — would either
 * need `location.href` anyway or, if wired to `fetch`, silently follow the
 * redirect in the background and land the consent page in a JSON parse error.
 *
 * A `<a>` styled with utilities is not a second implementation of a
 * `components/ui/` primitive: there is no Link primitive to duplicate. If a
 * second anchor-shaped control ever appears, that is the moment to build one.
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Wordmark from '@/components/brand/Wordmark.vue'
import ThemeToggle from '@/components/ui/ThemeToggle.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Panel from '@/components/ui/Panel.vue'
import GraphField from '@/components/shared/GraphField.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { BASE, apiGet, apiPost } from '@/api/client'
import { bouncedRecently, rememberBounce } from '@/lib/signinBounce'
import type { AuthMode } from '@/types/auth'
import googleG from '@/assets/icons/google-g.svg'

const route = useRoute()

const mode = ref<AuthMode | null>(null)
const loading = ref(true)

/** The callback redirects here with `?error=` when a login could not complete —
 *  an unlisted address, an expired state cookie, a misconfigured client. Those
 *  are the messages worth reading, so they are rendered rather than swallowed. */
const failure = computed(() => {
  const raw = route.query.error
  return typeof raw === 'string' && raw ? raw : ''
})

/**
 * The one failure with somewhere to go next.
 *
 * An unlisted Google account rendered as a raw `StatusNote` sentence —
 * "someone@example.com is not permitted to sign in here" — is accurate, is the
 * end of the road, and is the exact moment the visitor should be offered the
 * request-access form instead. Every other `?error=` is
 * the operator's own misconfiguration and stays a sentence.
 *
 * Keyed on `?code=`, which `api/routers/auth.py::NOT_ALLOWED` sets, and not on
 * the prose: matching a sentence works right up until somebody rewords it.
 */
const notAllowed = computed(() => route.query.code === 'not_allowed')

/** The address Google returned, pulled back out of the message so the state can
 *  name it. Falls back to nothing, and the copy reads correctly without it. */
const rejectedEmail = computed(() => failure.value.match(/^(\S+@\S+?)\s/)?.[1] ?? '')

/** Carried through Google and back, so a deep link survives the round trip. */
const next = computed(() => {
  const raw = route.query.next
  return typeof raw === 'string' && raw.startsWith('/') ? raw : '/'
})

/* Not `apiGet`, and deliberately: this is an `<a href>` because the endpoint
 * answers with a 302 to accounts.google.com, and a cross-origin redirect has to
 * be a real top-level navigation rather than a fetch. `BASE` all the same, so
 * the prefix is not a second literal. */
const loginHref = computed(() => `${BASE}/auth/login?next=${encodeURIComponent(next.value)}`)

/* Token sign-in. Rendered only when the server offers it, and the server is
 * strict: once an OIDC login is configured, `/api/auth/mode` reports
 * `token: true` only under VENONA_TOKEN_LOGIN=1 -- the break-glass switch --
 * so in the ordinary case this form does not exist and the page shows
 * exactly one way in. A token-only instance (the pre-hub bootstrap) still
 * gets the form without any flag. */
const token = ref('')
const tokenBusy = ref(false)
const tokenError = ref('')

async function tokenSignIn() {
  if (!token.value.trim() || tokenBusy.value) return
  tokenBusy.value = true
  tokenError.value = ''
  try {
    await apiPost('/auth/token-login', { token: token.value.trim() })
    /* A full navigation, not `router.push`: the session bootstrap in main.ts
     * runs before mount, and this is the same shape as the Google callback's
     * 302 back into the app. */
    location.href = next.value
  } catch (e) {
    tokenError.value = e instanceof Error ? e.message : 'that token is not valid'
    tokenBusy.value = false
  }
}

const redirecting = ref(false)

/* Per tab, which is the scope that matches a visit. The accessor itself throws
 * where site data is blocked, so it is reached for here and the decision is
 * made in `lib/signinBounce.ts`, which says why there is a cooldown at all. */
function bounceStore(): Storage | null {
  try {
    return sessionStorage
  } catch {
    return null
  }
}

onMounted(async () => {
  try {
    mode.value = await apiGet('/auth/mode')
  } catch {
    // The login page must render even when the server cannot say what it
    // offers. The button below is then the only way forward, which is correct:
    // pressing it produces a real error instead of a blank page.
    mode.value = { enabled: true, google: true, token: false, display_name: '', login_blurb: '', access_roles: [] }
    loading.value = false
    return
  }
  // Hub is the entry point. With an OIDC login configured and no token form
  // to offer, this page holds exactly one button, so press it: a visitor with
  // a hub session lands signed in without seeing a second sign-in page, and
  // one without lands on the hub's. The exceptions all carry query params or
  // a mode flag and still render: a callback error or the not-allowed state
  // (auto-bouncing those would loop), the break-glass token form, and a tab
  // that bounced a moment ago.
  if (
    mode.value?.google &&
    !mode.value.token &&
    !failure.value &&
    !notAllowed.value &&
    !bouncedRecently(bounceStore(), Date.now())
  ) {
    rememberBounce(bounceStore(), Date.now())
    redirecting.value = true
    location.replace(loginHref.value)
    return
  }
  loading.value = false
})
</script>

<template>
  <main class="relative grid min-h-screen place-items-center p-6">
    <!-- Only behind the refusal. The sign-in panel is a door, and a door does
         not need a picture behind it; this screen is a dead end and reads as
         one. `relative` above is what the field fills, and the notice below
         needs `relative` of its own or the positioned field paints over it. -->
    <GraphField v-if="notAllowed" />
    <!-- Not the sign-in panel at all. Offering "Continue with Google" to
         somebody Google has just been consulted about, and refused, is the one
         thing this screen must not do. -->
    <StateNotice
      v-if="notAllowed"
      tone="critical"
      status="no access yet"
      heading="This account is not on the list"
      class="relative w-full max-w-hint"
    >
      {{ rejectedEmail || 'That account' }} signed in with Google but has no account here yet.
      Request access below and somebody will add you.
      <template #action>
        <RouterLink to="/request-access" class="inline-flex no-underline hover:no-underline"><Button icon="send">Request access</Button></RouterLink>
      </template>
    </StateNotice>

    <Panel v-else variant="raised" padded class="w-full max-w-hint">
      <!-- The lockup, not an `<h1>` reading "Venona". This is the one page a
           stranger sees first, arriving from a separate front site: the product
           name at `--text-lg` would be smaller than a brief's section headings
           and two sizes under the hero they had just been looking at. The mark
           plus the display rung is what makes it the same product. -->
      <Wordmark :size="26" />
      <h1
        class="mt-5 font-ui text-3xl font-medium tracking-display leading-tight text-text-primary"
      >
        Sign in
      </h1>
      <p class="mt-3 font-prose text-base leading-normal text-text-secondary">
        {{
          mode?.login_blurb ||
          'Venona reads the trade press every morning and keeps what it learns.'
        }}
      </p>

      <StatusNote v-if="failure" kind="error" class="mt-4">{{ failure }}</StatusNote>

      <p v-if="redirecting" class="mt-6 font-ui text-xs text-text-tertiary">Taking you to sign-in…</p>

      <div v-if="!loading" class="mt-6 flex flex-col gap-3">
        <!-- Google's own button (brand guidelines): their light button on both
             themes, the G at its specified 18px. The three colours resolve
             through the google-button tokens, which are Google's constants the
             way the plate is the kit's. -->
        <a
          v-if="mode?.google"
          :href="loginHref"
          class="inline-flex h-control items-center justify-center gap-3 rounded-sm border border-(--google-button-stroke) bg-(--google-button-surface) px-3 py-1 font-ui font-medium text-(--google-button-ink) no-underline transition-opacity duration-fast ease-standard hover:opacity-90"
        >
          <img :src="googleG" alt="" width="18" height="18" />
          Sign in with Google
        </a>

        <StatusNote v-else kind="info">
          Google sign-in is not configured on this server.
          <template #hint>
            Set VENONA_GOOGLE_CLIENT_ID and VENONA_GOOGLE_CLIENT_SECRET{{
              mode?.token ? ', or sign in with the API token below' : ' or use the API token'
            }}.
          </template>
        </StatusNote>

        <form
          v-if="mode?.token"
          class="flex flex-col gap-2"
          novalidate
          @submit.prevent="tokenSignIn"
        >
          <label
            for="token-login"
            class="font-ui text-2xs uppercase tracking-label text-text-tertiary"
          >
            API token
          </label>
          <Input
            id="token-login"
            v-model="token"
            type="password"
            autocomplete="off"
            required
          />
          <Button icon="log-in" type="submit" :disabled="tokenBusy || !token.trim()">
            Sign in with token
          </Button>
          <StatusNote v-if="tokenError" kind="error">{{ tokenError }}</StatusNote>
        </form>

        <!-- The other half of the funnel, and OUTSIDE the `google` branch on
             purpose: an unlisted visitor arriving from a separate front site needs
             somewhere to go whatever this server has configured. Nesting it
             under the Google control hid it on exactly the deployment where the
             operator is still setting sign-in up. -->
        <p class="font-ui text-2xs text-text-quiet">
          Access is by allow-list.
          <RouterLink to="/request-access" class="text-accent-primary">Request access</RouterLink>
        </p>
      </div>
    </Panel>
    <!-- The theme control, on the two pages that bypass `AppShell` and therefore
         have no status line to put it in. `LoginView`'s own docstring calls this
         "the one page a stranger sees first"; arriving from a light marketing
         site into a hard-pinned dark app was the whole reason this row exists,
         so the pages a signed-out visitor sees are exactly the wrong ones to
         omit it from.

         `fixed`, because the page is a single centred grid cell: adding a second
         row would move the panel off centre to make room for a control nobody is
         looking for. -->
    <div class="fixed bottom-3 right-page">
      <ThemeToggle />
    </div>
  </main>
</template>
