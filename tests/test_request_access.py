"""The only unauthenticated write in the application.

Every test here is about a property that would be a security or product bug
rather than a broken feature, because that is what this endpoint's surface is:
it is reachable by anyone on the internet, it writes to the system of record,
and it sits next to a sign-in allow-list it must never be able to widen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona.api import ratelimit
from venona.api.middleware import EXEMPT_PATHS
from venona.api.routers.access import ACKNOWLEDGED

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_buckets():
    """Bucket state is process-wide, so without this the order tests run in
    decides whether the rate-limit test sees a full bucket."""
    ratelimit.reset()
    yield
    ratelimit.reset()


async def test_a_request_is_recorded(api_client, api_db) -> None:
    response = await api_client.post(
        "/api/request-access",
        json={"email": "A.Reyes@Example.com", "coverage": "  ground autonomy  ", "role": "analyst"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["received"] is True

    row = api_db.execute("SELECT * FROM access_requests").fetchone()
    assert row is not None
    # Lower-cased on the way in, because `email_is_allowed` lower-cases on the
    # way out. Storing it as typed makes "did I already add this person?" a
    # comparison the operator has to remember to do case-insensitively.
    assert row["email"] == "a.reyes@example.com"
    assert row["coverage"] == "ground autonomy"
    assert row["status"] == "pending"


def test_it_needs_no_authentication() -> None:
    """The whole point. A visitor asking for an account does not have one."""
    assert "/api/request-access" in EXEMPT_PATHS


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-address", "role": "analyst"},
        {"email": "no@tld", "role": "analyst"},
        {"email": "a@b.com", "role": "spy"},
        {"email": "a@b.com"},
        {"role": "analyst"},
    ],
)
async def test_a_malformed_request_is_refused(api_client, payload) -> None:
    assert (await api_client.post("/api/request-access", json=payload)).status_code == 422


async def test_an_empty_coverage_note_is_stored_as_absent(api_client, api_db) -> None:
    """An empty textarea posts `""`, not absence. Storing that makes "did they
    tell us anything?" a two-case question forever."""
    await api_client.post(
        "/api/request-access",
        json={"email": "a@b.com", "coverage": "   ", "role": "press"},
    )
    assert api_db.execute("SELECT coverage FROM access_requests").fetchone()["coverage"] is None


async def test_a_duplicate_is_indistinguishable_from_a_first_request(api_client) -> None:
    """⭐ This endpoint must not become an account oracle.

    It is unauthenticated and it takes an email address, which is exactly the
    shape of a "does this person have an account here?" probe against a small,
    named user base. A different status, body or timing for a repeat submission
    would answer that question for free.
    """
    body = {"email": "a@b.com", "role": "investor"}
    first = await api_client.post("/api/request-access", json=body)
    second = await api_client.post("/api/request-access", json=body)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_it_cannot_grant_access(api_client) -> None:
    """Recording a request must never widen the sign-in allow-list.

    The allow-list is an environment variable read through `get_auth_config`,
    and `api/security.py` explains why it is deliberately not in the
    HTTP-writable settings document. This asserts the boundary rather than
    trusting the comment: submitting a request leaves `email_is_allowed`
    answering exactly what it answered before.
    """
    from venona.api.security import email_is_allowed, get_auth_config

    cfg = get_auth_config()
    before = email_is_allowed(cfg, "stranger@example.com")
    await api_client.post(
        "/api/request-access", json={"email": "stranger@example.com", "role": "press"}
    )
    assert email_is_allowed(get_auth_config(), "stranger@example.com") == before


def test_it_is_rate_limited_far_below_the_write_budget() -> None:
    """Left in the `write` bucket this would be a 60-per-minute anonymous
    INSERT. It gets its own, and a global ceiling on top -- per-IP alone is weak
    when the caller is a stranger who can change address."""
    assert ratelimit.bucket_for("/api/request-access", "POST") == ratelimit.SIGNUP
    assert ratelimit.LIMITS[ratelimit.SIGNUP].capacity <= 5
    assert ratelimit.SIGNUP in ratelimit.GLOBAL_BACKSTOP

    # A GET is not charged the signup budget: a preflight or a HEAD against the
    # form must not spend a third of somebody's hourly allowance.
    assert ratelimit.bucket_for("/api/request-access", "GET") == ratelimit.API


def test_the_bucket_actually_refuses_the_fourth_attempt() -> None:
    limiter = ratelimit.RateLimiter()
    verdicts = [limiter.check(ratelimit.SIGNUP, "1.2.3.4") for _ in range(4)]
    assert [v.allowed for v in verdicts] == [True, True, True, False]
    assert verdicts[-1].retry_after > 0


# ── the no-script path ───────────────────────────────────────────────


async def test_a_form_post_is_answered_with_a_page(api_client, api_db) -> None:
    """⭐ The reader with JavaScript off.

    The marketing form posts itself when its script does not run, and a
    form-encoded body reaching a JSON-only endpoint is a raw 422 — a worse
    experience than the leak it replaced. One endpoint answers both, because the
    funnel is one funnel and a second path for the no-script case is a second
    thing to keep in step.
    """
    response = await api_client.post(
        "/api/request-access",
        data={"email": "quiet@example.com", "role": "analyst", "coverage": "drones"},
    )

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/html")
    assert ACKNOWLEDGED in response.text
    # And it is a real request, not a placated browser.
    row = api_db.execute(
        "SELECT email, role FROM access_requests WHERE email = 'quiet@example.com'"
    ).fetchone()
    assert row is not None and row["role"] == "analyst"


async def test_a_form_post_is_validated_like_the_json_one(api_client, api_db) -> None:
    """The no-script path must not be a way to write a row the JSON path would
    refuse. Same model, same validators, both directions."""
    response = await api_client.post(
        "/api/request-access", data={"email": "a@b.com", "role": "spy"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert (
        api_db.execute(
            "SELECT COUNT(*) AS n FROM access_requests WHERE email = 'a@b.com'"
        ).fetchone()["n"]
        == 0
    )


async def test_the_json_contract_is_unchanged(api_client) -> None:
    """Every programmatic caller, and a separate front page whose script DID run,
    still gets JSON."""
    response = await api_client.post(
        "/api/request-access", json={"email": "scripted@example.com", "role": "analyst"}
    )

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["message"] == ACKNOWLEDGED
