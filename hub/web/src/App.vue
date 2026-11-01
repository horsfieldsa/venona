<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import Lockup from '@/components/brand/Lockup.vue'
import Button from '@/components/ui/Button.vue'
import AdminRail from '@/components/nav/AdminRail.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { get, ApiError } from '@/api/client'
import { NAV } from '@/router'

/**
 * The operator shell: who you are, where you are, and the rail.
 *
 * THE IDENTITY LINE SAYS `all domains`, NOT `admin`. The app's convention is
 * `admin · <section> · <detail>` and the middle segment is the one doing the
 * work. But on an instance `admin` means that domain's administration, and here
 * it means across every domain -- and this surface now looks enough like the
 * instance's that the byte-identical string at two different scopes is the one
 * thing that would make them harder to tell apart. "Domains" is the word these
 * pages already use for their subject.
 *
 * THE COUNTS ARE FETCHED HERE, not by the view that describes them. The rail
 * shows them on every page, so a count that is only correct on the page it
 * describes is worse than no count.
 */
interface Session {
  email: string
  pending: number
  stricken_out: number
}

const route = useRoute()
const session = ref<Session | null>(null)
const error = ref('')

const current = computed(() => NAV.find((item) => item.path === route.path)?.key ?? 'overview')
const identity = computed(() => {
  const section = NAV.find((item) => item.path === route.path)
  return section ? `all domains · ${section.label.toLowerCase()}` : 'all domains'
})

onMounted(async () => {
  try {
    session.value = await get<Session>('/session')
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : 'The hub did not answer. Try again in a moment.'
  }
})
</script>

<template>
  <!-- THE APP'S BAR, class for class: `--surface-raised` over the page's own
       `--surface-base`, `h-nav` tall, a `--border-subtle` hairline under it, and
       `gap-5` between zones. The ground is the part that carries it -- a
       transparent header sits on the body colour and reads as a different
       application from three feet away, whatever the type is doing.

       `select-none` on the brand, as the app has it: a selection rectangle over
       a lockup reads as a coloured box sitting ON the mark, and a drag that
       starts in the page and runs up into the bar catches it constantly. -->
  <header
    class="bar min-w-0 flex flex-wrap items-center gap-5 px-page h-nav border-b border-b-border-subtle bg-surface-raised [overflow-wrap:anywhere]"
  >
    <a
      href="/"
      aria-label="Venona, back to your domains"
      class="brand flex items-center flex-none h-control select-none text-accent-primary no-underline hover:no-underline"
    >
      <!-- 28, the app's own: `size` is the LOCKUP BOX height and the mark is 104
           of its 124 units, so the drawn word scales with it. Below the kit's
           16px floor the x-height reads as a smudge; 28 puts it at ~7.2px and
           fills the slot a `--control-h` bar already reserves.
           `label=""` because the anchor above already names this. -->
      <Lockup :size="28" label="" />
    </a>

    <!-- THE WAY OUT, NAMED. The lockup above goes to the same place, but a mark
         that happens to be a link is not an affordance anybody looks for -- and
         this is a bundle with its own routing, so nothing else on screen leaves
         it. The rail holds destinations WITHIN admin; leaving is the header's
         job, which is exactly the division the app's own header keeps.
         It says what the destination calls itself: `/` opens on the heading
         "Your domains".

         A plain `<a>`, never a `RouterLink`: that page is server-rendered and
         outside this bundle, so a client-side navigation would resolve to
         nothing.

         NOT hidden on a phone, unlike the app's nav row -- that one collapses
         into a drawer holding the same list, and there is no drawer here. One
         link is affordable at any width, and hiding the only way back is worse
         than a slightly tighter bar.

         The landmark is named for its REGION, not for what it does: a reader
         tabbing landmarks hears "Venona" here and "Admin" at the rail, which
         tells them which of the two navigations they are in. The app leaves its
         header nav unlabelled and only names the rail, which works there
         because the pairing is the same -- but an unnamed landmark beside a
         named one is the weaker half of that, so this one says its name. -->
    <nav class="nav flex-none flex items-center gap-4" aria-label="Venona">
      <a
        href="/"
        class="link relative inline-flex items-center gap-1 h-control select-none whitespace-nowrap text-sm text-text-tertiary no-underline border-b border-b-transparent hover:text-text-secondary hover:no-underline"
      >
        Your domains
      </a>
    </nav>

    <!-- The spacer the app's header uses, for the same reason: brand and nav
         are one cluster on the left and the utilities belong at the trailing
         edge, which `justify-between` cannot express with three zones -- it
         spreads them evenly and leaves the nav floating mid-bar. -->
    <span class="grow" />

    <div class="who flex items-center gap-3 min-w-0 font-ui text-2xs text-text-quiet">
      <span class="truncate">{{ session?.email }}</span>
      <!-- A real form, not a fetch: signing out clears a cookie and has to work
           whether or not this bundle is still healthy. -->
      <form method="post" action="/logout">
        <Button variant="outline" submit>Sign out</Button>
      </form>
    </div>
  </header>

  <!-- The identity row, on `--surface-base` under the raised bar: the app's
       ContextBar is the same two grounds stacked, and reversing them makes the
       header recede instead of sitting forward. -->
  <div
    class="context flex items-center min-w-0 gap-4 min-h-subnav py-1 px-page border-b border-b-border-subtle bg-surface-base"
  >
    <span class="identity mono min-w-0 truncate text-2xs tracking-label uppercase text-text-quiet">
      {{ identity }}
    </span>
  </div>

  <main class="app pt-5 pb-8 pr-page pl-edge max-w-[calc(var(--measure-page)+var(--edge))]">
    <StatusNote v-if="error" kind="error">{{ error }}</StatusNote>
    <RailLayout v-else>
      <template #rail>
        <AdminRail
          :current="current"
          :pending="session?.pending ?? 0"
          :stricken-out="session?.stricken_out ?? 0"
        />
      </template>
      <div class="pane min-w-0">
        <RouterView />
      </div>
    </RailLayout>
  </main>
</template>
