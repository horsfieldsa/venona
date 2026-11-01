import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'

/**
 * The theme-sync content script, built alone.
 *
 * Its own config rather than a second input on `vite.content.config.ts`, and
 * that is forced rather than chosen: that build sets `inlineDynamicImports`,
 * which Rollup rejects outright with more than one input, and it pulls in Vue,
 * Tailwind and the whole card. This script is ~40 lines and must stay that way
 * -- it runs on venona's OWN origin, where the splicer deliberately does not,
 * and the cheapest way to keep those two apart is to give them no shared build.
 *
 * IIFE, like `content.js`: a content script is not a module and `type: module`
 * is not available to one.
 */
export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    // Pinned to `light-dark()`'s own baseline; see `web/vite.config.ts` for why,
    // and `tests/test_css_target.py` for what holds the copies together. This
    // bundle ships no CSS today, and carries the pin anyway so that adding one
    // is not a silent downgrade.
    cssTarget: ['chrome123', 'safari17.5', 'firefox120'],
    rollupOptions: {
      input: fileURLToPath(new URL('./src/content/theme.ts', import.meta.url)),
      output: {
        format: 'iife',
        entryFileNames: 'theme.js',
        inlineDynamicImports: true,
      },
    },
  },
})
