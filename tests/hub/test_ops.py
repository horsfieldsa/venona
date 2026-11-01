"""The operator pages: the gate, the triage writes, and the /admin split."""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi.routing import APIRoute
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo
from hub.routers import ops, ops_api

from hub import ids, middleware, security

REPO = Path(__file__).resolve().parents[2]

OPERATOR = "op@example.com"  # conftest's hub_settings names this one


def _sign_in(hub_client, hub_settings, email: str) -> None:
    token, _ = security.mint_session(hub_settings, email=email, now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")


def _seed_instance(conn, slug="acme", name="Acme Watch") -> str:
    instance_id = ids.instance_id()
    instances_repo.create(
        conn,
        instance_id=instance_id,
        slug=slug,
        display_name=name,
        tagline=f"the {slug} watch",
        public_url=f"https://{slug}.example",
        oidc_client_id=ids.client_id(),
        oidc_client_secret_hash="x",
        mgmt_token_sealed="y",
    )
    return instance_id


def _submit_request(
    conn, email: str, note: str = "", wanted: str | None = None, kind: str = "access"
) -> str:
    request_id = ids.request_id()
    requests_repo.submit(
        conn,
        request_id=request_id,
        email=email,
        note=note,
        wanted_instance_id=wanted,
        kind=kind,
    )
    return request_id


def _page_routes() -> list[tuple[str, str]]:
    found = [
        (method, route.path)
        for route in ops.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    ]
    assert found, "the ops router mounts nothing -- the router is broken"
    return found


def _data_routes() -> list[tuple[str, str]]:
    found = [
        (method, route.path)
        for route in ops_api.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    ]
    assert found, "the ops data router mounts nothing -- the router is broken"
    return found


def _admin_routes(hub_app) -> list[tuple[str, str]]:
    found = []

    def walk(routes) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                if route.path.startswith("/admin"):
                    for method in route.methods - {"HEAD", "OPTIONS"}:
                        found.append((method, route.path))
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)

    walk(hub_app.routes)
    assert found, "no /admin routes mounted -- the walk is broken, not the app"
    return found


async def test_every_page_route_refuses_non_operators(hub_client, hub_settings):
    """The born-guarded sweep: EVERY route in the ops router -- including one
    added after this test was written -- bounces the signed-out and 404s the
    signed-in non-operator. A route answering anything else has escaped the
    router-level gate."""
    for method, path in _page_routes():
        url = path.replace("{request_id}", "req:whatever")

        hub_client.cookies.clear()
        anonymous = await hub_client.request(method, url)
        assert anonymous.status_code == 302, f"{method} {url}: {anonymous.status_code}"
        assert anonymous.headers["location"] == "/?next=/admin"

        _sign_in(hub_client, hub_settings, "stranger@example.com")
        refused = await hub_client.request(method, url)
        assert refused.status_code == 404, f"{method} {url}: {refused.status_code}"
        assert "There is nothing at this address." in refused.text


async def test_every_data_route_refuses_non_operators(hub_client, hub_settings):
    """⭐ The same born-guarded sweep as the pages, for the surface the operator
    app actually calls -- including a route added after this test was written.

    The refusals differ from the pages' on purpose and both halves matter. A
    signed-out caller gets 401 rather than a 302, because a redirect reaches
    `fetch` as an opaque hop to HTML that the client cannot tell from success.
    A signed-in non-operator gets the same 404 a stranger gets, because whether
    this surface exists for an email is not something they get to learn.
    """
    for method, path in _data_routes():
        url = path.replace("{request_id}", "req:whatever")

        hub_client.cookies.clear()
        anonymous = await hub_client.request(method, url)
        assert anonymous.status_code == 401, f"{method} {url}: {anonymous.status_code}"
        assert anonymous.json()["detail"]["error"] == "unauthorized", url

        _sign_in(hub_client, hub_settings, "stranger@example.com")
        refused = await hub_client.request(method, url)
        assert refused.status_code == 404, f"{method} {url}: {refused.status_code}"
        assert refused.json()["detail"]["error"] == "not_found", url
        # Byte for byte what a stranger reads, so the difference tells them
        # nothing about whether the surface is there.
        assert refused.json()["detail"]["message"] == "There is nothing at this address."


def test_the_operator_surface_has_a_named_way_out() -> None:
    """⭐ A signed-in operator must be able to leave admin without the URL bar.

    THIS IS A REGRESSION TEST, and the regression was mine. The server-rendered
    pages carried `Portal · Sign out` in a footer row. That row went when the
    surface became a bundle and the way back moved onto the lockup -- which is
    the same destination and not an affordance anybody looks for, so in practice
    there was no way out at all.

    A NAMED LINK, not just the mark. The check is for a hyperlink whose own text
    says where it goes; a `router-link` would not do, because `/` is served by
    `pages.py` and is outside this bundle entirely -- a client-side navigation
    there resolves to nothing.

    Read from the source rather than from a rendered page: the markup lives in a
    bundle this suite does not build, and the alternative is a check that passes
    vacuously in any checkout where `make hub-web` has not run.
    """
    app = (REPO / "hub" / "web" / "src" / "App.vue").read_text()
    nav = re.search(r'<nav[^>]*aria-label="Venona"[^>]*>(.*?)</nav>', app, re.S)
    assert nav, "the operator surface has no way back to the portal"

    link = re.search(r'<a\s+href="/"(.*?)>\s*([^<]+?)\s*<', nav.group(1), re.S)
    assert link, 'the way back is not a plain `<a href="/">`'
    label = link.group(2).strip()
    assert label, "the way back has no words of its own"
    # It says what the destination calls itself: `/` opens on "Your domains".
    assert "domains" in label.lower(), label


