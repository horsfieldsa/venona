/** Turn a third-party page into blocks the alias index can match, and keep a
 * map back to the DOM.
 *
 * THE PROBLEM THIS FILE EXISTS FOR.
 *
 * `markdown_to_blocks` produces single-spaced text, so every multi-word alias
 * in the index -- 433 of 1003 of them -- has exactly one space between its
 * words. A DOM text node carries the HTML source's newlines and indentation
 * verbatim, and the Aho-Corasick automaton matches literally:
 *
 *     'Lockheed Martin won.'         -> ('Lockheed Martin', org:lockheed-martin)
 *     'Lockheed\n      Martin won.'  -> ('Lockheed',        org:lockheed-martin)
 *
 * The second is what a real article looks like, and the failure is silent: the
 * mention just isn't there and it reads as a gap in the corpus.
 *
 * So the text we SEND is whitespace-collapsed, and the text we SPLICE is the
 * original. Bridging them is the `Piece` map. We cannot instead normalise the
 * DOM, because rewriting a page's text nodes to change its whitespace is a
 * visible mutation of someone else's document.
 *
 * Note what is deliberately NOT done here: nothing is NFC-normalised. The
 * server treats the text as opaque and `blocks_to_runs` slices the exact string
 * it was given, so `join(runs) === text` holds and the splice is safe. NFC
 * would change the string's LENGTH and break precisely that.
 */

/** Elements whose text is not prose. `code`/`pre` mirror `annotate_blocks`,
 * which sets `spans = []` for code blocks. */
const SKIP_TAGS = new Set([
  'SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'PRE', 'CODE', 'KBD', 'SAMP',
  'TEXTAREA', 'INPUT', 'SELECT', 'OPTION', 'BUTTON', 'SVG', 'CANVAS',
  'IFRAME', 'OBJECT', 'EMBED', 'VIDEO', 'AUDIO', 'MATH',
])

/** What counts as one unit of context. Matching `annotate.py`'s block types,
 * plus the containers a news page actually uses. */
const BLOCK_TAGS = new Set([
  'P', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE',
  'DD', 'DT', 'TD', 'TH', 'FIGCAPTION', 'SUMMARY',
])

/** Shorter than this and it is furniture -- a byline, a share label, a tag. */
const MIN_BLOCK_CHARS = 24

/** The server's request caps, mirrored. Each is a whole-request 422 in
 *  `api/schemas/annotate.py`, so exceeding any one of them costs the page every
 *  annotation rather than the offending block. Keep these in step with it. */
export const MAX_BLOCKS = 400
export const MAX_BLOCK_CHARS = 10_000
export const MAX_TOTAL_CHARS = 300_000
export const MAX_URL_CHARS = 2048

/** One stretch of the collapsed output and where it came from.
 *
 * `collapsed` pieces stand for a whitespace run (or a skipped link) that became
 * a single output character. Their source range is never used: matches are
 * word-bounded, so a match boundary always lands inside a verbatim piece. They
 * exist only to keep the output offsets honest.
 */
export interface Piece {
  node: Text
  srcStart: number
  srcEnd: number
  outStart: number
  outEnd: number
  collapsed: boolean
}

export interface ExtractedBlock {
  id: string
  el: Element
  /** Whitespace-collapsed. This is what goes to `POST /api/annotate`. */
  text: string
  pieces: Piece[]
  /** `[start, end)` output ranges covered by a link, for the server to suppress
   *  spans inside -- a mention inside a link would be two competing click
   *  targets. The same information `markdown_to_blocks` puts in `href` marks. */
  hrefs: [number, number][]
}

const isWhitespace = (ch: string) => ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r' || ch === '\f'

