/** The one channel out of the page.
 *
 * A long-lived port rather than `chrome.runtime.sendMessage`, because the MV3
 * service worker is terminated after ~30s idle and a connected port is what
 * keeps it alive across a slow request -- including the 180-second run poll a
 * grounding lookup needs.
 */

import { PORT_NAME, type Request, type Response, type Unidentified } from '../shared/protocol'

let port: chrome.runtime.Port | null = null
let nextId = 1
const waiting = new Map<number, (res: Response) => void>()

function connect(): chrome.runtime.Port {
  const opened = chrome.runtime.connect({ name: PORT_NAME })
  opened.onMessage.addListener((res: Response) => {
    waiting.get(res.id)?.(res)
    waiting.delete(res.id)
  })
  opened.onDisconnect.addListener(() => {
    port = null
    // A worker restart is ordinary. Anything still in flight will never be
    // answered, so fail it rather than leak the promise -- a card waiting on a
    // promise that never settles shows a spinner forever.
    for (const [id, resolve] of waiting) {
      resolve({ id, ok: false, error: 'the Venona worker restarted' })
    }
    waiting.clear()
  })
  return opened
}

export function ask(req: Unidentified<Request>): Promise<Response> {
  port ??= connect()
  const id = nextId++
  return new Promise((resolve) => {
    waiting.set(id, resolve)
    try {
      port!.postMessage({ ...req, id } as Request)
    } catch {
      waiting.delete(id)
      resolve({ id, ok: false, error: 'could not reach the Venona worker' })
    }
  })
}
