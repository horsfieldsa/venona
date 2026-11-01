import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type Plugin } from 'vite'

import { stripComments } from '../tooling/vite-strip-comments.mjs'

/**
 * Preload the two latin text faces on the built page.
 *
 * Fonts are discovered HTML → CSS → woff2: three serialized hops before the
 * first real glyph, because nothing names a font file until the stylesheet is
 * parsed. `font-display: swap` keeps text visible either way, so what this
 * buys is not paint — it is the fallback→Inter reflow window, cut by starting
 * the two downloads with the HTML. Only the latin normal faces: `unicode-range`
 * means the other subsets may never be fetched at all, and preloading a file
 * the page might not use is bandwidth spent to warm nothing. Filenames are
 * hashed, hence a plugin over a hand-written `<link>`.
 */
function preloadFonts(): Plugin {
  const WANTED = [/inter-latin-wght-normal/, /jetbrains-mono-latin-wght-normal/]
  return {
    name: 'venona:preload-fonts',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const names = Object.keys(ctx.bundle ?? {}).filter(
          (f) => f.endsWith('.woff2') && WANTED.some((re) => re.test(f)),
        )
        return names.map((href) => ({
          tag: 'link',
          // `crossorigin` is required even same-origin: font requests are CORS
          // mode, and a preload with mismatched mode is re-downloaded, making
          // the hint a pure regression.
          attrs: { rel: 'preload', as: 'font', type: 'font/woff2', href: `/${href}`, crossorigin: '' },
          injectTo: 'head',
        }))
      },
    },
  }
}

// A second local instance runs its API on its own port (make dev
// INSTANCE=<slug> exports API_PORT from local/<slug>.env); bare `make dev`
// keeps 8000.
const API = `http://127.0.0.1:${process.env.API_PORT ?? '8000'}`

export default defineConfig({
  plugins: [vue(), tailwindcss(), stripComments(), preloadFonts()],
  build: {
    /* PINNED, and this is not a compatibility preference -- it is what makes
       `light-dark()` mean the same thing in `vite dev` and in `vite build`.
       Vite's default cssTarget is older than `light-dark()`, so Lightning CSS
       POLYFILLS it on build into
           var(--lightningcss-light, A) var(--lightningcss-dark, B)
       with a pair of custom properties stamped onto whichever rule declared
       `color-scheme` -- statically, at build time. The dev server does no such
       lowering and serves the function untouched. So without this line the two
       modes ship different mechanisms for the same tokens, and the build's
       version stops answering to the runtime `color-scheme` cascade that the
       whole theme switch rests on. This project already has one scar from
       `@source` globs resolving differently in dev than in build; that one at
       least rendered visibly wrong.

       These three are `light-dark()`'s own baseline. Raising the floor is the
       point: below it the function is invalid at computed-value time and every
       colour token resolves to nothing. */
    cssTarget: ['chrome123', 'safari17.5', 'firefox120'],
  },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    // IPv4, explicitly. Vite's default is the string `localhost`, which node 17+
    // hands to the resolver and which lands on `[::1]` here — so the dev server
    // listened on IPv6 loopback and `http://127.0.0.1:5173` was CONNECTION
    // REFUSED. A browser hides that (it retries the other family), which is why
    // it went unnoticed; a client that names one literal address does not. The
    // extension is that client: `host_permissions` and its API base are literal
    // strings, and `127.0.0.1` is the literal the rest of this repo uses.
    host: '127.0.0.1',
    proxy: {
      // RFC 9728 and RFC 8414 fix these at the ROOT of the origin, so they are
      // the only non-/api paths the app serves. Without this the dev server
      // returns index.html for them and the OAuth flow cannot be exercised
      // locally at all -- see the matching `handle` block in the reverse proxy config.
      '/.well-known/oauth-protected-resource': { target: API, changeOrigin: true },
      '/.well-known/oauth-authorization-server': { target: API, changeOrigin: true },
      '/api': {
        target: API,
        changeOrigin: false,
        // Contract C6. Two separate SSE hazards, both only visible through a
        // proxy — verifying against uvicorn directly proves neither.
        configure: (proxy) => {
          // 1. `Last-Event-ID` is stripped in transit, so the browser's NATIVE
          //    reconnect silently replays from zero (or, worse, from nothing).
          //    Measured: direct to uvicorn the header arrives and replay works;
          //    through the proxy the reconnect returned an empty body.
          //    Forwarding it explicitly is what makes reconnect lossless.
          proxy.on('proxyReq', (proxyReq, req) => {
            const lastEventId = req.headers['last-event-id']
            if (typeof lastEventId === 'string') {
              proxyReq.setHeader('Last-Event-ID', lastEventId)
            }
          })

          // 2. Response buffering makes a live stream arrive in one lump at the
          //    end, which reads as "the pipeline hung".
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['x-accel-buffering'] = 'no'
            proxyRes.headers['cache-control'] = 'no-cache, no-transform'
          })
        },
      },
    },
  },
})
