import { computed, getCurrentInstance, onActivated, onDeactivated, onUnmounted, ref } from 'vue'

import { IS_MAC, MOD_LABEL, hasMod } from '@/lib/platform'
import { useSessionStore } from '@/stores/session'

/**
 * One keyboard registry, one listener.
 *
 * Why this is not a matter of taste: `MergeReview` binds bare `r` to *relate
 * (PART_OF)* and `DefinitionQueue` binds bare `r` to *reject*. Both listen on
 * `window`, and both write to the database. A `g`-prefixed go-to chord -- the
 * obvious Linear-style shortcut -- would fire the route jump AND the write,
 * and two `window` listeners cannot be separated by `stopPropagation`.
 *
 * So the global layer emits NO BARE LETTERS. Every global shortcut carries a
 * modifier (`mod+k`, `alt+1`) or is unambiguous punctuation (`?`, `Escape`).
 * That makes the whole collision class structurally impossible rather than
 * merely avoided, and it is why every view registers here instead of adding
 * another listener.
 *
 * The `?` overlay is GENERATED from this registry, so it cannot drift from what
 * is REGISTERED -- the hand-written cheatsheet it replaces already could. It can
 * still drift from what actually FIRES, which is what `candidates` below and
 * `useKeyboard.dom.spec.ts` exist to stop: `shift+l` sat in this registry, and
 * in the overlay, for as long as it was unmatchable.
 */

export interface KeyBinding {
  /** Canonical, lowercased: `m`, `mod+k`, `alt+1`, `shift+l`, `arrowright`. */
  keys: string[]
  /**
   * What it does, shown in the `?` overlay. (Only there -- this said "and on
   * the settings page", which no page has ever read.)
   *
   * A FUNCTION when the caption depends on state. Bindings are captured once,
   * at registration, so `label: someComputed.value` freezes whatever that
   * happened to be on mount -- `MergeReview`'s Relate said "Relate (PART_OF)"
   * for the rest of the session however many pairs went past. A getter is read
   * when the overlay renders.
   */
  label: string | (() => string)
  /** Section heading in the overlay. */
  group: string
  run: (event: KeyboardEvent) => void
  /** Survives focus being in a text field. Reserve for `mod+s`, `mod+k`, Esc. */
  allowInInput?: boolean
  /**
   * Fires on key AUTO-REPEAT as well as on the initial press. Off by default,
   * and that default is the safe one: holding a key delivers a keydown every
   * ~30ms, and a binding that WRITES then advances the queue -- `m`, `k`, `r`
   * -- runs again against whatever pair loaded in the meantime, deciding a
   * conflict nobody looked at. Turn it on for navigation, where repeat is the
   * point and the worst case is scrolling too far.
   */
  repeatable?: boolean
  /**
   * Admin-only. NEITHER FIRES NOR APPEARS IN THE OVERLAY for a reader.
   *
   * This is the FOURTH mirror of a route's `meta.operator`, after the router
   * guard, `AppHeader::NAV` and `CommandPalette::ROUTES`, and it is a mirror for
   * the same reason they are: a route's meta is not reachable from a path
   * without resolving it. `tests/test_frontend_drift.py::TestOperatorNav` is
   * what keeps all four honest.
   *
   * Not filtering is not merely untidy. An unfiltered operator key points a
   * reader at a page the guard bounces them off, back to the brief with no
   * explanation -- for a key the `?` overlay had just told them to press.
   *
   * UX, NEVER THE GATE. `api/authz.py` refuses the request server-side.
   */
  operator?: boolean
}

interface Scope {
  id: number
  bindings: KeyBinding[]
}

const scopes = ref<Scope[]>([])
const dismissables: Array<() => void> = []
let nextScopeId = 1
let suspendDepth = 0
let installed = false

/**
 * Every key string this event could match, in PREFERENCE ORDER.
 *
 * It returns a LIST rather than one canonical string because two of the three
 * rules below need a fallback, and the caller tries them in turn.
 *
 * SHIFT, FOR LETTERS ONLY. This function ignored `shiftKey` entirely for a long
 * time, on the argument that `?` is already shift+`/` and folding shift in would
 * make every binding platform- and layout-dependent. That argument holds for
 * punctuation and digits, where shift CHANGES the character -- and it is why
 * `?` and `!` are still matched as themselves. It does not hold for letters,
 * where shift leaves `event.key` a letter on every layout. Meanwhile
 * `DefinitionQueue` had bound `shift+l` and `shift+e`, which could therefore
 * never fire: Shift+L fell through to the bare `l` binding, which spends about
 * $0.13 on a grounded lookup. The bare letter stays in the list as a FALLBACK,
 * so Shift+A still archives on the brief -- nothing that worked stops working.
 *
 * ALT+DIGIT BY PHYSICAL KEY. macOS composes Option+1 into `¡`, Option+2 into
 * `™`, and so on, so `alt+1` through `alt+4` had been dead on a Mac since the
 * day they were added. `event.code` is `Digit1` whatever the modifier and
 * whatever the layout, so the nav keys are matched off that first.
 */
