"""URL canonicalisation, text extraction, and date resolution.

Three things here are load-bearing for everything downstream:

  * `url_key` is dedup layer 1 and the document id. Getting it wrong means the
    same article arrives twice every day, forever.
  * `text_nfc` is stored VERBATIM and never re-normalised. Every evidence span
    and every acronym grounding offset points into it.
  * `date_confidence` exists because feeds lie. Nothing here ever defaults a
    missing date to "now" -- that silently corrupts recency ranking, and the
    corruption is invisible because the value looks plausible.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura
from dateutil import parser as dateparser

# Tracking and campaign parameters. Stripping these is what makes the same
# article shared from two places collapse to one document.
#
# Prefixes and EXACT names are separate because a single alternation under
# `re.match` with no `$` makes every entry a prefix: `ref` also matches
# `reference` and `referrer`, `source` matches `sourceId`, `sh` matches
# `showAll` and `shortname`, `ito` matches `itemId`. Those name genuinely
# different articles, so two distinct URLs collapse to one `url_key` and one of
# the two is dropped as a duplicate that never existed.
TRACKING_PREFIXES = ("utm_",)

TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "igshid",
        "ref",
        "ref_src",
        "source",
        "cmpid",
        "sh",
        "s_cid",
        "ito",
        "smid",
        "partner",
        # Google News serves the same article under `?output=rss` and friends.
        "output",
    }
)


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


MIN_BODY_CHARS = 400

# Bounds for a plausible publication year (see _date_from_html).
MIN_PLAUSIBLE_YEAR = 2000
MAX_PLAUSIBLE_YEAR = 2100


@dataclass
class Document:
    id: str
    url: str
    url_key: str
    title: str
    text_nfc: str
    content_hash: str
    site_domain: str
    source_id: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    date_confidence: str = "unknown"
    excerpt: str | None = None
    paywalled: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def text_chars(self) -> int:
        return len(self.text_nfc)


def canonical_url(url: str) -> str:
    """Dedup layer 1.

    Deliberately conservative: over-collapsing merges genuinely different
    articles, which is much worse than a missed duplicate.
    """
    parts = urlsplit(url.strip())

    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.removesuffix(":80").removesuffix(":443")

    path = parts.path or "/"
    # AMP variants are the same article at a different address.
    path = re.sub(r"/amp/?$", "", path)
    path = re.sub(r"\.amp$", "", path)
    if len(path) > 1:
        path = path.rstrip("/")

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not _is_tracking_param(k)
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, host, path, query, ""))


def url_key_for(url: str) -> str:
    return canonical_url(url)


def document_id(url: str) -> str:
    return "doc:" + hashlib.sha256(url_key_for(url).encode()).hexdigest()[:24]


def content_hash(text: str) -> str:
    """Dedup layer 2: catches wire reprints published under different URLs.

    Normalised aggressively (case, whitespace, punctuation) because syndicated
    copies differ in exactly those ways.
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def resolve_date(
    *,
    feed_date: str | None,
    html: str | None,
    header_date: str | None = None,
) -> tuple[str | None, str]:
    """Return (iso_date, confidence).

    Confidence, most to least trusted: `feed` > `parsed` (from the page) >
    `header` (HTTP Last-Modified, which often reflects a CDN touch rather than
    publication) > `unknown`.

    Returning None is a valid, honest answer. Do not be tempted to fall back to
    the current time.
    """
    if feed_date:
        return feed_date, "feed"

    if html:
        extracted = _date_from_html(html)
        if extracted:
            return extracted, "parsed"

    if header_date:
        try:
            parsed = dateparser.parse(header_date)
            if parsed:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "header"
        except (ValueError, OverflowError, TypeError):
            pass

    return None, "unknown"


_DATE_META = [
    re.compile(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)', re.I
    ),
    re.compile(
        r'<meta[^>]+name=["\'](?:pubdate|publishdate|date)["\'][^>]+content=["\']([^"\']+)', re.I
    ),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
]


def _date_from_html(html: str) -> str | None:
    for pattern in _DATE_META:
        match = pattern.search(html)
        if not match:
            continue
        try:
            parsed = dateparser.parse(match.group(1))
        except (ValueError, OverflowError, TypeError):
            continue
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            # Epoch defaults (1970), unset template values (0001, 9999) and
            # placeholder years are what actually arrives here, and a FIXED
            # range catches all of them. Not `datetime.now().year`: a fixture
            # recorded today would replay differently next year, which is what
            # decision 7 exists to prevent (`make lint-determinism`).
            if MIN_PLAUSIBLE_YEAR <= parsed.year <= MAX_PLAUSIBLE_YEAR:
                return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


PAYWALL_MARKERS = (
    "subscribe to continue",
    "subscribers only",
    "to continue reading",
    "create a free account",
    "this content is for members",
)