async def test_the_admin_prefix_partitions_cleanly(hub_client, hub_app):
    """⭐ /admin is THREE surfaces behind one prefix, and the split must be
    exact. Every mounted /admin route is one of:

      a page      the middleware passes it to the session gate, which bounces
                  a signed-out browser to the front door (302)
      the data    the operator app's own JSON, same session gate, but refused
                  as a status the app can act on rather than a redirect it
                  cannot tell from success (401 with an envelope)
      the machine bearer-gated, refused bare (401, no envelope of ours)

    A route that lands in the wrong one of those fails here the day it is added.
    The two 401s are told apart by their body, because the status alone cannot:
    `WWW-Authenticate` is the bearer gate's and only the bearer gate's.
    """
    pages = set(_page_routes())
    data = set(_data_routes())
    hub_client.cookies.clear()
    for method, path in _admin_routes(hub_app):
        url = path.replace("{request_id}", "req:x").replace("{instance_id}", "inst:x")
        bare = await hub_client.request(method, url)
        if (method, path) in pages:
            assert bare.status_code == 302, f"{method} {url}: {bare.status_code} (want sign-in)"
        elif (method, path) in data:
            assert bare.status_code == 401, f"{method} {url}: {bare.status_code} (want 401)"
            assert "www-authenticate" not in bare.headers, (
                f"{method} {url}: refused by the BEARER gate, so the middleware's "
                "pass-through no longer covers the operator app's data"
            )
            assert bare.json()["detail"]["error"] == "unauthorized", url
        else:
            assert bare.status_code == 401, f"{method} {url}: {bare.status_code} (want bearer 401)"
            assert bare.headers.get("www-authenticate") == "Bearer", (
                f"{method} {url}: not refused by the bearer gate -- the machine "
                "surface has fallen inside the session pass-through"
            )


async def test_chooser_offers_admin_only_to_operators(hub_client, hub_conn, hub_settings):
    """The link rides the chooser, but only for an email the env names --
    everyone else's portal must not even hint the page exists."""
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    for email in (OPERATOR, "ada@example.com"):
        user_id = ids.user_id()
        users_repo.create(hub_conn, user_id=user_id, email=email, display_name=None)
        users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="reader")

    _sign_in(hub_client, hub_settings, OPERATOR)
    assert '<a href="/admin">Admin</a>' in (await hub_client.get("/")).text

    _sign_in(hub_client, hub_settings, "ada@example.com")
    assert "/admin" not in (await hub_client.get("/")).text


async def test_operator_triages_grant_and_dismiss(hub_client, hub_conn, hub_settings):
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    granted_id = _submit_request(hub_conn, "new@example.com", note="please", wanted=instance_id)
    dismissed_id = _submit_request(hub_conn, "spam@example.com")
    _sign_in(hub_client, hub_settings, OPERATOR)

    overview = (await hub_client.get("/admin/data/overview")).json()
    assert overview["pending"] == 2
    assert [d["display_name"] for d in overview["domains"]] == ["Acme Watch"]
    # The overview counts; it does not list.
    assert "new@example.com" not in str(overview)

    listing = (await hub_client.get("/admin/data/requests")).json()
    addresses = {row["email"]: row for row in listing["rows"]}
    assert addresses["new@example.com"]["note"] == "please"
    assert "spam@example.com" in addresses
    # The grant control needs the domains, and must not have to fetch them.
    assert listing["domains"] == ["acme"]

    grant = await hub_client.post(
        f"/admin/data/requests/{granted_id}/grant", json={"slug": "acme", "role": "reader"}
    )
    assert grant.status_code == 200
    user = users_repo.by_email(hub_conn, "new@example.com")
    assert user is not None
    assert users_repo.role_for(hub_conn, user_id=user["id"], instance_id=instance_id) == "reader"
    assert requests_repo.by_id(hub_conn, granted_id)["status"] == "granted"

    dismiss = await hub_client.post(f"/admin/data/requests/{dismissed_id}/dismiss")
    assert dismiss.status_code == 200
    assert requests_repo.by_id(hub_conn, dismissed_id)["status"] == "dismissed"

    # Both settled: the queue is empty and the new account is listed, with its
    # membership as a pair rather than as a rendered string.
    assert (await hub_client.get("/admin/data/requests")).json()["rows"] == []
    accounts = (await hub_client.get("/admin/data/accounts")).json()["rows"]
    member = next(row for row in accounts if row["email"] == "new@example.com")
    assert member["memberships"] == [{"slug": "acme", "role": "reader"}]


