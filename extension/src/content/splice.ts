/** Put the runs into the page.
 *
 * The one rule: a server offset is never used as an index. Runs are consumed
 * sequentially by the length of their own text, measured in JavaScript. Python
 * offsets are code points and JavaScript's are UTF-16 units, so an astral-plane
 * character in a headline would shift every highlight after it -- which is
 * exactly why contract C3 splits into runs server-side instead of shipping
 * offsets. Walking `run.t.length` inherits that guarantee for free, because
 * `join(runs) === text` is asserted on both sides.
 */

import type { TextRun } from '../shared/types'
import { slice, type ExtractedBlock } from './extract'

export const STAMP = 'data-venona-v'
export const REF = 'data-venona-ref'

interface Wrap {
  start: number
  end: number
  ref: string
  kind: string
  confidence: number
  uncorroborated: boolean
}

/** Port of `BriefBlock.vue::classesFor`.
 *
 * Colour comes from the C4 id prefix with no lookup table anywhere -- contract
 * C5's whole point, and it survives the client boundary because
 * `emit-styles.mjs` generates the rules from the same tokens.
 */
function classesFor(
  ref: string,
  kind: string,
  confidence: number,
  uncorroborated: boolean,
): string[] {
  const classes = ['venona-mention']
  if (kind === 'entity') {
    classes.push('venona-mention--entity', `venona-mention--${ref.split(':')[0]}`)
  } else {
    classes.push(`venona-mention--${kind}`)
  }
  // A modifier stacked on the kind, exactly as in the app: the entity keeps its
  // hue and only the underline changes to dashed.
  if (uncorroborated) classes.push('venona-mention--uncorroborated')
  if (confidence < 0.7) classes.push('venona-mention--low')
  return classes
}

function titleFor(kind: string, uncorroborated: boolean): string | null {
  if (kind === 'undefined') return 'No grounded definition found'
  if (uncorroborated) return 'Named by one source; not yet corroborated'
  return null
}

/** Splice one block. Returns how many mentions were drawn. */
export function spliceBlock(block: ExtractedBlock, runs: TextRun[], version: string): number {
  // Group by text node first: a node must be rebuilt in a single pass, or the
  // second wrap would be computing offsets into a node the first one replaced.
  const byNode = new Map<Text, Wrap[]>()
  let cursor = 0
  let drawn = 0

  for (const run of runs) {
    const length = run.t.length
    if (run.r && run.k) {
      // A match can straddle inline markup, so one run may yield several
      // wraps -- one per text node, all carrying the same ref. They draw as one
      // continuous underline and nothing in the page is restructured.
      for (const part of slice(block.pieces, cursor, cursor + length)) {
        const list = byNode.get(part.node) ?? []
        list.push({
          start: part.start,
          end: part.end,
          ref: run.r,
          kind: run.k,
          confidence: run.c ?? 1,
          uncorroborated: run.u === true,
        })
        byNode.set(part.node, list)
        drawn++
      }
    }
    cursor += length
  }

  for (const [node, wraps] of byNode) rebuild(node, wraps)
  block.el.setAttribute(STAMP, version)
  return drawn
}

/** Replace one text node with a fragment of text and mention spans. */
function rebuild(node: Text, wraps: Wrap[]): void {
  // The node may already be detached if the page mutated under us between
  // extraction and splicing. Doing nothing is correct; the observer will
  // re-annotate.
  if (!node.parentNode) return

  wraps.sort((a, b) => a.start - b.start)
  const data = node.data
  const fragment = document.createDocumentFragment()
  let at = 0

  for (const wrap of wraps) {
    // Overlaps cannot happen -- `_resolve_overlaps` guarantees spans never
    // nest, and runs are a flat split of the block. Skip rather than trust it.
    if (wrap.start < at || wrap.end > data.length) continue
    if (wrap.start > at) fragment.append(data.slice(at, wrap.start))

    const span = document.createElement('span')
    span.className = classesFor(
      wrap.ref,
      wrap.kind,
      wrap.confidence,
      wrap.uncorroborated,
    ).join(' ')
    span.setAttribute(REF, wrap.ref)
    span.setAttribute('role', 'button')
    span.setAttribute('tabindex', '0')
    const title = titleFor(wrap.kind, wrap.uncorroborated)
    if (title) span.setAttribute('title', title)
    span.textContent = data.slice(wrap.start, wrap.end)
    fragment.append(span)

    at = wrap.end
  }
  if (at < data.length) fragment.append(data.slice(at))

  node.replaceWith(fragment)
}

/** Undo every mention under `root`, restoring the original text nodes.
 *
 * `normalize()` matters: without it, un-annotating and re-annotating leaves the
 * text split into ever more adjacent nodes, and a multi-word alias that fits in
 * one node today would stop matching tomorrow.
 */
export function unsplice(root: Element): void {
  for (const span of root.querySelectorAll(`[${REF}]`)) {
    const parent = span.parentNode
    if (!parent) continue
    span.replaceWith(document.createTextNode(span.textContent ?? ''))
    parent.normalize()
  }
  for (const el of root.querySelectorAll(`[${STAMP}]`)) el.removeAttribute(STAMP)
  if (root.hasAttribute(STAMP)) root.removeAttribute(STAMP)
}
