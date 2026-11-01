import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'

import { useConfigDocsStore } from '@/stores/configDocs'
import { entityIdFromRoute } from '@/router/entityRoute'
import { redirectFor } from '@/router/guards'
import { useEntitiesStore } from '@/stores/entities'
import { useSessionStore } from '@/stores/session'
import ArchiveView from '@/views/ArchiveView.vue'
import BriefView from '@/views/BriefView.vue'
import LoginView from '@/views/LoginView.vue'
import LookupView from '@/views/LookupView.vue'
import RequestAccessView from '@/views/RequestAccessView.vue'
import SearchView from '@/views/SearchView.vue'

/*
 * EAGER above, LAZY below, and the line between them is the reader's path.
 *
 * The entry chunk is what every session parses before anything paints, so it
 * carries only the views a READER can reach from a cold load: the brief, the
 * archive, lookup, search and the two public pages. Everything operator-facing
 * -- admin, config, people, data, runs, curation -- plus the account/agents
 * settings pages loads on first navigation instead. `scripts/lint_bundle.py`
 * pins this split: a static view import added to this file fails `make lint`
 * unless the eager list there says so too.
 */

/**
 * Routes are declared up front even where the view is a placeholder, so that
 * links written in earlier stages (the hover card's "Open in graph") already
 * resolve rather than needing rework later.
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // `public` means the auth redirect must not fire here, and `useAppPolling`
    // must not tick here. Declared BEFORE the catch-all below, which redirects
    // everything unmatched to `/` and would otherwise swallow it.
    { path: '/login', name: 'login', component: LoginView, meta: { title: 'Sign in', public: true } },
    // The other public route, and a separate front site's only call to action. It
    // has to be `public` for the same two reasons `/login` is: the 401 hook in
    // `main.ts` must not bounce a stranger who has no session by definition, and
    // `useAppPolling` must not spend their rate-limit budget on health checks
    // behind a page they cannot see. Declared up here with `/login` rather than
    // beside the app routes, because the catch-all at the foot of this list
    // swallows anything declared after it.
    {
      path: '/request-access',
      name: 'request-access',
      component: RequestAccessView,
      meta: { title: 'Request access', public: true },
    },
    { path: '/', name: 'brief', component: BriefView, meta: { title: 'Brief' } },
    // The archive. `GET /briefs` has always returned the full paginated list
    // and `stores/briefs.ts::load(id)` has always existed; neither had a caller
    // until now, so every brief before today's was unreachable in the UI.
    { path: '/briefs', name: 'archive', component: ArchiveView, meta: { title: 'Archive' } },
    { path: '/briefs/:id', name: 'brief-by-id', component: BriefView, meta: { title: 'Brief' } },
    // Lazy for a heavier reason than the operator views: the graph ENGINE --
    // `cytoscape` 133KB gzipped, `layout-base` 32KB, `cose-base` 22KB,
    // `cytoscape-fcose` 13KB -- is about 200KB gzipped, and `useCytoscape.ts`
    // is statically imported by this view, so an eager import here has every
    // text surface downloading and parsing a graph library.
    //
    // `<KeepAlive include="BriefView,GraphView">` in `AppShell.vue` matches by
    // component NAME, and this view is cached deliberately: a cold fcose layout
    // is the most expensive thing the app does, so falling out of the cache
    // would make every return to /graph pay for it and turn this into a net
    // loss. vue-router resolves the module and writes the real component back
    // into the route record before `<RouterView>` renders it, so `include` sees
    // the same object it always did -- and `GraphView.vue` states its own name
    // rather than leaning on the compiler inferring one from the filename.
    {
      path: '/graph',
      name: 'graph',
      component: () => import('@/views/GraphView.vue'),
      meta: { title: 'Graph' },
    },
    // The instance itself: who can reach it, what is waiting, what it has
    // spent. `operator: true`, which the guard at the foot of this file reads --
    // and which `AppHeader::NAV` and `CommandPalette::ROUTES` must MIRROR, or a
    // reader is shown a link to a page the guard bounces them off. That mirror
    // is `tests/test_frontend_drift.py`'s job now rather than a habit.
    { path: '/admin', name: 'admin', component: () => import('@/views/AdminView.vue'), meta: { title: 'Admin', operator: true } },
    // The domain-setup wizard. A fresh instance's admins are redirected here
    // by the guard below (`setup_required` on the session payload); a configured one
    // reaches it from Admin to refine documents. Operator-gated like the rest
    // of /admin -- readers keep the normal app either way.
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/SetupView.vue'),
      meta: { title: 'Setup', operator: true },
    },
    // Everything that belongs to whoever runs this box rather than to whoever
    // reads it: its people, its data, its configuration, and the two surfaces
    // where that instance is worked -- curation and runs. The split is by
    // OWNER, not by sensitivity (ADR 0134), which is what makes it decidable
    // where a new page goes.
    //
    // ORDER DOES NOT MATTER HERE, and that is the point of the nesting. Under
    // `/settings/:key?` a wildcard sat at the same depth as the literal
    // sections, so `/settings/people` was one declaration order away from
    // rendering a config-document editor for a document named "people" -- a
    // trap this file warned about three times because it kept nearly happening.
    // `/admin/config/:key` puts the wildcard one level down behind a static
    // segment, where it cannot reach a sibling; `/admin/runs/:id` is safe for
    // the same reason, one segment deeper.
    //
    // **Never replace these with `/admin/:section?`.** That reintroduces
    // exactly the hazard the nesting removes, and swallows every one of them
    // at once.
    {
      path: '/admin/people',
      name: 'admin-people',
      component: () => import('@/views/PeopleView.vue'),
      // Roles, suspensions and the access queue. Admin for the writes and for
      // the reads alike -- the queue carries addresses and what strangers wrote
      // about themselves.
      meta: { title: 'People · Admin', operator: true },
    },
    {
      path: '/admin/agents',
      name: 'admin-agents',
      component: () => import('@/views/AdminAgentsView.vue'),
      // Every assistant connected to this instance, whoever owns it. Admin for
      // the read as much as the write: the list names who each one belongs to.
      meta: { title: 'Agents · Admin', operator: true },
    },
    {
      path: '/admin/notifications',
      name: 'admin-notifications',
      component: () => import('@/views/NotificationsView.vue'),
      // Admin for the write and for the read alike: the list names every
      // operations channel this box can reach, and a webhook address is a
      // credential even where the hint shown for it is not.
      meta: { title: 'Notifications · Admin', operator: true },
    },
    {
      path: '/admin/data',
      name: 'admin-data',
      component: () => import('@/views/DataView.vue'),
      // Admin: this page holds `POST /api/data/purge` and the corpus counts
      // behind it.
      meta: { title: 'Data · Admin', operator: true },
    },
    {
      path: '/admin/model',
      name: 'admin-model',
      component: () => import('@/views/ModelView.vue'),
      meta: { title: 'Model', operator: true },
    },
    {
      path: '/admin/config',
      name: 'admin-config',
      component: () => import('@/views/ConfigIndexView.vue'),
      // Admin, INCLUDING THE READS, and that is why the index carries the flag
      // as well as the editor: `GET /api/config` is admin, so a reader reaching
      // this page would meet the global forbidden toast rather than a redirect.
      meta: { title: 'Configuration · Admin', operator: true },
    },
    {
      path: '/admin/config/:key',
      name: 'admin-config-doc',
      component: () => import('@/views/ConfigView.vue'),
      meta: {
        // A function, for the same reason the others are strings: twenty
        // history entries all reading "Admin" is the problem `afterEach` exists
        // to solve, not a smaller version of it. The document's TITLE, not its
        // key -- `relevance_profile` is this repo's word for it, and a history
        // menu is read by a person.
        title: (to: RouteLocationNormalized) => {
          const key = String(to.params.key)
          const title = useConfigDocsStore().items.find((d) => d.key === key)?.title
          return `${title ?? 'Configuration'} · Admin`
        },
        // Admin, INCLUDING THE READS. `profile/relevance_profile.md` is the
        // operator's own thesis about what matters and who they compete with,
        // and `GET /api/config/{key}` returns it whole -- so this is not just a
        // question of who may press Save. That property is why these documents
        // could not stay on a page named for the reader's own account.
        //
        // THE PARAM IS NAMED `key` and must stay named `key`:
        // `ConfigView::keyFromRoute` reads `route.params.key` and a `watch`
        // drives document switching off it. Rename it and both go silently
        // inert -- deep links stop working, with no error and no type failure.
        //
        // REQUIRED, not `:key?`. The index owns the bare path now, so an
        // optional param would give two routes the same address and let
        // declaration order decide which renders.
        operator: true,
      },
    },
    // The instance's WORKING surfaces, as against its configuration above. The
    // split that decides where a page goes is by owner, not by sensitivity
    // (ADR 0134): what Venona proposed and what it spent belong to whoever runs
    // this box. Neither is in the header nav -- both are reached from
    // `AdminRail`, which is why `AppHeader::NAV`'s `/admin` entry has to claim
    // all four names below.
    {
      path: '/admin/curation',
      name: 'admin-curation',
      component: () => import('@/views/CurationView.vue'),
      meta: { title: 'Curation · Admin', operator: true },
    },
    {
      path: '/admin/runs',
      name: 'admin-runs',
      component: () => import('@/views/RunsView.vue'),
      meta: { title: 'Runs · Admin', operator: true },
    },
    // `:id` sits behind TWO static segments, and that is what makes it safe --
    // vue-router 4 ranks by score rather than by declaration order, and a
    // static segment outranks a param, so `config` and `runs` are distinct
    // statics and no two records here can claim one address. The warning above
    // covers these as well.
    {
      path: '/admin/runs/:id',
      name: 'admin-run-detail',
      component: () => import('@/views/RunDetailView.vue'),
      meta: { title: 'Run · Admin', operator: true },
    },
    // Cost, per-call latency and per-feed yield are an operator's business
    // rather than a reader's, so this carries the marker its siblings do.
    {
      path: '/admin/runs/:id/metrics',
      name: 'admin-run-metrics',
      component: () => import('@/views/RunMetricsView.vue'),
      meta: { title: 'Run metrics · Admin', operator: true },
    },
    // The glossary as a SET, rather than one term at a time inside a hover card
    // in prose. The undefined acronyms are the interesting half and there was no
    // way to look at them together.
    { path: '/lookup', name: 'lookup', component: LookupView, meta: { title: 'Lookup' } },
    // Lazy: study is opened deliberately, like the guide — it is not on the
    // path to a brief. Cards derive from the corpus and progress belongs to
    // the signed-in reader alone (docs/adr/0157).
    {
      path: '/learn',
      name: 'learn',
      component: () => import('@/views/LearnView.vue'),
      meta: { title: 'Learn' },
    },
    // Search across entities AND brief bodies. The ⌘K palette filters briefs
    // client-side over `/briefs?limit=100`, so it can only ever match a title or
    // a dek; this route uses `/briefs/search`, which reads the body.
    { path: '/search', name: 'search', component: SearchView, meta: { title: 'Search' } },
    // One name, at an address. Lazy, like everything outside the six eager
    // views `scripts/lint_bundle.py` names: the page is reached deliberately,
    // from a card, a search result or a link somebody sent, and it is never on
    // the path to a brief.
    {
      path: '/entity/:type/:slug',
      name: 'entity',
      component: () => import('@/views/EntityView.vue'),
      meta: {
        // A function, for the reason `/admin/config/:key` needs one: the name
        // arrives with the card, after the navigation. Left as a string, every
        // name in the corpus would share one history entry.
        title: (to: RouteLocationNormalized) => {
          const id = entityIdFromRoute(to.params)
          return (id && useEntitiesStore().get(id)?.name) || 'Name'
        },
      },
    },
    // Lazy: 202 blocks of help text is not on the path to a brief, and the
    // guide is opened deliberately rather than passed through.
    {
      path: '/guide',
      name: 'guide',
      component: () => import('@/views/GuideView.vue'),
      meta: { title: 'Guide' },
    },
    // `/settings` is the reader's OWN account, and nothing else. Neither record
    // is marked `operator`: their address, their display name and their agent
    // tokens are exactly what a reader should be able to manage. Data, People
    // and the config documents are admin-only and live under `/admin`.
    //
    // The bare path is a redirect rather than a record: `⌘,` and a typed
    // `/settings` have to land somewhere real, and there is no settings home to
    // land on once the page is two destinations.
    { path: '/settings', redirect: '/settings/account' },
    {
      path: '/settings/account',
      name: 'account',
      component: () => import('@/views/AccountView.vue'),
      meta: { title: 'Account · Settings' },
    },
    // Personal, not operator: what matters to this reader and the names they
    // follow are theirs alone -- the same owner split as display name.
    {
      path: '/settings/relevance',
      name: 'settings-relevance',
      component: () => import('@/views/RelevanceSettingsView.vue'),
      meta: { title: 'Relevance · Settings' },
    },
    // Personal, not operator: where a reader has their own brief sent is
    // theirs alone, and no admin surface reads it -- the same owner split as
    // relevance and display name.
    {
      path: '/settings/notifications',
      name: 'settings-notifications',
      component: () => import('@/views/NotificationSettingsView.vue'),
      meta: { title: 'Notifications · Settings' },
    },
    {
      path: '/settings/agents',
      name: 'settings-agents',
      component: () => import('@/views/AgentsView.vue'),
      meta: { title: 'Agents · Settings' },
    },
    // Lazy, like every settings view: `scripts/lint_bundle.py` computes its
    // lazy roots as the views glob minus a named eager six, so this needs no
    // entry there. A static import here would fail `make lint-bundle`.
    {
      path: '/settings/billing',
      name: 'settings-billing',
      component: () => import('@/views/BillingView.vue'),
      meta: { title: 'Subscription · Settings' },
    },
    // The one-time tailoring invitation for a reader who has not tuned their
    // relevance yet. Reachable directly forever; the guard below only
    // REDIRECTS here once, and skipping is remembered per device.
    {
      path: '/welcome',
      name: 'welcome',
      component: () => import('@/views/WelcomeView.vue'),
      meta: { title: 'Welcome' },
    },
    // Merges and definitions are one job and one view. These redirects keep
    // existing links and any bookmarked filter landing somewhere real.
    { path: '/glossary', redirect: '/admin/curation' },
    // LAST, and it has to be: this pattern matches everything, so any route
    // declared after it is unreachable. A view rather than a redirect, because
    // a bounce to the brief tells the reader nothing and they cannot tell a
    // typo from a stale link. Deliberately NOT `meta.public`: public routes
    // render outside `AppShell`, and a signed-in reader who mistyped wants the
    // nav that gets them back.
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: 'Not found' },
    },
  ],
})

/**
 * The tab title. Nothing set it before, so every entry in the browser's history
 * read "Venona" -- which stopped being tolerable the moment ids entered the
 * address bar (`/briefs/:id`, `/admin/runs/:id`).
 *
 * There is deliberately no `scrollBehavior`: `AppShell`'s content region is
 * `overflow: hidden` and each view owns its own scroller, so `window` never
 * scrolls and vue-router would have nothing to act on. The brief restores its
 * own position via `<KeepAlive>` instead.
 */
