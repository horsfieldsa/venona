/**
 * What a signed-in stranger is offered on a domain, in words.
 *
 * THE WIRE CARRIES CODES AND THIS IS THE ONLY PLACE THEY BECOME SENTENCES.
 * `no_price` is a fact about a database column; "nobody has priced it" is a
 * thing a person can act on, and the operator reading this page is a person
 * even though the page is called admin.
 *
 * The blockers arrive from `billing_state.blockers`, which the front door also
 * calls to decide which button to draw. That is what makes this page able to
 * say why the card reads what it reads rather than guessing alongside it.
 */

// CLAUSES THAT JOIN, not sentences. Each one is read after the word "Because"
// and with two others beside it, so anything carrying its own "so ..." reads as
// a run-on the moment a second reason applies.
const REASONS: Record<string, string> = {
  no_payments: 'this hub cannot take payments yet',
  no_price: 'nobody has set what this one costs',
  invite_only: 'new accounts are set to ask you first',
}

/** The button a stranger sees on this domain's card. */
export function offerLabel(blockers: string[]): string {
  return blockers.length ? 'Request access' : 'Start free trial'
}

/**
 * Why, when it is not on sale. Every reason at once rather than the first:
 * told one at a time, fixing each one reveals the next, which is three trips
 * to learn what was always one list.
 */
export function offerReason(blockers: string[]): string {
  const said = blockers.map((code) => REASONS[code]).filter(Boolean)
  if (!said.length) return ''
  if (said.length === 1) return `Because ${said[0]}.`
  return `Because ${said.slice(0, -1).join(', ')} and ${said[said.length - 1]}.`
}

/** A price a person reads, from Stripe's minor units. Empty when unpriced. */
export function priceLabel(amount: number | null, currency: string | null): string {
  if (amount === null || !currency) return ''
  const symbol: Record<string, string> = { usd: '$', gbp: '£', eur: '€' }
  const value = amount / 100
  const body = Number.isInteger(value) ? String(value) : value.toFixed(2)
  const mark = symbol[currency.toLowerCase()]
  return mark ? `${mark}${body}` : `${body} ${currency.toUpperCase()}`
}
