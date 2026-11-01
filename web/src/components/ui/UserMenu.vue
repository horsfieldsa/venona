<script setup lang="ts">
/**
 * The account menu: who you are, your settings, and the way out.
 *
 * One glyph in the header's utility cluster, opening a menu that holds the
 * Settings link and Sign out under the signed-in address. Sign out belongs
 * here as well as on the account page: ending a session should not require
 * finding the one page with the button on it. The email at the top is
 * identity, not an action -- the header's answer to "who am I signed in as".
 *
 * Sign out mirrors `AccountView.signOut` exactly: POST, then a full
 * `location.assign('/login')` whatever the response, because every store in
 * memory belongs to the session that just ended and a cold load re-runs the
 * pre-mount session probe.
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { apiPost } from '@/api/client'
import { usePopover } from '@/composables/usePopover'
import { useSessionStore } from '@/stores/session'
import { useThemeStore } from '@/stores/theme'

const session = useSessionStore()
const theme = useThemeStore()

/** The three, in the order the cycle used: the two explicit choices first and
 *  System last, so the reader who simply wants the other palette finds it at
 *  the top rather than after the one that defers. */
const THEMES = [
  { value: 'light', label: 'Light', icon: 'sun' },
  { value: 'dark', label: 'Dark', icon: 'moon' },
  { value: 'system', label: 'System', icon: 'monitor' },
] as const
const open = ref(false)
const root = ref<HTMLElement | null>(null)
usePopover(open)
const signingOut = ref(false)

async function signOut() {
  if (signingOut.value) return
  signingOut.value = true
  try {
    await apiPost('/auth/logout')
  } catch {
    // The intent was to stop being signed in; an invalid session lands on
    // /login by the normal 401 path anyway.
  } finally {
    // Through the hub's end-session URL when there is one: signing out of
    // the instance while the hub session lives is one click from signed
    // back in, which reads as sign-out not working.
    window.location.assign(session.logoutUrl ?? '/login')
  }
}

/* Closed by clicking anywhere else or by Escape. `mousedown`, not `click`:
 * a drag that starts outside should dismiss before any click resolves. */
function onDocMousedown(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close(true)
}

/**
 * A menu is not a modal.
 *
 * Deliberately NOT `useOverlay`: that traps Tab, and a menu must let Tab leave
 * — it is a small list beside a button, not a surface that owns the screen. So
 * the menu contract is honoured by hand instead: focus enters on open, the
 * arrows move within it and wrap, and Escape returns focus to the trigger
 * rather than dropping it on `<body>`, which strands a keyboard user at the top
 * of the document.
 */
// `radio` as well as `menuitem`: the theme rows pick one of a set rather than
// running an action, so they carry the role that says so -- and a keyboard
// reader arrowing down the menu must still reach them. Selecting on the role
// alone would step over three of the six rows without appearing to.
const items = () =>
  Array.from(
    root.value?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="radio"]') ?? [],
  )

function close(restoreFocus = false) {
  if (!open.value) return
  open.value = false
  if (restoreFocus) root.value?.querySelector<HTMLElement>('button')?.focus()
}

function moveFocus(step: number) {
  const menuItems = items()
  if (!menuItems.length) return
  const at = menuItems.indexOf(document.activeElement as HTMLElement)
  const next = at === -1 ? 0 : (at + step + menuItems.length) % menuItems.length
  menuItems[next]?.focus()
}

function onMenuKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    moveFocus(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    moveFocus(-1)
  }
}

watch(open, (isOpen) => {
  if (!isOpen) return
  // After the panel renders: the items do not exist until then.
  void nextTick(() => items()[0]?.focus())
})
onMounted(() => {
  document.addEventListener('mousedown', onDocMousedown)
  document.addEventListener('keydown', onDocKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMousedown)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>

<template>
  <div ref="root" class="usermenu relative">
    <IconButton
      label="Account"
      :active="open"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="open = !open"
    >
      <!-- The Google avatar when a login carried one; the glyph until then.
           `alt=""`: the button's own label names the control, and a second
           name inside it would be announced twice. -->
      <img
        v-if="session.picture"
        :src="session.picture"
        alt=""
        class="w-4 h-4 rounded-pill object-cover"
        referrerpolicy="no-referrer"
      />
      <Icon v-else name="user" />
    </IconButton>

    <!-- The overlay surface, the hover-card's rung: this floats over content,
         so it sits on --surface-overlay with the default border, same as every
         other transient panel. -->
    <div
      v-if="open"
      role="menu"
      class="menu absolute right-0 top-full z-popover mt-1 flex flex-col whitespace-nowrap rounded-lg border border-border-default bg-surface-overlay p-1"
      @keydown="onMenuKeydown"
    >
      <p class="who m-0 px-2 py-1 font-ui text-2xs text-text-quiet">
        {{ session.email ?? 'signed in' }}
      </p>
      <div class="mx-1 border-t border-t-border-subtle" role="separator" />

      <!-- THEME ON ONE ROW, three marks and no words. The menu's other rows
           are destinations and verbs, each needing a name; this is one setting
           with three states, and the marks carry it in a fifth of the height.
           The row is the width of the menu rather than the width of its
           content, so it reads as a control rather than as three more items.

           `radio`, not `menuitem`: these pick one of a set rather than running
           an action. Nothing here is labelled on screen, so the accessible name
           is the ONLY name -- `aria-label` on each, and the group labelled too,
           or a screen reader gets three unnamed buttons in a row.

           THE CHOSEN ONE TAKES `--surface-selected` AND THE ACCENT, which is
           what `SegmentedControl` gives its selected segment: the same ground
           everywhere in the app means "this one". On `--surface-inset` it wore
           the HOVER ground instead, so the mark under the pointer and the mark
           actually in force looked alike -- and with no labels that ground is
           the only thing saying which is which. -->
      <div
        role="radiogroup"
        aria-label="Theme"
        class="flex items-center justify-between gap-1 px-2 py-1"
      >
        <button
          v-for="option in THEMES"
          :key="option.value"
          type="button"
          role="radio"
          :aria-checked="theme.choice === option.value"
          :aria-label="option.label"
          class="grow flex h-control cursor-pointer items-center justify-center rounded-sm border-0 transition-colors duration-fast ease-standard"
          :class="
            theme.choice === option.value
              ? 'is-selected bg-surface-selected text-accent-primary'
              : 'bg-transparent text-text-tertiary hover:bg-surface-inset hover:text-text-primary'
          "
          :title="
            option.value === 'system'
              ? `Follow the system, currently ${theme.systemResolves}`
              : option.label
          "
          @click="theme.set(option.value)"
        >
          <Icon :name="option.icon" />
        </button>
      </div>

      <div class="mx-1 border-t border-t-border-subtle" role="separator" />
      <RouterLink
        to="/settings/account"
        role="menuitem"
        class="item flex h-control items-center rounded-sm px-2 text-sm text-text-body no-underline hover:bg-surface-inset hover:text-text-primary hover:no-underline"
        @click="close()"
      >
        Settings
      </RouterLink>
      <button
        type="button"
        role="menuitem"
        class="item flex h-control w-full cursor-pointer items-center rounded-sm border-0 bg-transparent px-2 text-left font-prose text-sm text-text-body hover:bg-surface-inset hover:text-text-primary"
        :disabled="signingOut"
        @click="signOut"
      >
        {{ signingOut ? 'Signing out…' : 'Sign out' }}
      </button>
    </div>
  </div>
</template>