def extract_text(html: str) -> tuple[str | None, bool]:
    """Main-article text via trafilatura. Returns (text, looks_paywalled).

    TWO PASSES, and the second one is not a refinement of the first. Precision
    mode drops a block it is unsure about, which is the right trade against
    boilerplate -- but on some markup it is unsure about the whole article and
    returns NOTHING, which is not a cleaner extraction, it is a lost document.

    Measured 2026-09-11: `federalnewsnetwork.com` had offered 540 items over
    this corpus's whole history and kept ONE. The page fetches fine, and bare
    `trafilatura.extract` reads 9,327 characters out of the same bytes that
    `favor_precision=True` reduces to zero. It read as a thin feed for months
    because `build_document` returns None for an empty body and an 80-character
    blurb alike, so a source losing everything to an extraction flag looked
    exactly like a source with nothing to say.

    The fallback fires only on a TOTAL loss, so it trades no precision away:
    there is no quality choice between a worse extraction and no document.
    """
    common = {"include_comments": False, "include_tables": False}
    text = trafilatura.extract(html, favor_precision=True, **common)
    if not text:
        text = trafilatura.extract(html, **common)
    if not text:
        return None, False

    lowered = text[:1500].lower()
    paywalled = any(marker in lowered for marker in PAYWALL_MARKERS)
    return text.strip(), paywalled


# A masthead is a name, not a sentence. Anything longer is a tagline, a slogan,
# or the whole `<title>` -- `og:site_name` is a free-text field and sites put
# what they like in it.
MAX_SITENAME_CHARS = 60

# Values that parse as a site name and mean nothing as a byline. Deliberately
# tiny: this is for the degenerate cases a length check cannot catch, not a
# quality filter on real mastheads.
JUNK_SITENAMES = frozenset({"home", "homepage", "index", "news", "untitled", "article"})

# ONE TAG, in both attribute orders. Sites write `property` (the OG spec) and
# `name` (what several CMSs emit) about equally often, and put `content` on
# either side of it.
#
# This is a targeted regex and NOT `trafilatura.extract_metadata(...).sitename`.
# That field is documented as `og:site_name` and is not: verified against
# trafilatura 2.2.0 it cascades og:site_name -> `article:publisher` ->
# `dc.publisher`/`copyright` -> `twitter:site` -> any `<title>` segment
# containing a dot -> the canonical URL's host. Measured 2026-08-16 against
# this repo's own recorded pages it produced the article HEADLINE `DARPA, U.S.
# Air Force fly AI-controlled F-16` (`U.S.` supplies the dot it looks for),
# `https://www.facebook.com/DefenseNews` from a Yoast tag, and `Copyright 2026
# Sightline Media Group` from a `copyright` meta -- 18 of 125 bodies yielding
# something that is not a masthead, one of which reached `documents.publisher`
# and a paid analyze call before anyone noticed. A byline that reads as a
# sentence is worse than a bare domain, which at least looks derived.
#
# It is also the cheaper call. `extract_metadata` runs author, category,
# licence and an `htmldate` extensive search and throws all of it away: 1386ms
# across the recorded corpus beside `extract_text`'s own 1471ms (2026-08-16),
# 70% of it `extract_author`, from a synchronous call inside `async` stages.
# And with `date_config=None` it reaches `set_date_params`, which is a
# `datetime.now()` -- banned in `pipeline/`, and invisible to
# `lint_determinism.py` because that greps source text and cannot see a
# transitive call.
#
# `([\"'])([^>]*?)\1` rather than `[\"']([^\"']*)[\"']`: the closing quote must
# be the SAME character as the opening one, or every masthead truncates at its
# first apostrophe -- `Jane's Defence Weekly` becomes `Jane`, and a slogan long
# enough to be rejected becomes a short one that passes.
#
# `[^>]` rather than `.` inside the value, because `.` crosses the tag
# boundary: with two meta tags in a row it starts at the first, runs the value
# through `"/><meta content=` and matches `og:site_name` on the SECOND,
# yielding the first tag's content glued to the markup between them.
_OG_SITE_NAME = re.compile(
    r"<meta[^>]+?(?:property|name)=[\"']og:site_name[\"'][^>]*?content=([\"'])([^>]*?)\1",
    re.IGNORECASE,
)
_OG_SITE_NAME_REVERSED = re.compile(
    r"<meta[^>]+?content=([\"'])([^>]*?)\1[^>]*?(?:property|name)=[\"']og:site_name[\"']",
    re.IGNORECASE,
)


