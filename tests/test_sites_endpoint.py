"""`GET /api/sources/sites` — the extension's site catalog.

This replaced a hand-written list of five hosts in the extension's
`manifest.json`. Nothing generated that list and nothing checked it, so it
drifted to five names against fourteen domains the corpus had actually produced
documents from — with every domain the discovery sweeps found invisible by
construction, because a `kind: discovery` source carries no `url`.

The tests that matter most here are not the union arithmetic. They are
`test_a_manually_ingested_host_is_never_offered` and the pattern-shape checks:
this endpoint's output feeds `chrome.permissions.request`, so a bad row is a
one-click grant to inject into, and POST the text of, every page on a host.
"""

from __future__ import annotations

import yaml

SITES = "/api/sources/sites"


def _add_document(conn, **overrides) -> None:
    """A minimally valid `documents` row.

    `source_id` defaults to a real configured source, because the endpoint
    filters on it — a helper defaulting to None would make every test that
    forgot to set it silently assert nothing.
    """
    row = {
        "id": "doc:test",
        "run_id": "run:test",
        "source_id": "defense_one",
        "url": "https://example.org/a",
        "url_key": "https://example.org/a",
        "title": "A title",
        "text_nfc": "body",
        "text_chars": 4,
        "content_hash": "hash",
        "site_domain": "example.org",
        "cluster_id": "cluster:test",
        "fetched_at": "2026-08-16T12:00:00Z",
        "created_at": "2026-08-16T12:00:00Z",
    }
    row.update(overrides)
    columns = ",".join(row)
    placeholders = ",".join("?" for _ in row)
    conn.execute(f"INSERT INTO documents ({columns}) VALUES ({placeholders})", tuple(row.values()))
    conn.commit()


def _by_domain(payload) -> dict:
    return {site["domain"]: site for site in payload["sites"]}


async def test_the_configured_feeds_are_offered_with_no_corpus(api_client) -> None:
    """The whole reason the union carries the feed half.

    The seeded documents have `source_id: None` (`services/seed.py`), so the
    corpus half contributes nothing here — which is exactly the fresh-checkout
    case. Without the configured half this endpoint would answer with an empty
    list on a machine that has simply not run yet, and the extension would offer
    nothing to grant.
    """
    payload = (await api_client.get(SITES)).json()
    sites = _by_domain(payload)

    assert "defenseone.com" in sites
    assert sites["defenseone.com"]["configured"] is True
    assert sites["defenseone.com"]["documents"] == 0
    assert sites["defenseone.com"]["last_seen_at"] is None
    # `www.` is stripped by `canonical_url` on the way in, and the feed host is
    # derived with that same function so the two halves cannot disagree.
    assert "www.defenseone.com" not in sites


async def test_the_match_pattern_is_built_server_side(api_client) -> None:
    """`match` goes straight into `chrome.permissions.request`, so it is minted
    and validated in one place rather than rebuilt by the client.

    The `*.` prefix is what recovers the `www.` the canonicaliser removed:
    Chrome matches `*.example.com` against `example.com` itself as well as its
    subdomains.
    """
    sites = _by_domain((await api_client.get(SITES)).json())
    assert sites["defenseone.com"]["match"] == "https://*.defenseone.com/*"


async def test_a_manually_ingested_host_is_never_offered(api_client, api_db) -> None:
    """THE security test.

    The toolbar button POSTs any http(s) page to `/ingest` with no allowlist —
    deliberately, because the article worth adding is usually from a source
    `sources.yaml` has never heard of — and those documents are stored under
    `MANUAL_SOURCE_ID`. If they reached this catalog, an intranet host or a
    webmail domain someone once ingested would appear as a row whose checkbox
    grants `https://*.<that-host>/*`.

    The hostname validator cannot catch this: `mail.internal.example` is
    perfectly well formed. Only the `source_id` filter can.
    """
    _add_document(
        api_db,
        id="doc:manual",
        source_id="manual",
        site_domain="webmail.example.net",
        url="https://webmail.example.net/inbox",
        url_key="https://webmail.example.net/inbox",
    )

    sites = _by_domain((await api_client.get(SITES)).json())
    assert "webmail.example.net" not in sites


async def test_a_document_from_an_unknown_source_is_not_offered(api_client, api_db) -> None:
    """The same filter from the other side: a `source_id` that is not in
    `sources.yaml` at all — a removed source, or a hand-written row — does not
    get to put a host in front of the user either."""
    _add_document(
        api_db,
        id="doc:orphan",
        source_id="a_source_that_was_deleted",
        site_domain="orphan.example",
        url="https://orphan.example/a",
        url_key="https://orphan.example/a",
    )

    assert "orphan.example" not in _by_domain((await api_client.get(SITES)).json())


async def test_a_discovery_domain_appears_and_is_not_marked_configured(api_client, api_db) -> None:
    """The case the feature exists for. A discovery sweep has no `url`, so its
    finds can never appear in a manifest anyone maintains by hand — the corpus
    is the only place they are visible."""
    _add_document(
        api_db,
        id="doc:discovered",
        source_id="sweep_edge_ai",
        site_domain="navalnews.com",
        url="https://navalnews.com/a",
        url_key="https://navalnews.com/a",
        publisher="Naval News",
    )

    site = _by_domain((await api_client.get(SITES)).json())["navalnews.com"]
    assert site["configured"] is False
    assert site["documents"] == 1
    assert site["publisher"] == "Naval News"
    assert site["match"] == "https://*.navalnews.com/*"


