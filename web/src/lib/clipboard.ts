/** Copy text, and say whether it worked.
 *
 * The first clipboard helper in the tree. `navigator.clipboard` is undefined on
 * a page served over plain http from anything other than localhost, which is a
 * real configuration for a venona instance on a LAN — so the caller has to be
 * able to tell "copied" from "select it yourself", and a helper that swallowed
 * the failure would leave a toast claiming success over an empty clipboard.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    // The DOM lib types `navigator.clipboard` as always present, and it is not:
    // the property is absent outside a secure context, so the type is a lie the
    // linter believes. `in` rather than a truthiness check, which ESLint would
    // (correctly, given the type) call always-falsy.
    if (!('clipboard' in navigator)) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Permission refused, or a non-secure context. Either way the honest answer
    // is the same one.
    return false
  }
}