function candidates(event: KeyboardEvent): string[] {
  const key = event.key.toLowerCase()
  const prefix: string[] = []
  if (hasMod(event)) prefix.push('mod')
  if (event.altKey) prefix.push('alt')
  const combo = (...rest: string[]) => [...prefix, ...rest].join('+')

  const out: string[] = []
  const digit = /^Digit([0-9])$/.exec(event.code)
  if (event.altKey && digit) out.push(combo(digit[1]))
  if (event.shiftKey && /^[a-z]$/.test(key)) out.push(combo('shift', key))
  out.push(combo(key))
  return [...new Set(out)]
}

function isEditable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return true
  return el.isContentEditable
}

/**
 * Register bindings for the lifetime of the calling component.
 *
 * Unregisters on unmount AND on `<KeepAlive>` deactivation -- a cached view
 * that kept its bindings alive would still be stealing keys from whatever the
 * user navigated to.
 */
export function useKeyBindings(bindings: KeyBinding[]) {
  const scope: Scope = { id: nextScopeId++, bindings }

  function add() {
    if (!scopes.value.some((s) => s.id === scope.id)) scopes.value.push(scope)
  }
  function remove() {
    scopes.value = scopes.value.filter((s) => s.id !== scope.id)
  }

  add()
  // These hooks are only valid inside a component; guarded so this stays
  // callable from a plain function.
  if (getCurrentInstance()) {
    onUnmounted(remove)
    onActivated(add)
    onDeactivated(remove)
  }

  return { remove }
}

/**
 * Esc closes the topmost thing, not everything.
 *
 * With a palette over a dialog over a hover card over a search dropdown, a
 * broadcast would collapse the lot on one press.
 */
export function useDismissable(close: () => void) {
  function push() {
    if (!dismissables.includes(close)) dismissables.push(close)
  }
  function pop() {
    const at = dismissables.indexOf(close)
    if (at !== -1) dismissables.splice(at, 1)
  }
  return { push, pop }
}

/** Silence view bindings while a text-entry overlay owns the keyboard --
 *  otherwise typing "graph" into the palette fires whatever `g` is bound to. */
export function suspendBindings() {
  suspendDepth++
}
export function resumeBindings() {
  suspendDepth = Math.max(0, suspendDepth - 1)
}

export interface GlobalHandlers {
  openPalette: () => void
  openHelp: () => void
}

/**
 * The go-to keys, POSITIONAL AGAINST `AppHeader::NAV` -- same destinations, same
 * order, same `operator` flag on each row.
 *
 * A TABLE rather than six inline bindings, and each row carries `to` on its
 * own line, because `tests/test_frontend_drift.py::TestOperatorNav` parses this
 * the same way it parses `AppHeader::NAV` and `CommandPalette::ROUTES`. Reshape
 * a row onto two lines and that test stops seeing it.
 *
 * POSITIONAL AGAINST `AppHeader::NAV`, in its order, and adding a destination
 * to the header means adding it here in the same place. The two lists drifted
 * once and `alt+3` went to Curation while the nav's third item was Archive.
 */
const NAV_KEYS = [
  { keys: ['alt+1'], to: '/', label: 'Go to Brief', operator: false },
  { keys: ['alt+2'], to: '/graph', label: 'Go to Graph', operator: false },
  { keys: ['alt+3'], to: '/briefs', label: 'Go to Archive', operator: false },
  { keys: ['alt+4'], to: '/lookup', label: 'Go to Lookup', operator: false },
  { keys: ['alt+5'], to: '/learn', label: 'Go to Learn', operator: false },
  { keys: ['alt+6'], to: '/admin', label: 'Go to Admin', operator: true },
] as const

/** Called once, by `AppShell`. Idempotent so a hot reload cannot stack a
 *  second listener and fire every binding twice. */
