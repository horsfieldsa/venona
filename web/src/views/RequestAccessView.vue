<script setup lang="ts">
/**
 * A separate front site's only call to action, and the one page here that a
 * stranger reaches before they have an account.
 *
 * PUBLIC, and that shapes everything. The route carries `meta.public`, so the
 * 401 hook in `main.ts` does not bounce it and `useAppPolling` does not tick on
 * it -- polling here would spend a stranger's rate-limit budget on health checks
 * they cannot see. `POST /api/request-access` is the only unauthenticated write
 * in the app; `api/routers/access.py` carries why it can neither read nor grant
 * anything.
 *
 * NO NAVIGATION ON SUCCESS. The form is replaced in place by the acknowledgement
 * it earned. Routing away would lose the one thing the visitor wants to see, and
 * there is nowhere to route them TO -- they still have no account, which is the
 * entire reason they are here.
 */
import { computed, onMounted, ref } from 'vue'

import Wordmark from '@/components/brand/Wordmark.vue'
import ThemeToggle from '@/components/ui/ThemeToggle.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiGet, apiPost } from '@/api/client'
import Input from '@/components/ui/Input.vue'

/* The offered set is the domain descriptor's (`/auth/mode` carries it); this
 * literal is only the domain-neutral fallback for a fetch that fails before
 * the form renders. `schemas/access.py` remains the gate either way. */
const FALLBACK_ROLES = ['analyst', 'government', 'industry', 'press', 'academic', 'other']
const roles = ref<string[]>(FALLBACK_ROLES)
type Role = string

onMounted(async () => {
  try {
    const mode = await apiGet<{ access_roles?: string[] }>('/auth/mode')
    if (mode.access_roles?.length) roles.value = mode.access_roles
  } catch {
    /* the fallback list already renders; the server remains the gate */
  }
})

const email = ref('')
const coverage = ref('')
const role = ref<Role | null>(null)
const submitting = ref(false)
const sent = ref(false)
const failure = ref('')

/** Client-side, deliberately the same shape as the server's. It answers before
 *  a round trip, never in place of one: `schemas/access.py` is the gate, and a
 *  form that only validates in the browser validates nothing. */
const emailLooksRight = computed(() => /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/.test(email.value.trim()))
const canSubmit = computed(() => emailLooksRight.value && role.value !== null && !submitting.value)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  failure.value = ''
  try {
    await apiPost('/request-access', {
      email: email.value.trim(),
      coverage: coverage.value.trim() || null,
      role: role.value,
    })
    sent.value = true
  } catch (error) {
    // The 429 is the one worth spelling out: the rate limit here is three an
    // hour, which a person who mistypes their address twice can genuinely hit,
    // and "something went wrong" would read as the form being broken.
    const message = error instanceof Error ? error.message : ''
    failure.value = /rate|429|too many/i.test(message)
      ? 'That is a few too many requests from here. Try again in an hour.'
      : 'That did not send. Check your connection and try again.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="grid min-h-screen place-items-center p-6">
    <!-- Two equal columns, divided rather than boxed: the left states what
         Venona is and the right asks for something, and a border between them
         says those are two halves of one page. On a narrow window they stack
         and the divider becomes the top edge of the form. -->
    <div
      class="request w-full max-w-shell grid gap-0 md:grid-cols-2 border border-border-subtle rounded-lg bg-surface-raised overflow-hidden"
    >
      <section class="p-6">
        <Wordmark :size="22" />

        <!-- `--text-3xl` is 44px and is a marketing rung; inside this panel it
             would set two lines of heading against a four-line page. `--text-2xl`
             with the display tracking is the same voice at the size the column
             actually is. -->
        <h1 class="mt-5 font-ui text-2xl font-medium tracking-display leading-tight text-text-primary">
          Request access
        </h1>

        <p class="mt-4 max-w-hint font-prose text-md leading-normal text-text-secondary">
          Accounts are added by hand. Tell us what you follow and we will say yes or no within a working day.
        </p>

        <div class="mt-6 pt-5 border-t border-border-subtle flex flex-col gap-3">
          <p class="font-prose text-sm text-text-tertiary">One brief a day, generated overnight.</p>
          <p class="font-prose text-sm text-text-tertiary">A graph that grows with what you read.</p>
          <p class="font-prose text-sm text-text-tertiary">No definition without a source behind it.</p>
        </div>
      </section>

      <section class="p-6 border-t border-border-subtle md:border-t-0 md:border-l">
        <StatusNote v-if="sent" kind="success">
          Thanks. We'll be in touch within a working day.
          <template #hint>
            Accounts are added by hand, so this is a person reading it rather than a queue.
          </template>
        </StatusNote>

        <form v-else class="flex flex-col gap-4" novalidate @submit.prevent="submit">
          <div class="flex flex-col gap-2">
            <label for="ra-email" class="font-ui text-2xs uppercase tracking-label text-text-tertiary">
              Work email
            </label>
            <Input id="ra-email" v-model="email" type="email" autocomplete="email" required />
          </div>

          <div class="flex flex-col gap-2">
            <label for="ra-coverage" class="font-ui text-2xs uppercase tracking-label text-text-tertiary">
              What you cover
            </label>
            <!-- Mono, like every other input in the app.

                 This was `font-prose`, on the argument that it is the one field
                 someone writes a sentence into and mono is a worse place to
                 write a paragraph than to read an id. That is a real argument
                 and it lost to a simpler one: a form control is an instrument
                 whatever goes into it, and one textarea in a different family
                 from every other field reads as a rendering fault rather than
                 as care. The prose face is for what is READ back, and this text
                 is read back on the brief, not here. -->
            <textarea
              id="ra-coverage"
              v-model="coverage"
              rows="3"
              class="w-full p-2 resize-none bg-surface-inset border border-border-default rounded-sm font-ui text-sm leading-normal text-text-body"
            />
          </div>

          <div class="flex flex-col gap-2">
            <span class="font-ui text-2xs uppercase tracking-label text-text-tertiary">Role</span>
            <!-- Single-select chips, not a `<select>`: options that are all
                 visible is a decision, and a dropdown hides them behind a
                 click. `SegmentedControl` is the other candidate and is for
                 switching a VIEW, not for answering a question.
                 `flex-wrap` because the list grew from three to nine and an
                 unwrapped row pushed the last of them off the panel. -->
            <div class="flex flex-wrap gap-2">
              <Chip
                v-for="option in roles"
                :key="option"
                interactive
                :tone="role === option ? 'accent' : 'neutral'"
                :aria-pressed="role === option"
                @click="role = option"
              >
                {{ option }}
              </Chip>
            </div>
          </div>

          <StatusNote v-if="failure" kind="error">{{ failure }}</StatusNote>

          <div class="mt-1 flex items-center gap-3">
            <Button icon="send" type="submit" :disabled="!canSubmit">
              {{ submitting ? 'Sending…' : 'Send request' }}
            </Button>
            <span class="font-ui text-2xs text-text-quiet">
              Already listed?
              <RouterLink to="/login" class="text-accent-primary">Sign in</RouterLink>
            </span>
          </div>
        </form>
      </section>
    </div>
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
