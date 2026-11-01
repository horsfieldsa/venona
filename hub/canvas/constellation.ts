/* The living graph behind the hub's front door.
 *
 * ENHANCEMENT. The page ships a static SVG field that is complete and handsome on its own; this file
 * lays a canvas over it and fades the SVG back only once it is actually
 * running. Scripts off, or `prefers-reduced-motion`, and the visitor keeps the
 * still version rather than a blank rectangle where the artwork was.
 *
 * Canvas rather than more SVG, because sixty nodes drifting, with pulses travelling along the
 * edges between them, is sixty transform writes and a hundred attribute writes
 * per frame through the DOM, against one draw call here. The static field stays
 * SVG precisely because it never moves.
 *
 * NO COLOUR IS NAMED IN THIS FILE. Every value is resolved from the same tokens
 * the rest of the page uses, through a probe element, so the graph follows the
 * theme and a token change reaches it without anyone remembering it exists.
 */
import { reducedMotion } from './motion'

/* Tokens resolve through a probe rather than by reading the custom property
 * off the root. `--entity-org` and friends are `light-dark()` pairs, and what
 * comes back from a custom property is the unresolved function text, which
 * canvas cannot paint. Assigning it to `color` and reading THAT back gets the
 * resolved rgb for whichever half of the pair is in force. */
type Rgb = readonly [number, number, number]

const TOKENS = [
  '--grey-500',
  '--entity-org',
  '--entity-prog',
  '--entity-tech',
  '--entity-plat',
  '--entity-person',
  '--accent-primary',
  '--accent-bright',
] as const

type Token = (typeof TOKENS)[number]
type Palette = Record<Token, Rgb>

function resolvePalette(host: HTMLElement): Palette {
  const probe = document.createElement('span')
  probe.style.display = 'none'
  host.append(probe)
  const out = {} as Record<Token, Rgb>
  for (const name of TOKENS) {
    probe.style.color = `var(${name})`
    const parts = getComputedStyle(probe)
      .color.match(/[\d.]+/g)
      ?.map(Number)
    out[name] = parts && parts.length >= 3 ? [parts[0], parts[1], parts[2]] : [128, 128, 128]
  }
  probe.remove()
  return out
}

/* The one place in this file a colour string is assembled, which is why every
 * call site names a token and an opacity instead of a colour. Canvas accepts
 * neither `color-mix()` nor a custom property, so an alpha has to be composed
 * into the value; the three channels are always a resolved token. */
const shade = (c: Rgb, a: number): string =>
  `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})` /* token-lint: ok -- resolved token, literal alpha */

/* A seeded generator, so the field has the same shape on every load. Layout is
 * artwork here: an unlucky draw that clumps every node into one corner would be
 * a broken-looking page that nobody could reproduce to fix. */
function rng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 0x100000000
  }
}

interface Node {
  x: number
  y: number
  /* Drift is a slow circle around the base position, not a random walk: a walk
   * accumulates and the composition eventually drifts apart. */
  phase: number
  speed: number
  amp: number
  r: number
  colour: Rgb
  degree: number
  /* Counts down after a pulse lands, driving the ring and a brief brighten. */
  lit: number
}

interface Edge {
  a: number
  b: number
  long: boolean
}

interface Pulse {
  edge: number
  /* 0..1 along the edge, always from `from` toward the other end. */
  t: number
  from: number
  speed: number
  hops: number
}

const HUB = ['--entity-org', '--entity-prog', '--entity-tech', '--entity-plat', '--entity-person'] as const

/* THE FIELD HAS ITS OWN COORDINATE SPACE, and it is the still SVG's viewBox.
 *
 * Everything below is laid out and drawn in these units -- positions, radii,
 * line widths, drift amplitudes -- and `fit()` scales and centres the lot to
 * cover whatever viewport it is given, which is `xMidYMid slice` done in
 * arithmetic instead of by the SVG engine. Two things fall out of that. The
 * graph is ONE drawing, identical on every page and at every size, so crossing
 * between the properties does not rearrange it; and the two layers frame
 * themselves by the same rule, so the canvas sits exactly where the still it
 * replaces did.
 *
 * The count is fixed for the same reason. Deriving it from viewport area made
 * a scrollbar's worth of width a different number of nodes.
 */
