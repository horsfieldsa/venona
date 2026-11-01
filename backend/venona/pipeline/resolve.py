"""Surface -> canonical entity id. The highest-risk component in the system.

The model returns SURFACES ("the Army", "NGC2", "Anduril"); this module decides
what they refer to. Everything downstream inherits its mistakes, and its
mistakes are quiet: a wrongly-merged entity produces a graph that looks fine and
answers questions incorrectly.

So the ladder is ordered cheapest-and-safest first, and every rung that could be
wrong prefers to mint a new provisional entity over guessing:

  1. **exact alias**   normalised surface already claimed. Free, certain.
  2. **fuzzy**         rapidfuzz >= 92 AND the runner-up at least 4 behind.
                       The margin matters as much as the threshold -- two
                       candidates both scoring 93 means the surface is ambiguous,
                       not that the first one wins.
  3. *(adjudication)*  a deliberate no-op hook, and a decision rather than a
                       gap -- `docs/pm/NOT-DOING.md` has the trigger. Distinct
                       from `curate.py::adjudicate`, which ships: that one
                       settles filed alias CONFLICTS after the fact, this one
                       would run inline on one surface before anything is
                       written.
  4. **provisional**   mint, but hide it until corroborated.

Two guards do most of the real work:

* The **ambiguous stoplist** is never auto-resolved on the NAME alone. A surface
  with two plausible owners must not be silently assigned to one. Where the
  stoplist declares what those owners are, the document gets a vote: a surface
  resolves when the text names exactly one of its readings, and declines when it
  names two or none. ADR 0197.
* **Provisional status**: a newly-minted entity is invisible until it is seen
  at least twice from at least two sources. One-off model hallucinations
  therefore never reach the graph, and they cost nothing to leave lying around.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from venona.db import core as db
from venona.db.models import (
    acronym_variants,
    entity_types,
    is_writable_acronym,
    mint_id,
    normalize,
)
from venona.db.repositories import pipeline_resolve as repo
from venona.ontology import get_ontology
from venona.services.alias_index import default_strictness

Method = Literal["exact_alias", "fuzzy", "provisional", "ambiguous", "rejected"]

MIN_ALIAS_LENGTH = 3


def _searchable(text: str) -> str:
    """Case, punctuation and whitespace folded, with word boundaries kept.

    Padded with spaces at both ends so a phrase test is a WORD test: without
    that, "Iran" matches inside "Irani" and the country of an unrelated
    organisation votes for a reading nobody named.
    """
    folded = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return f" {folded} " if folded else ""


@dataclass
class Resolution:
    surface: str
    entity_type: str
    entity_id: str | None
    method: Method
    confidence: float
    created: bool = False

    @property
    def ok(self) -> bool:
        return self.entity_id is not None


@dataclass
class Candidate:
    entity_id: str
    name: str
    score: float


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", name.lower()) if t}


def _is_qualified_containment(a: str, b: str, non_distinguishing: set[str]) -> bool:
    """True when one name's tokens contain the other's AND the extra tokens
    carry meaning -- i.e. they name a DIFFERENT thing.

    `fuzz.token_set_ratio` unions and intersects token sets, so a subset scores
    exactly 100 -- indistinguishable from an exact match (re-checked
    2026-09-14). Unguarded that silently merged four organisations, three of
    them ACTIVE and already annotating briefs: ADR 0049 names them.

    But containment alone is the wrong test: "Air Force" -> "United States Air
    Force" and "Defense Department" -> "Department of Defense" are the same body
    and must keep matching. The signal is in the EXTRA tokens, so a containment
    match survives only when every one of them is listed in
    `profile/lexicon.yaml: non_distinguishing`. Everything else names a
    subsidiary, division or numbered unit, and the surfaces stay apart.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or ta == tb:
        return False
    if not (ta < tb or tb < ta):
        return False
    extra = (tb - ta) if ta < tb else (ta - tb)
    return not extra <= non_distinguishing


