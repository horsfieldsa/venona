"""Source config, HTTP transports, normalisation and dedup.

The dedup tests carry the most weight: getting `url_key` wrong means the same
article arrives every day forever, and getting the reprint case wrong means one
wire story dominates a brief with five near-identical items.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona.db import core as db
from venona.pipeline import dedup
from venona.pipeline.normalize import (
    build_document,
    canonical_url,
    content_hash,
    document_id,
    resolve_date,
    resolve_publisher,
)
from venona.sources.base import SourceSpec
from venona.sources.http import ReplayHttpClient
from venona.sources.registry import (
    STRIKE_LIMIT,
    SourceConfigError,
    load_sources,
    record_failure,
    record_success,
    source_order,
    sync_sources,
)
from venona.sources.rss import parse_feed

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── URL canonicalisation (dedup layer 1) ─────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Tracking params are the single biggest source of false uniqueness.
        (
            "https://example.com/a?utm_source=twitter&utm_medium=social",
            "https://example.com/a",
        ),
        ("https://example.com/a?fbclid=xyz", "https://example.com/a"),
        ("https://example.com/a?ref=newsletter", "https://example.com/a"),
        # www, scheme case, trailing slash, fragment
        ("https://WWW.Example.com/a/", "https://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        # AMP variants are the same article at a different address
        ("https://example.com/a/amp", "https://example.com/a"),
        ("https://example.com/a.amp", "https://example.com/a"),
        # Meaningful params survive, and are sorted for stability
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?id=7", "https://example.com/a?id=7"),
    ],
)
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_same_article_shared_two_ways_gets_one_id() -> None:
    """This is what makes a second consecutive run produce zero new documents."""
    a = "https://breakingdefense.com/2026/08/ngc2-award/?utm_source=x"
    b = "https://www.breakingdefense.com/2026/08/ngc2-award#top"
    assert document_id(a) == document_id(b)


def test_canonicalisation_does_not_over_collapse() -> None:
    """Over-collapsing merges genuinely different articles, which is much worse
    than a missed duplicate."""
    assert document_id("https://example.com/a") != document_id("https://example.com/b")
    assert document_id("https://example.com/a?id=1") != document_id("https://example.com/a?id=2")


# ── content hash (dedup layer 2) ─────────────────────────────────────


def test_content_hash_ignores_cosmetic_differences() -> None:
    """Syndicated copies differ in exactly these ways."""
    a = "The Army awarded the contract."
    b = "the  army   awarded the contract!"
    assert content_hash(a) == content_hash(b)


def test_content_hash_distinguishes_real_differences() -> None:
    assert content_hash("The Army awarded it.") != content_hash("The Navy awarded it.")


# ── dates: never silently default to now ─────────────────────────────


def test_feed_date_is_most_trusted() -> None:
    date, confidence = resolve_date(feed_date="2026-08-04T10:00:00Z", html=None)
    assert (date, confidence) == ("2026-08-04T10:00:00Z", "feed")


def test_date_parsed_from_html_when_feed_has_none() -> None:
    html = '<meta property="article:published_time" content="2026-08-03T09:15:00Z">'
    date, confidence = resolve_date(feed_date=None, html=html)
    assert date is not None and date.startswith("2026-08-03")
    assert confidence == "parsed"


@pytest.mark.negative
def test_missing_date_stays_missing() -> None:
    """Returning None is the honest answer. Defaulting to now() would corrupt
    every recency ranking, invisibly, because the value looks plausible."""
    date, confidence = resolve_date(feed_date=None, html="<p>no date here</p>")
    assert date is None
    assert confidence == "unknown"


@pytest.mark.negative
def test_absurd_html_dates_are_rejected() -> None:
    """Template defaults and far-future dates would skew ranking."""
    html = '<meta property="article:published_time" content="1970-01-01T00:00:00Z">'
    date, confidence = resolve_date(feed_date=None, html=html)
    assert confidence == "unknown"


# ── document construction ────────────────────────────────────────────


@pytest.mark.negative
def test_stub_content_is_skipped_not_stored() -> None:
    """A 60-character feed blurb analysed as if it were an article produces
    confident nonsense, so it must not become a document at all."""
    doc = build_document(
        url="https://example.com/a",
        title="Short",
        html="<p>Too short.</p>",
        text=None,
        source_id="s",
        publisher="P",
        feed_date=None,
        summary="also short",
    )
    assert doc is None


def test_document_text_is_nfc_normalised() -> None:
    body = "The Army awarded the contract to a vendor. " * 20
    doc = build_document(
        url="https://example.com/a",
        title="T",
        html=None,
        text=body,
        source_id="s",
        publisher="P",
        feed_date="2026-08-04T00:00:00Z",
    )
    assert doc is not None
    assert doc.text_chars > 400
    assert doc.date_confidence == "feed"


# ── the byline every document must have ──────────────────────────────


def test_a_document_with_neither_a_publisher_nor_a_page_falls_back_to_its_domain() -> None:
    """The bottom rung, reached when there is nothing else to read.

    A sweep spec carries no publisher, so a candidate the model returned without
    one landed `publisher = NULL` and the brief rendered the byline as the
    literal word `source · Aug 15`, with a blank line in its SOURCES footer.
    Two of the twelve documents cited in the first real brief were in that
    state -- both from sweeps, neither from RSS.

    `html=None` IS THE PRECONDITION, not incidental setup. This used to claim to
    reproduce the discovery path while passing an input that path never
    produces -- discovery items carry `body_html=None`, so `fetch.py` always
    fetches one -- and it went on asserting `navalnews.com` for the same URL
    that `test_the_page_rung_reaches_build_document` asserts `Naval News` for,
    one screen apart. Two tests stating contradictory rules about one document,
    with the stale one carrying the more emphatic rationale. What is true here
    is narrower: with no page to read, the domain is all there is.
    """
    body = "The Navy completed a long transit with the vehicle. " * 20
    doc = build_document(
        url="https://www.navalnews.com/orca-transit",
        title="Orca XLUUV completes 1,000-mile transit",
        html=None,
        text=body,
        source_id="sweep_undersea",
        publisher=None,
        feed_date="2026-08-15T00:00:00Z",
    )
    assert doc is not None
    # The domain, not a prettified guess: inventing `Naval News` from
    # `navalnews.com` is the mistake invariant 3 bans for definitions. Reading
    # it off the page, where the site says it itself, is not that.
    assert doc.publisher == "navalnews.com"


def test_a_real_publisher_is_never_overwritten_by_the_domain() -> None:
    body = "The Air Force completed the prototype phase. " * 20
    doc = build_document(
        url="https://executivegov.com/cca-c2e",
        title="DAF completes initial CCA C2 enclave testing",
        html=None,
        text=body,
        source_id="rss_executivegov",
        publisher="ExecutiveGov",
        feed_date="2026-08-14T00:00:00Z",
    )
    assert doc is not None
    assert doc.publisher == "ExecutiveGov"


@pytest.mark.parametrize(
    ("netloc", "expected"),
    [
        ("www.navalnews.com", "navalnews.com"),
        ("NavalNews.com", "navalnews.com"),
        ("localhost:8000", "localhost"),
        ("user@example.com:443", "example.com"),
        ("", None),
    ],
)
def test_the_domain_fallback_strips_what_does_not_belong_in_a_byline(
    netloc: str, expected: str | None
) -> None:
    assert resolve_publisher(None, netloc) == expected
    assert resolve_publisher("  ", netloc) == expected


# ── clustering ───────────────────────────────────────────────────────


def _doc(url: str, title: str, body: str, published: str, source: str = "s1"):
    return build_document(
        url=url,
        title=title,
        html=None,
        text=body,
        source_id=source,
        publisher=source,
        feed_date=published,
    )


def test_wire_reprint_joins_the_same_cluster(conn: db.Conn) -> None:
    """THE gate for S3.

    The same wire story republished by a second outlet under a different URL is
    a separate document (it really is a separate article) but must land in ONE
    cluster -- otherwise a single story fills the brief five times over.
    """
    body = (
        "The Army has awarded a contract for next generation command and control "
        "integration work, officials said on Tuesday. The award covers edge "
        "computing capability for forward units operating without reachback. "
    ) * 4

    first = _doc(
        "https://outlet-a.com/ngc2-award",
        "Army awards NGC2 work",
        body,
        "2026-08-04T10:00:00Z",
        "breaking_defense",
    )
    second = _doc(
        "https://outlet-b.com/2026/army-ngc2",
        "Army awards NGC2 work",
        body,
        "2026-08-04T12:00:00Z",
        "defense_news",
    )
    assert first and second

    out1 = dedup.classify(conn, first)
    dedup.persist(conn, out1, run_id="r", now="2026-08-04T13:00:00Z", sighting_id="s1")

    out2 = dedup.classify(conn, second)
    dedup.persist(conn, out2, run_id="r", now="2026-08-04T13:00:00Z", sighting_id="s2")

    assert out2.is_new, "a reprint is a real, separately-addressable document"
    assert out2.reason == "content_hash"
    assert out2.cluster_id == out1.cluster_id, "but it belongs to ONE cluster"

    counts = dedup.cluster_sighting_counts(conn)
    assert counts[out1.cluster_id] >= 2, "pickup count is the salience signal"


def test_identical_url_is_not_a_new_document(conn: db.Conn) -> None:
    body = "The Army awarded a contract for command and control work. " * 12
    doc = _doc("https://outlet-a.com/x?utm_source=a", "Award", body, "2026-08-04T10:00:00Z")
    assert doc

    out1 = dedup.classify(conn, doc)
    dedup.persist(conn, out1, run_id="r", now="n", sighting_id="s1")

    again = _doc("https://www.outlet-a.com/x", "Award", body, "2026-08-04T10:00:00Z")
    out2 = dedup.classify(conn, again)

    assert not out2.is_new
    assert out2.reason == "url_key"


def test_canonical_document_is_earliest_then_source_order(conn: db.Conn) -> None:
    """Frozen rule: analyze runs on the canonical document's text, so a
    wobbling choice would make runs non-reproducible."""
    body = "Army command and control award details follow in this article body. " * 12
    late = _doc(
        "https://b.com/x",
        "Same headline about the award",
        body,
        "2026-08-04T18:00:00Z",
        "defense_news",
    )
    early = _doc(
        "https://a.com/x",
        "Same headline about the award",
        body,
        "2026-08-04T06:00:00Z",
        "breaking_defense",
    )

    for i, doc in enumerate([late, early]):
        out = dedup.classify(conn, doc)
        dedup.persist(conn, out, run_id="r", now="n", sighting_id=f"sc{i}")

    dedup.refresh_clusters(conn, now="n", source_order={"breaking_defense": 0, "defense_news": 1})
    row = conn.execute(
        "SELECT canonical_doc_id FROM clusters WHERE id = ?",
        (dedup.classify(conn, early).cluster_id,),
    ).fetchone()
    assert row["canonical_doc_id"] == early.id


def test_undated_document_never_becomes_canonical(conn: db.Conn) -> None:
    body = "Award coverage text that is long enough to count as a real article. " * 12
    dated = _doc("https://a.com/y", "Headline", body, "2026-08-04T06:00:00Z")
    undated = _doc("https://b.com/y", "Headline", body, None)

    for i, doc in enumerate([undated, dated]):
        out = dedup.classify(conn, doc)
        dedup.persist(conn, out, run_id="r", now="n", sighting_id=f"su{i}")

    dedup.refresh_clusters(conn, now="n")
    cluster_id = dedup.classify(conn, dated).cluster_id
    row = conn.execute(
        "SELECT canonical_doc_id FROM clusters WHERE id = ?", (cluster_id,)
    ).fetchone()
    assert row["canonical_doc_id"] == dated.id


# ── source config (C9) ───────────────────────────────────────────────


def test_real_sources_config_loads() -> None:
    specs = load_sources(REPO_ROOT / "config")
    assert specs
    assert all(s.kind in {"rss", "discovery"} for s in specs)
    assert len({s.id for s in specs}) == len(specs), "ids must be unique"


@pytest.mark.negative
def test_unknown_kind_fails_at_load_not_at_runtime(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - {id: x, name: X, kind: telepathy, url: 'https://e.com'}\n"
    )
    with pytest.raises(SourceConfigError):
        load_sources(tmp_path)


@pytest.mark.negative
def test_duplicate_source_id_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text(
        "sources:\n"
        "  - {id: x, name: X, kind: rss, url: 'https://e.com/1'}\n"
        "  - {id: x, name: Y, kind: rss, url: 'https://e.com/2'}\n"
    )
    with pytest.raises(SourceConfigError):
        load_sources(tmp_path)


def test_source_order_is_declaration_order() -> None:
    specs = load_sources(REPO_ROOT / "config")
    order = source_order(specs)
    assert order[specs[0].id] == 0
    assert order[specs[-1].id] == len(specs) - 1


# ── source health / strike counter ───────────────────────────────────


def test_three_strikes_auto_disables(conn: db.Conn) -> None:
    """A moved feed must stop being retried and must be visible -- a silently
    empty source is indistinguishable from a quiet news day."""
    spec = SourceSpec(id="flaky", name="Flaky", kind="rss", url="https://e.com/f")
    sync_sources(conn, [spec], now="n")

    for expected in range(1, STRIKE_LIMIT + 1):
        assert record_failure(conn, "flaky", now="n", error="HTTP 404") == expected

    row = conn.execute(
        "SELECT auto_disabled, last_error FROM source_health WHERE source_id = 'flaky'"
    ).fetchone()
    assert row["auto_disabled"] == 1
    assert "404" in row["last_error"]


def test_success_clears_strikes(conn: db.Conn) -> None:
    spec = SourceSpec(id="blip", name="Blip", kind="rss", url="https://e.com/b")
    sync_sources(conn, [spec], now="n")
    record_failure(conn, "blip", now="n", error="timeout")

    record_success(conn, "blip", now="n", etag='"abc"', last_modified=None, item_count=5)
    row = conn.execute(
        "SELECT consecutive_failures, auto_disabled, etag FROM source_health "
        "WHERE source_id = 'blip'"
    ).fetchone()
    assert row["consecutive_failures"] == 0
    assert row["auto_disabled"] == 0
    assert row["etag"] == '"abc"', "etag is stored so the next run can 304"


# ── RSS parsing ──────────────────────────────────────────────────────


def test_parse_feed_extracts_items_and_dates() -> None:
    spec = SourceSpec(id="t", name="T", kind="rss", url="https://e.com/f", publisher="T")
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Army awards NGC2 work</title>
        <link>https://e.com/a</link>
        <pubDate>Mon, 04 Aug 2026 10:00:00 GMT</pubDate>
        <description>Summary text.</description>
      </item>
      <item><title>No link</title></item>
    </channel></rss>"""

    items = parse_feed(spec, feed)
    assert len(items) == 1, "entries without a link are unusable"
    assert items[0].title == "Army awards NGC2 work"
    assert items[0].published_at == "2026-08-04T10:00:00Z"
    assert items[0].date_confidence == "feed"


