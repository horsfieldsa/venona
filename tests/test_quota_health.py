"""What `/health` says about the subscription, through real serialization.

Service-level tests do not run FastAPI's `response_model`, and that filter is
exactly what could drop this block on the way to the wire: a field the model
omits vanishes silently. So this goes through `api_client`.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.db.repositories import quota as repo

pytestmark = pytest.mark.anyio

READING = {
    "observed_at": "2026-08-30T12:00:00Z",
    "resets_at": "2026-08-30T21:00:00Z",
    "status": "allowed_warning",
    "run_id": None,
}


async def test_an_instance_with_no_readings_says_so(api_client) -> None:
    """Distinguishable from a window at zero. A meter drawn at zero reads as an
    untouched subscription, which is the opposite of "not reported"."""
    res = await api_client.get("/api/health")

    quota = res.json()["quota"]
    assert quota["available"] is False
    assert quota["windows"] == []


async def test_every_window_reaches_the_wire(api_client, conn: db.Conn) -> None:
    repo.record_sample(
        conn,
        sample_id="a:seven_day",
        window_name="seven_day",
        utilization=0.8,
        representative=True,
        **READING,
    )
    repo.record_sample(
        conn,
        sample_id="a:five_hour",
        window_name="five_hour",
        utilization=0.1,
        representative=False,
        **READING,
    )

    res = await api_client.get("/api/health")

    quota = res.json()["quota"]
    assert quota["available"] is True
    assert quota["status"] == "allowed_warning"
    windows = {w["name"]: w for w in quota["windows"]}
    assert windows["seven_day"]["used"] == 0.8
    assert windows["seven_day"]["representative"] is True
    assert windows["five_hour"]["used"] == 0.1


async def test_used_stays_a_fraction_on_the_wire(api_client, conn: db.Conn) -> None:
    """Not pre-scaled to a percentage. The single conversion lives in the
    component that draws the bar, so no second copy can disagree with it."""
    repo.record_sample(
        conn,
        sample_id="a:seven_day",
        window_name="seven_day",
        utilization=0.8,
        representative=True,
        **READING,
    )

    res = await api_client.get("/api/health")

    assert res.json()["quota"]["windows"][0]["used"] == 0.8
