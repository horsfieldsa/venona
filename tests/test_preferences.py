"""The reader's display preferences: one zone, written by its owner alone.

Served through `api_client`, which runs the real routers over a throwaway
database -- a service-level test would skip FastAPI's serialization, which is
where a `response_model` mismatch actually shows up.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_no_row_means_the_browsers_zone(api_client) -> None:
    """An absent row is the default, and the default is "ask the browser"."""
    response = await api_client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["timezone"] is None


async def test_a_saved_zone_comes_back_on_the_session(api_client) -> None:
    """THE ASSERTION THAT CATCHES THE TWO RESOLVERS DRIFTING APART.

    The write resolves its subject through `learning._actor`; the read resolves
    it through `_session_info`'s own `subject` local. They must name the same
    person. If they ever stop doing so the symptom is exactly this: a reader
    saves a zone, and the next page load hands back the old one.
    """
    saved = await api_client.patch("/api/preferences", json={"timezone": "Pacific/Auckland"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["timezone"] == "Pacific/Auckland"

    session = await api_client.get("/api/auth/session")
    assert session.json()["timezone"] == "Pacific/Auckland"


async def test_a_second_save_replaces_rather_than_duplicates(api_client) -> None:
    """The upsert is keyed on the reader, so moving cities is one row, not two."""
    await api_client.patch("/api/preferences", json={"timezone": "Europe/London"})
    second = await api_client.patch("/api/preferences", json={"timezone": "Asia/Tokyo"})

    assert second.json()["timezone"] == "Asia/Tokyo"
    session = await api_client.get("/api/auth/session")
    assert session.json()["timezone"] == "Asia/Tokyo"


@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_blank_clears_the_override(api_client, blank) -> None:
    """ "Clear this" and "never set it" are ONE state in the database.

    Two representations of "use the browser's zone" would render identically
    and drift, so the service normalises before it writes.
    """
    await api_client.patch("/api/preferences", json={"timezone": "Asia/Tokyo"})

    cleared = await api_client.patch("/api/preferences", json={"timezone": blank})

    assert cleared.status_code == 200
    assert cleared.json()["timezone"] is None
    session = await api_client.get("/api/auth/session")
    assert session.json()["timezone"] is None


@pytest.mark.parametrize(
    "bad", ["Mars/Olympus", "GMT+37", "Europe/Londonn", "'; DROP TABLE users; --"]
)
async def test_an_unknown_zone_is_refused_and_stores_nothing(api_client, bad) -> None:
    """Validated through the loader that will actually resolve the name, the
    way `config_service` validates `schedule.timezone`."""
    await api_client.patch("/api/preferences", json={"timezone": "Europe/London"})

    refused = await api_client.patch("/api/preferences", json={"timezone": bad})

    assert refused.status_code == 400
    session = await api_client.get("/api/auth/session")
    assert session.json()["timezone"] == "Europe/London", "a refused save must not overwrite"


async def test_the_route_takes_no_user_id(api_client) -> None:
    """The safety property is structural: there is no user id to substitute.

    A caller naming somebody else is not refused, because the field does not
    exist -- it is ignored, and their OWN zone is what moves.
    """
    response = await api_client.patch(
        "/api/preferences",
        json={"timezone": "Europe/London", "user_id": "user:somebody-else"},
    )

    assert response.status_code == 200
    assert response.json() == {"timezone": "Europe/London"}
