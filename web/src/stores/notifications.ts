import { defineStore } from 'pinia'
import { computed, ref, type Ref } from 'vue'

import type { components } from '@/api/schema'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import { useSessionStore } from '@/stores/session'

type Notifications = components['schemas']['NotificationsOut']
type ReaderNotifications = components['schemas']['ReaderNotificationsOut']
type Destination = components['schemas']['DestinationOut']
type DeliveryList = components['schemas']['DeliveryList']
type NotificationStatus = components['schemas']['NotificationStatus']
type TestResult = components['schemas']['TestResult']

/**
 * Where the instance's notices go, and where this reader's do.
 *
 * TWO SURFACES, ONE STORE, and they never share a ref. `/api/notifications` is
 * admin and lists the instance's destinations; `/api/preferences/notifications`
 * is the acting reader's own and an admin cannot reach it. Keeping both here
 * means one place holds the shapes and the freshness wiring; keeping the refs
 * apart means neither screen can ever render the other's rows.
 *
 * NOTHING HERE HOLDS A DESTINATION'S ADDRESS. The server does not send one --
 * `hint` is what a screen may show -- so there is no client-side decision to
 * get wrong and no pre-filled edit control to build by accident.
 *
 * `status` is the small answer behind the rail's mark and rides the app's
 * existing poll. It is admin-gated INSIDE the callback rather than at
 * registration, the way `stores/runs.ts` does it: the loop ticks every three
 * seconds during a light run, and an ungated refetch hands every reader a
 * refusal toast for a request they never made.
 */
export const useNotificationsStore = defineStore('notifications', () => {
  const instance = ref<Notifications | null>(null)
  const instanceHistory = ref<DeliveryList | null>(null)
  const mine = ref<ReaderNotifications | null>(null)
  const myHistory = ref<DeliveryList | null>(null)
  const status = ref<NotificationStatus | null>(null)

  /** True while a FIRST load is in flight. A revalidation keeps the last good
   *  rows on screen: blanking a list to re-answer a question it already
   *  answered reads as the page reloading. */
  const loading = ref(false)
  const error = ref<string | null>(null)

  /** Venona has stopped sending somewhere, and the thing that broke is the
   *  telling -- so nothing else in the app would report it. Drives the mark on
   *  the Admin rail and the banner on the page that owns the fix. */
  const strickenOut = computed(() => status.value?.struck_out ?? 0)

  async function loadInstance() {
    const first = instance.value === null
    if (first) loading.value = true
    try {
      // Neither read is an input to the other, so they go together: in series
      // the page waited the sum of two round trips to show either half.
      const [settings, history] = await Promise.all([
        apiGet<Notifications>('/notifications'),
        apiGet<DeliveryList>('/notifications/history'),
      ])
      instance.value = settings
      instanceHistory.value = history
      error.value = null
    } finally {
      loading.value = false
    }
  }

  async function loadMine() {
    const first = mine.value === null
    if (first) loading.value = true
    try {
      const [settings, history] = await Promise.all([
        apiGet<ReaderNotifications>('/preferences/notifications'),
        apiGet<DeliveryList>('/preferences/notifications/history'),
      ])
      mine.value = settings
      myHistory.value = history
      error.value = null
    } finally {
      loading.value = false
    }
  }

  /** How many delivery rows one read carries. The panel is a recent-history
   *  list somebody glances at, not an archive to page through at speed. */
  const HISTORY_PAGE = 50

  const moreInstanceHistory = computed(
    () => (instanceHistory.value?.items.length ?? 0) < (instanceHistory.value?.total ?? 0),
  )
  const moreMyHistory = computed(
    () => (myHistory.value?.items.length ?? 0) < (myHistory.value?.total ?? 0),
  )

  /** Extend a held history by one window.
   *
   *  Appends rather than replacing, so reading further back does not throw away
   *  what is already on screen -- and `total` comes from the new page, because
   *  a delivery landing between two reads should move the count it is counted in.
   */
  async function extend(held: Ref<DeliveryList | null>, path: string) {
    const current = held.value
    if (!current) return
    const next = await apiGet<DeliveryList>(
      `${path}?limit=${HISTORY_PAGE}&offset=${current.items.length}`,
    )
    held.value = { ...next, items: [...current.items, ...next.items] }
  }

  const loadMoreInstanceHistory = () => extend(instanceHistory, '/notifications/history')
  const loadMoreMyHistory = () => extend(myHistory, '/preferences/notifications/history')

  async function refreshStatus() {
    status.value = await apiGet<NotificationStatus>('/notifications/status')
  }

  function base(owner: 'instance' | 'mine'): string {
    return owner === 'instance' ? '/notifications' : '/preferences/notifications'
  }

  async function reload(owner: 'instance' | 'mine') {
    if (owner === 'instance') await loadInstance()
    else await loadMine()
  }

  async function add(
    owner: 'instance' | 'mine',
    body: { kind: 'webhook' | 'email'; label: string; target: string },
  ): Promise<Destination> {
    const created = await apiPost<Destination>(base(owner), body)
    await reload(owner)
    return created
  }

  async function setNotice(
    owner: 'instance' | 'mine',
    id: string,
    noticeType: string,
    on: boolean,
  ) {
    await apiPatch<Destination>(`${base(owner)}/${id}`, { notice_type: noticeType, on })
    await reload(owner)
  }

  async function replaceTarget(id: string, target: string) {
    await apiPut<Destination>(`/notifications/${id}/target`, { target })
    await loadInstance()
  }

  async function clearStrikes(id: string) {
    await apiPost<Destination>(`/notifications/${id}/clear`, {})
    await Promise.all([loadInstance(), refreshStatus()])
  }

  async function sendTest(owner: 'instance' | 'mine', id: string): Promise<TestResult> {
    const result = await apiPost<TestResult>(`${base(owner)}/${id}/test`, {})
    await reload(owner)
    return result
  }

  async function remove(owner: 'instance' | 'mine', id: string) {
    await apiDelete(`${base(owner)}/${id}`)
    await reload(owner)
  }

  // A delivery's whole life is status changes and not one of them moves a row
  // count, which is why the `notifications` domain carries an update-sensitive
  // term as well. Without it the history table would sit stale while every
  // row on it had already been sent.
  onFreshness('notifications', () => {
    if (!useSessionStore().isKnownAdmin()) return
    void refreshStatus()
    if (instance.value !== null) void loadInstance()
  })

  return {
    instance,
    instanceHistory,
    moreInstanceHistory,
    loadMoreInstanceHistory,
    mine,
    myHistory,
    moreMyHistory,
    loadMoreMyHistory,
    status,
    strickenOut,
    loading,
    error,
    loadInstance,
    loadMine,
    refreshStatus,
    add,
    setNotice,
    replaceTarget,
    clearStrikes,
    sendTest,
    remove,
  }
})