/** Already ours, or somewhere we must not touch. */
function skippable(el: Element): boolean {
  if (SKIP_TAGS.has(el.tagName)) return true
  if (el.hasAttribute('data-venona-ref') || el.hasAttribute('data-venona-v')) return true
  if (el instanceof HTMLElement && el.isContentEditable) return true
  if (el.getAttribute('aria-hidden') === 'true') return true
  // NOTE: `<a href>` is deliberately NOT skipped. Pruning the subtree deleted
  // the link's text from the string we send, which broke matching in both
  // directions: a partly-linked entity ("Golden <a>Dome</a>") stopped matching
  // at all, and the text either side closed up into an adjacency the page does
  // not contain, so the server matched across the gap and we underlined two
  // words that are not next to each other. The brief path has always kept the
  // text and suppressed spans inside `href_ranges` instead; `hrefs` below is
  // that, measured in output offsets.
  return false
}

/** Is this node inside a link, up to (but not past) its block? */
function inHref(node: Node, block: Element): boolean {
  let el = node.parentElement
  while (el && el !== block.parentElement) {
    if (el.tagName === 'A' && el.hasAttribute('href')) return true
    el = el.parentElement
  }
  return false
}

/** Where the article is.
 *
 * Explicit semantics first, because when a page has them they are right. The
 * density fallback exists for the sites that have none, and deliberately scores
 * on paragraph text rather than total text -- a nav with forty links has plenty
 * of the latter.
 */
export function articleRoot(doc: Document = document): Element {
  const explicit =
    doc.querySelector('article') ??
    doc.querySelector('[role="main"]') ??
    doc.querySelector('main')
  if (explicit) return explicit

  let best: Element = doc.body
  let bestScore = 0
  for (const candidate of doc.body.querySelectorAll('div, section')) {
    let score = 0
    for (const p of candidate.querySelectorAll(':scope > p')) {
      score += (p.textContent ?? '').length
    }
    if (score > bestScore) {
      bestScore = score
      best = candidate
    }
  }
  return best
}

/** Nearest block-level ancestor, bounded by the root. */
function blockOf(node: Node, root: Element): Element {
  let el = node.parentElement
  while (el && el !== root.parentElement) {
    if (BLOCK_TAGS.has(el.tagName)) return el
    el = el.parentElement
  }
  return root
}

export function extract(root: Element): ExtractedBlock[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        return skippable(node as Element) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })

  // Gather text nodes per block first. Doing the collapse in a second pass
  // keeps the offset bookkeeping in one place instead of interleaved with tree
  // traversal, which is where this kind of code usually goes wrong.
  const grouped = new Map<Element, Text[]>()
  const order: Element[] = []
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n as Text
    if (!text.data) continue
    const block = blockOf(text, root)
    let bucket = grouped.get(block)
    if (!bucket) {
      bucket = []
      grouped.set(block, bucket)
      order.push(block)
    }
    bucket.push(text)
  }

  const blocks: ExtractedBlock[] = []
  for (const el of order) {
    const nodes = grouped.get(el)!
    const built = collapse(nodes)
    if (built.text.length < MIN_BLOCK_CHARS) continue
    blocks.push({ id: `b${blocks.length}`, el, ...built, hrefs: hrefRanges(built.pieces, el) })
    if (blocks.length >= MAX_BLOCKS) break
  }
  return blocks
}

/** Output ranges covered by a link, merged where they touch.
 *
 * Derived from the pieces rather than tracked during the collapse, because the
 * pieces already carry the node-to-output map and re-deriving it here keeps the
 * collapse doing one thing.
 */
function hrefRanges(pieces: Piece[], block: Element): [number, number][] {
  const ranges: [number, number][] = []
  for (const piece of pieces) {
    if (piece.collapsed || !inHref(piece.node, block)) continue
    const last = ranges[ranges.length - 1]
    if (last && last[1] >= piece.outStart) last[1] = Math.max(last[1], piece.outEnd)
    else ranges.push([piece.outStart, piece.outEnd])
  }
  return ranges
}

