import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

import { stripComments } from '../../tooling/vite-strip-comments.mjs'

/**
 * The hub's operator surface.
 *
 * WHY THE ASSETS DO NOT LIVE UNDER `/admin`. `AdminBearerMiddleware` default-
 * denies everything under that prefix except an exact set of operator paths, so
 * a hashed chunk served from `/admin/assets/` answers 401 and the page arrives
 * unstyled with nothing in the console to explain it. Serving the bundle from
 * `/app/` keeps every asset clear of that gate, and leaves `_is_operator_page`
 * an exact set rather than something widened to let a stylesheet through.
 *
 * NO `@source` GLOB, and that is the point of owning the components here.
 * Tailwind's automatic detection walks this root, so it sees every `.vue` below
 * `src/` without being told. `web/src/styles/theme.css` records what explicit
 * globs cost: they resolved differently in `vite dev` than in `vite build`, and
 * production shipped classes the dev server never generated.
 */
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: '/app/',
  plugins: [vue(), tailwindcss(), stripComments()],
  build: {
    /* Straight into the package the hub ships, rather than into a `dist/` that
       something then has to copy. `routers/brand.py` enumerates this directory
       at import and serves one explicit route per file. */
    outDir: fileURLToPath(new URL('../hub/static/app', import.meta.url)),
    emptyOutDir: true,
    /* The same floor as every other surface, for the same reason: below it
       Lightning CSS polyfills `light-dark()` into a pair of custom properties
       resolved at BUILD time, so the theme switch works in dev and is frozen in
       production. `tests/test_css_target.py` holds the copies equal. */
    cssTarget: ['chrome123', 'safari17.5', 'firefox120'],
  },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5183,
    /* IPv4, explicitly, and the same note as the app's config: Vite's default
       is the string `localhost`, which node 17+ resolves to `[::1]` while the
       hub listens on 127.0.0.1, so the proxy below would refuse every call. */
    host: '127.0.0.1',
    proxy: { '/admin': 'http://127.0.0.1:8180' },
  },
})
