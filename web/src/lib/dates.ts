/**
 * Absolute instants, rendered for a reader. Elapsed and relative time live in
 * `lib/duration.ts`.
 *
 * All return `''` for unparseable input: every call site is a template
 * interpolation, and `Invalid Date` is worse than nothing.
 */
import { ref } from 'vue'

/** `undefined` locale = the reader's. */
const LOCALE = undefined

/**
 * The reader's stored zone override, or `null` for whatever their browser
 * reports. Set once from the session probe before mount, and again when they
 * save a new one.
 *
 * A `ref` rather than a plain variable so a save re-renders every label in the
 * app without a reload: these formatters are called from templates, so reading
 * it happens inside a render effect and the dependency is tracked like any
 * other. `undefined` is already `Intl`'s "this browser's zone", so the fallback
 * needs no branch at any call site.
 */
export const zone = ref<string | null>(null)

function tz(): string | undefined {
  return zone.value ?? undefined
}

/**
 * `Intl.DateTimeFormat` is expensive to construct and cheap to reuse, and the
 * archive re-renders every row on every keystroke of its filter. Keyed on the
 * zone as well as the options, so saving a new zone cannot serve stale
 * formatters; the map is tiny because both parts come from this file.
 */
const formatters = new Map<string, Intl.DateTimeFormat>()
//: `isoDayLabel` pins `en-CA` rather than the reader's locale, so it cannot
//: share the cache above, which is keyed on this file's options alone.
const isoFormatters = new Map<string, Intl.DateTimeFormat>()

function formatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${zone.value ?? ''}|${JSON.stringify(options)}`
  let found = formatters.get(key)
  if (!found) {
    found = new Intl.DateTimeFormat(LOCALE, { ...options, timeZone: tz() })
    formatters.set(key, found)
  }
  return found
}

function parsed(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** "Mar 4, 2:15 PM". For brief items, which can share a day. */
export function dayTimeLabel(value?: string | null): string {
  const date = parsed(value)
  return date
    ? formatter({ month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
    : ''
}

/**
 * "4 March 2026" — a date inside a SENTENCE, in the reader's zone.
 *
 * Carries the year because the sentences it appears in are about money and can
 * name a date a year old ("your subscription ended on ..."), and spells the
 * month because `isoDayLabel`'s "2026-03-04" reads as a serial number when it
 * sits mid-clause. `dayLabel` drops the year and cannot be used for this.
 */
export function longDate(value?: string | null): string {
  const date = parsed(value)
  return date
    ? formatter({ day: 'numeric', month: 'long', year: 'numeric' }).format(date)
    : ''
}

/** "Mar 4". For an entity card's source list, under a headline. */
export function dayLabel(value?: string | null): string {
  const date = parsed(value)
  return date ? formatter({ month: 'short', day: 'numeric' }).format(date) : ''
}

/**
 * "2026-08-06" — the instant's date IN THE READER'S ZONE, ISO-shaped.
 *
 * Not `dayLabel`, which drops the year: the sources under a brief routinely
 * span months, and on an archived brief they span years. ISO order also keeps
 * these columns aligned under `--font-mono`, which "6 Aug" would not.
 *
 * `en-CA` rather than assembling parts by hand — its short numeric date is
 * already `YYYY-MM-DD`, so there is no zero-padding to get wrong.
 */
export function isoDayLabel(value?: string | null): string {
  const date = parsed(value)
  if (!date) return ''
  let iso = isoFormatters.get(zone.value ?? '')
  if (!iso) {
    iso = new Intl.DateTimeFormat('en-CA', { timeZone: tz() })
    isoFormatters.set(zone.value ?? '', iso)
  }
  return iso.format(date)
}

/**
 * "09:14". Bare, and `h23` so every row is the same width under
 * `tabular-nums` — the same choice `QuotaWindows.vue` made.
 *
 * NO ZONE SUFFIX. The zone is stated once per surface (a column header, a
 * dateline) rather than once per row: repeated on every row it is noise, and
 * it breaks the archive's 4.5rem time column.
 */
export function timeLabel(value?: string | null): string {
  const date = parsed(value)
  return date
    ? formatter({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)
    : ''
}

/**
 * "PDT", or "GMT+12" — the reader's zone, for the one place per surface that
 * names it.
 *
 * `short` gives a true abbreviation only where the locale's data has one: US
 * zones render `PDT`, and London, Auckland and Tokyo render `GMT+1`, `GMT+12`,
 * `GMT+9`. That is the right trade for a compact identity line. The readable
 * alternative, `shortGeneric`, spells those "United Kingdom Time" and "New
 * Zealand Time" — too long for the bar, and it drops the DST distinction that
 * makes `PDT` worth more than `PT`.
 *
 * Read off a formatted part rather than appended as a string, so it is the
 * zone `Intl` actually rendered in and follows the date it is shown beside
 * across a DST boundary. Empty when the runtime will not name it, which is a
 * state every call site renders as simply nothing.
 */
export function zoneAbbrev(at?: string | null): string {
  const date = parsed(at) ?? new Date()
  try {
    const parts = formatter({ timeZoneName: 'short' }).formatToParts(date)
    return parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
  } catch {
    return ''
  }
}

/** Thousands separators, so two tables cannot format a count differently. */
export function count(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString(LOCALE)
}