/** The collapse, and the map that survives it. */
function collapse(nodes: Text[]): { text: string; pieces: Piece[] } {
  const pieces: Piece[] = []
  let out = ''

  // An open verbatim stretch: 1:1 with its source node.
  let vNode: Text | null = null
  let vSrcStart = 0
  let vOutStart = 0

  // An open whitespace run, which may span nodes.
  let wsNode: Text | null = null
  let wsStart = 0
  let wsEnd = 0

  const closeVerbatim = (srcEnd: number) => {
    if (!vNode) return
    pieces.push({
      node: vNode,
      srcStart: vSrcStart,
      srcEnd,
      outStart: vOutStart,
      outEnd: out.length,
      collapsed: false,
    })
    vNode = null
  }

  const flushWhitespace = () => {
    if (!wsNode) return
    // Leading whitespace produces no output character at all, so there is
    // nothing to map and nothing to emit.
    if (out.length > 0) {
      pieces.push({
        node: wsNode,
        srcStart: wsStart,
        srcEnd: wsEnd,
        outStart: out.length,
        outEnd: out.length + 1,
        collapsed: true,
      })
      out += ' '
    }
    wsNode = null
  }

  for (const node of nodes) {
    const data = node.data
    for (let i = 0; i < data.length; i++) {
      const ch = data[i]
      if (isWhitespace(ch)) {
        closeVerbatim(i)
        if (!wsNode) {
          wsNode = node
          wsStart = i
        }
        // Only extend the run's SOURCE range while we are still inside the node
        // it began in. A run that crosses a node boundary keeps the range it had
        // in the first node; the remainder maps to no output character, and a
        // match spanning that boundary is emitting one span per node anyway.
        if (wsNode === node) wsEnd = i + 1
        continue
      }
      flushWhitespace()
      if (!vNode) {
        vNode = node
        vSrcStart = i
        vOutStart = out.length
      }
      out += ch
    }
    closeVerbatim(data.length)
  }
  // A trailing whitespace run is dropped: it would add a space with nothing
  // after it, and the server would see a block that does not match the text a
  // reader sees.

  return { text: out, pieces }
}

/** Translate an output offset to its DOM position.
 *
 * Returns null for an offset inside a collapsed run, which a word-bounded match
 * boundary can never be -- so a null here means the server returned a span that
 * disagrees with the text we sent, and the caller should drop it rather than
 * guess.
 */
export function locate(pieces: Piece[], outOffset: number): { node: Text; offset: number } | null {
  // Linear scan. A block is tens of pieces; a binary search here would be
  // faster in the same way that a faster answer to a question nobody asks is
  // faster.
  for (const piece of pieces) {
    if (outOffset >= piece.outStart && outOffset < piece.outEnd) {
      if (piece.collapsed) return null
      return { node: piece.node, offset: piece.srcStart + (outOffset - piece.outStart) }
    }
  }
  return null
}

/** Every (node, start, end) covered by an output range.
 *
 * A match can straddle inline markup -- `Lockheed <b>Martin</b>` -- so this
 * returns one entry per text node touched. The caller wraps each separately
 * with the same ref, which draws as one continuous underline and never
 * restructures the page. Extracting a Range across an element boundary would.
 */
export function slice(
  pieces: Piece[],
  outStart: number,
  outEnd: number,
): { node: Text; start: number; end: number }[] {
  const spans: { node: Text; start: number; end: number }[] = []
  for (const piece of pieces) {
    const from = Math.max(outStart, piece.outStart)
    const to = Math.min(outEnd, piece.outEnd)
    if (from >= to) continue

    let start: number
    let end: number
    if (piece.collapsed) {
      // A collapsed run INSIDE the match is the space in `Lockheed Martin`, and
      // it has to be carried or the two halves never join: they would be
      // adjacent spans with a bare gap between them, so the underline would
      // break at every space and no single span would hold the whole surface.
      //
      // A collapsed run at the edge of the match is not ours -- a word-bounded
      // match never starts or ends on whitespace, so an overlap here means the
      // range only clips it.
      if (piece.outStart < outStart || piece.outEnd > outEnd) continue
      start = piece.srcStart
      end = piece.srcEnd
    } else {
      start = piece.srcStart + (from - piece.outStart)
      end = piece.srcStart + (to - piece.outStart)
    }

    const last = spans[spans.length - 1]
    if (last && last.node === piece.node && last.end === start) last.end = end
    else spans.push({ node: piece.node, start, end })
  }
  return spans
}
