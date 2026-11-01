"""The operator surface: the app shell, its one download, and the gate.

WHAT THIS MODULE IS. Six paths that answer with the operator app's document,
the launch list's CSV, and `require_operator` -- which `routers/ops_api.py`
imports and applies to the JSON behind those pages. The markup lives in
`hub/web/`; what is here is the part a bundle cannot do for itself.

The paths share the `/admin` prefix with the bearer'd JSON surface (the app's
Admin lives at /admin; one spelling everywhere). `AdminBearerMiddleware` passes
exactly this router's routes and `ops_api`'s through to the gate here -- its
`_is_operator_page` predicate and `tests/hub/test_ops.py`'s partition sweep hold
the three-way split: every /admin route is a page here, the operator app's own
data, or answers 401 bare.

SIX EXPLICIT ROUTES RATHER THAN A CATCH-ALL, and that is not ceremony. A
`/admin/{rest:path}` would have to be matched by the middleware predicate too,
and the only predicate matching an arbitrary deep path is `startswith("/admin")`
-- which swallows `GET /admin/instances` into the session gate and hands the
machine surface to a browser cookie. The partition sweep fails on exactly that.
These six are the same six `OPERATOR_PAGES` already names.

Who may enter is `HUB_OPERATOR_EMAILS` (env, like the instance's break-glass
allowlist) -- never a database fact, so the surface that edits the hub's
database cannot be reached by editing the hub's database. The gate is a
ROUTER-LEVEL dependency: every route here is born guarded, and the sweep drives
each one as a non-operator to keep it that way.

The one read that is not the app is the launch list's CSV. It is here rather
than on the bearer'd API because the person who needs it is the operator
holding a browser session and no token, and it stays a real link rather than
something the bundle assembles: it arrives as an attachment with a filename,
and a download built in the page would lose both.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from hub import config, errors, pages
from hub.db.core import Conn
from hub.deps import get_db, session_email
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.routers.brand import BUNDLE

#: `(title, message)` and the heading, unpacked so the call below reads as a
#: call. `errors.PAGES` is the table every other refusal renders from.
_NOT_FOUND_TITLE, _NOT_FOUND_MESSAGE, _NOT_FOUND_HEADING = errors.PAGES[404]
_NOT_FOUND = (_NOT_FOUND_TITLE, _NOT_FOUND_MESSAGE)


class NotSignedIn(Exception):
    pass


class NotOperator(Exception):
    pass


def require_operator(request: Request, conn: Conn = Depends(get_db)) -> str:
    email = session_email(request, conn)
    if email is None:
        raise NotSignedIn()
    if email.strip().lower() not in config.operator_set(request.app.state.settings):
        raise NotOperator()
    return email


log = logging.getLogger("hub.ops")

#: How many deliveries the notifications surface shows. Enough to see that a
#: channel has stopped working, short of turning it into a log viewer.
HISTORY_LIMIT = 25

#: The paths that answer with the app, and the same six `OPERATOR_PAGES` names.
#: `tests/hub/test_ops.py` holds the two equal: a destination the app routes to
#: but the gate does not name answers 401 on a refresh, which reads as a broken
#: deep link rather than as a missing line here.
APP_PATHS = (
    "",
    "/domains",
    "/requests",
    "/launch",
    "/accounts",
    "/settings",
    "/notifications",
)

SHELL = BUNDLE / "index.html"

router = APIRouter(prefix="/admin", tags=["ops"], dependencies=[Depends(require_operator)])


def _data_prefix() -> str:
    """Imported lazily: `ops_api` imports `require_operator` from this module,
    and a module-level import here would close the circle."""
    from hub.routers.ops_api import PREFIX

    return PREFIX + "/"


def _is_data(request: Request) -> bool:
    """Whether this refusal is being read by the operator app rather than by a
    browser following a link.

    The two want different answers and neither stands in for the other: a
    redirect reaches `fetch` as an opaque hop to HTML, which a client cannot
    tell from success, and a bare 401 in a tab is a blank screen. The PATH is
    what decides, not `Accept` -- `fetch` sends `*/*` by default, so negotiating
    would hand the app a sign-in page for exactly the same reason.
    """
    return request.url.path.startswith(_data_prefix())


def install(app) -> None:
    @app.exception_handler(NotSignedIn)
    async def _sign_in_first(request: Request, exc: NotSignedIn) -> Response:
        if _is_data(request):
            # A status the app can act on. What it does with this one is send
            # the reader to the front door itself, which keeps the decision in
            # the client that knows what is half-rendered behind it.
            return JSONResponse(
                errors.envelope("unauthorized", "Sign in to continue."), status_code=401
            )
        return RedirectResponse("/?next=/admin", status_code=302)

    @app.exception_handler(NotOperator)
    async def _no_such_page(request: Request, exc: NotOperator) -> Response:
        # The same refusal a wrong URL gets: whether this surface exists for an
        # email is not something a signed-in non-operator gets to learn. The
        # words come from `errors.PAGES` rather than from here -- a second place
        # writing the 404 copy is a second place for it to drift, and this one
        # is read by somebody who must see byte for byte what a stranger sees.
        if _is_data(request):
            # The HEADING, not the lede: it is the sentence the page puts in
            # front of a reader, so it is the one an envelope has to match.
            return JSONResponse(errors.envelope("not_found", _NOT_FOUND_HEADING), status_code=404)
        return HTMLResponse(
            pages.error(
                *_NOT_FOUND,
                status=404,
                heading=_NOT_FOUND_HEADING,
                theme=pages.theme_of(request),
            ),
            status_code=404,
        )


def _shell(request: Request) -> Response:
    """The app's own document, unchanged.

    `no-store`, unlike every asset it names: this file carries the current
    hashes, so a cached copy is a page pointing at a bundle that is gone.

    A MISSING BUNDLE SAYS SO. Answering 404 would be indistinguishable from the
    gate refusing, and an operator would go looking for a permissions problem in
    a checkout that simply has not been built.
    """
    if not SHELL.is_file():
        return HTMLResponse(
            pages.error(
                "Not built",
                "This checkout has no operator surface built into it yet.",
                status=503,
                heading="The operator surface is not built here.",
                theme=pages.theme_of(request),
            ),
            status_code=503,
        )
    return FileResponse(SHELL, media_type="text/html", headers={"cache-control": "no-store"})


def _register_shell(path: str) -> None:
    """One closure per path, so each route keeps its own name.

    A bare loop body would rebind the same function object six times and
    FastAPI would mount six routes sharing one name, which is the kind of thing
    that reads correctly and breaks `url_for`.
    """

    @router.get(path, include_in_schema=False, name=f"operator_app{path.replace('/', '_')}")
    def _app_shell(request: Request, email: str = Depends(require_operator)) -> Response:
        return _shell(request)


for _path in APP_PATHS:
    _register_shell(_path)


#: Leading characters a spreadsheet reads as the start of a formula rather
#: than as text. The addresses in this file were typed into a PUBLIC form by
#: anybody at all, and the file exists to be opened in a spreadsheet, so a
#: value like `=HYPERLINK(...)` would execute on the operator's machine at the
#: moment they open the thing they downloaded to do a mailing.
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Prefix a formula-leading value with an apostrophe, which every
    spreadsheet reads as "the rest of this is text"."""
    return "'" + value if value.startswith(FORMULA_LEADERS) else value


