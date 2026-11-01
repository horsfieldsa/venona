"""One preview shape, for one launch dialog.

Four endpoints grew a price quote independently and reached the same discipline
by four sets of field names, so the dialog in front of each was a separate
component restating the same three sentences. Four chances to leave one out,
and one of them did: the corroboration sweep quoted money and no duration, so
an afternoon read like a coffee.

These hold the shape equal across every endpoint that feeds
`components/runs/RunLaunchDialog.vue`. A new launch that quotes a price adds a
row here, and a field renamed on one endpoint fails on all of them.
"""

from __future__ import annotations

import pytest

#: Every endpoint the shared dialog reads a quote from.
PREVIEWS = [
    "/api/glossary/lookup-all/preview",
    "/api/learning/generate/preview",
    "/api/runs/second-source/preview",
    "/api/entities/definitions/preview",
]

#: What the dialog renders, and therefore what every one of them must send.
REQUIRED = {
    "eligible",
    "will_attempt",
    "unit",
    "unit_plural",
    "per_item_usd",
    "estimate_usd",
    "per_item_seconds",
    "estimate_seconds",
    "cost_sample",
    "sample",
    "extra",
}


@pytest.mark.parametrize("path", PREVIEWS)
async def test_every_preview_serves_the_shared_shape(api_client, path: str) -> None:
    response = await api_client.get(path)
    assert response.status_code == 200, response.text
    assert REQUIRED <= set(response.json())


@pytest.mark.parametrize("path", PREVIEWS)
async def test_a_price_is_never_null(api_client, path: str) -> None:
    """A null price renders as a blank where a figure belongs, and a person
    authorising a spend reads a blank as free. With nothing to average the
    endpoint quotes its fallback and says so through `cost_sample: 0` -- which
    is what the dialog needs to tell a guess from a measurement.

    `generate_preview` sent null here, and it was the one launch in the app
    with no figure in front of it.
    """
    body = (await api_client.get(path)).json()
    assert isinstance(body["per_item_usd"], int | float)
    assert body["per_item_usd"] > 0
    assert isinstance(body["estimate_usd"], int | float)
    assert isinstance(body["cost_sample"], int)


@pytest.mark.parametrize("path", PREVIEWS)
async def test_the_unit_is_a_readers_word(api_client, path: str) -> None:
    """The dialog says "605 names" without knowing which run it is in front of,
    so the endpoint supplies the noun -- and it is the reader's noun, never this
    repo's. `entity`, `assertion` and `surface` are words a person has not
    heard."""
    body = (await api_client.get(path)).json()
    assert body["unit"] and body["unit_plural"]
    assert body["unit"] not in {"entity", "assertion", "surface", "cluster", "gloss"}
    assert body["unit_plural"] not in {"entities", "assertions", "surfaces", "clusters"}


@pytest.mark.parametrize("path", PREVIEWS)
async def test_will_attempt_never_exceeds_what_is_waiting(api_client, path: str) -> None:
    """The count the confirm is labelled with is what the RUN will do, not the
    length of the queue behind it. A preview counting the 303-row queue while
    the run attempted the 180 never tried quoted nearly double and was believed
    (2026-08-09)."""
    body = (await api_client.get(path)).json()
    assert 0 <= body["will_attempt"] <= body["eligible"]