async def test_a_feed_host_and_its_corpus_rows_collapse_to_one_site(api_client, api_db) -> None:
    """`defense_one`'s feed is `www.defenseone.com`; `site_domain` is stored
    with `www.` stripped. Deriving the feed host with anything other than
    `canonical_url` would list the publisher twice — once at 0 documents and
    once with them."""
    _add_document(
        api_db,
        id="doc:do-1",
        source_id="defense_one",
        site_domain="defenseone.com",
        url="https://defenseone.com/a",
        url_key="https://defenseone.com/a",
    )

    payload = (await api_client.get(SITES)).json()
    matching = [s for s in payload["sites"] if s["domain"] == "defenseone.com"]
    assert len(matching) == 1
    assert matching[0]["configured"] is True
    assert matching[0]["documents"] == 1


async def test_duplicates_do_not_inflate_the_count(api_client, api_db) -> None:
    """`documents` is the number the user sorts by and reads as "how much of
    this site is in here". `duplicate_of` marks a non-canonical row, so counting
    it reports stored rows rather than articles."""
    _add_document(
        api_db,
        id="doc:canonical",
        source_id="darpa",
        site_domain="darpa.mil",
        url="https://darpa.mil/a",
        url_key="https://darpa.mil/a",
    )
    _add_document(
        api_db,
        id="doc:dupe",
        source_id="darpa",
        site_domain="darpa.mil",
        url="https://darpa.mil/b",
        url_key="https://darpa.mil/b",
        duplicate_of="doc:canonical",
    )

    assert _by_domain((await api_client.get(SITES)).json())["darpa.mil"]["documents"] == 1


async def test_a_malformed_site_domain_is_dropped_not_emitted(api_client, api_db) -> None:
    """A netloc can carry a port or userinfo, and `site_domain` is a raw netloc.

    Emitting `https://*.evil.example@good.example/*` or a host with a port would
    put a broken — or worse, a misleading — pattern in front of
    `chrome.permissions.request`. `urlguard.check_static` is what refuses these,
    which is the same guard the fetcher uses.
    """
    for index, bad in enumerate(
        ["host.example:8080", "good.example@evil.example", "nodot", "127.0.0.1"]
    ):
        _add_document(
            api_db,
            id=f"doc:bad-{index}",
            source_id="gao_reports",
            site_domain=bad,
            url=f"https://example.com/bad-{index}",
            url_key=f"https://example.com/bad-{index}",
        )

    domains = _by_domain((await api_client.get(SITES)).json())
    for bad in ["host.example:8080", "good.example@evil.example", "nodot", "127.0.0.1"]:
        assert bad not in domains


async def test_a_disabled_source_is_not_offered(api_client, api_settings) -> None:
    """`enabled: false` is how EXTENDING says to remove a source — its documents
    and assertions stay, and the graph is unaffected. Continuing to offer its
    host would make the extension keep annotating a publisher the user
    deliberately stopped following.

    Written into the sandbox's own `config_dir`, which also proves the endpoint
    reads `app.state.settings` rather than the repo's real file.
    """
    path = api_settings.config_dir / "sources.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["sources"].append(
        {
            "id": "sandbox_only_feed",
            "name": "Sandbox only",
            "kind": "rss",
            "url": "https://sandbox-only.example/feed.xml",
            "publisher": "Sandbox",
            "enabled": False,
        }
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    assert "sandbox-only.example" not in _by_domain((await api_client.get(SITES)).json())


async def test_an_enabled_sandbox_source_is_offered(api_client, api_settings) -> None:
    """The other half of the previous test: the sandbox file IS what is read, so
    an enabled source only present there must appear. Without this, the disabled
    case above would pass just as well if the endpoint read nothing at all."""
    path = api_settings.config_dir / "sources.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["sources"].append(
        {
            "id": "sandbox_live_feed",
            "name": "Sandbox live",
            "kind": "rss",
            "url": "https://sandbox-live.example/feed.xml",
            "publisher": "Sandbox Live",
            "enabled": True,
        }
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    site = _by_domain((await api_client.get(SITES)).json())["sandbox-live.example"]
    assert site["configured"] is True
    assert site["publisher"] == "Sandbox Live"


async def test_sites_are_sorted_by_document_count(api_client, api_db) -> None:
    """The options page is a list someone scans to decide what to grant. The
    site they have most of should not be below one they have never read."""
    _add_document(
        api_db,
        id="doc:many-1",
        source_id="war_on_the_rocks",
        site_domain="warontherocks.com",
        url="https://warontherocks.com/a",
        url_key="https://warontherocks.com/a",
    )

    payload = (await api_client.get(SITES)).json()
    assert payload["sites"][0]["domain"] == "warontherocks.com"


async def test_a_broken_sources_file_degrades_rather_than_500s(api_client, api_settings) -> None:
    """`load_sources` does a bare `read_text` and `parse_sources` raises on an
    invalid document. `enable_source` has the same exposure and gets away with
    it because nobody polls a mutation; this endpoint is on the extension's
    refresh alarm, so it answers with what it has instead."""
    (api_settings.config_dir / "sources.yaml").write_text("sources: [")

    response = await api_client.get(SITES)
    assert response.status_code == 200
    assert response.json()["sites"] == []