@router.get("/launch.csv")
def launch_csv(conn: Conn = Depends(get_db), email: str = Depends(require_operator)) -> Response:
    """The launch list as a file, because the point of the list is a mailing
    and a mailing is done somewhere else.

    Every pending launch row, not the page on screen: the file is the whole
    mailing, and a download that silently carried fifty of nine hundred
    addresses would look exactly like a complete one.

    Pending rows only. A removed address is removed, and a file that carries
    it anyway would put it back in the mailing the file exists to feed.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "added"])
    for row in requests_repo.list_pending(conn, kind="launch"):
        added = row["created_at"]
        writer.writerow([_csv_safe(row["email"]), added.date().isoformat() if added else ""])

    today = datetime.now(UTC).date().isoformat()
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={
            # `attachment` and a real filename: without it the browser renders
            # the addresses in a tab, which is the one place they should not
            # end up on an operator's screen by accident.
            "content-disposition": f'attachment; filename="venona-launch-list-{today}.csv"',
        },
    )


async def _deliver(settings, conn: Conn, instance_id: str) -> None:
    """Push one instance's switches now, and say nothing if it cannot be done.

    Errors are swallowed DELIBERATELY and this is the one place in the hub where
    that is right: the caller has already saved, the poller already retries, and
    the settings page already shows the disagreement. Raising here would turn a
    successful decision into a failed page.
    """
    from hub import mgmt_client, seal

    row = instances_repo.by_id(conn, instance_id)
    if row is None:
        return
    try:
        token = seal.unseal(settings.seal_key, row["mgmt_token_sealed"])
        status, _ = await mgmt_client.push_settings(
            row["public_url"], token, billing_enforced=bool(row["billing_enforced"])
        )
    except Exception:
        log.warning("settings push to %s failed; the poller will retry", row["slug"])
        return
    if status == 200:
        instances_repo.record_settings_push(
            conn, instance_id=instance_id, version=row["settings_version"]
        )
    else:
        log.warning("settings push to %s answered %s; the poller will retry", row["slug"], status)