# ── replay transport ─────────────────────────────────────────────────


@pytest.mark.negative
async def test_replay_hard_fails_on_missing_recording(tmp_path: Path) -> None:
    """Never fall through to the network -- that is how an "offline" suite
    quietly starts hitting the internet and stops being reproducible."""
    client = ReplayHttpClient(tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        await client.get("https://example.com/never-recorded")
    assert "make record" in str(exc.value)


# ── the client must not ask for what it cannot read ──────────────────


def test_accept_encoding_only_advertises_decoders_we_have() -> None:
    """The regression that took out three sources for three days.

    `Accept-Encoding` was `gzip, deflate, br`, copied from a browser along with
    the rest of the request envelope. `httpx[http2]` ships no Brotli decoder,
    and httpx does not raise on an unknown `Content-Encoding` -- it falls back
    to identity and hands the caller raw compressed bytes. Every origin that
    preferred Brotli returned a 200 full of mojibake, feedparser found no
    entries, and DefenseScoop, Defense One and War on the Rocks each earned
    three strikes and switched themselves off.

    Asserted against httpx's OWN decoder table rather than against a literal, so
    adding the `brotli` extra makes this pass with `br` present instead of
    failing.
    """
    import httpx
    from venona.sources.http import DEFAULT_HEADERS

    advertised = {e.strip() for e in DEFAULT_HEADERS["Accept-Encoding"].split(",")}
    supported = set(httpx._decoders.SUPPORTED_DECODERS)

    assert advertised, "some encoding must be offered, or every response is uncompressed"
    assert advertised <= supported, (
        f"advertising {advertised - supported} which httpx cannot decode; "
        "httpx falls back to identity SILENTLY and the body reaches the parser as bytes"
    )


def test_an_undecodable_body_is_never_recorded_as_a_fixture() -> None:
    """A fixture is test data. Recording mojibake would have turned a live-only
    bug into a permanently green offline suite over three broken feeds."""
    from venona.sources.http import Response, _looks_recordable

    good = Response(url="u", status=200, text='<?xml version="1.0"?><rss><channel/></rss>')
    mojibake = Response(url="u", status=200, text="U�S���\x01�\x0c")
    empty = Response(url="u", status=200, text="")

    assert _looks_recordable(good)
    assert not _looks_recordable(mojibake)
    assert not _looks_recordable(empty)


# ── an empty feed says WHY ───────────────────────────────────────────


def test_an_undecoded_body_is_named_as_such_not_as_a_moved_feed() -> None:
    """The old message guessed "(moved or reformatted?)" for every empty parse.

    None of the three feeds had moved or reformatted anything, and feedparser
    knew: it set `bozo` and a `SAXParseException`. Throwing that away sent the
    investigation at the publishers for three runs.
    """
    from venona.sources.rss import diagnose_empty

    why = diagnose_empty("U�S����\x01�\x0c\x1c�c")

    assert "compression" in why
    assert "moved" not in why


def test_a_genuinely_empty_feed_says_so() -> None:
    """The one case where "moved, or a quiet day" is a fair guess -- and the only
    one it is now used for."""
    from venona.sources.rss import diagnose_empty

    why = diagnose_empty(
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Nothing</title></channel></rss>'
    )

    assert "lists no articles" in why


def test_malformed_xml_is_named_as_malformed_xml() -> None:
    from venona.sources.rss import diagnose_empty

    assert "not valid XML" in diagnose_empty("<rss><channel><title>oops</rss>")


# ── a switched-off source can be switched back on ────────────────────


def test_clearing_strikes_puts_a_source_back_in_the_rotation(
    conn: db.Conn,
) -> None:
    """`active_specs` excludes a disabled source from every run, so nothing but
    this clears the counter -- the source is shut out of the very run that would
    have cleared it. It was reachable only from the CLI, and the UI's advice was
    to go and type it."""
    from venona.sources import registry

    # `source_health` is keyed to `sources`, so register the spec the way a run
    # would rather than poking the health table directly.
    registry.sync_sources(
        conn,
        [SourceSpec(id="src-x", name="Src X", kind="rss", url="https://e.com/feed", publisher="X")],
        now="n",
    )
    for _ in range(registry.STRIKE_LIMIT):
        registry.record_failure(conn, "src-x", now="n", error="boom")

    row = conn.execute(
        "SELECT consecutive_failures, auto_disabled FROM source_health WHERE source_id = ?",
        ("src-x",),
    ).fetchone()
    assert row["auto_disabled"] == 1, "three strikes should switch it off"

    assert registry.clear_strikes(conn, "src-x")

    row = conn.execute(
        "SELECT consecutive_failures, auto_disabled, last_success_at FROM source_health "
        "WHERE source_id = ?",
        ("src-x",),
    ).fetchone()
    assert (row["consecutive_failures"], row["auto_disabled"]) == (0, 0)
    # Deliberately NOT a success: nothing has succeeded yet, and stamping one
    # would hide a still-broken source behind a fresh-looking timestamp.
    assert row["last_success_at"] is None


@pytest.mark.negative
def test_clearing_strikes_on_an_unknown_source_reports_it(conn: db.Conn) -> None:
    from venona.sources import registry

    assert not registry.clear_strikes(conn, "no-such-source")


# ── the byline ladder: what arrived, then the page, then the domain ──


def _page(
    site_name: str | None = None, *, body: str = "The Navy sailed. " * 40, head: str = ""
) -> str:
    """A page shaped like a real one.

    THE `<title>`, THE CANONICAL AND THE SOCIAL TAGS ARE NOT DECORATION. This
    helper used to emit `<html><head></head>` -- a shape no published article
    has -- which is the only reason the fall-through test below passed at all,
    and it is what hid the fact that the code under test was reading
    `trafilatura`'s six-source `sitename` cascade rather than the `og:site_name`
    the tests asserted. With a `<title>` present that cascade returns a title
    segment; with a canonical it returns a host; with `article:publisher` it
    returns a Facebook URL. The suite asserted one claim while the code made
    another, and every case in between went unseen.

    `head` appends the tags a particular case is about.
    """
    tag = f'<meta property="og:site_name" content="{site_name}"/>' if site_name else ""
    return (
        "<html><head>"
        "<title>Orca XLUUV completes 1,000-mile transit | Naval News</title>"
        '<link rel="canonical" href="https://www.navalnews.com/orca-transit"/>'
        '<meta name="twitter:site" content="@navalnews"/>'
        f"{tag}{head}"
        f"</head><body><article><p>{body}</p></article></body></html>"
    )


def test_the_page_names_itself_when_nothing_arrived_with_the_item() -> None:
    """The rung that was missing.

    `og:site_name` is the outlet naming itself, and it was in HTML the pipeline
    had already downloaded and parsed -- `extract_text` asks trafilatura for the
    body and never read the metadata beside it. Both of the corpus's two
    publisher-less documents would have come out as mastheads rather than bare
    domains.
    """
    assert resolve_publisher(None, "www.navalnews.com", html=_page("Naval News")) == "Naval News"


def test_a_page_with_no_name_of_its_own_falls_through_to_the_domain() -> None:
    """A REAL page with no `og:site_name` -- title, canonical and twitter tags
    all present. Every one of those is something a heuristic would happily
    return a name from, and none of them is the site naming itself."""
    assert resolve_publisher(None, "navalnews.com", html=_page(None)) == "navalnews.com"


@pytest.mark.parametrize(
    ("case", "head"),
    [
        # Each of these produced a real byline in the recorded corpus when this
        # read `trafilatura.extract_metadata(...).sitename` instead of the one
        # tag it claimed to. 18 of 125 recorded bodies yielded something that is
        # not a masthead; one reached `documents.publisher` and a paid analyze
        # call before anyone noticed.
        (
            "a Yoast publisher tag",
            '<meta property="article:publisher" content="https://www.facebook.com/DefenseNews"/>',
        ),
        (
            "a copyright notice",
            '<meta name="copyright" content="Copyright 2026 Sightline Media Group"/>',
        ),
        ("a dublin core publisher", '<meta name="dc.publisher" content="Sightline Media Group"/>'),
        ("an application name", '<meta name="application-name" content="DefenseNews"/>'),
    ],
)
@pytest.mark.negative
def test_only_og_site_name_counts_as_the_page_naming_itself(case: str, head: str) -> None:
    """ONE TAG. `og:site_name` is the outlet naming itself; the rest are
    adjacent facts that a cascade will happily promote to a byline.

    A Facebook URL and a copyright sentence are both strictly worse than the
    domain they displaced -- a bare domain is visibly derived, where a sentence
    reads as real attribution.
    """
    assert resolve_publisher(None, "defensenews.com", html=_page(None, head=head)) == (
        "defensenews.com"
    ), case


def test_the_headline_is_never_the_byline() -> None:
    """The recorded case, exactly: `DARPA, U.S. Air Force fly AI-controlled
    F-16` became a publisher because `U.S.` supplies the dot `extract_sitename`
    looks for in a `<title>` segment. 44 characters, so the length guard passed
    it, and it is still in `documents.publisher` today."""
    page = _page(None).replace(
        "<title>Orca XLUUV completes 1,000-mile transit | Naval News</title>",
        "<title>DARPA, U.S. Air Force fly AI-controlled F-16</title>",
    )
    assert resolve_publisher(None, "www.darpa.mil", html=page) == "darpa.mil"


@pytest.mark.parametrize(
    "site_name",
    [
        # A URL, and a bare domain wearing a masthead's hat. Both are the last
        # rung's job, and the last rung does them properly -- it strips
        # userinfo, the port and `www.`, none of which a page-supplied string
        # has been through. Letting these past produced `www.army.mil` as a
        # byline where every other document from that host said `army.mil`, and
        # `tracker@cdn.example.com:8443` from a canonical with userinfo in it.
        "https://www.navalnews.com",
        "navalnews.com",
        "www.navalnews.com",
    ],
)
@pytest.mark.negative
def test_a_domain_shaped_site_name_defers_to_the_domain_rung(site_name: str) -> None:
    assert resolve_publisher(None, "www.navalnews.com", html=_page(site_name)) == "navalnews.com"


def test_a_dotted_masthead_with_spaces_is_still_a_masthead() -> None:
    """The domain guard keys on "no spaces AND a dot", so a real name keeps its
    full stops."""
    assert (
        resolve_publisher(None, "usni.org", html=_page("U.S. Naval Institute"))
        == "U.S. Naval Institute"
    )


@pytest.mark.parametrize(
    "tag",
    [
        '<meta name="og:site_name" content="Naval News"/>',
        '<meta content="Naval News" property="og:site_name"/>',
        "<meta property='og:site_name' content='Naval News'>",
    ],
)
def test_the_tag_is_read_in_the_shapes_pages_actually_write_it(tag: str) -> None:
    """`property` is the OG spec and `name` is what several CMSs emit; `content`
    lands on either side of it; quoting varies. All four combinations are one
    outlet naming itself."""
    assert resolve_publisher(None, "navalnews.com", html=_page(None, head=tag)) == "Naval News"


def test_entities_in_a_masthead_are_decoded() -> None:
    """`C4ISR &amp; Networks` is an escaped ampersand, not part of the name."""
    assert (
        resolve_publisher(None, "c4isrnet.com", html=_page("C4ISR &amp; Networks"))
        == "C4ISR & Networks"
    )


@pytest.mark.parametrize(
    "site_name",
    [
        "Home",
        "news",
        "Untitled",
        # A slogan rather than a masthead. `og:site_name` is free text and sites
        # put what they like in it; a sentence in a byline is worse than a domain.
        "The world's most trusted source for defense and aerospace news since 1921",
    ],
)
@pytest.mark.negative
def test_a_useless_site_name_falls_through_rather_than_becoming_a_byline(site_name: str) -> None:
    assert resolve_publisher(None, "example.com", html=_page(site_name)) == "example.com"


@pytest.mark.parametrize(
    "broken", ["<html><head><meta", "", "not html at all", "<meta og:site_name>"]
)
@pytest.mark.negative
def test_malformed_html_costs_the_byline_and_not_the_document(broken: str) -> None:
    """A page that is not a page must still produce a document with a usable
    byline.

    This once sat behind a bare `except Exception`, which the test claimed to
    cover and never reached -- lxml's recovering parser does not raise on
    truncated HTML, so the handler had zero coverage while catching a
    `TypeError` from an upstream API rename identically to a broken page. A
    trafilatura release renaming `sitename` would have degraded every byline to
    the domain, permanently, with a green suite and nothing in the run log.
    There is no parser to break now: a regex that does not match returns None,
    which is the same answer said out loud.
    """
    assert resolve_publisher(None, "example.com", html=broken) == "example.com"


# ── the model's rung: an assertion, not an observation ────────────────


def test_a_model_asserted_publisher_defers_to_the_page() -> None:
    """`RawItem.publisher` carries two provenances: a hand-written name from
    `sources.yaml` and an unverified guess from a discovery sweep. They shared
    the top rung, so a model's guess outranked the site telling us its own name
    -- backwards on this ladder's own reasoning. `publisher_asserted` is what
    separates them.
    """
    assert (
        resolve_publisher(
            "Naval News Daily", "navalnews.com", html=_page("Naval News"), publisher_asserted=True
        )
        == "Naval News"
    )


def test_a_model_asserted_publisher_still_beats_the_domain() -> None:
    """It was right for 4 of the 6 sweep documents in the first corpus. Below
    the page, above nothing at all."""
    assert (
        resolve_publisher("Naval News", "navalnews.com", html=_page(None), publisher_asserted=True)
        == "Naval News"
    )


def test_a_configured_name_outranks_everything() -> None:
    """Someone wrote it down. A feed we named `Defense News` must not be
    relabelled by whatever its CMS emits."""
    assert (
        resolve_publisher("Defense News", "defensenews.com", html=_page("Sightline Media Group"))
        == "Defense News"
    )


def test_the_page_rung_reaches_build_document() -> None:
    """End to end, because the wiring is the part that was absent -- the helper
    existing and `build_document` not passing `html` to it would look identical
    from the unit tests above.

    A DIFFERENT URL from the no-html test near the top of this file, on purpose:
    the two used to be the same document with opposite expected bylines.
    """
    doc = build_document(
        url="https://www.navalnews.com/2026/08/orca-second-transit",
        title="Orca XLUUV completes a second transit",
        html=_page("Naval News"),
        text=None,
        source_id="sweep_undersea",
        publisher=None,
        feed_date="2026-08-15T00:00:00Z",
    )
    assert doc is not None
    assert doc.publisher == "Naval News"


def test_a_scoped_refresh_matches_the_whole_corpus_pass(conn: db.Conn) -> None:
    """⭐ The "add article" click that grows daily.

    A cluster's canonical document is decided by its own members and nothing
    else, so recomputing one is independent of every other — which makes the
    whole-corpus pass on a single-article ingest pure waste, and waste that grows
    by one cluster a day while the work stays one article.

    What must not change is the ANSWER: contract C7 freezes the canonical
    choice, so the scoped pass has to agree with the full one exactly.
    """
    body = "Award coverage text long enough to count as a real article body. " * 12
    order = {"breaking_defense": 0, "defense_news": 1}
    for index in range(4):
        for source, hour in (("defense_news", "18"), ("breaking_defense", "06")):
            doc = _doc(
                f"https://{source}.com/story{index}",
                f"Same headline about award {index}",
                body,
                f"2026-08-04T{hour}:00:00Z",
                source,
            )
            out = dedup.classify(conn, doc)
            dedup.persist(conn, out, run_id="r", now="n", sighting_id=f"s{source}{index}")

    dedup.refresh_clusters(conn, now="n", source_order=order)
    full = {
        row["id"]: (row["canonical_doc_id"], row["published_at"], row["first_seen"])
        for row in conn.execute(
            "SELECT id, canonical_doc_id, published_at, first_seen FROM clusters"
        ).fetchall()
    }
    assert len(full) >= 4

    one = next(iter(full))
    recomputed = dedup.refresh_clusters(conn, now="n", source_order=order, cluster_ids=[one])

    assert recomputed == 1, "a scoped refresh must touch exactly the clusters it was given"
    after = {
        row["id"]: (row["canonical_doc_id"], row["published_at"], row["first_seen"])
        for row in conn.execute(
            "SELECT id, canonical_doc_id, published_at, first_seen FROM clusters"
        ).fetchall()
    }
    assert after == full, "the scoped pass disagreed with the whole-corpus pass"