export function installKeyboard(handlers: GlobalHandlers) {
  if (installed) return
  installed = true

  const globals: KeyBinding[] = [
    {
      keys: ['mod+k'],
      label: 'Search and jump',
      group: 'global',
      allowInInput: true,
      run: handlers.openPalette,
    },
    { keys: ['?'], label: 'Keyboard help', group: 'global', run: handlers.openHelp },
    {
      keys: ['escape'],
      label: 'Dismiss the topmost overlay',
      group: 'global',
      allowInInput: true,
      // Pop, then invoke. Every consumer also removes itself via a watch on its
      // own open-state, and a double-pop is a harmless no-op -- but a consumer
      // that forgets to, or whose state is cleared by some other path, would
      // strand a dead callback on top of the stack and silently kill Esc for
      // every dialog and palette in the app until reload.
      run: () => dismissables.pop()?.(),
    },
    ...NAV_KEYS.map((item) => ({
      keys: [...item.keys],
      label: item.label,
      group: 'global',
      operator: item.operator,
      run: () => go(item.to),
    })),
    // `/settings/account`, not `/settings`. The bare path is only a redirect,
    // and a shortcut that depends on a redirect hop is one refactor away from
    // landing on the catch-all.
    { keys: ['mod+,'], label: 'Settings', group: 'global', run: () => go('/settings/account') },
  ]

  scopes.value.unshift({ id: 0, bindings: globals })

  // Capture phase, so this runs before anything a component might still be
  // listening for, and one listener so precedence is decidable at all.
  window.addEventListener(
    'keydown',
    (event) => {
      if (event.isComposing) return
      const combos = candidates(event)
      const editable = isEditable(event.target)
      const session = useSessionStore()

      // CANDIDATES OUTSIDE, SCOPES INSIDE, and that order is the whole point of
      // returning a list: `shift+l` and `l` are both registered by
      // `DefinitionQueue`, in that array and in that scope. Loop scopes first
      // and the bare `l` -- earlier in the array -- wins on the preferred
      // candidate's own pass, which is exactly the bug this replaced.
      for (const combo of combos) {
        // Later scopes win: a view's binding beats a global of the same name.
        for (let i = scopes.value.length - 1; i >= 0; i--) {
          const scope = scopes.value[i]
          // Scope 0 is the global set; it is never suspended, so Esc and the
          // palette still work while the palette itself is open.
          if (suspendDepth > 0 && scope.id !== 0) continue
          for (const binding of scope.bindings) {
            if (!binding.keys.includes(combo)) continue
            // Not `return`: a reader falls THROUGH to whatever else claims the
            // key, and to nothing at all if the answer is nothing. Returning
            // here would let an admin-only binding swallow a key it may not run.
            if (binding.operator && !session.isAdmin()) continue
            if (editable && !binding.allowInInput) continue
            event.preventDefault()
            event.stopImmediatePropagation()
            // Swallowed, not passed through: the key IS bound, so letting the
            // browser have its default back mid-hold would be stranger than
            // doing nothing.
            if (event.repeat && !binding.repeatable) return
            binding.run(event)
            return
          }
        }
      }
    },
    true,
  )
}

/** Router navigation without importing the router into every caller. */
let navigate: ((path: string) => void) | null = null
export function setNavigator(fn: (path: string) => void) {
  navigate = fn
}
function go(path: string) {
  navigate?.(path)
}

/** Every registered binding, grouped -- the single source behind the `?`
 *  overlay. (It said "and the palette's action list and the settings page" for a
 *  long time; neither was ever true. `CommandPalette` builds its own `ROUTES`,
 *  and no settings page reads this.)
 *
 *  Labels are RESOLVED here, and an empty one drops the row: a caption is what
 *  makes a key legible, and a blank one is a row saying a key exists and
 *  refusing to say what it does. Dropping them also keeps `binding.label`
 *  usable as the overlay's `v-for` key -- eight blank labels in one group were
 *  eight duplicate Vue keys, which lets the reconciler reuse the wrong row.
 *
 *  Admin-only bindings are dropped for a reader, by the same test the dispatch
 *  loop applies. A `computed`, so a session demoted while the overlay is open
 *  re-filters itself. */
export const keyboardGroups = computed(() => {
  const session = useSessionStore()
  const groups = new Map<string, Array<{ keys: string[]; label: string }>>()
  for (const scope of scopes.value) {
    for (const binding of scope.bindings) {
      if (binding.operator && !session.isAdmin()) continue
      const label = typeof binding.label === 'function' ? binding.label() : binding.label
      if (!label) continue
      const list = groups.get(binding.group) ?? []
      list.push({ keys: binding.keys, label })
      groups.set(binding.group, list)
    }
  }
  return [...groups.entries()].map(([name, bindings]) => ({ name, bindings }))
})

const DISPLAY: Record<string, string> = {
  arrowup: '↑',
  arrowdown: '↓',
  arrowleft: '←',
  arrowright: '→',
  escape: 'Esc',
  enter: '⏎',
  ' ': 'Space',
}

/** `mod+k` -> `⌘K` or `Ctrl K`; `arrowright` -> `→`; `m` -> `M`. */
export function displayKey(combo: string): string {
  return combo
    .split('+')
    .map((part) => {
      if (part === 'mod') return MOD_LABEL
      if (part === 'alt') return IS_MAC ? '⌥' : 'Alt'
      // Same shape as `alt` above. Without this the overlay rendered the literal
      // string `shift+L`, which is the registry's vocabulary leaking onto a page
      // whose whole job is to be read.
      if (part === 'shift') return IS_MAC ? '⇧' : 'Shift'
      return DISPLAY[part] ?? (part.length === 1 ? part.toUpperCase() : part)
    })
    .join(IS_MAC ? '' : '+')
}
