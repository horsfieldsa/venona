/* The hub's entry into the living graph behind its front door.
 *
 * The hub has no bundler of its own, so `make field` bundles this with the
 * esbuild in `hub/web/node_modules` and writes the result to
 * `hub/hub/static/constellation.js`, which is committed and diffed by
 * `make lint-generated` like every other generated copy of this artwork.
 *
 * Nothing is configured here but the class names. The field carries the
 * `data-graph-field` hook and the colour tokens `scripts/graph_field.py`
 * emits into the hub's inline stylesheet, so the graph resolves its palette
 * off the hub's theme.
 */
import { initConstellation } from './constellation'

/* `defer` on the tag means the document is parsed before this runs, so the
 * field is already in the DOM and there is nothing to wait for. */
initConstellation(document.querySelector<HTMLElement>('[data-graph-field]'), {
  live: 'graph-field--live',
  canvas: 'graph-field__canvas',
})
