import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

/**
 * Unit tests only, and deliberately so.
 *
 * `scripts/ui_smoke.py` drives the real app against the real database and is the
 * right tool for "does this render". It is CI-only and it WRITES, so it cannot
 * be fast, free or safe against a working corpus.
 *
 * So this covers what typecheck cannot see and ui-smoke should not have to: an
 * out-of-order response overwriting a newer one, a timeout that never fires, a
 * POST that gets retried. Pure logic, no browser, no server.
 *
 * THE ONE EXCEPTION, kept narrow. `environment: 'node'` has no `document`, so a
 * composable that installs a listener on it, reads `offsetParent` or calls
 * `.focus()` cannot be tested at all -- and `useFocusTrap` shipped a real leak
 * fix with nothing able to prove it. `ui_smoke` is no fallback there: it could
 * not have seen that one either, because the leaked listener early-returns on a
 * null container and changes nothing observable.
 *
 * So `happy-dom` is a devDependency and the few files that need it opt in with a
 * `// @vitest-environment happy-dom` docblock. Per-file rather than
 * `environmentMatchGlobs`: that option is deprecated in vitest 3, and the reason
 * belongs next to the code that needs it. The `.dom.spec.ts` suffix is
 * convention, not mechanism.
 *
 * NOT an opening for component tests -- there is no `@vue/test-utils`, and "does
 * it render" is still `ui_smoke`'s job.
 */
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.spec.ts'],
  },
})
