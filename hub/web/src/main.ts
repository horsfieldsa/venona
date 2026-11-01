/** The hub's operator surface.
 *
 * THE TWO FONT IMPORTS ARE NOT DECORATION. `tokens.css` names
 * `'JetBrains Mono Variable'` and `'Inter Variable'` -- the families the
 * `@fontsource-variable` packages actually declare -- and a surface that names
 * them without shipping them falls through to `ui-monospace` and
 * `ui-sans-serif` while looking deliberate. `web/src/main.ts` records the same
 * failure for `Inter var` and `Charter`, and `extension/src/shared/fonts.ts`
 * records it a second time. Both faces, because the chrome is mono and the
 * explanatory sentences are prose.
 */
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import '@/styles/app.css'

import { createApp } from 'vue'

import App from '@/App.vue'
import { router } from '@/router'

createApp(App).use(router).mount('#app')