class Resolver:
    """Holds the alias table in memory for a run.

    A few thousand rows (2026-08-06), so a linear rapidfuzz pass is fine and an
    ANN index would be premature. Rebuilt per run; `_add_alias` keeps it current
    as entities are minted mid-run, otherwise the same new surface would mint
    twice.
    """

    def __init__(self, conn: db.Conn, *, settings=None, lexicon=None) -> None:
        self._conn = conn
        self._lexicon = lexicon or {}
        self._non_distinguishing: set[str] = set()
        if settings is not None:
            from venona.pipeline.acronyms import load_non_distinguishing

            self._non_distinguishing = load_non_distinguishing(settings)
        pipeline = getattr(settings, "pipeline", None)
        self._accept = getattr(pipeline, "fuzzy_accept_score", 92)
        self._margin = getattr(pipeline, "fuzzy_runnerup_margin", 4)

        # normalized surface -> the entity ids it could mean, or [] for a
        # surface declared ambiguous with no readings listed. Both decline by
        # default; only a surface WITH readings can ever be picked.
        self._ambiguous: dict[str, list[str]] = {
            row["normalized"]: list(db.jload(row["readings"], []) or [])
            for row in repo.select_ambiguous_surfaces(conn)
        }
        # entity id -> the phrases whose presence in a document votes for it.
        # Built only for ids some surface actually lists, so this costs nothing
        # on an instance that declares no readings.
        wanted = {rid for readings in self._ambiguous.values() for rid in readings}
        self._reading_evidence: dict[str, tuple[str, ...]] = {}
        if wanted:
            for row in repo.select_reading_evidence(conn):
                if row["id"] not in wanted:
                    continue
                phrases = [row["name"]]
                if row["home"]:
                    phrases.append(row["home"])
                self._reading_evidence[row["id"]] = tuple(_searchable(p) for p in phrases if p)
        # normalized surface -> {entity_type: entity_id}
        self._by_alias: dict[str, dict[str, str]] = {}
        # entity_type -> {entity_id: canonical name}
        self._names: dict[str, dict[str, str]] = {t: {} for t in entity_types()}

        for row in repo.select_aliases(conn):
            self._by_alias.setdefault(row["normalized"], {})[row["type"]] = row["id"]
            self._names.setdefault(row["type"], {})[row["id"]] = row["name"]

    # ── the ladder ───────────────────────────────────────────────────
    def resolve(
        self,
        surface: str,
        entity_type: str,
        *,
        confidence: float = 0.8,
        article_id: str | None = None,
        now: str = "",
        allow_mint: bool = True,
        org_type: str | None = None,
        context: str = "",
    ) -> Resolution:
        surface = surface.strip()
        if not surface or entity_type not in self._names:
            return Resolution(surface, entity_type, None, "rejected", 0.0)

        key = normalize(surface)
        if not key:
            return Resolution(surface, entity_type, None, "rejected", 0.0)

        # Guard 1: an ambiguous surface is never auto-resolved on its own.
        # Assigning one to a single owner is worse than leaving it unlinked.
        #
        # Unless the DOCUMENT says which. A surface with declared readings is
        # offered the surrounding text, and resolves only when exactly one of
        # those readings is named in it -- the writer having spelled out "U.S.
        # Army" once before writing "the Army", which is why the short form is
        # ambiguous in the first place. Silence and ties still decline, so the
        # failure mode stays a missing mention rather than a wrong one. ADR 0197,
        # and the same discipline as `alias_index.pick_sense` one layer up.
        if key in self._ambiguous:
            picked = self._pick_reading(self._ambiguous[key], entity_type, context)
            if picked is not None:
                return Resolution(surface, entity_type, picked, "exact_alias", 0.9)
            return Resolution(surface, entity_type, None, "ambiguous", 0.0)

        # Rung 1: exact alias.
        owners = self._by_alias.get(key, {})
        if entity_type in owners:
            return Resolution(surface, entity_type, owners[entity_type], "exact_alias", 1.0)

        # A surface owned by a DIFFERENT type is a signal, not a match. Types
        # are scoped deliberately: "Titan" may be both a Program and a Platform.
        if owners and entity_type not in owners:
            pass  # fall through to fuzzy within the requested type

        # Rung 2: fuzzy, within type only.
        candidate, runner_up = self._best_candidates(surface, entity_type)
        if candidate and _is_qualified_containment(
            surface, candidate.name, self._non_distinguishing
        ):
            # Containment is a signal that one name QUALIFIES the other, which is
            # the opposite of them being the same thing. Mint instead: a subset
            # scores exactly 100, so every parent/subsidiary pair otherwise
            # clears the accept threshold with a maximal runner-up margin, which
            # is how JIATF-401 (counter-drone) merged into JIATF-South (drug
            # interdiction). ADR 0049 lists the four live pairs.
            return self._contained(
                surface,
                entity_type,
                candidate,
                confidence,
                article_id=article_id,
                now=now,
                allow_mint=allow_mint,
                org_type=org_type,
            )

        if candidate and candidate.score >= self._accept:
            gap = candidate.score - (runner_up.score if runner_up else 0.0)
            if runner_up is None or gap >= self._margin:
                self._add_alias(
                    candidate.entity_id,
                    surface,
                    "extraction",
                    article_id=article_id,
                    now=now,
                )
                return Resolution(
                    surface,
                    entity_type,
                    candidate.entity_id,
                    "fuzzy",
                    round(candidate.score / 100, 2),
                )
            # Two near-equal candidates: the surface is ambiguous *here*, so
            # decline rather than pick. This is the case rung 3 would adjudicate.
            return Resolution(surface, entity_type, None, "ambiguous", 0.0)

        # Rung 3 (not built): LLM adjudication would go here, for scores below
        # `fuzzy_accept_score`. Intentionally absent -- `docs/pm/NOT-DOING.md`
        # carries the trigger, and why this is not `curate.py::adjudicate`.

        # Rung 4: mint provisional.
        if not allow_mint:
            return Resolution(surface, entity_type, None, "rejected", 0.0)
        return self._mint(
            surface, entity_type, confidence, article_id=article_id, now=now, org_type=org_type
        )

    def _pick_reading(self, readings: list[str], entity_type: str, context: str) -> str | None:
        """Which declared reading this document names, or None.

        **The point is that it usually returns None.** Only an unambiguous
        mention in the same text earns a pick, and the evidence is deliberately
        narrow: the reading's own name, or the country it is based in. Not a
        similarity score, not a model call, and nothing that could rank two
        readings neither of which is present.

        Scoped to the requested type, like every other rung: `Titan` may be a
        Program and a Platform, and a reading of the wrong type is not a reading
        of this surface at all.
        """
        if not readings or not context:
            return None
        haystack = _searchable(context)
        if not haystack:
            return None
        present = [
            rid
            for rid in readings
            if self._names.get(entity_type, {}).get(rid)
            and any(phrase in haystack for phrase in self._reading_evidence.get(rid, ()))
        ]
        return present[0] if len(present) == 1 else None

    def _contained(
        self,
        surface,
        entity_type,
        candidate,
        confidence,
        *,
        article_id,
        now,
        allow_mint,
        org_type=None,
    ) -> Resolution:
        """Mint separately, and FILE THE PAIR for review.

        Declining silently would trade a wrong merge for a lost signal. A
        containment pair is the highest-precision merge candidate the resolver
        ever sees -- it is either a genuine parent/subsidiary distinction worth
        keeping apart, or a real duplicate worth merging -- and either way a
        human should see it. `alias_conflicts` already has exactly the right
        shape once the new entity exists.
        """
        if not allow_mint:
            return Resolution(surface, entity_type, None, "rejected", 0.0)

        minted = self._mint(
            surface, entity_type, confidence, article_id=article_id, now=now, org_type=org_type
        )
        if minted.entity_id and minted.entity_id != candidate.entity_id:
            db.upsert(
                self._conn,
                "alias_conflicts",
                {
                    "id": f"contain|{minted.entity_id}|{candidate.entity_id}",
                    "surface": surface,
                    "normalized": normalize(surface),
                    "existing_entity_id": candidate.entity_id,
                    "proposed_entity_id": minted.entity_id,
                    "entity_type": entity_type,
                    "article_id": article_id,
                    "created_at": now,
                },
                key=["id"],
            )
        return minted

    def _best_candidates(
        self, surface: str, entity_type: str
    ) -> tuple[Candidate | None, Candidate | None]:
        names = self._names.get(entity_type) or {}
        if not names:
            return None, None

        choices = list(names.values())
        ids = list(names.keys())
        matches = process.extract(surface, choices, scorer=fuzz.token_set_ratio, limit=2)
        if not matches:
            return None, None

        results: list[Candidate] = []
        for name, score, index in matches:
            results.append(Candidate(entity_id=ids[index], name=name, score=float(score)))
        return results[0], (results[1] if len(results) > 1 else None)

    def _mint(
        self,
        surface: str,
        entity_type: str,
        confidence: float,
        *,
        article_id: str | None,
        now: str,
        org_type: str | None = None,
    ) -> Resolution:
        """Create a provisional entity. Hidden until corroborated.

        `org_type` is the ontology's sub-vocabulary within an organisation type
        -- `program_office` inside GovernmentBody, `startup` inside Company. It
        is recorded only at MINT, never on a later sighting: the column is a
        property of the entity, and letting each article overwrite it would make
        it whatever the most recent one guessed.
        """
        try:
            entity_id = mint_id(entity_type, surface)
        except ValueError:
            return Resolution(surface, entity_type, None, "rejected", 0.0)

        existing = repo.select_entities_by_id(self._conn, entity_id).fetchone()

        if existing is None:
            db.insert(
                self._conn,
                "entities",
                {
                    "id": entity_id,
                    "type": entity_type,
                    "name": surface,
                    "canonical_key": normalize(surface),
                    "org_type": org_type
                    if org_type in get_ontology().org_types(entity_type)
                    else None,
                    "status": "provisional",
                    "relevance": 0.0,
                    "mention_count": 0,
                    "source_count": 0,
                    "props": db.jdump({}),
                    "first_seen": now,
                    "last_seen": now,
                    "created_at": now,
                },
            )
            self._names.setdefault(entity_type, {})[entity_id] = surface

        self._add_alias(entity_id, surface, "extraction", article_id=article_id, now=now)

        # Generated acronym forms, so "Next Generation Command and Control"
        # minted today resolves "NGC2" tomorrow without anyone listing it. Read
        # from `get_ontology()` at the point of use rather than a snapshot: the
        # type set is editable over HTTP while a UI-triggered run is in flight.
        if get_ontology().generates_acronyms(entity_type):
            for variant in acronym_variants(surface, lexicon=self._lexicon):
                if is_writable_acronym(variant):
                    self._add_alias(
                        entity_id, variant, "acronym_gen", article_id=article_id, now=now
                    )

        return Resolution(
            surface, entity_type, entity_id, "provisional", confidence, created=existing is None
        )

    # ── alias writes ─────────────────────────────────────────────────
    def _add_alias(
        self,
        entity_id: str,
        surface: str,
        added_by: str,
        *,
        article_id: str | None = None,
        now: str = "",
    ) -> bool:
        """Write an alias, subject to the conflict rules.

        A surface already owned by a DIFFERENT entity of the SAME type is
        rejected into `alias_conflicts` for human review, never merged. An
        automatic merge here would be invisible and would poison every
        downstream query.

        The conflict upsert deliberately does NOT carry `resolved`. Both
        conflict ids are stable and deterministic, so the same pair re-files on
        every run over the same corpus -- and `db.upsert` writes every column it
        is given on conflict, so passing `resolved: 0` reopens every decision a
        human has just made, silently, nightly. Omitting the column lets the
        schema default cover the insert and leaves an existing verdict alone.
        """
        surface = surface.strip()
        key = normalize(surface)
        if not key or len(surface) < MIN_ALIAS_LENGTH:
            return False
        if key in self._ambiguous:
            return False

        entity_type = repo.select_entities_by_id_2(self._conn, entity_id).fetchone()
        if entity_type is None:
            return False
        entity_type = entity_type["type"]

        owner = (self._by_alias.get(key) or {}).get(entity_type)
        if owner and owner != entity_id:
            db.upsert(
                self._conn,
                "alias_conflicts",
                {
                    "id": f"{key}|{entity_type}|{entity_id}",
                    "surface": surface,
                    "normalized": key,
                    "existing_entity_id": owner,
                    "proposed_entity_id": entity_id,
                    "entity_type": entity_type,
                    "article_id": article_id,
                    "created_at": now,
                },
                key=["id"],
            )
            return False

        is_acronym = surface.isupper() and len(surface) >= MIN_ALIAS_LENGTH
        db.upsert(
            self._conn,
            "aliases",
            {
                "id": f"{entity_id}|{surface}",
                "entity_id": entity_id,
                "surface": surface,
                "normalized": key,
                "strictness": default_strictness(
                    surface=surface, added_by=added_by, is_acronym=is_acronym
                ),
                "is_acronym": int(is_acronym),
                "added_by": added_by,
                "evidence_article_id": article_id,
                "created_at": now,
            },
            key=["entity_id", "surface"],
        )
        self._by_alias.setdefault(key, {})[entity_type] = entity_id
        return True