/**
 * The app's FIRST navigation guard, and the reason there was none before: a 401
 * is handled by the hook in `main.ts`, which is cheaper than a round trip in
 * front of every route change.
 *
 * This adds no round trip. `useSessionStore` is already populated by the single
 * pre-mount probe in `main.ts`, so the guard reads memory.
 *
 * **UX, NOT ENFORCEMENT.** `api/authz.py` refuses the request server-side, before
 * routing, and defaults an unclassified route to admin. All this does is stop a
 * reader landing on a page whose every panel would 403. A reader who types the
 * URL is still refused by the server, which is where the check belongs, and
 * `tests/test_authz.py` is what proves it.
 *
 * Redirects to the brief rather than to `/login`: the reader IS signed in, and
 * bouncing them to a login page would tell them to fix the one thing that is not
 * wrong.
 */
router.beforeEach((to) => {
  // Every redirect lives in `redirectFor`, together, because the bug they had
  // was an INTERACTION rather than any one of them being wrong. See its
  // docblock. This reads the stores and translates; it decides nothing.
  const session = useSessionStore()
  const target = redirectFor(
    {
      name: String(to.name ?? ''),
      isPublic: Boolean(to.meta.public),
      isOperator: Boolean(to.meta.operator),
    },
    {
      setupRequired: session.setupRequired,
      isAdmin: session.isAdmin(),
      hasSession: session.info !== null,
      billing: session.info?.billing ?? null,
      relevanceTailored: session.relevanceTailored,
      welcomeDismissed: welcomeDismissed(),
    },
  )
  return target === null ? true : { name: target }
})

/** Per-device, so "skip for now" survives reloads without storing anything
 *  server-side about a choice the reader declined to make. */
function welcomeDismissed(): boolean {
  try {
    return localStorage.getItem('venona.welcome.1') !== null
  } catch {
    return true
  }
}

/**
 * The tab title for a route, applied.
 *
 * Exported because a param route's title can depend on data that arrives after
 * the navigation: `/admin/config/:key` names the document, and the list that
 * turns a key into a title is fetched by the view. Calling this again once that
 * lands is what stops every document sharing one fallback title.
 */
export function applyTitle(to: RouteLocationNormalized = router.currentRoute.value): void {
  // A function title, for routes whose identity is in a param. Twenty documents
  // sit behind `/admin/config/:key`; without this every one of them puts an
  // identical entry in the history.
  const declared = to.meta.title as string | ((to: RouteLocationNormalized) => string) | undefined
  const title = typeof declared === 'function' ? declared(to) : (declared ?? '')
  document.title = title ? `${title} · Venona` : 'Venona'
}

router.afterEach((to) => applyTitle(to))
