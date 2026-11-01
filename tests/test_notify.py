"""Notices, destinations and the outbox.

Four properties carry this design, and each has a test below whose failure would
be silent everywhere else:

  * `VENONA_OFFLINE=1` queues nothing and sends nothing. A replayed run that
    posted a webhook is neither offline nor reproducible.
  * A webhook `target` is a credential and never leaves the repository except
    for a send.
  * A reader notice reaches that reader and nobody else, and a reader cannot
    subscribe to an instance notice at all.
  * Untrusted text is escaped in every channel that has markup to escape into.
"""

from __future__ import annotations

import json

import pytest
from venona import notify
from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.notify import outbox, policy, render
from venona.notify.channels import webhook
from venona.notify.types import NOTICE_TYPES, Notice

pytestmark = pytest.mark.anyio


# ── helpers ──────────────────────────────────────────────────────────


def _destination(
    conn,
    *,
    kind: str = "webhook",
    target: str = "https://hooks.slack.com/services/T0/B0/abcd",
    owner_id: str | None = None,
    subscribe: tuple[str, ...] = (),
    label: str = "ops",
) -> str:
    destination_id = f"dest:{label}-{kind}-{owner_id or 'instance'}"
    repo.insert_destination(
        conn,
        destination_id=destination_id,
        owner_kind="reader" if owner_id else "instance",
        owner_id=owner_id,
        kind=kind,
        label=label,
        target=target,
        created_by=None,
        now="2026-09-01T00:00:00.000Z",
    )
    for notice_type in subscribe:
        repo.subscribe(
            conn,
            destination_id=destination_id,
            notice_type=notice_type,
            now="2026-09-01T00:00:00.000Z",
        )
    conn.commit()
    return destination_id


def _user(conn, *, user_id: str, email: str) -> str:
    conn.execute(
        "INSERT INTO users (id, email, role, status, created_at) "
        "VALUES (?, ?, 'reader', 'active', '2026-09-01T00:00:00.000Z')",
        (user_id, email),
    )
    conn.commit()
    return user_id


class _Recorder:
    """An `HttpClient` stand-in that records instead of connecting.

    Not `httpx.MockTransport`: `post_once` is the seam under test and it owns
    the resolve-and-pin step, so a transport-level fake would exercise the pin
    and need DNS. `tests/test_http_client.py` covers `post_once` itself against
    a mock transport; this covers what the outbox does with its answer.
    """

    def __init__(self, status: int = 200, body: str = "", boom: Exception | None = None) -> None:
        self.status, self.body, self.boom = status, body, boom
        self.calls: list[tuple[str, bytes, dict]] = []

    async def post_once(self, url, *, content, headers=None):
        self.calls.append((url, content, dict(headers or {})))
        if self.boom is not None:
            raise self.boom
        from venona.sources.http import Response

        return Response(url=url, status=self.status, text=self.body, headers={})

    async def aclose(self):
        return None


# ── the offline contract ─────────────────────────────────────────────


async def test_offline_queues_nothing(database_url, monkeypatch) -> None:
    """The guard is before the ENQUEUE, not merely before the send.

    A row is output. An offline replay that wrote one would not be
    byte-identical to the run it replays, and the next drain in a non-offline
    process would then deliver a message about a run that never happened.
    """
    monkeypatch.setenv("VENONA_OFFLINE", "1")
    conn = db.connect(database_url)
    try:
        _destination(conn, subscribe=("run.failed",))
        assert notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed") == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM notify_outbox").fetchone()["n"] == 0
    finally:
        conn.close()


async def test_offline_refuses_to_post(tmp_path) -> None:
    """The replay client raises rather than faking a success.

    Marking a delivery as sent when nothing was sent is the one lie an outbox
    must never tell, so the offline path fails loudly if the guard above is ever
    moved.
    """
    from venona.sources.http import ReplayHttpClient

    client = ReplayHttpClient(tmp_path)
    with pytest.raises(RuntimeError, match="offline"):
        await client.post_once("https://example.test/hook", content=b"x")


# ── the credential ───────────────────────────────────────────────────


