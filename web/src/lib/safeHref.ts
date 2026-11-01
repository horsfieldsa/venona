/**
 * The scheme check on every URL this app renders as an `href`.
 *
 * The server drops a non-http(s) URL out of the payload already, and this is
 * the second layer rather than the first. It is worth having because the
 * failure it prevents -- a `javascript:` URL executing in the reader's own
 * session, with their cookie -- is the one where a single miss anywhere costs
 * everything, and the values reaching these links come from fetched documents,
 * model output and a curator's paste box rather than from us.
 *
 * Returns `undefined` rather than `''` for a URL that must not be followed:
 * Vue omits the attribute entirely for `undefined`, and an `<a href="">` is a
 * link to the current page, which looks like a working link and is not one.
 */
const ALLOWED = ['http:', 'https:']

export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  // Control characters and whitespace first: a browser strips them before it
  // parses, so `java\nscript:alert(1)` is `javascript:alert(1)` by the time it
  // runs, and any URL containing one is not what it appears to be.
  if (/[\u0000-\u0020\u007f]/.test(url)) return undefined
  try {
    return ALLOWED.includes(new URL(url).protocol) ? url : undefined
  } catch {
    // Not absolute. Nothing this app renders should be relative, so an
    // unparseable value means something upstream is wrong.
    return undefined
  }
}
