<script setup lang="ts">
/**
 * The configuration editor's text surface: CodeMirror over one document.
 *
 * WHY A REAL EDITOR AND NOT A TEXTAREA. `config/ontology.yaml` is 29KB and
 * `profile/anchors.yaml` is 83KB, and both open on sixteen or twenty-one lines
 * of comment before the first key. Folding that block and numbering the lines
 * is what makes those files navigable; syntax colour is what makes a mistyped
 * quote visible before Save refuses it. It is safe where a comment-preserving
 * round-trip is not, and that distinction is the whole of docs/adr/0200: this
 * never parses and reprints the document, it holds the same string a textarea
 * held.
 *
 * The server still validates on save through the loader startup uses. Nothing
 * here decides whether a document is valid.
 */
import { EditorState, type Extension } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from '@codemirror/view'
import {
  codeFolding,
  foldGutter,
  foldKeymap,
  foldEffect,
  syntaxHighlighting,
} from '@codemirror/language'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { closeSearchPanel, search, searchKeymap, searchPanelOpen } from '@codemirror/search'
import { yaml } from '@codemirror/lang-yaml'
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { highlight, theme } from '@/components/config/editorTheme'
import { useDismissable } from '@/composables/useKeyboard'

const props = defineProps<{
  modelValue: string
  /** `plain` for the markdown documents: they are prose, and a grammar for
   *  them would drag four more language packages in for no reading gained. */
  language: 'yaml' | 'plain'
  readonly: boolean
  /** Accessible name for the text surface: the document's title. */
  label: string
  /** Leading comment lines to fold on open; 0 to fold nothing. */
  foldLines: number
  /** Identity of the open document. A CHANGE OF THIS IS A NEW EDITOR: see
   *  `swap` below, where the undo history has to be dropped with it. */
  docKey: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const host = ref<HTMLElement | null>(null)
/**
 * `shallowRef`, never `ref` or `reactive`.
 *
 * The same rule the Cytoscape instance carries in CLAUDE.md, for the same
 * reason: a Vue proxy over an editor's internal object graph makes it unusable
 * at size and is very painful to retrofit.
 */
const view = shallowRef<EditorView | null>(null)

/**
 * The last string this component emitted or was handed.
 *
 * Compared against instead of `state.doc.toString()`, which materialises 83KB
 * on every keystroke for `anchors`. It is also what makes the reconcile below
 * a no-op for the value we just sent up.
 */
let known = ''
/** Set while WE are writing the document, so the listener does not echo. */
let applying = false

function grammar(kind: 'yaml' | 'plain'): Extension {
  return kind === 'yaml' ? yaml() : []
}

/**
 * The search panel joins the app's dismissable stack while it is open.
 *
 * `useKeyboard` listens in CAPTURE phase and binds Escape with `allowInInput`,
 * so the app's handler runs BEFORE CodeMirror's own and swallows the key. On
 * the stack the panel is the topmost dismissable while it is open, which is
 * what makes Escape close it and only it.
 */
const searchOpen = ref(false)
const dismissable = useDismissable(() => {
  const v = view.value
  if (v) closeSearchPanel(v)
})
watch(searchOpen, (open) => (open ? dismissable.push() : dismissable.pop()))

function extensions(): Extension[] {
  return [
    lineNumbers(),
    codeFolding(),
    foldGutter(),
    history(),
    drawSelection(),
    highlightActiveLine(),
    highlightActiveLineGutter(),
    search({ top: true }),
    EditorView.lineWrapping,
    syntaxHighlighting(highlight),
    theme,
    // NO `indentWithTab`. It traps Tab inside the editor, which strands anyone
    // moving through the page by keyboard, and YAML wants spaces regardless.
    keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap, ...searchKeymap]),
    grammar(props.language),
    EditorState.readOnly.of(props.readonly),
    EditorView.updateListener.of((update) => {
      if (update.docChanged && !applying) {
        known = update.state.doc.toString()
        emit('update:modelValue', known)
      }
      searchOpen.value = searchPanelOpen(update.state)
    }),
  ]
}

/** Fold the file's own header, once, on open. */
function foldPreamble(v: EditorView, lines: number) {
  if (lines < 1 || lines >= v.state.doc.lines) return
  const from = v.state.doc.line(1).from
  const to = v.state.doc.line(lines).to
  v.dispatch({ effects: foldEffect.of({ from, to }) })
}

function build() {
  if (!host.value) return
  known = props.modelValue
  const v = new EditorView({
    state: EditorState.create({ doc: props.modelValue, extensions: extensions() }),
    parent: host.value,
  })
  v.contentDOM.setAttribute('aria-label', props.label)
  v.contentDOM.setAttribute('spellcheck', 'false')
  view.value = v
  expose(v)
  foldPreamble(v, props.foldLines)
}

/**
 * The editor, reachable from `scripts/ui_smoke.py`. DEV BUILDS ONLY.
 *
 * A textarea could be driven by setting `.value` and firing `input`; a
 * contenteditable cannot, and the checks that need to are the ones worth
 * keeping -- that the draft backup is debounced off the keystroke, and that a
 * refused save leaves the buffer dirty. Both are regressions this project has
 * actually shipped. This is the same shape as `HttpClient`'s `transport=` seam:
 * a hook that exists so the thing can be tested at all.
 */
function expose(v: EditorView) {
  if (import.meta.env.DEV) (window as unknown as { __cm?: EditorView }).__cm = v
}

/**
 * A different document is a NEW state, not a change transaction.
 *
 * A transaction keeps the previous file's undo history, so one undo after
 * switching pastes document A's text into document B's buffer -- and `save()`
 * PUTs the buffer to whichever key is active. `setState` drops the history with
 * the document, which is the only correct answer.
 */
function swap() {
  const v = view.value
  if (!v) return
  applying = true
  known = props.modelValue
  v.setState(EditorState.create({ doc: props.modelValue, extensions: extensions() }))
  v.contentDOM.setAttribute('aria-label', props.label)
  v.contentDOM.setAttribute('spellcheck', 'false')
  expose(v)
  applying = false
  foldPreamble(v, props.foldLines)
}

onMounted(build)
onBeforeUnmount(() => {
  dismissable.pop()
  view.value?.destroy()
  view.value = null
})

watch(() => props.docKey, swap)

// Same document, new text: a conflict resolved, a draft discarded, a snapshot
// restored, or the echo of a save. A transaction, so the undo history survives.
watch(
  () => props.modelValue,
  (value) => {
    const v = view.value
    if (!v || value === known) return
    applying = true
    known = value
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } })
    applying = false
    // Replacing the document drops its folds. Every path that lands here is a
    // reset to a known text, so the header folds again exactly as it would on a
    // fresh open -- otherwise discarding a draft leaves you looking at twenty
    // lines of comment you had already put away.
    foldPreamble(v, props.foldLines)
  },
)

</script>

<template>
  <div
    ref="host"
    class="code flex-1 min-h-0 max-md:min-h-(--scroll-xl) w-full overflow-hidden bg-surface-inset border border-border-subtle rounded-sm"
  />
</template>