def test_a_target_never_leaves_the_repository(database_url) -> None:
    """Every read a surface uses omits `target`; only the sender's does not.

    A Slack incoming URL is a bearer token in URL clothing, and a `SELECT *`
    here is exactly how it would reach a response model without anybody
    deciding to send it.
    """
    conn = db.connect(database_url)
    try:
        secret = "https://hooks.slack.com/services/T0/B0/SUPERSECRET"
        destination_id = _destination(conn, target=secret)
        for row in (
            repo.destination(conn, destination_id=destination_id),
            *repo.instance_destinations(conn),
            *repo.struck_out(conn),
        ):
            assert "target" not in dict(row), dict(row)
        assert repo.destination_target(conn, destination_id=destination_id) == secret
    finally:
        conn.close()


def test_a_hint_identifies_without_revealing() -> None:
    secret = "https://hooks.slack.com/services/T0/B0/SUPERSECRET"
    hint = policy.target_hint("webhook", secret)
    assert "SUPERSECRET" not in hint
    assert "hooks.slack.com" in hint
    # An address is not a secret, and a reader checking where their brief goes
    # has to be able to read it.
    assert policy.target_hint("email", "reader@example.test") == "reader@example.test"


# ── addressing ───────────────────────────────────────────────────────


async def test_a_reader_notice_reaches_only_that_reader(database_url) -> None:
    conn = db.connect(database_url)
    try:
        one = _user(conn, user_id="user:one", email="one@example.test")
        two = _user(conn, user_id="user:two", email="two@example.test")
        _destination(conn, owner_id=one, subscribe=("brief.shared.published",), label="one")
        _destination(conn, owner_id=two, subscribe=("brief.shared.published",), label="two")

        assert (
            notify.raise_notice(
                conn, "brief.shared.published", user_id=one, brief_id="brief:1", title="T"
            )
            == 1
        )
        rows = conn.execute(
            "SELECT d.owner_id FROM notify_outbox o "
            "JOIN notify_destinations d ON d.id = o.destination_id"
        ).fetchall()
        assert [r["owner_id"] for r in rows] == [one]
    finally:
        conn.close()


async def test_an_instance_notice_never_reaches_a_reader(database_url) -> None:
    """The audience check is authorization, not tidiness.

    Without it a reader could point a webhook at `access.requested` and collect
    every stranger's address and self-description as they arrive.
    """
    conn = db.connect(database_url)
    try:
        reader = _user(conn, user_id="user:r", email="r@example.test")
        _destination(conn, owner_id=reader, subscribe=("access.requested",), label="sneaky")
        queued = notify.raise_notice(
            conn, "access.requested", email="x@y.test", role="analyst", request_id="req:1"
        )
        assert queued == 0
    finally:
        conn.close()


def test_a_reader_cannot_subscribe_to_an_instance_notice() -> None:
    with pytest.raises(policy.DestinationError):
        policy.check_notice_type("access.requested", audience="reader")
    with pytest.raises(policy.DestinationError):
        policy.check_notice_type("brief.personal.ready", audience="instance")
    assert policy.check_notice_type("brief.personal.ready", audience="reader").key


# ── dedupe ───────────────────────────────────────────────────────────


async def test_one_edition_delivers_once(database_url) -> None:
    """Keyed on the shared EDITION, never the calendar day (ADR 0185).

    A second run on the same edition -- a rewrite, a manual trigger -- must not
    tell every reader again that their brief is ready.
    """
    conn = db.connect(database_url)
    try:
        reader = _user(conn, user_id="user:d", email="d@example.test")
        _destination(conn, owner_id=reader, subscribe=("brief.shared.published",), label="d")
        first = notify.raise_notice(
            conn, "brief.shared.published", user_id=reader, brief_id="brief:2026-09-01", title="T"
        )
        second = notify.raise_notice(
            conn, "brief.shared.published", user_id=reader, brief_id="brief:2026-09-01", title="T"
        )
        assert (first, second) == (1, 0)
    finally:
        conn.close()


# ── delivery, strikes and the way back ───────────────────────────────


async def test_a_delivered_row_records_it_and_clears_the_strikes(database_url) -> None:
    conn = db.connect(database_url)
    try:
        destination_id = _destination(conn, subscribe=("run.failed",))
        notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed")
        client = _Recorder(status=200)
        assert await outbox.drain(conn, client, base="https://x.test") == 1
        row = conn.execute("SELECT status, delivered_at FROM notify_outbox").fetchone()
        assert row["status"] == "delivered" and row["delivered_at"]
        assert repo.destination(conn, destination_id=destination_id)["failures"] == 0
        # A Slack host gets the card, not the plain text.
        assert json.loads(client.calls[0][1])["blocks"][0]["type"] == "header"
    finally:
        conn.close()


