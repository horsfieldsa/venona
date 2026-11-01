import { createRouter, createWebHistory } from 'vue-router'

/** The operator's destinations, in one table.
 *
 * IT IS THE SAME LIST THE RAIL RENDERS AND THE SAME LIST THE SERVER GATES.
 * `hub/hub/middleware.py`'s `OPERATOR_PAGES` names every path that reaches the
 * session gate, as an exact set rather than a prefix, and the server must serve
 * this bundle at each one or a refresh on a deep link answers 404. Three copies
 * agree by test (`tests/hub/test_ops.py`), not by memory.
 */
export const NAV = [
  { key: 'overview', path: '/admin', label: 'Overview' },
  { key: 'domains', path: '/admin/domains', label: 'Domains' },
  { key: 'requests', path: '/admin/requests', label: 'Access requests' },
  { key: 'launch', path: '/admin/launch', label: 'Waiting for launch' },
  { key: 'accounts', path: '/admin/accounts', label: 'Accounts' },
  { key: 'settings', path: '/admin/settings', label: 'Settings' },
  { key: 'notifications', path: '/admin/notifications', label: 'Notifications' },
] as const

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/admin', name: 'overview', component: () => import('@/views/OverviewView.vue') },
    { path: '/admin/domains', name: 'domains', component: () => import('@/views/DomainsView.vue') },
    { path: '/admin/requests', name: 'requests', component: () => import('@/views/RequestsView.vue') },
    { path: '/admin/launch', name: 'launch', component: () => import('@/views/LaunchView.vue') },
    { path: '/admin/accounts', name: 'accounts', component: () => import('@/views/AccountsView.vue') },
    { path: '/admin/settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
    {
      path: '/admin/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
    },
  ],
})

/** `<title>` per destination, the app's own convention: `<name> · Venona`. */
router.afterEach((to) => {
  const entry = NAV.find((item) => item.path === to.path)
  document.title = entry ? `${entry.label} · Venona` : 'Admin · Venona'
})
