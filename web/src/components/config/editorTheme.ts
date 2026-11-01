/**
 * The editor's colours and dimensions, as one stylesheet.
 *
 * A MODULE AND NOT `<script setup>`, which runs per component instance:
 * `EditorView.theme` builds a `StyleModule` and injects it, so a copy declared
 * inside the component would inject another thirty-odd rules on every mount and
 * leave the previous ones behind. The editor mounts once per document opened.
 */
import { HighlightStyle } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { tags } from '@lezer/highlight'

/**
 * Colour by meaning, from `tokens.css`.
 *
 * Comments and punctuation reuse the text ladder rather than declaring hues of
 * their own: a comment is secondary text that happens to be in a file.
 */
const highlight = HighlightStyle.define([
  { tag: [tags.propertyName, tags.definition(tags.propertyName)], color: 'var(--code-key)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--code-string)' },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: 'var(--code-number)' },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: 'var(--text-tertiary)' },
  { tag: [tags.punctuation, tags.separator, tags.bracket], color: 'var(--text-secondary)' },
  { tag: [tags.keyword, tags.operator], color: 'var(--text-body)' },
])

/**
 * Every base rule CodeMirror ships that carries a colour, repainted.
 *
 * `lint_tokens.py` cannot see any of them -- they live in `node_modules`, which
 * `scan_files` excludes -- and they are hardcoded light-theme values, so an
 * unlisted rule is a black caret on the night palette rather than a lint
 * failure. The `dark` flag is NOT the theme switch: it is a boolean resolved
 * once at construction, while this palette is `light-dark()` swapped at runtime
 * with a System default. Every value below is a token, so the palette does the
 * switching and the flag never has to be right.
 */
const theme = EditorView.theme({
  '&': {
    color: 'var(--text-body)',
    backgroundColor: 'var(--surface-inset)',
    height: '100%',
    fontSize: 'var(--text-sm)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': {
    fontFamily: 'var(--font-mono)',
    lineHeight: 'var(--leading-normal)',
    overflow: 'auto',
  },
  '.cm-content': { caretColor: 'var(--text-primary)', padding: 'var(--space-3) 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text-primary)' },
  '.cm-selectionBackground, .cm-content ::selection': {
    backgroundColor: 'var(--surface-selected)',
  },
  '&.cm-focused .cm-selectionBackground': { backgroundColor: 'var(--surface-selected)' },
  '.cm-gutters': {
    backgroundColor: 'var(--surface-inset)',
    color: 'var(--text-quiet)',
    borderRight: '1px solid var(--border-subtle)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--surface-base)' },
  '.cm-activeLineGutter': {
    backgroundColor: 'var(--surface-base)',
    color: 'var(--text-secondary)',
  },
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--surface-raised)',
    border: '1px solid var(--border-subtle)',
    color: 'var(--text-tertiary)',
    padding: '0 var(--space-2)',
  },
  '.cm-panels': {
    backgroundColor: 'var(--surface-overlay)',
    color: 'var(--text-body)',
    borderTop: '1px solid var(--border-subtle)',
  },
  '.cm-panels input, .cm-panels button': {
    fontFamily: 'var(--font-ui)',
    color: 'var(--text-body)',
    backgroundColor: 'var(--surface-inset)',
    border: '1px solid var(--border-default)',
  },
  '.cm-searchMatch': { backgroundColor: 'var(--code-search-match)' },
  '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'var(--surface-selected)' },
  '.cm-selectionMatch': { backgroundColor: 'var(--code-search-match)' },
  '.cm-tooltip': {
    backgroundColor: 'var(--surface-overlay)',
    border: '1px solid var(--border-subtle)',
    color: 'var(--text-body)',
  },
})

export { highlight, theme }