async def test_three_consecutive_failures_strike_a_destination_out(database_url) -> None:
    """Consecutive, not a rate: a destination that failed one send in ten is
    working. `sources/registry.py` is the pattern and the number."""
    conn = db.connect(database_url)
    try:
        destination_id = _destination(conn, subscribe=("run.failed",))
        for n in range(outbox.STRIKE_LIMIT):
            notify.raise_notice(conn, "run.failed", run_id=f"run:{n}", status="failed")
            # Due immediately: the backoff would otherwise park the retry.
            conn.execute("UPDATE notify_outbox SET next_attempt_at = '2000-01-01T00:00:00.000Z'")
            conn.commit()
            await outbox.drain(conn, _Recorder(status=500, body="boom"), base="")
        destination = repo.destination(conn, destination_id=destination_id)
        assert destination["status"] == "struck_out"
        assert destination["failures"] >= outbox.STRIKE_LIMIT
        assert "500" in destination["last_error"]

        # A struck-out destination is skipped by the claim, so a broken endpoint
        # stops costing a request per pass.
        notify.raise_notice(conn, "run.failed", run_id="run:after", status="failed")
        conn.execute("UPDATE notify_outbox SET next_attempt_at = '2000-01-01T00:00:00.000Z'")
        conn.commit()
        quiet = _Recorder(status=200)
        await outbox.drain(conn, quiet, base="")
        assert quiet.calls == []

        # And the way back is reachable from the app, not a CLI incantation.
        repo.clear_strikes(conn, destination_id=destination_id, now="2026-09-02T00:00:00.000Z")
        conn.commit()
        assert repo.destination(conn, destination_id=destination_id)["status"] == "active"
    finally:
        conn.close()


async def test_a_permanent_status_does_not_retry(database_url) -> None:
    """A revoked webhook answers 404 forever. Retrying it six times only
    delays the strike that tells somebody."""
    conn = db.connect(database_url)
    try:
        _destination(conn, subscribe=("run.failed",))
        notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed")
        await outbox.drain(conn, _Recorder(status=404), base="")
        assert conn.execute("SELECT status FROM notify_outbox").fetchone()["status"] == "failed"
    finally:
        conn.close()


async def test_a_stalled_claim_is_reclaimed(database_url) -> None:
    """Every deploy of the api is stop then start, so a claim held when the
    process died is the ordinary case rather than a crash path."""
    conn = db.connect(database_url)
    try:
        _destination(conn, subscribe=("run.failed",))
        notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed")
        conn.execute(
            "UPDATE notify_outbox SET status = 'sending', updated_at = '2000-01-01T00:00:00.000Z'"
        )
        conn.commit()
        assert outbox.reclaim(conn) == 1
        assert conn.execute("SELECT status FROM notify_outbox").fetchone()["status"] == "queued"
    finally:
        conn.close()


# ── email without a transport ────────────────────────────────────────


async def test_an_email_subscription_parks_visibly(database_url) -> None:
    """Honoured, not refused, and never silently dropped.

    Skipping the enqueue would make a reader's switch look persisted and do
    nothing, which is the control `AccountView.vue` refused to ship.
    """
    conn = db.connect(database_url)
    try:
        reader = _user(conn, user_id="user:e", email="e@example.test")
        _destination(
            conn,
            kind="email",
            target="e@example.test",
            owner_id=reader,
            subscribe=("brief.personal.ready",),
            label="mine",
        )
        notify.raise_notice(conn, "brief.personal.ready", user_id=reader, brief_id="pbrief:1")
        row = conn.execute("SELECT status FROM notify_outbox").fetchone()
        assert row["status"] == "blocked"
        # And the drainer leaves it alone rather than counting a strike against
        # an address that is perfectly fine.
        client = _Recorder()
        assert await outbox.drain(conn, client, base="") == 0
        assert client.calls == []
    finally:
        conn.close()


# ── the SSRF guard ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/services/x",  # plaintext
        "https://127.0.0.1/hook",  # loopback literal
        "https://user:pw@hooks.slack.com/x",  # userinfo
        "https://hooks.slack.com:8080/x",  # port
        "file:///etc/passwd",
        "",
    ],
)
def test_a_destination_url_is_checked_at_save_time(url: str) -> None:
    """A refusal belongs in the form, with a sentence, not in a delivery that
    quietly never arrives. Same function `PUT /api/config/sources` runs."""
    with pytest.raises(policy.DestinationError):
        policy.check_webhook(url)


