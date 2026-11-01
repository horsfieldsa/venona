// Applied before first paint so the shell never flashes the wrong theme.
//
// A CLASSIC SCRIPT IN A FILE. In a file so the CSP stays `script-src 'self'`
// with no nonce or hash; classic so it is still render-blocking. Do not add
// `defer` or `type="module"`: either one restores the flash.
//
// IT DELETES AS WELL AS SETS. `tokens.css` treats the ABSENCE of `data-theme`
// as System, so a script that only sets can never return a reader to it.
//
// READ-ONLY. `stores/theme.ts` owns every write, including healing the cookie
// from the mirror. The cookie can be shared across subdomains; the shared
// channel, the precedence, and why the mirror is not an ITP backstop are all
// in docs/adr/0177.
;(() => {
  const KEY = 'venona.theme'
  const accepted = (value) => value === 'light' || value === 'dark' || value === 'system'

  let choice = null
  // Two guards, not one: `document.cookie` throws in a sandboxed frame, and the
  // cookie is read first, so a shared catch would skip the mirror entirely.
  try {
    // LAST match, not first. RFC 6265 orders same-name cookies by longer path
    // then EARLIER creation, so a stale host-only cookie sorts ahead of the
    // shared one.
    for (const pair of document.cookie.split(';')) {
      const eq = pair.indexOf('=')
      if (eq > 0 && pair.slice(0, eq).trim() === KEY) {
        const value = pair.slice(eq + 1).trim()
        if (accepted(value)) choice = value
      }
    }
  } catch (_) {
    /* cookies unreadable here; the mirror is the fallback */
  }
  if (choice === null) {
    try {
      const stored = localStorage.getItem(KEY)
      if (accepted(stored)) choice = stored
    } catch (_) {
      /* private mode; System is the right answer when we cannot remember */
    }
  }

  if (choice === 'light' || choice === 'dark') {
    document.documentElement.dataset.theme = choice
  } else {
    delete document.documentElement.dataset.theme
  }
})()
