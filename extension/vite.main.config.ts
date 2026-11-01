import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import { stripComments } from '../tooling/vite-strip-comments.mjs'

/** Service worker and options page.
 *
 * Both may be ES modules -- the worker is declared `"type": "module"` in the
 * manifest -- so unlike the content script they can share chunks. `emptyOutDir`
 * is false because `vite.content.config.ts` runs first and owns the directory.
 */
export default defineConfig({
  plugins: [stripComments()],
  resolve: {
    alias: { '@web': fileURLToPath(new URL('../web/src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    // Pinned to `light-dark()`'s own baseline, and it must stay in step with
    // `web/vite.config.ts` -- that file carries the reasoning, and
    // `tests/test_css_target.py` fails if the two drift. Without it Lightning
    // CSS polyfills `light-dark()` on build and leaves it alone in dev, so the
    // shadow root's tokens would answer to a build-time snapshot of
    // `color-scheme` rather than to the runtime cascade.
    cssTarget: ['chrome123', 'safari17.5', 'firefox120'],
    rollupOptions: {
      input: {
        background: fileURLToPath(new URL('./src/background.ts', import.meta.url)),
        // At the project root, not beside its script: Vite emits HTML at a path
        // relative to the config root, and the manifest wants `options.html` at
        // the top of `dist`.
        options: fileURLToPath(new URL('./options.html', import.meta.url)),
      },
      output: {
        format: 'es',
        entryFileNames: '[name].js',
        chunkFileNames: 'chunk-[name].js',
        assetFileNames: '[name][extname]',
      },
    },
  },
})