def publisher_from_page(html: str | None) -> str | None:
    """The site's own masthead, from the page we already fetched.

    `og:site_name` is the outlet naming itself -- `Naval News`, not
    `navalnews.com` -- and it sits in HTML this module already holds in memory.

    Rung 2 of `resolve_publisher`: reached when no configured name arrived with
    the item (2 of 181 documents in the first corpus, 2026-08-16), and whenever
    the name that did arrive is a model's assertion.

    GUARDED, because this is page-controlled text rather than something we
    configured. A site that puts its slogan in `og:site_name` should fall
    through to the domain, not put a sentence in a byline. Nothing here
    title-cases or otherwise tidies the value: prettifying is inventing, and
    `c4isrnet` is what that page says its name is.
    """
    if not html:
        return None
    found = _OG_SITE_NAME.search(html) or _OG_SITE_NAME_REVERSED.search(html)
    if not found:
        return None
    name = " ".join(unescape(found.group(2)).split())
    if not name or len(name) > MAX_SITENAME_CHARS or name.lower() in JUNK_SITENAMES:
        return None
    # A URL, or a bare domain wearing a masthead's hat. Both are the domain
    # rung's job, and it does them properly -- it strips userinfo, the port and
    # `www.`, none of which this value has been through. `U.S. Naval Institute`
    # keeps its dots because it also has spaces; `navalnews.com` does not.
    if "://" in name or ("." in name and " " not in name):
        return None
    return name


def resolve_publisher(
    publisher: str | None,
    site_domain: str,
    *,
    html: str | None = None,
    publisher_asserted: bool = False,
) -> str | None:
    """A byline for every document: what we configured, the page, the model, the domain.

    Four rungs, most authoritative first.

    1. A CONFIGURED NAME, from `sources.yaml` (`spec.publisher or spec.name`).
       Someone wrote it down; it beats anything derived.
    2. THE PAGE'S OWN NAME. `og:site_name` from the HTML already fetched.
    3. A MODEL'S ANSWER, which is what `publisher` carries for a discovery
       sweep -- right for 4 of the 6 sweep documents in the first corpus
       (2026-08-16), and an assertion rather than an observation for all 6.
    4. THE DOMAIN, which is derived from the URL and cannot be wrong.

    RUNGS 1 AND 3 ARE THE SAME FIELD, which is why `publisher_asserted` exists.
    `RawItem.publisher` carries a hand-written name for RSS and an unverified
    model guess for discovery, and without a flag saying which they share a
    rung -- putting the guess above the page telling us its own name.

    The scar (2026-08-16): a sweep candidate with no `publisher` fell back to
    `spec.publisher`, which is `None` for every sweep because a sweep is not a
    publisher, and nothing downstream looked anywhere else -- the publisher was
    treated as metadata that ARRIVES with an item rather than as a property of
    the document being built. The brief rendered the byline as the literal word
    `source · Aug 15`, with a blank line in its SOURCES footer, on two of the
    twelve documents it cited, both from sweeps; the 175 RSS documents had no
    gaps at all. `llm/schemas.py::discovery_schema` now requires and describes
    the field, and this ladder is what stands under an omission anyway.

    The last rung is never a prettified guess. `navalnews.com` is a fact about
    the URL; turning it into `Naval News` would be this module inventing a
    masthead, which is the same class of mistake invariant 3 bans for
    definitions. Rung 2 is not that -- there the site is telling us its own name.
    """
    cleaned = (publisher or "").strip()
    if cleaned and not publisher_asserted:
        return cleaned
    from_page = publisher_from_page(html)
    if from_page:
        return from_page
    if cleaned:
        return cleaned
    # `netloc` carries the port and any userinfo; neither belongs in a byline.
    host = site_domain.rpartition("@")[2].partition(":")[0].strip().lower()
    return host.removeprefix("www.") or None


def build_document(
    *,
    url: str,
    title: str,
    html: str | None,
    text: str | None,
    source_id: str | None,
    publisher: str | None,
    feed_date: str | None,
    header_date: str | None = None,
    summary: str | None = None,
    publisher_asserted: bool = False,
) -> Document | None:
    """Assemble a Document, or None if there is not enough text to be useful.

    Returning None rather than a stub matters: a 60-character feed blurb
    analysed as if it were an article produces confident nonsense.
    """
    body = text
    paywalled = False
    if body is None and html:
        body, paywalled = extract_text(html)

    if not body or len(body) < MIN_BODY_CHARS:
        if not summary or len(summary) < MIN_BODY_CHARS:
            return None
        body = summary

    body = unicodedata.normalize("NFC", body).strip()
    published, confidence = resolve_date(feed_date=feed_date, html=html, header_date=header_date)

    canonical = canonical_url(url)
    site_domain = urlsplit(canonical).netloc
    return Document(
        id=document_id(url),
        url=url,
        url_key=canonical,
        title=unicodedata.normalize("NFC", title).strip(),
        text_nfc=body,
        content_hash=content_hash(body),
        site_domain=site_domain,
        source_id=source_id,
        publisher=resolve_publisher(
            publisher, site_domain, html=html, publisher_asserted=publisher_asserted
        ),
        published_at=published,
        date_confidence=confidence,
        excerpt=(summary or body[:280]).strip(),
        paywalled=paywalled,
    )