def test_a_good_url_survives() -> None:
    assert policy.check_webhook(" https://hooks.slack.com/services/T/B/x ").startswith("https://")


# ── rendering, and the untrusted half ────────────────────────────────


def test_a_strangers_words_cannot_reach_slacks_markup() -> None:
    """Every block is `plain_text`, venona's own included.

    `mrkdwn` would turn `<https://evil|click me>` inside a coverage note into a
    link, in a message the reader trusts because venona sent it.
    """
    notice = NOTICE_TYPES["access.requested"].render(
        {
            "email": "x@y.test",
            "role": "analyst",
            "request_id": "req:1",
            "coverage": "<https://evil.test|click me> *bold*",
        }
    )
    payload = json.loads(render.as_slack(notice, base="https://x.test"))
    for block in payload["blocks"]:
        for element in [block.get("text"), *(block.get("elements") or [])]:
            if isinstance(element, dict) and "type" in element and element["type"] != "button":
                assert element["type"] == "plain_text", element


def test_a_strangers_words_are_escaped_in_html() -> None:
    notice = NOTICE_TYPES["access.requested"].render(
        {"email": "x@y.test", "role": "analyst", "coverage": "<script>alert(1)</script>"}
    )
    _, _, body = render.as_email(notice, base="https://x.test")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_invisible_text_cannot_hide_inside_a_notice() -> None:
    """Scrubbed before escaped, and the order matters. Escaping alone leaves
    the zero-widths and bidi overrides that make injected text invisible to the
    person reading it."""
    hidden = "safe​‮reversed"
    notice = Notice(title="t", quoted=hidden)
    assert "​" not in render.as_text(notice)
    assert "‮" not in render.as_email(notice)[2]


def test_a_link_is_absolute_or_absent() -> None:
    """A relative path is no use in an inbox, and a broken link is worse than
    none. The base is resolved at SEND time, so a notice queued before an
    instance moved to its real domain still sends a working address."""
    notice = Notice(title="t", link="/admin/runs", link_label="Open")
    assert "https://x.test/admin/runs" in render.as_text(notice, base="https://x.test")
    assert "/admin/runs" not in render.as_text(notice, base="")


def test_a_plain_receiver_gets_plain_text() -> None:
    notice = Notice(title="t", body=("b",))
    content, headers = webhook.body_for(notice, "https://ntfy.sh/venona", base="")
    assert headers["Title"] == "t"
    assert content.decode().startswith("venona: t")
    assert not webhook.wants_json("https://ntfy.sh/venona")
    assert webhook.wants_json("https://hooks.slack.com/services/x")


# ── the registry's own words ─────────────────────────────────────────


def test_every_notice_type_is_written_for_a_reader() -> None:
    """The same three constraints `run_types` carries, for the same reasons.

    `undefined` is `scripts/ui_smoke.py`'s marker for a JS value rendered into
    the DOM, so a legitimate label carrying the word disarms that check for a
    whole page. The dashes and the vocabulary are `tests/test_copy_style.py`'s
    rules, and these strings ship to two settings screens.
    """
    repo_words = ("corpus", "entity", "assertion", "lane", "outbox", "projection", "gloss")
    for declared in NOTICE_TYPES.values():
        for text in (declared.label, declared.detail):
            assert text and text[0].isupper(), f"{declared.key}: {text!r}"
            assert "undefined" not in text, f"{declared.key}: {text!r}"
            assert "—" not in text and "–" not in text, f"{declared.key}: {text!r}"
            assert not any(w in text.lower() for w in repo_words), f"{declared.key}: {text!r}"
        assert declared.audience in ("instance", "reader"), declared.key


def test_every_notice_type_renders_from_empty_facts() -> None:
    """A renderer that needs a key the raise site forgot would raise inside
    `raise_notice`, which swallows -- so the notice would vanish rather than
    arrive wrong, and nothing would say so."""
    for declared in NOTICE_TYPES.values():
        notice = declared.render({})
        assert notice.title, declared.key
        assert declared.dedupe({}) is None or isinstance(declared.dedupe({}), str)