const FIELD_W = 1600
const FIELD_H = 1000
const COUNT = 67

/* The class names come from the caller so the stylesheet that owns them is
 * the only place they are spelled. */
export interface ConstellationClasses {
  /** Set on the host once the canvas is drawing; fades the still SVG out. */
  live: string
  /** The canvas element's own class. */
  canvas: string
}

export function initConstellation(
  host: HTMLElement | null,
  classes: ConstellationClasses,
): (() => void) | undefined {
  if (!host || reducedMotion()) return undefined
  const canvas = document.createElement('canvas')
  canvas.className = classes.canvas
  canvas.setAttribute('aria-hidden', 'true')
  const ctx = canvas.getContext('2d')
  if (!ctx) return undefined
  host.append(canvas)

  let palette = resolvePalette(host)
  let nodes: Node[] = []
  let edges: Edge[] = []
  let pulses: Pulse[] = []
  let raf = 0
  let last = 0
  let sinceSpawn = 0

  /* The canvas is sized and FRAMED here, and nothing else happens: the graph
   * itself does not depend on the viewport at all. `scale` and the two offsets
   * are `xMidYMid slice` written out in numbers -- cover, then centre -- which
   * is the same rule the still SVG's `preserveAspectRatio` follows, so the two
   * layers crop and scale as one drawing rather than as two. */
  function fit(): void {
    const rect = host!.getBoundingClientRect()
    const w = Math.max(1, rect.width)
    const h = Math.max(1, rect.height)
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    const scale = Math.max(w / FIELD_W, h / FIELD_H)
    const ox = (w - FIELD_W * scale) / 2
    const oy = (h - FIELD_H * scale) / 2
    ctx!.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * ox, dpr * oy)
  }

  /* Laid out ONCE, in the field's own coordinates, and never again. Sizing the
   * graph to the viewport made it a different graph on every page: a 15px
   * scrollbar on one page and not the next moved every node and changed the
   * node count, and 54px of width flipped the grid from 11x6 to 10x7, which is
   * not a shift but a different drawing. A reader crossing between the
   * properties saw the graph rearrange itself under them. */
  function layout(): void {
    const random = rng(0x5eed)
    const cols = Math.max(2, Math.round(Math.sqrt(COUNT * (FIELD_W / FIELD_H))))
    const rows = Math.max(2, Math.ceil(COUNT / cols))
    const cw = FIELD_W / cols
    const ch = FIELD_H / rows

    /* A jittered grid rather than free placement: it fills the field evenly at
     * any aspect ratio, and the jitter is what stops it reading as a grid. */
    nodes = []
    for (let iy = 0; iy < rows; iy++) {
      for (let ix = 0; ix < cols; ix++) {
        if (nodes.length >= COUNT) break
        nodes.push({
          x: (ix + 0.5) * cw + (random() - 0.5) * cw * 0.72,
          y: (iy + 0.5) * ch + (random() - 0.5) * ch * 0.72,
          phase: random() * Math.PI * 2,
          speed: 0.06 + random() * 0.09,
          amp: 3 + random() * 7,
          r: 0,
          colour: palette['--grey-500'],
          degree: 0,
          lit: 0,
        })
      }
    }

    /* Each node reaches for its two nearest neighbours. Nearest-neighbour
     * rather than a radius: a radius produces a dense middle and a bare edge
     * on any non-square viewport. */
    const seen = new Set<string>()
    edges = []
    const link = (a: number, b: number, long: boolean): void => {
      const key = a < b ? `${a}:${b}` : `${b}:${a}`
      if (a === b || seen.has(key)) return
      seen.add(key)
      edges.push({ a, b, long })
      nodes[a].degree++
      nodes[b].degree++
    }
    for (let i = 0; i < nodes.length; i++) {
      const near = nodes
        .map((n, j) => ({ j, d: (n.x - nodes[i].x) ** 2 + (n.y - nodes[i].y) ** 2 }))
        .filter((c) => c.j !== i)
        .sort((p, q) => p.d - q.d)
        .slice(0, 2)
      for (const c of near) link(i, c.j, false)
    }
    /* A handful of long reaches across the field, drawn dashed. They are what
     * stops it reading as a mesh and starts it reading as a graph. */
    const hubs = nodes
      .map((n, i) => ({ i, d: n.degree }))
      .sort((p, q) => q.d - p.d)
      .slice(0, 10)
    for (let k = 0; k + 1 < hubs.length; k += 2) link(hubs[k].i, hubs[k + 1].i, true)

    for (const [i, n] of nodes.entries()) {
      const hub = n.degree >= 4
      n.r = hub ? 3.4 + Math.min(n.degree, 8) * 0.42 : 1.5 + n.degree * 0.3
      n.colour = hub ? palette[HUB[i % HUB.length]] : palette['--grey-500']
    }
    pulses = []
  }

  const adjacent = (node: number, notEdge: number): number[] =>
    edges.reduce<number[]>((acc, e, i) => {
      if (i !== notEdge && (e.a === node || e.b === node)) acc.push(i)
      return acc
    }, [])

  function spawn(): void {
    if (!edges.length || pulses.length > 7) return
    const i = Math.floor(Math.random() * edges.length)
    pulses.push({
      edge: i,
      t: 0,
      from: Math.random() < 0.5 ? edges[i].a : edges[i].b,
      speed: 0.32 + Math.random() * 0.34,
      hops: 2 + Math.floor(Math.random() * 3),
    })
  }

  /* Live position: base plus a slow circle. Kept as a function so the edges and
   * the pulses read exactly the same numbers the nodes are drawn at. */
  const px = (n: Node, t: number): number => n.x + Math.cos(t * n.speed + n.phase) * n.amp
  const py = (n: Node, t: number): number => n.y + Math.sin(t * n.speed * 0.8 + n.phase) * n.amp

  function frame(now: number): void {
    raf = requestAnimationFrame(frame)
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0
    last = now
    const t = now / 1000

    sinceSpawn += dt
    if (sinceSpawn > 0.55) {
      sinceSpawn = 0
      spawn()
    }

    /* Cleared in DEVICE space. The drawing transform scales and offsets, so
     * `clearRect(0, 0, w, h)` would leave the cropped margins painted. */
    ctx!.save()
    ctx!.setTransform(1, 0, 0, 1, 0, 0)
    ctx!.clearRect(0, 0, canvas.width, canvas.height)
    ctx!.restore()

    /* Edges first, and faintly. They are the texture the nodes sit in. */
    for (const e of edges) {
      const a = nodes[e.a]
      const b = nodes[e.b]
      const ax = px(a, t)
      const ay = py(a, t)
      const bx = px(b, t)
      const by = py(b, t)
      const glow = Math.max(a.lit, b.lit)
      ctx!.beginPath()
      ctx!.setLineDash(e.long ? [4, 5] : [])
      ctx!.lineWidth = e.long ? 0.8 : 1
      ctx!.strokeStyle = glow > 0
        ? shade(palette['--accent-primary'], 0.1 + glow * 0.3)
        : shade(palette['--grey-500'], e.long ? 0.16 : 0.26)
      ctx!.moveTo(ax, ay)
      ctx!.lineTo(bx, by)
      ctx!.stroke()
    }
    ctx!.setLineDash([])

    /* Pulses: a bright head with a short tail, travelling one edge at a time
     * and hopping to an adjoining edge on arrival. The hop is what makes it
     * read as something propagating through the graph rather than as
     * decoration blinking on and off. */
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i]
      const e = edges[p.edge]
      const from = nodes[p.from]
      const to = nodes[p.from === e.a ? e.b : e.a]
      p.t += dt * p.speed
      if (p.t >= 1) {
        const landed = p.from === e.a ? e.b : e.a
        nodes[landed].lit = 1
        const next = adjacent(landed, p.edge)
        if (p.hops > 1 && next.length) {
          p.edge = next[Math.floor(Math.random() * next.length)]
          p.from = landed
          p.t = 0
          p.hops--
        } else {
          pulses.splice(i, 1)
        }
        continue
      }
      const fx = px(from, t)
      const fy = py(from, t)
      const tx = px(to, t)
      const ty = py(to, t)
      const hx = fx + (tx - fx) * p.t
      const hy = fy + (ty - fy) * p.t
      const tail = Math.max(0, p.t - 0.22)
      ctx!.beginPath()
      ctx!.lineWidth = 1.6
      ctx!.strokeStyle = shade(palette['--accent-bright'], 0.5)
      ctx!.moveTo(fx + (tx - fx) * tail, fy + (ty - fy) * tail)
      ctx!.lineTo(hx, hy)
      ctx!.stroke()
      ctx!.beginPath()
      ctx!.fillStyle = shade(palette['--accent-primary'], 0.95)
      ctx!.arc(hx, hy, 1.9, 0, Math.PI * 2)
      ctx!.fill()
    }

    for (const n of nodes) {
      const x = px(n, t)
      const y = py(n, t)
      if (n.lit > 0) {
        n.lit = Math.max(0, n.lit - dt * 1.15)
        /* The ring expands as the light decays, so the arrival reads as an
         * event at the node rather than the node simply changing colour. */
        ctx!.beginPath()
        ctx!.lineWidth = 1
        ctx!.strokeStyle = shade(palette['--accent-primary'], n.lit * 0.5)
        ctx!.arc(x, y, n.r + (1 - n.lit) * 16, 0, Math.PI * 2)
        ctx!.stroke()
      }
      ctx!.beginPath()
      ctx!.fillStyle = n.lit > 0
        ? shade(palette['--accent-primary'], 0.5 + n.lit * 0.5)
        : shade(n.colour, 0.62)
      ctx!.arc(x, y, n.r, 0, Math.PI * 2)
      ctx!.fill()
    }
  }

  /* The class is what fades the static SVG out. Set only now, so a browser that
   * never gets here keeps the artwork it was served.
   *
   * The first frame is drawn SYNCHRONOUSLY, before the class, because the class
   * starts the still fading and a canvas with nothing on it yet is a field that
   * empties. In a background tab that is not a flicker but the resting state:
   * Chrome parks `requestAnimationFrame` until the tab is looked at, so the
   * still fades to zero over a callback that never comes. `frame` schedules the
   * next one itself, which is why nothing is scheduled here. */
  fit()
  layout()
  frame(performance.now())
  host.classList.add(classes.live)

  /* A resize re-frames and does NOT re-lay: the graph is the same graph, shown
   * through a different window onto it. Debounced because `fit()` reallocates
   * the backing store, which a drag would otherwise do sixty times a second. */
  let resizeTimer = 0
  const onResize = (): void => {
    window.clearTimeout(resizeTimer)
    resizeTimer = window.setTimeout(fit, 160)
  }
  window.addEventListener('resize', onResize)

  /* The theme can change under a running canvas, from the footer toggle or from
   * the operating system. Both rebuild the palette; neither relays the graph,
   * because the shape has nothing to do with the colours. */
  const repaint = (): void => {
    palette = resolvePalette(host)
    for (const [i, n] of nodes.entries()) {
      n.colour = n.degree >= 4 ? palette[HUB[i % HUB.length]] : palette['--grey-500']
    }
  }
  const scheme = window.matchMedia('(prefers-color-scheme: dark)')
  scheme.addEventListener('change', repaint)
  const observer = new MutationObserver(repaint)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })

  return () => {
    cancelAnimationFrame(raf)
    window.removeEventListener('resize', onResize)
    scheme.removeEventListener('change', repaint)
    observer.disconnect()
    canvas.remove()
    host.classList.remove(classes.live)
  }
}
