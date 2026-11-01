"""The one place a seat's entitlement is computed, for every consumer.

Three callers need the same answer and must not each derive it: the id_token
mint (`routers/oidc.py`), the push to an instance (`poller.py`), and the
operator page. Deriving it three times is how two of them end up disagreeing
about whether `past_due` still reads -- and the reader whose card is being
retried finds out by being locked out.

Returns a wire shape, not a row: `status`, `through`, `notice`, `notice_at`.
The words for a notice live in `web/src/lib/billing.ts`; this decides only
which kind applies.
"""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn
from hub.repositories import subscriptions as subs_repo


def _iso(value: Any) -> str | None:
    """ISO-8601 UTC with `Z`, the repo convention for a timestamp on the wire.

    psycopg hands back an aware datetime rendering as `+00:00`; every consumer
    of this parses `Z`. Converting here rather than at three call sites is the
    same argument as the module docstring.
    """
    if value is None:
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return text.replace("+00:00", "Z")


def for_seat(conn: Conn, *, user_id: str, instance_id: str) -> dict[str, Any]:
    """What this instance should be told about this reader.

    `latest_for` rather than `live_for`, because a lapsed reader's archive
    ceiling comes from a subscription in a terminal state. Asking only for live
    ones reports them as never having subscribed, which is a different screen
    and the wrong one (ADR 0211).
    """
    row = subs_repo.latest_for(conn, user_id=user_id, instance_id=instance_id)
    if row is None:
        return {"status": "none", "through": None, "notice": None, "notice_at": None}

    status = row["status"]
    if status in ("incomplete", "incomplete_expired"):
        # The first payment never succeeded, so they never became a customer.
        # NEVER-STARTED, not lapsed: a frozen archive they never paid for would
        # be a gift, and the screen that offers to start a trial is the honest
        # one.
        return {"status": "none", "through": None, "notice": None, "notice_at": None}

    entitled = status in subs_repo.ENTITLING
    return {
        # `past_due` reaches the instance as itself rather than as "active", so
        # the app can say what is happening. Access is unaffected -- that is
        # what `entitled` is for.
        "status": status if entitled else "lapsed",
        "through": _iso(row["current_period_end"]),
        "notice": row["notice"],
        "notice_at": _iso(row["notice_at"]),
    }


def entitled(state: dict[str, Any]) -> bool:
    """Whether a state reads. One predicate, so no caller re-spells it."""
    return state["status"] in subs_repo.ENTITLING


#: Why a stranger cannot buy a domain. Codes rather than sentences, because the
#: front door and the operator page say the same fact in different words: one is
#: talking to somebody deciding whether to subscribe, the other to the person
#: who has to go and fix it.
NO_PAYMENTS = "no_payments"
NO_PRICE = "no_price"
INVITE_ONLY = "invite_only"


def blockers(*, self_serve: bool, takes_payment: bool, price_id: str | None) -> list[str]:
    """Everything standing between a signed-in stranger and a subscription.

    Empty means the card sells. THE SAME ANSWER FOR BOTH SURFACES, for the
    reason at the top of this module: the gallery decides what button to draw
    and the operator page reports why it drew that one, and the two disagreeing
    is precisely the failure worth preventing. A hub with self-serve switched on
    and no prices showed `Request access` on every card while `/admin/settings`
    said the door was open, and nothing on either page named the gap.

    ALL OF THEM, not the first. Reported one at a time, fixing the price reveals
    the missing keys and fixing those reveals the switch, which is three trips
    to find out what was always one list.
    """
    missing = []
    if not takes_payment:
        missing.append(NO_PAYMENTS)
    if not price_id:
        missing.append(NO_PRICE)
    if not self_serve:
        missing.append(INVITE_ONLY)
    return missing