def test_the_owner_and_the_audience_are_the_same_two_words() -> None:
    """One distinction, one vocabulary. This is a regression test.

    `notify_destinations.owner_kind` and `NoticeType.audience` name the same
    thing, and for one commit they named it differently: the router wrote
    `reader` and the repository queried `user`. Nothing raised. A reader who had
    turned delivery on simply received nothing, and the only way to see it was
    to look at the row. Any future third caller has to agree with both.
    """
    from venona.api.routers import notifications as router
    from venona.notify.types import AUDIENCES, INSTANCE, READER

    assert set(AUDIENCES) == {"instance", "reader"}
    assert router._audience(None) == INSTANCE
    assert router._audience("user:someone") == READER
    # And the repository has to filter on the same words the router writes.
    source = __import__("pathlib").Path("backend/venona/db/repositories/notify.py").read_text()
    assert "owner_kind = 'user'" not in source
    assert "owner_kind = 'reader'" in source


def test_reader_notices_are_off_by_default() -> None:
    """Nobody has asked to be written to. A first delivery nobody opted into is
    the worst possible introduction to the feature."""
    for declared in NOTICE_TYPES.values():
        if declared.audience == "reader":
            assert declared.default_on is False, declared.key


# ── carrying the environment variable forward ────────────────────────


async def test_the_legacy_url_is_adopted_once(database_url) -> None:
    conn = db.connect(database_url)
    try:
        first = notify.adopt_legacy_url(conn, url="https://ntfy.sh/venona-ops")
        assert first is not None
        # Subscribed to everything, because the variable meant "tell me
        # everything" and quietly narrowing it is the worse surprise.
        assert set(repo.subscriptions(conn, destination_id=first)) == {
            t.key for t in policy.subscribable("instance")
        }
        # Idempotent: somebody who has been to the screen keeps their choices.
        assert notify.adopt_legacy_url(conn, url="https://ntfy.sh/venona-ops") is None
    finally:
        conn.close()


async def test_a_bad_legacy_url_does_not_stop_the_boot(database_url) -> None:
    """This runs in the lifespan. A refusal has to be a log line, not a
    container that will not start."""
    conn = db.connect(database_url)
    try:
        assert notify.adopt_legacy_url(conn, url="http://192.168.0.1/hook") is None
        assert repo.instance_destinations(conn) == []
    finally:
        conn.close()


# ── the reader's two notices ─────────────────────────────────────────


async def test_the_shared_brief_reaches_the_whole_readership(database_url) -> None:
    """Every subscribed reader, not only the recently-seen ones.

    `users.last_seen_at` bounds the personal-brief WARM PASS because generating
    one costs a model call. Telling somebody the brief landed costs one POST,
    and the reader who has not visited in a week is exactly who a message is
    for. Sharing `recently_seen` between the two would have merged them
    silently.
    """
    conn = db.connect(database_url)
    try:
        one = _user(conn, user_id="user:a", email="a@example.test")
        two = _user(conn, user_id="user:b", email="b@example.test")
        _destination(conn, owner_id=one, subscribe=("brief.shared.published",), label="a")
        _destination(conn, owner_id=two, subscribe=("brief.shared.published",), label="b")
        conn.execute(
            "INSERT INTO briefs (id, date, title, generated_at, created_at) "
            "VALUES ('brief:2026-09-01', '2026-09-01', 'Today', "
            "'2026-09-01T06:00:00.000Z', '2026-09-01T06:00:00.000Z')"
        )
        conn.commit()

        assert notify.announce_shared_brief(conn) == 2
        # And a rewrite of the same edition says nothing twice.
        assert notify.announce_shared_brief(conn) == 0
    finally:
        conn.close()


async def test_a_reader_who_pressed_the_button_is_not_told(database_url) -> None:
    """A message that arrives while somebody is watching the page render is
    noise, and noise teaches people to ignore the next one."""
    conn = db.connect(database_url)
    try:
        reader = _user(conn, user_id="user:w", email="w@example.test")
        _destination(conn, owner_id=reader, subscribe=("brief.personal.ready",), label="w")
        assert (
            notify.announce_personal_brief(
                conn, user_id=reader, brief_id="pbrief:1", reader_started=True
            )
            == 0
        )
        assert (
            notify.announce_personal_brief(
                conn, user_id=reader, brief_id="pbrief:1", reader_started=False
            )
            == 1
        )
    finally:
        conn.close()


def test_only_the_warm_pass_announces_a_personal_brief() -> None:
    """`announce` defaults to off, and exactly one call site turns it on.

    Two of the three callers run while the reader is on the page. If a fourth
    appears, this is the line that makes it a decision rather than an accident.
    """
    import inspect

    from venona.services import personal_brief

    signature = inspect.signature(personal_brief.start_generation)
    assert signature.parameters["announce"].default is False
    source = inspect.getsource(personal_brief)
    assert source.count("announce=True") == 1