async def test_every_operator_path_answers_with_the_app(hub_client, hub_conn, hub_settings):
    """⭐ The six destinations the app routes to are the six the gate names, and
    each one serves the app's own document.

    A refresh on a deep link is the case this protects. The app handles
    `/admin/settings` in the browser, but the first load of that URL is a real
    request -- and if the server does not answer it with the shell, the reader
    gets a 401 that looks like a broken link rather than a page.

    `no-store` on the document and nothing else: it names the current asset
    hashes, so a cached copy points at a bundle that no longer exists.

    WHERE THE PHONE CHECK WENT. A server-rendered table could be swept for its
    stacking rules in this suite. The layout is the bundle's now, so it is
    checked where a bundle can be checked -- in a browser, against the built
    app, the way `make ext-smoke` checks the extension's card.
    """
    _seed_instance(hub_conn, "acme", "Acme Watch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    for path in middleware.OPERATOR_PAGES - {"/admin/launch.csv"}:
        reply = await hub_client.get(path)
        assert reply.status_code in (200, 503), f"{path}: {reply.status_code}"
        if reply.status_code == 503:
            # An unbuilt checkout says so rather than looking like a refusal.
            assert "not built" in reply.text.lower(), path
            continue
        assert reply.headers["content-type"].startswith("text/html"), path
        assert reply.headers["cache-control"] == "no-store", path
        assert 'id="app"' in reply.text, path


async def test_the_app_routes_and_the_gate_name_the_same_paths():
    """⭐ Three lists have to agree or a destination is unreachable: the gate's
    pass-through, the routes this router mounts, and the rail the app renders.

    A path the app links to but the gate does not name answers 401 on a refresh.
    A path the gate names but nothing serves is a 404 behind a session check.
    Both look like a permissions problem and neither is.
    """
    served = {"/admin" + path for path in ops.APP_PATHS}
    assert served == middleware.OPERATOR_PAGES - {"/admin/launch.csv"}

    nav = (REPO / "hub" / "web" / "src" / "router.ts").read_text()
    linked = set(re.findall(r"path: '(/admin[^']*)'", nav))
    assert linked == served, "the app's rail and the server's shell routes disagree"


async def test_settled_requests_and_bogus_targets_are_404(hub_client, hub_conn, hub_settings):
    """Every way of naming a request that cannot be granted, and the envelope
    each one comes back as.

    JSON in both directions now: these fire from `fetch`, so there is no
    browser `Accept` to negotiate on and no tab for an HTML refusal to land in.
    """
    _seed_instance(hub_conn, "acme")
    request_id = _submit_request(hub_conn, "new@example.com")
    _sign_in(hub_client, hub_settings, OPERATOR)

    missing = await hub_client.post(
        "/admin/data/requests/req:missing/grant", json={"slug": "acme", "role": "reader"}
    )
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")

    bogus_instance = await hub_client.post(
        f"/admin/data/requests/{request_id}/grant", json={"slug": "nope", "role": "reader"}
    )
    assert bogus_instance.status_code == 404
    assert requests_repo.by_id(hub_conn, request_id)["status"] == "pending"

    bad_role = await hub_client.post(
        f"/admin/data/requests/{request_id}/grant", json={"slug": "acme", "role": "root"}
    )
    assert bad_role.status_code == 422

    requests_repo.set_status(hub_conn, request_id, "dismissed")
    settled = await hub_client.post(
        f"/admin/data/requests/{request_id}/grant", json={"slug": "acme", "role": "reader"}
    )
    assert settled.status_code == 404


async def test_a_launch_signup_cannot_be_granted_a_domain(hub_client, hub_conn, hub_settings):
    """⭐ The launch list is a list, not a queue. Somebody who left an address
    on the front page named no domain -- the form asked for an address and
    nothing else -- so granting one is picking a domain on their behalf, and
    the person who only wanted telling gets an account on a guess.

    The app offers no control for it. The refusal is on the server because a
    control that is merely absent is still a request anybody can post.
    """
    _seed_instance(hub_conn, "acme", "Acme Watch")
    waiting_id = _submit_request(hub_conn, "waiting@example.com", kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    refused = await hub_client.post(
        f"/admin/data/requests/{waiting_id}/grant", json={"slug": "acme", "role": "reader"}
    )
    assert refused.status_code == 409
    assert users_repo.by_email(hub_conn, "waiting@example.com") is None
    assert requests_repo.by_id(hub_conn, waiting_id)["status"] == "pending"


async def test_the_launch_list_is_a_count_and_a_file(hub_client, hub_conn, hub_settings):
    """⭐ Not a table of addresses on the way in. There is no per-row decision
    here -- a launch signup cannot be granted a domain -- so the overview
    carries how many, and the file is the thing that is actually useful."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    for address in ("one@example.com", "two@example.com"):
        _submit_request(hub_conn, address, kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    overview = (await hub_client.get("/admin/data/overview")).json()
    assert overview["waiting"] == 2
    # The figure, not the addresses: they are on the page that manages them.
    assert "one@example.com" not in str(overview)

    listing = (await hub_client.get("/admin/data/launch")).json()
    assert listing["total"] == 2
    assert {row["email"] for row in listing["rows"]} == {"one@example.com", "two@example.com"}


async def test_the_launch_list_downloads_as_a_file(hub_client, hub_conn, hub_settings):
    """The mailing happens somewhere else, so the list has to leave as
    something a mail tool reads. `attachment` matters: rendered inline, the
    addresses land in a browser tab, which is the one place they should not
    appear by accident."""
    _submit_request(hub_conn, "one@example.com", kind="launch")
    _submit_request(hub_conn, "asking@example.com")  # an access request
    dismissed = _submit_request(hub_conn, "spam@example.com", kind="launch")
    requests_repo.set_status(hub_conn, dismissed, "dismissed")
    _sign_in(hub_client, hub_settings, OPERATOR)

    reply = await hub_client.get("/admin/launch.csv")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/csv")
    assert "attachment" in reply.headers["content-disposition"]
    assert ".csv" in reply.headers["content-disposition"]

    rows = [line for line in reply.text.splitlines() if line]
    assert rows[0] == "email,added"
    assert any(row.startswith("one@example.com,") for row in rows)
    # Neither the other door nor a removed address rides along.
    assert "asking@example.com" not in reply.text
    assert "spam@example.com" not in reply.text


async def test_the_download_cannot_execute_in_a_spreadsheet(
    hub_client, hub_conn, hub_settings
) -> None:
    """⭐ The addresses were typed into a PUBLIC form by anybody at all, and
    this file exists to be opened in a spreadsheet. A value starting `=` is a
    formula there, so it would run on the operator's machine the moment they
    open the thing they downloaded to do a mailing."""
    _submit_request(hub_conn, '=HYPERLINK("http://evil.example")', kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/launch.csv")).text
    # Still legible -- the operator has to be able to see what came in.
    assert "hyperlink" in body.lower()
    # But quoted as text: the apostrophe is what every spreadsheet reads as
    # "the rest of this is not a formula".
    assert "'=" in body
    assert not any(line.lstrip('"').startswith("=") for line in body.splitlines()), (
        "a formula-leading value reached a cell as a formula"
    )


async def test_the_launch_list_can_still_drop_a_bad_address(hub_client, hub_conn, hub_settings):
    """The form is public and anything can type into it, so removal has to stay
    reachable. It is its own route rather than the request queue's dismissal:
    one endpoint serving both would have to be told which list it was called
    from, which is the same fact spelled twice."""
    waiting_id = _submit_request(hub_conn, "spam@example.com", kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    dropped = await hub_client.post(f"/admin/data/launch/{waiting_id}/remove")
    assert dropped.status_code == 200
    assert requests_repo.by_id(hub_conn, waiting_id)["status"] == "dismissed"


# ── paging ───────────────────────────────────────────────────────────
#
# All three lists only ever grow: requests arrive, launch signups arrive,
# accounts arrive and are not deleted. A page that renders every row gets
# slower and less readable exactly as the thing it reports on succeeds.


def test_the_page_window_clamps_anything_a_url_can_carry() -> None:
    """⭐ A page number arrives in a URL, so it can be anything. Every value
    lands somewhere real rather than 404ing: a dead end on `?page=9` after a
    queue drained is a wrong answer to an obvious question."""
    assert ops_api._window(0, 1, size=10) == (1, 1, 0), "an empty list is still page 1 of 1"
    assert ops_api._window(10, 1, size=10) == (1, 1, 0), "exactly one full page"
    assert ops_api._window(11, 2, size=10) == (2, 2, 10)
    assert ops_api._window(25, 999, size=10) == (3, 3, 20), "past the end clamps to the last"
    assert ops_api._window(25, 0, size=10) == (1, 3, 0), "zero clamps to the first"
    assert ops_api._window(25, -4, size=10) == (1, 3, 0), "negative clamps to the first"


def test_the_domains_readout_carries_every_field_the_poll_returns() -> None:
    """⭐ The cross-package drift alarm for the readout.

    The page's whole promise is that it shows EVERYTHING the poll returns, at
    one weight, and lets the operator decide what matters. A field added to
    `MgmtStatus` on the instance side is collected by the hub from that moment
    -- the body is stored verbatim -- and would then sit in `last_status`,
    displayed nowhere, with nothing failing anywhere. That is precisely the
    state this page was built to end, and it would re-open silently.

    So the two schemas are compared rather than trusted. Adding a field to the
    instance's status contract turns this red until it is either carried here or
    named below as deliberately not.
    """
    import sys

    sys.path.insert(0, str(REPO / "backend"))
    from hub.schemas.ops import DomainHealth, DomainSetup
    from venona.api.schemas.mgmt import MgmtSetupState, MgmtStatus

    #: Instance field -> what carries it here. `users` is one object on the wire
    #: and two fields on the card, which is a rendering choice rather than an
    #: omission.
    CARRIED = {
        "version": "version",
        "commit": "commit",
        "started_at": "started_at",
        "domain_slug": "reported_slug",
        "display_name": "reported_name",
        "setup": "setup",
        "documents": "documents",
        "entities": "entities",
        "relationships": "relationships",
        "users": "admins/readers",
        "billing_enforced": "billing_enforced",
        "login_blurb": "login_blurb",
        "latest_brief_at": "latest_brief_at",
    }
    for field in CARRIED.values():
        if "/" not in field:
            assert field in DomainHealth.model_fields, f"{field} left DomainHealth"

    missing = [name for name in MgmtStatus.model_fields if name not in CARRIED]
    assert not missing, (
        f"the instance now reports {missing} and the domains page does not show it. "
        "Carry it in `schemas/ops.DomainHealth` and `views/DomainsView.vue`, or add "
        "it to CARRIED here with the reason it is deliberately not shown."
    )

    #: The two the nested object duplicates. `setup.slug` and
    #: `setup.display_name` repeat the top-level `domain_slug` and
    #: `display_name` verbatim, and a card showing each name twice is noise
    #: rather than completeness.
    DUPLICATED = {"slug", "display_name"}
    unhandled = [
        name
        for name in MgmtSetupState.model_fields
        if name not in DomainSetup.model_fields and name not in DUPLICATED
    ]
    assert not unhandled, f"the instance now reports setup.{unhandled} and nothing shows it"


async def test_the_domains_readout_carries_both_halves(hub_client, hub_conn, hub_settings):
    """⭐ Two sources in one object, neither reconciled against the other.

    The hub's own columns say what it decided and what it has observed; the rest
    is that domain's `GET /api/mgmt/status` body, stored verbatim at the last
    poll. A version, a name or a paywall setting disagreeing across the two is
    the reason the page exists, so nothing resolves them into one figure -- and
    this seeds a disagreement to prove both halves survive.
    """
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    instances_repo.record_poll_success(
        hub_conn,
        instance_id,
        {
            "version": "0.9.1",
            # It calls itself something else. The hub keeps its own column and
            # reports this beside it rather than picking one.
            "domain_slug": "acme-watch",
            "display_name": "Acme Watch (staging)",
            "setup": {
                "configured": True,
                "has_dossier": True,
                "drafts": ["sources"],
                "active_run": "run:abc",
            },
            "documents": 412,
            "entities": 1180,
            "relationships": 2640,
            "users": {"admins": 1, "readers": 7},
            "billing_enforced": True,
            "latest_brief_at": "2026-09-10T06:00:00+00:00",
            "login_blurb": "Defence industry intelligence.",
        },
    )
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/data/domains")).json()
    assert body["polled_at"], "a sweep that happened has to be distinguishable from none"
    domain = body["domains"][0]

    # The hub's half.
    assert domain["slug"] == "acme"
    assert domain["display_name"] == "Acme Watch"
    assert domain["status"] == "live"
    assert domain["poll_misses"] == 0
    # `registered_at` reads a column rather than a key of the status body, and
    # `row.get` would return None forever if that column were renamed -- which
    # renders as an empty field rather than as an error.
    assert domain["registered_at"], "the hub knows when it registered this domain"

    # The domain's half, including where the two disagree.
    assert domain["version"] == "0.9.1"
    assert domain["reported_slug"] == "acme-watch"
    assert domain["reported_name"] == "Acme Watch (staging)"
    assert domain["documents"] == 412
    assert domain["relationships"] == 2640
    assert domain["admins"] == 1 and domain["readers"] == 7
    assert domain["billing_enforced"] is True
    assert domain["latest_brief_at"].startswith("2026-09-10")
    assert domain["setup"]["drafts"] == ["sources"]
    assert domain["setup"]["active_run"] == "run:abc"


async def test_the_domains_readout_never_carries_a_secret(hub_client, hub_conn, hub_settings):
    """⭐ `instances` holds `mgmt_token_sealed` and `oidc_client_secret_hash`,
    and `list_all` is `SELECT *`.

    The sealed token opens every management call against that domain, so the one
    thing this page must never do is print it. `response_model` FILTERS, which
    is what stops it -- a field the model does not name cannot reach the wire
    however the row got here. This asserts the whole response body rather than
    the absence of two keys, because the next secret column added to that table
    will not be one of these two.
    """
    instance_id = _seed_instance(hub_conn, "acme")
    instances_repo.record_poll_success(hub_conn, instance_id, {"version": "1.0.0"})
    _sign_in(hub_client, hub_settings, OPERATOR)

    raw = (await hub_client.get("/admin/data/domains")).text
    for secret in ("mgmt_token_sealed", "oidc_client_secret_hash", "y", "sealed"):
        assert f'"{secret}"' not in raw, secret
    assert "oidc_client_id" not in raw, (
        "the client id is not a secret, and is still not needed here"
    )


async def test_a_domain_that_has_never_answered_reads_as_silent(hub_client, hub_conn, hub_settings):
    """Nothing reported is not the same as nothing there.

    A registered domain the poller has not yet reached has no `last_status` at
    all, so every field from its own half is null -- and null renders as a dash
    rather than as a zero, because "it did not say" and "it said none" are
    different answers and a corpus of 0 documents is a claim.
    """
    _seed_instance(hub_conn, "quiet")
    _sign_in(hub_client, hub_settings, OPERATOR)

    domain = (await hub_client.get("/admin/data/domains")).json()["domains"][0]
    assert domain["slug"] == "quiet"
    assert domain["version"] is None
    assert domain["documents"] is None
    assert domain["setup"] is None
    assert domain["unreadable"] is False, "never answered is not the same as answered badly"


async def test_a_domain_that_answered_unreadably_says_so(hub_client, hub_conn, hub_settings):
    """⭐ The third state, and the one that would otherwise be invisible.

    A domain that answers a poll with something this hub cannot read shows the
    hub's own columns and nothing else -- which is exactly what a domain that
    has never answered shows. Without this flag the page would render the two
    identically, and the second is a working domain talking to a hub that cannot
    understand it.
    """
    instance_id = _seed_instance(hub_conn, "odd")
    # The poller stores the body verbatim; a non-object is what a proxy error
    # page or a rewritten response looks like by the time it lands here.
    instances_repo.record_poll_success(hub_conn, instance_id, {"version": "1.0.0"})
    hub_conn.execute(
        "UPDATE instances SET last_status = '\"not an object\"'::jsonb WHERE id = %s",
        (instance_id,),
    )
    _sign_in(hub_client, hub_settings, OPERATOR)

    domain = (await hub_client.get("/admin/data/domains")).json()["domains"][0]
    assert domain["unreadable"] is True
    assert domain["version"] is None
    assert domain["slug"] == "odd", "the hub's own columns still read"


async def test_the_lists_page_and_the_pager_moves(hub_client, hub_conn, hub_settings):
    """⭐ The reason paging exists. Past the page size the surface has to stay
    readable, and every row has to remain reachable -- a list that silently
    shows the first fifty of nine hundred is a list that lost the rest."""
    size = ops_api.PAGE_SIZE
    total = size + 5
    for i in range(total):
        _submit_request(hub_conn, f"p{i:03d}@example.com", kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    assert (await hub_client.get("/admin/data/overview")).json()["waiting"] == total

    first = (await hub_client.get("/admin/data/launch")).json()
    assert len(first["rows"]) == size
    assert (first["page"], first["pages"], first["total"]) == (1, 2, total)
    emails = {row["email"] for row in first["rows"]}
    assert "p000@example.com" in emails and "p054@example.com" not in emails

    second = (await hub_client.get("/admin/data/launch?page=2")).json()
    assert len(second["rows"]) == 5
    assert (second["page"], second["pages"]) == (2, 2)
    emails = {row["email"] for row in second["rows"]}
    assert "p054@example.com" in emails and "p000@example.com" not in emails


async def test_one_page_reports_one_page(hub_client, hub_conn, hub_settings):
    """The client hides the pager when there is nowhere to go, so the server
    has to be honest about how many pages there are rather than always
    claiming at least two."""
    _submit_request(hub_conn, "only@example.com", kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    assert (await hub_client.get("/admin/data/launch")).json()["pages"] == 1


async def test_the_download_is_the_whole_list_not_the_page(hub_client, hub_conn, hub_settings):
    """⭐ The file is the mailing. A download that silently carried the fifty
    rows on screen would look exactly like a complete one, and the people it
    dropped would never be written to."""
    total = ops_api.PAGE_SIZE + 5
    for i in range(total):
        _submit_request(hub_conn, f"p{i:03d}@example.com", kind="launch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/launch.csv")).text
    assert len([line for line in body.splitlines() if line]) == total + 1  # + the header
    assert "p054@example.com" in body


async def test_the_overview_counts_the_whole_list(hub_client, hub_conn, hub_settings):
    """The count is its own query. `len()` of a listing would report the page
    size as the total the moment either list outgrew one page."""
    total = ops_api.PAGE_SIZE + 3
    for i in range(total):
        _submit_request(hub_conn, f"a{i:03d}@example.com")
    _sign_in(hub_client, hub_settings, OPERATOR)

    assert (await hub_client.get("/admin/data/overview")).json()["pending"] == total
    # And the rail's own copy of it, which is answered separately.
    assert (await hub_client.get("/admin/data/session")).json()["pending"] == total


async def test_accounts_list_everyone_and_the_count_is_the_active(
    hub_client, hub_conn, hub_settings
):
    """The overview counts who can sign in; the listing shows everyone, because
    a suspended account you cannot see is one you cannot reason about."""
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    for i, status in enumerate(("active", "active", "suspended")):
        user_id = ids.user_id()
        users_repo.create(hub_conn, user_id=user_id, email=f"u{i}@example.com", display_name=None)
        users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="reader")
        if status == "suspended":
            users_repo.set_status(hub_conn, user_id, "suspended")
    _sign_in(hub_client, hub_settings, OPERATOR)

    assert (await hub_client.get("/admin/data/overview")).json()["accounts"] == 2

    rows = (await hub_client.get("/admin/data/accounts")).json()["rows"]
    listed = {row["email"]: row for row in rows}
    for i in range(3):
        assert f"u{i}@example.com" in listed
    assert listed["u2@example.com"]["status"] == "suspended"


# ── subscriptions on the accounts page ───────────────────────────────
#
# A subscription buys a seat on ONE instance, so an operator has to be able to
# see WHICH. Somebody with two memberships and one subscription is the case that
# breaks a naive rendering.


def _seat_for(conn, email: str, slug: str, role: str = "reader") -> tuple[str, str]:
    from hub.repositories import instances as instances_repo
    from hub.repositories import users as users_repo

    from hub import ids

    instance = instances_repo.by_slug(conn, slug)
    if instance is None:
        instance_id = ids.instance_id()
        instances_repo.create(
            conn,
            instance_id=instance_id,
            slug=slug,
            display_name=slug.title(),
            tagline="t",
            public_url=f"https://{slug}.example",
            oidc_client_id=f"cid-{slug}",
            oidc_client_secret_hash="hash",
            mgmt_token_sealed="sealed",
        )
    else:
        instance_id = instance["id"]
    user = users_repo.by_email(conn, email)
    if user is None:
        user_id = ids.user_id()
        users_repo.create(conn, user_id=user_id, email=email, display_name=None)
    else:
        user_id = user["id"]
    users_repo.grant(conn, user_id=user_id, instance_id=instance_id, role=role)
    return user_id, instance_id


def _subscribe(conn, user_id: str, instance_id: str, status: str, stripe_id: str) -> None:
    from hub.repositories import subscriptions as subs_repo

    from hub import ids

    subs_repo.upsert(
        conn,
        subscription_id=ids.subscription_id(),
        user_id=user_id,
        instance_id=instance_id,
        stripe_subscription_id=stripe_id,
        status=status,
        price_id="price_x",
        current_period_end=None,
        cancel_at_period_end=False,
        trial_end=None,
        stripe_updated_at=None,
    )


def _row_for(conn, email: str) -> str:
    from hub.repositories import users as users_repo

    row = next(r for r in users_repo.list_with_memberships(conn) if r["email"] == email)
    return row["memberships"] or ""


def test_a_subscription_is_shown_against_its_own_instance(hub_conn) -> None:
    """THE case: two memberships, one subscription. Which one is paid for has to
    be legible, and two parallel lists would leave an operator matching them up
    by position."""
    uid, defense = _seat_for(hub_conn, "two@example.com", "defense", role="admin")
    _, aitech = _seat_for(hub_conn, "two@example.com", "aitech")
    _subscribe(hub_conn, uid, aitech, "active", "sub_ai")

    rendered = _row_for(hub_conn, "two@example.com")
    assert "aitech:reader · active" in rendered, rendered
    assert "defense:admin" in rendered, rendered
    assert "defense:admin · " not in rendered, "an unsubscribed seat must carry no status"


def test_several_subscriptions_are_each_shown(hub_conn) -> None:
    uid, defense = _seat_for(hub_conn, "both@example.com", "defense")
    _, aitech = _seat_for(hub_conn, "both@example.com", "aitech")
    _subscribe(hub_conn, uid, defense, "trialing", "sub_d")
    _subscribe(hub_conn, uid, aitech, "past_due", "sub_a")

    rendered = _row_for(hub_conn, "both@example.com")
    assert "defense:reader · trialing" in rendered, rendered
    assert "aitech:reader · past_due" in rendered, rendered


def test_a_resubscribe_shows_the_current_one_not_the_history(hub_conn) -> None:
    """A seat accumulates rows over time. A plain join would multiply the
    membership by its whole history; the newest is the one that describes it."""
    uid, defense = _seat_for(hub_conn, "again@example.com", "defense")
    _subscribe(hub_conn, uid, defense, "canceled", "sub_old")
    _subscribe(hub_conn, uid, defense, "active", "sub_new")

    rendered = _row_for(hub_conn, "again@example.com")
    assert rendered.count("defense") == 1, f"the seat appeared more than once: {rendered}"
    assert "· active" in rendered, rendered


def test_a_user_with_no_membership_still_renders(hub_conn) -> None:
    from hub.repositories import users as users_repo

    from hub import ids

    users_repo.create(
        hub_conn, user_id=ids.user_id(), email="lonely@example.com", display_name=None
    )
    assert _row_for(hub_conn, "lonely@example.com") == ""


# ── the settings page ────────────────────────────────────────────────


async def test_the_switches_are_saved_and_shown(hub_client, hub_conn, hub_settings):
    """Throwing a switch stores the decision and bumps the version that carries
    it. The delivery is the poller's job and is allowed to fail; the decision is
    not."""
    from hub.repositories import settings as settings_repo

    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    state = (await hub_client.get("/admin/data/settings")).json()
    assert [d["display_name"] for d in state["domains"]] == ["Acme Watch"]
    assert state["self_serve"] is False

    saved = await hub_client.post(
        "/admin/data/settings/billing", json={"instance_id": instance_id, "enforced": True}
    )
    assert saved.status_code == 200
    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["billing_enforced"] is True
    assert row["settings_version"] > row["settings_pushed_version"], "nothing delivered it yet"

    accounts = await hub_client.post("/admin/data/settings/accounts", json={"self_serve": True})
    assert accounts.status_code == 200
    assert settings_repo.self_serve(hub_conn) is True


async def test_the_surface_says_when_a_domain_has_not_confirmed(hub_client, hub_conn, hub_settings):
    """⭐ The field that makes this page worth having. A switch nothing has
    delivered looks exactly like a delivered one unless the domain's own answer
    is read back beside it.

    Three states, and an operator acts differently on each: not reported yet
    (it has never answered), reported and disagreeing (it is up and ignoring
    the switch, or the push has not landed), and agreeing.
    """
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    _sign_in(hub_client, hub_settings, OPERATOR)
    await hub_client.post(
        "/admin/data/settings/billing", json={"instance_id": instance_id, "enforced": True}
    )

    state = (await hub_client.get("/admin/data/settings")).json()
    assert state["domains"][0]["reported"] is None, "never answered"

    # Now the domain answers, and disagrees: it has been told to charge and is
    # not, which is what `VENONA_BILLING_ENFORCED=0` on the box looks like.
    instances_repo.record_poll_success(hub_conn, instance_id, {"billing_enforced": False})
    state = (await hub_client.get("/admin/data/settings")).json()
    assert state["domains"][0]["billing_enforced"] is True
    assert state["domains"][0]["reported"] is False, "saved, and contradicted"

    instances_repo.record_poll_success(hub_conn, instance_id, {"billing_enforced": True})
    state = (await hub_client.get("/admin/data/settings")).json()
    assert state["domains"][0]["reported"] is True


async def test_an_unknown_domain_is_refused(hub_client, hub_conn, hub_settings):
    _sign_in(hub_client, hub_settings, OPERATOR)
    missing = await hub_client.post(
        "/admin/data/settings/billing", json={"instance_id": "inst:nope", "enforced": True}
    )
    assert missing.status_code == 404


async def test_settings_reports_why_a_domain_is_not_on_sale(hub_client, hub_conn, hub_settings):
    """The gap this closes (2026-09-11): the page carried the self-serve switch
    and the paywall switch, so an operator could turn the door on, be told it
    saved, and still watch every card offer `Request access`. Two of the three
    conditions were on no screen anywhere.

    The test fixture holds no Stripe credentials, so this is also the state a
    hub ships in.
    """
    _seed_instance(hub_conn, "acme", "Acme Watch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/data/settings")).json()
    assert body["takes_payment"] is False
    row = body["domains"][0]
    # Every reason at once: no keys on the hub, no price on the domain, and the
    # switch still set to ask.
    assert row["blockers"] == ["no_payments", "no_price", "invite_only"]
    assert row["price_monthly_amount"] is None

    instances_repo.set_pricing(
        hub_conn,
        slug="acme",
        product_id="prod_x",
        price_monthly="price_m",
        price_annual="price_a",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )
    priced = (await hub_client.get("/admin/data/settings")).json()["domains"][0]
    assert "no_price" not in priced["blockers"]
    assert priced["price_monthly_amount"] == 1200
    assert priced["price_currency"] == "usd"


# ── which build each surface is running ──────────────────────────────


async def test_the_overview_reports_every_surface_that_deploys(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """Three properties, one branch, three build filters.

    Marketing, the hub and each domain deploy independently, so they can each
    be on a different release at the same moment. The domains table could only
    ever show the domains; without the hub's own reading and the site's, the one
    comparison an operator wants is the one the page cannot make.

    The hub's is read live at the request. The site's is the poller's last
    successful read, five minutes stale by construction like everything else
    here -- a static site has no process to ask, so its build publishes a file.
    """
    from hub.repositories import site as site_repo
    from hub.routers import ops_api

    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    instances_repo.record_poll_success(
        hub_conn,
        instance_id,
        {"version": "1.0.0", "commit": "aaaaaaa", "started_at": "2026-09-14T06:00:00Z"},
    )
    site_repo.record(
        hub_conn,
        body={"version": "1.0.1", "commit": "bbbbbbb", "built_at": "2026-09-17T09:00:00Z"},
    )
    monkeypatch.setattr(ops_api, "COMMIT", "ccccccc")
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/data/overview")).json()

    assert body["commit"] == "ccccccc"
    assert body["started_at"].endswith("Z")
    assert body["site_version"] == "1.0.1"
    assert body["site_commit"] == "bbbbbbb"
    assert body["site_built_at"] == "2026-09-17T09:00:00Z"
    assert body["site_seen_at"] is not None
    # At a glance, on the table itself: the detail page carries the build.
    assert body["domains"][0]["version"] == "1.0.0"


async def test_the_overview_is_quiet_about_a_site_never_read(hub_client, hub_conn, hub_settings):
    """Nulls, not zeroes or empty strings.

    A hub that has never reached the site and a site reporting nothing must not
    render the same, which is the rule the domains page already holds: a dash is
    not a value anywhere on this surface.
    """
    _seed_instance(hub_conn, "acme", "Acme Watch")
    _sign_in(hub_client, hub_settings, OPERATOR)

    body = (await hub_client.get("/admin/data/overview")).json()
    assert body["site_version"] is None
    assert body["site_seen_at"] is None
    # Its own release is never null -- it is read from the installed package.
    assert body["version"]
    # A domain that has never answered says nothing about its version either.
    assert body["domains"][0]["version"] is None


async def test_a_domains_build_and_uptime_reach_the_detail_page(hub_client, hub_conn, hub_settings):
    """The fields that tell two domains apart when both report the same release.

    Between bumps every domain reports the same `version`, so on its own that
    row cannot show a drifted deploy. `commit` is what moves.
    """
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    instances_repo.record_poll_success(
        hub_conn,
        instance_id,
        {"version": "1.0.0", "commit": "aaaaaaa", "started_at": "2026-09-14T06:00:00Z"},
    )
    _sign_in(hub_client, hub_settings, OPERATOR)

    domain = (await hub_client.get("/admin/data/domains")).json()["domains"][0]
    assert domain["commit"] == "aaaaaaa"
    assert domain["started_at"] == "2026-09-14T06:00:00Z"


async def test_an_unreadable_poll_body_yields_no_build(hub_client, hub_conn, hub_settings):
    """The third state the domains page insists on: answered, unreadably.

    `_told` returns nothing rather than raising, so this renders as absent
    fields plus the `unreadable` notice -- which is a different thing from a
    domain that has never answered, and must not be collapsed into it.
    """
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    hub_conn.execute(
        "UPDATE instances SET last_status = %s::jsonb WHERE id = %s",
        ('"not an object"', instance_id),
    )
    _sign_in(hub_client, hub_settings, OPERATOR)

    domain = (await hub_client.get("/admin/data/domains")).json()["domains"][0]
    assert domain["unreadable"] is True
    assert domain["version"] is None and domain["commit"] is None