# ── promotion ────────────────────────────────────────────────────────


def promote_provisionals(
    conn: db.Conn,
    *,
    now: str,
    min_sightings: int = 2,
    min_sources: int = 2,
    dormant_after_days: int = 0,
    only: Iterable[str] | None = None,
    promote_only: bool = False,
) -> list[str]:
    """Reconcile entity status against the evidence. Promotes AND demotes.

    `only` restricts the reconciliation to a set of entity ids; `promote_only`
    suppresses the downward half. Both exist for one caller,
    `services/curate.py::merge_entities`, and the pairing is deliberate: a merge
    changes the evidence of exactly ONE entity, the survivor, and it can only
    ever ADD to it. Re-evaluating the whole table as a side effect of one merge
    would demote entities the merge said nothing about, and a merge that
    silently demotes is precisely the invisible kind of damage CLAUDE.md warns
    about. The evidence arithmetic below stays the single implementation.

    Promotion requires CORROBORATION: at least `min_sightings` distinct articles
    from at least `min_sources` distinct PUBLISHERS, counted by
    `documents.site_domain` and never by `source_id`, which is a FEED. One
    publisher routinely arrives through several: measured 2026-08-06, once
    discovery was split into eight topic sweeps, `defensedaily.com` reached us
    under four different `source_id`s and `insidedefense.com` under three.
    Counting feeds lets a single outlet satisfy "two independent sources" on its
    own, which is the opposite of what corroboration means. ADR 0050.

    Corroboration is the only path in. A gate that also promoted on a single
    extraction the model rated >= 0.9 confidence promoted 48 entities against 6
    genuinely corroborated ones on a 15-article corpus: self-reported model
    confidence is not calibrated, it is 0.9+ for almost everything extracted,
    so that branch defeated the gate entirely. ADR 0021.

    The cost of being strict is that a genuinely new entity takes a second,
    independent sighting before it becomes a graph node. That is the right
    trade: the article still scores and still appears in the brief, and the name
    lands in `unresolved_mentions` as a curation prompt. A polluted graph is
    much more expensive than a late one.

    Entities declared in `fixtures/seed/entities.yaml` are never demoted -- they
    are active by declaration, not by evidence.

    `merged` entities are excluded by the status filter below, or a merged-away
    entity would be re-evaluated every run and could flip back to `active`,
    resurrecting it into the projection after a human had merged it. ADR 0051.

    ── dormancy ─────────────────────────────────────────────────────────

    `dormant_after_days` is what stops the provisional pool growing forever.
    Measured 2026-08-06, before it existed: 710 provisional entities, 690 of
    them at (1 article, 1 publisher), 184 of them a `Person` quoted once who
    will never be quoted again, and **no code path anywhere deleted or retired
    an entity**. The head of the distribution drains fine and the tail never
    does, so the number only goes up and nobody can act on it. ADR 0054 has the
    audit.

    So an entity that has been provisional longer than `dormant_after_days` with
    no new evidence in that window becomes `dormant`: still hidden, and not
    counted as pending. Nothing is deleted -- its assertions stay, invariant 1
    holds, and a single new sighting pulls it straight back to `provisional` (or
    to `active` if that sighting corroborates it). Dormancy is a statement about
    silence, not about correctness.

    `dormant_after_days = 0` disables it, which is what every caller that does
    not pass a settings value gets.
    """
    seeded = {
        row["entity_id_raw"] for row in repo.select_assertions_2(conn) if row["entity_id_raw"]
    }

    # A person attaching a surface to an entity is the same kind of declaration
    # as a seed. Without this, an entity promoted by hand -- to make its aliases
    # indexable, which is the entire point of the attach -- is re-gated here,
    # drops back to `provisional`, and the surface silently stops annotating:
    # the curator sees it work, then sees it stop, with nothing to point at.
    # `added_by = 'manual'` is written by exactly one path,
    # `POST /glossary/{surface}/attach-entity`. Nothing automatic can reach it.
    declared = {row["entity_id"] for row in repo.select_aliases_2(conn) if row["entity_id"]}
    seeded |= declared

    # A merge consolidates EVIDENCE, not just identity. Without this the
    # survivor keeps counting only its own assertions -- so merging two
    # single-article provisionals would leave both below the gate and the merge
    # would achieve nothing. `assertions` are never rewritten (invariant 1), so
    # the aggregation has to happen here, at read time.
    from venona.graph.projector import resolve_mappings

    mappings = resolve_mappings(conn)

    # EVIDENCE is gathered over every entity, including merged-away ones --
    # their assertions are exactly what the survivor inherits. STATUS is only
    # decided for provisional/active rows. Filtering evidence by status would
    # drop the loser's articles and make the merge pointless, which is why they
    # are two reads rather than one join. Counted in SQL, except the merge map,
    # which is passed in rather than derived there: see `entity_evidence`.
    evidence = {row["id"]: row for row in repo.entity_evidence(conn, mappings)}

    # `dormant` is re-evaluated alongside the other two: an entity that goes
    # quiet must be able to come back the moment it is mentioned again.
    statuses: dict[str, str] = {
        row["id"]: row["status"] for row in repo.select_entity_statuses(conn)
    }

    cutoff = _days_before(now, dormant_after_days) if dormant_after_days > 0 else None

    promoted: list[str] = []
    changes: list[tuple[str, str, int, int]] = []
    scope = set(only) if only is not None else None

    for entity_id, status in statuses.items():
        if entity_id in seeded:
            continue
        if scope is not None and entity_id not in scope:
            continue

        found = evidence.get(entity_id)
        article_count = found["articles"] if found else 0
        source_count = found["sources"] if found else 0
        corroborated = article_count >= min_sightings and source_count >= min_sources

        if corroborated:
            target = "active"
        elif cutoff is not None and ((found["newest"] if found else "") or "") < cutoff:
            # Uncorroborated AND silent for the whole window. Reads the newest
            # ASSERTION, not `last_seen` -- which this very function rewrites,
            # so it would reset the clock on every run and nothing would ever go
            # dormant.
            target = "dormant"
        else:
            target = "provisional"

        if target == status:
            continue
        if promote_only and target != "active":
            continue

        changes.append((entity_id, target, article_count, source_count))
        if target == "active":
            promoted.append(entity_id)

    # ONE statement for every decision, which is a round trip saved per entity
    # on a function that runs several times a run.
    repo.update_entity_statuses(conn, now, changes)
    return promoted


def _days_before(now: str, days: int) -> str:
    """ISO-8601 UTC `days` before `now`. String comparison is safe because every
    timestamp in this system is `...Z` (see CLAUDE.md conventions)."""
    from datetime import datetime, timedelta

    stamp = datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=days)
    return stamp.isoformat().replace("+00:00", "Z")


def refresh_mention_counts(conn: db.Conn) -> None:
    """Keep `mention_count`/`source_count` in step with the assertion log.

    Deliberately the SAME definitions `promote_provisionals` uses -- distinct
    articles and distinct PUBLISHERS, seed assertions excluded, evidence folded
    through `entity_mappings`. These two columns are the promotion gate's own
    inputs, so a second definition of them is not a second opinion, it is a
    silent disagreement -- and this runs seven lines after `promote_provisionals`
    in `run_analyze`, so whatever it writes is what the reader sees. Counting
    distinct `documents.source_id` here would read two feeds from one outlet as
    two independent sources (ADR 0050), and skipping the `entity_mappings` fold
    would lose a survivor every article its merged entities brought with them.
    """
    from venona.graph.projector import resolve_mappings

    mappings = resolve_mappings(conn)

    # The same aggregate `promote_provisionals` decides on -- one scan, the
    # merge fold applied by the map rather than derived in SQL, and publishers
    # counted as distinct DOMAINS. See `entity_evidence`.
    evidence = repo.entity_evidence(conn, mappings)

    # Reset first: an entity whose last assertion was retracted, and a loser
    # whose evidence has moved to its survivor, must both fall to zero rather
    # than keep the figure from the run before.
    repo.update_entities(conn)
    repo.update_entity_counts(
        conn,
        [(row["id"], row["articles"], row["sources"]) for row in evidence],
    )
