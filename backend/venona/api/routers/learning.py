"""The learning surface: queue, review, progress, browser, settings.

Reader routes, and every one of them is scoped to the acting user the way
`PATCH /api/auth/me` is: no route carries a user id, so the subject is
always `authz.actor_id(request)` and there is nothing for a caller to
substitute. That, plus the repository's insistence on a `user_id` in every
per-user query, is the whole privacy mechanism -- there is no admin surface
over these tables (docs/adr/0157).

Time enters HERE. `services/` may not ask what time it is, so the routers
compute `now` and the day floor and pass them down -- the
`admin.py::_since` arrangement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from venona import timeutil
from venona.api import authz
from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.learning import (
    DecideRequest,
    DeckCard,
    LearningCounts,
    LearningOverview,
    LearningProgress,
    LearningQueue,
    LearningSettings,
    LearningSettingsUpdate,
    OverviewCard,
    QuestionDecision,
    QuestionPage,
    QuestionRow,
    QueueCounts,
    ReviewRequest,
    ReviewResult,
    SeriesDay,
    TypeCoverage,
)
from venona.api.schemas.run import RunPreview, RunStarted
from venona.api.security import get_auth_config
from venona.db import core as db
from venona.db.repositories import learning as repo
from venona.services import fsrs, learning_cards
from venona.untrust import inline, quote

router = APIRouter(prefix="/learning", tags=["learning"])

DEFAULTS = LearningSettings(new_per_day=10, daily_goal=30, retention_target=0.9)
#: A session serves at most this many due cards at once; the rest stay due
#: and arrive next fetch.
SESSION_CAP = 50
#: `strong` in the coverage split means stability at or beyond this many days.
STRONG_STABILITY_DAYS = 21.0
SERIES_CAP_DAYS = 365
#: The streak walk reads at most this many distinct days -- a bound, not a
#: truncation anyone will meet honestly.
STREAK_SCAN_DAYS = 400

_page = page_window(50, 200)


def _now() -> str:
    return timeutil.format_millis(datetime.now(UTC))


def _day_floor(now: str) -> str:
    """Midnight UTC in millis format, so it sorts below both timestamp
    spellings of the same day (the `timeutil` ordering hazard)."""
    return now[:10] + "T00:00:00.000Z"


#: The row an auth-off session studies as. Deliberately NOT threaded into
#: `actor_id` itself: curation verdicts and audit rows keep recording NULL
#: for an unauthenticated actor (inventing provenance is worse than absence),
#: but learning progress is the opposite case -- there is nothing honest
#: about refusing to remember the only person who could be studying.
LOCAL_USER_ID = "user:local-operator"


def _actor(request: Request, conn: db.Conn, *, what: str = "keep learning progress") -> str:
    """The acting user's row id, creating the local-operator row when auth is off.

    `what` completes the refusal sentence for the caller that has no row to
    write to. It is a parameter rather than a fixed string because this
    resolver is shared: telling somebody setting their time zone that there
    is nowhere to keep their LEARNING PROGRESS names the wrong feature.
    """
    user_id = authz.actor_id(request)
    if user_id:
        return user_id
    # With auth off, `AuthMiddleware` passes requests through WITHOUT stamping
    # a principal (its early return fires before `principal_for` would build
    # the `via="disabled"` one) -- so "no principal at all while auth is off"
    # IS the disabled case, and both spellings are accepted on purpose.
    via = authz.actor_via(request)
    if via == "disabled" or (via is None and not get_auth_config().enabled):
        # Auth off = a single-user dev box: the person at the keyboard IS the
        # user, so bind their progress to one well-known local row. Reader
        # role: the row exists to hold FSRS state, not to authorize anything
        # -- with auth off the principal already answers for that, and if
        # auth is later enabled this row cannot be signed into.
        from venona.services import user_service

        if repo_users_row(conn) is None:
            user_service.create(
                conn,
                user_id=LOCAL_USER_ID,
                email="operator@localhost",
                role="reader",
                now=_now(),
                display_name="local operator",
            )
        return LOCAL_USER_ID
    # 400, not 403: the role is fine, there is just no user row to hold
    # progress for -- the root bearer or the env break-glass admin. Same
    # shape as PATCH /api/auth/me.
    raise HTTPException(
        status_code=400,
        detail={
            "error": "no_account",
            "message": (
                "this session is not tied to a user account, so there is "
                f"no place to {what} -- sign in with "
                "Google, or add a row with `venona users add`"
            ),
        },
    )


def repo_users_row(conn: db.Conn):
    from venona.db.repositories import users as users_repo

    return users_repo.by_id(conn, LOCAL_USER_ID)


def _settings(conn: db.Conn, user_id: str) -> LearningSettings:
    row = repo.settings_row(conn, user_id=user_id)
    if row is None:
        return DEFAULTS
    return LearningSettings(
        new_per_day=row["new_per_day"],
        daily_goal=row["daily_goal"],
        retention_target=row["retention_target"],
    )


def _streak(conn: db.Conn, user_id: str, today: str) -> int:
    """Consecutive study days ending today or yesterday -- yesterday still
    counts, because a streak that dies at midnight before the day's session
    punishes timezones, not lapses (day boundaries are UTC)."""
    days = repo.review_days(conn, user_id=user_id, limit=STREAK_SCAN_DAYS)
    if not days:
        return 0
    moment = timeutil.parse(today + "T00:00:00Z")
    assert moment is not None
    from datetime import timedelta

    expected = today
    if days[0] != expected:
        expected = (moment - timedelta(days=1)).strftime("%Y-%m-%d")
        if days[0] != expected:
            return 0
        moment = moment - timedelta(days=1)
    streak = 0
    for day in days:
        if day != expected:
            break
        streak += 1
        moment = moment - timedelta(days=1)
        expected = moment.strftime("%Y-%m-%d")
    return streak


@router.get("/queue", response_model=LearningQueue)
def queue(request: Request, more: bool = False, conn: db.Conn = Depends(get_db)) -> LearningQueue:
    """`more=true` is "keep studying": a further full batch of new cards
    past the daily pace. The pace is a default, never a wall -- the learner
    asked, and refusing a willing learner teaches nothing."""
    user_id = _actor(request, conn)
    now = _now()
    settings = _settings(conn, user_id)
    today = repo.reviews_since(conn, user_id=user_id, since=_day_floor(now))

    due: list[DeckCard] = []
    for row in repo.due_cards(conn, user_id=user_id, now=now, limit=SESSION_CAP):
        card = learning_cards.derive(conn, card_key=row["card_key"], reps=row["reps"])
        if card is None:
            continue  # dormant: the fact behind it is not servable right now
        due.append(
            card.model_copy(update={"due": row["due"], "state": row["state"], "reps": row["reps"]})
        )

    new_budget = (
        settings.new_per_day if more else max(0, settings.new_per_day - int(today["new_cards"]))
    )
    new: list[DeckCard] = []
    if new_budget:
        # Over-fetch: a candidate can decline to derive (a fact went dormant
        # between the candidate query and now), and the budget should still
        # fill from the next in line.
        overfetch = new_budget * 2 + 8
        for key in learning_cards.new_candidates(conn, user_id=user_id, limit=overfetch):
            card = learning_cards.derive(conn, card_key=key, reps=0)
            if card is not None:
                new.append(card)
            if len(new) >= new_budget:
                break

    return LearningQueue(
        due=due,
        new=new,
        counts=QueueCounts(
            due=len(due),
            new_remaining=len(new),
            reviewed_today=int(today["reviews"]),
            new_today=int(today["new_cards"]),
            deck_remaining=repo.deck_remaining(conn, user_id=user_id),
        ),
        streak=_streak(conn, user_id, now[:10]),
        daily_goal=settings.daily_goal,
    )


@router.post("/review", response_model=ReviewResult)
def review(
    payload: ReviewRequest, request: Request, conn: db.Conn = Depends(get_db)
) -> ReviewResult:
    """The one write a learner makes, and the only thing this feature ever
    records about anybody."""
    user_id = _actor(request, conn)
    now = _now()
    settings = _settings(conn, user_id)

    with db.transaction(conn, what="learning review"):
        prev_row = repo.card_state_for_update(conn, user_id=user_id, card_key=payload.card_key)
        reps_before = prev_row["reps"] if prev_row else 0
        card = learning_cards.derive(conn, card_key=payload.card_key, reps=reps_before)
        if card is None:
            # Allowed caller, un-servable card (bad key, dormant fact): 409.
            raise HTTPException(
                status_code=409,
                detail={"error": "not_servable", "card_key": payload.card_key},
            )
        prev = (
            fsrs.CardState(
                state=prev_row["state"],
                stability=prev_row["stability"],
                difficulty=prev_row["difficulty"],
                due=prev_row["due"],
                last_review=prev_row["last_review"],
                reps=prev_row["reps"],
                lapses=prev_row["lapses"],
                interval_days=0.0,
            )
            if prev_row
            else None
        )
        next_state = fsrs.review(
            prev,
            payload.rating,
            now=now,
            retention_target=settings.retention_target,
            card_key=payload.card_key,
        )
        repo.upsert_card_state(
            conn,
            user_id=user_id,
            card_key=payload.card_key,
            state=next_state.state,
            stability=next_state.stability,
            difficulty=next_state.difficulty,
            due=next_state.due,
            last_review=next_state.last_review,
            reps=next_state.reps,
            lapses=next_state.lapses,
            now=now,
        )
        repo.insert_review(
            conn,
            id=f"review:{_review_id(user_id, payload.card_key, now)}",
            user_id=user_id,
            card_key=payload.card_key,
            rating=payload.rating,
            review_kind="new" if prev is None else prev.state,
            format=card.format,
            elapsed_days=0.0 if prev is None else fsrs.elapsed_between(prev.last_review, now),
            due_at=prev.due if prev else None,
            now=now,
        )

    return ReviewResult(
        card_key=payload.card_key,
        state=next_state.state,
        due=next_state.due,
        interval_days=next_state.interval_days,
        reps=next_state.reps,
        lapses=next_state.lapses,
    )


def _review_id(user_id: str, card_key: str, now: str) -> str:
    """Deterministic per (user, card, instant): a client retry of the same
    grade in the same millisecond collides on the primary key instead of
    double-logging."""
    import hashlib

    return hashlib.sha256(f"{user_id}:{card_key}:{now}".encode()).hexdigest()[:32]


@router.get("/progress", response_model=LearningProgress)
def progress(request: Request, days: int = 90, conn: db.Conn = Depends(get_db)) -> LearningProgress:
    user_id = _actor(request, conn)
    now = _now()
    days = max(1, min(days, SERIES_CAP_DAYS))
    from datetime import timedelta

    moment = timeutil.parse(now)
    assert moment is not None
    since = (moment - timedelta(days=days)).strftime("%Y-%m-%d") + "T00:00:00.000Z"

    series = [
        SeriesDay(
            day=r["day"],
            reviews=int(r["reviews"]),
            graded=int(r["graded"]),
            lapsed=int(r["lapsed"]),
            retention=(1.0 - int(r["lapsed"]) / int(r["graded"])) if int(r["graded"]) else None,
        )
        for r in repo.daily_series(conn, user_id=user_id, since=since)
    ]

    started_rows = repo.started_by_type(
        conn, user_id=user_id, strong_stability=STRONG_STABILITY_DAYS
    )
    started = {r["type"]: r for r in started_rows}
    coverage = [
        TypeCoverage(
            type=r["type"],
            total=int(r["total"]),
            started=int(started[r["type"]]["started"]) if r["type"] in started else 0,
            strong=int(started[r["type"]]["strong"]) if r["type"] in started else 0,
        )
        for r in repo.deck_totals_by_type(conn)
    ]
    gloss_total = repo.gloss_deck_total(conn)
    if gloss_total:
        gloss = repo.gloss_started(conn, user_id=user_id, strong_stability=STRONG_STABILITY_DAYS)
        coverage.append(
            TypeCoverage(
                type="gloss",
                total=gloss_total,
                started=int(gloss["started"]),
                strong=int(gloss["strong"]),
            )
        )
    coverage.sort(key=lambda c: (-c.total, c.type))

    today = repo.reviews_since(conn, user_id=user_id, since=_day_floor(now))
    settings = _settings(conn, user_id)
    return LearningProgress(
        series=series,
        series_days=days,
        coverage=coverage,
        streak=_streak(conn, user_id, now[:10]),
        reviewed_today=int(today["reviews"]),
        daily_goal=settings.daily_goal,
        strong_stability_days=STRONG_STABILITY_DAYS,
    )


#: The terms page's group sizes. Bounded lists, not pages: each answers one
#: question ("what needs another look?") and an answer longer than this is
#: noise -- the study queue is where the long tail actually gets worked.
OVERVIEW_ATTENTION = 12
OVERVIEW_UPCOMING = 8
OVERVIEW_KNOWN = 80
#: Below this stability a memory reads as `weak` (mirrored by the frontend's
#: `memoryLabel`).
WEAK_STABILITY_DAYS = 7.0


def _overview_card(r: db.Row) -> OverviewCard:
    return OverviewCard(
        card_key=r["card_key"],
        term=r["term"] or r["card_key"],
        type=r["type"],
        detail=r["detail"],
        entity_id=r["entity_id"],
        due=r["due"],
        stability=r["stability"],
        lapses=r["lapses"],
    )


@router.get("/counts", response_model=LearningCounts)
def counts(request: Request, conn: db.Conn = Depends(get_db)) -> LearningCounts:
    """The study nudge's numbers, cheap enough to fetch from any page: COUNTs
    and candidate keys only, never a derived card."""
    user_id = _actor(request, conn)
    now = _now()
    settings = _settings(conn, user_id)
    today = repo.reviews_since(conn, user_id=user_id, since=_day_floor(now))
    new_budget = max(0, settings.new_per_day - int(today["new_cards"]))
    new_available = (
        len(learning_cards.new_candidates(conn, user_id=user_id, limit=new_budget))
        if new_budget
        else 0
    )
    return LearningCounts(
        due=repo.due_count(conn, user_id=user_id, now=now),
        new_available=new_available,
        reviewed_today=int(today["reviews"]),
        daily_goal=settings.daily_goal,
    )


@router.get("/overview", response_model=LearningOverview)
def overview(request: Request, conn: db.Conn = Depends(get_db)) -> LearningOverview:
    """The reader's view of their own cards: worth another look, coming back
    next, and known well. Grouped here rather than paged, because the three
    groups sort differently and a page of raw scheduler rows was the thing
    this replaced."""
    user_id = _actor(request, conn)
    bounds = {"weak_stability": WEAK_STABILITY_DAYS, "strong_stability": STRONG_STABILITY_DAYS}
    return LearningOverview(
        attention=[
            _overview_card(r)
            for r in repo.attention_cards(conn, user_id=user_id, limit=OVERVIEW_ATTENTION, **bounds)
        ],
        upcoming=[
            _overview_card(r)
            for r in repo.upcoming_cards(conn, user_id=user_id, limit=OVERVIEW_UPCOMING, **bounds)
        ],
        known=[
            _overview_card(r)
            for r in repo.known_cards(
                conn, user_id=user_id, strong_stability=STRONG_STABILITY_DAYS, limit=OVERVIEW_KNOWN
            )
        ],
        total=repo.held_total(conn, user_id=user_id),
    )


@router.get("/settings", response_model=LearningSettings)
def get_settings(request: Request, conn: db.Conn = Depends(get_db)) -> LearningSettings:
    return _settings(conn, _actor(request, conn))


@router.patch("/settings", response_model=LearningSettings)
def patch_settings(
    payload: LearningSettingsUpdate, request: Request, conn: db.Conn = Depends(get_db)
) -> LearningSettings:
    user_id = _actor(request, conn)
    current = _settings(conn, user_id)
    merged = LearningSettings(
        new_per_day=payload.new_per_day if payload.new_per_day is not None else current.new_per_day,
        daily_goal=payload.daily_goal if payload.daily_goal is not None else current.daily_goal,
        retention_target=(
            payload.retention_target
            if payload.retention_target is not None
            else current.retention_target
        ),
    )
    repo.upsert_settings(
        conn,
        user_id=user_id,
        new_per_day=merged.new_per_day,
        daily_goal=merged.daily_goal,
        retention_target=merged.retention_target,
        now=_now(),
    )
    return merged


# ── the question bank (admin by the authz default-deny) ──────────────
#
# Generated questions are proposals: born `proposed` by the run below, moved
# by a person, served to learners only once `active` (docs/adr/0157). The
# admin surface sees the BANK -- never any learner's rows.

#: Articles bundled per model call; `source_index` is 1-based within a batch.
ARTICLES_PER_CALL = 3
DEFAULT_ARTICLE_CAP = 24
MAX_ARTICLE_CHARS = 12_000

#: action -> (required current status, resulting status). The from-status
#: guard makes a double-click a no-op instead of a second decision.
DECISIONS = {
    "approve": ("proposed", "active"),
    "reject": ("proposed", "rejected"),
    "retire": ("active", "retired"),
}


@router.get("/questions", response_model=QuestionPage)
def questions(
    status: str = "proposed",
    window: Window = Depends(_page),
    conn: db.Conn = Depends(get_db),
) -> QuestionPage:
    rows = repo.questions_listing(conn, status=status, limit=window.limit, offset=window.offset)
    return QuestionPage(
        items=[
            QuestionRow(
                id=r["id"],
                kind=r["kind"],
                prompt=r["prompt"],
                choices=db.jload(r["choices"], None),
                answer=r["answer"],
                entity_id=r["entity_id"],
                grounding_article_id=r["grounding_article_id"],
                grounding_quote=r["grounding_quote"],
                status=r["status"],
                run_id=r["run_id"],
                created_at=r["created_at"],
            )
            for r in rows
        ],
        total=repo.questions_total(conn, status=status),
    )


@router.post("/questions/{question_id}/decide", response_model=QuestionDecision)
def decide(
    question_id: str,
    payload: DecideRequest,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> QuestionDecision:
    from_status, to_status = DECISIONS[payload.action]
    moved = repo.decide_question(
        conn,
        question_id=question_id,
        from_status=from_status,
        to_status=to_status,
        decided_by=authz.actor_id(request),
        now=_now(),
    )
    if not moved:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_decided", "id": question_id, "action": payload.action},
        )
    return QuestionDecision(id=question_id, status=to_status)


#: What one article of a generation run costs when there is nothing to average.
#: A constant, reported with `cost_sample: 0` beside it so no surface presents
#: it as a measurement.
FALLBACK_QUESTION_USD = 0.04
FALLBACK_QUESTION_SECONDS = 12.0


@router.get("/generate/preview", response_model=RunPreview)
def generate_preview(
    limit: int | None = None,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """The confirm dialog's numbers, from the same query the run will use.

    PER ARTICLE, though the model is billed per call over
    `ARTICLES_PER_CALL` of them: the shared shape prices the unit a reader
    counts, and `calls` travels in `extra` for the sentence that says how the
    work is actually batched.

    `estimate_usd` was nullable here and is not any more. A null price renders
    as a blank where a figure belongs, and a blank in front of a spend reads as
    free -- so with no sample this quotes the fallback and says so, which is
    the discipline the other three previews already kept.
    """
    cap = limit or DEFAULT_ARTICLE_CAP
    articles = repo.question_articles(conn, limit=cap)
    calls = -(-len(articles) // ARTICLES_PER_CALL)  # ceil
    sample = repo.question_cost_sample(conn)

    per_call = (
        float(sample["avg_cost"]) if sample["n"] else FALLBACK_QUESTION_USD * ARTICLES_PER_CALL
    )
    per_article = per_call / ARTICLES_PER_CALL
    per_article_seconds = FALLBACK_QUESTION_SECONDS
    return {
        "eligible": len(articles),
        "will_attempt": len(articles),
        "cap": cap,
        "unit": "article",
        "unit_plural": "articles",
        "per_item_usd": round(per_article, 4),
        "estimate_usd": round(per_call * calls, 2),
        "per_item_seconds": per_article_seconds,
        "estimate_seconds": round(len(articles) * per_article_seconds),
        "cost_sample": int(sample["n"]),
        "extra": {
            "calls": calls,
            "proposed_open": repo.questions_total(conn, status="proposed"),
        },
    }


@router.post(
    "/generate",
    status_code=202,
    response_model=RunStarted,
    response_model_exclude_unset=True,
)
async def generate(request: Request, limit: int | None = None) -> dict:
    """Write proposed study questions from recent articles. Light lane: it
    writes `llm_questions` and nothing else, so it queues rather than
    contending for the research slot (C11)."""
    from venona.services import run_launcher

    settings = request.app.state.settings
    cap = max(1, min(limit or DEFAULT_ARTICLE_CAP, 200))

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return await _generate_questions(target, settings=settings, cap=cap)

    return await run_launcher.launch(
        request,
        kind="questions",
        params={"articles": cap},
        work=work,
        stage="questions",
    )


async def _generate_questions(target, *, settings, cap: int):
    """The run body: call, verify, write survivors as `proposed`.

    Verification is code's, never the model's: `source_index` must name a
    supplied article, the quote must be an exact substring of that article's
    `text_nfc` (the span is computed here), a choice item must contain its
    own answer, and a prompt the bank has seen -- whatever a person ruled --
    is dropped rather than re-proposed.
    """

    from venona.llm import budget as budget_mod
    from venona.llm.prompts import questions_system
    from venona.llm.runner import call_and_record
    from venona.llm.schemas import questions_schema
    from venona.pipeline.metrics import Funnel
    from venona.services import run_launcher

    conn, ctx = target.conn, target.ctx
    articles = repo.question_articles(conn, limit=cap)
    if not articles:
        return run_launcher.LaunchOutcome(
            outcome="nothing_to_do", data={"generated": 0, "reason": "no_articles"}
        )

    batches = [
        articles[i : i + ARTICLES_PER_CALL] for i in range(0, len(articles), ARTICLES_PER_CALL)
    ]
    # Sized to the authorised work, the lookup_all argument: the pipeline cap
    # would silently ceiling a batch someone just confirmed a price for.
    budget = budget_mod.Budget(
        settings=settings.llm.model_copy(
            update={"max_calls_per_run": max(settings.llm.max_calls_per_run, len(batches) * 2)}
        )
    )
    funnel = Funnel("questions", offered=0)
    seen_this_run: set[str] = set()
    generated = 0
    failed_calls = 0

    for batch in batches:
        target.check_cancel()
        # The index is what a returned question cites, so each article has to
        # stay one addressable unit: an un-`inline`d title carrying a newline
        # could forge a `[2]` the batch never contained (ADR 0166).
        user_content = "\n\n".join(
            f"[{index}] {inline(row['title'] or row['id'], limit=300)}\n"
            + quote(row["text_nfc"], kind="article-body", limit=MAX_ARTICLE_CHARS, keep_lines=True)
            for index, row in enumerate(batch, 1)
        )
        result = await call_and_record(
            ctx,
            "questions",
            user_content=user_content,
            schema=questions_schema(),
            system_prompt=questions_system(settings),
            model=settings.llm.questions_model,
            effort=settings.llm.questions_effort,
            tools=(),  # it may only read what we supplied
            timeout=240,
            budget=budget,
        )
        if not result.ok:
            failed_calls += 1
            continue

        items = (result.data or {}).get("questions") or []
        funnel.offered += len(items)
        for item in items:
            reason = _store_question(
                conn, item, batch=batch, run_id=target.run_id, ctx=ctx, seen=seen_this_run
            )
            if reason is None:
                funnel.keep()
                generated += 1
            else:
                funnel.drop(reason)

    if ctx is not None:
        funnel.emit(ctx)
    data = {
        "generated": generated,
        "rejected": funnel.dropped,
        "failed_calls": failed_calls,
        "articles": len(articles),
    }
    # A batch of calls that failed IS a degradation -- work this run set out to
    # do and could not -- so it goes through `ctx.warn` and reaches the status.
    # How many questions came back is a separate question, and the outcome
    # answers it.
    if failed_calls and ctx is not None:
        ctx.warn(f"{failed_calls} of {len(batches)} question calls did not return")
    if generated == 0:
        verdict = "nothing_found"
    elif funnel.dropped:
        verdict = "some"
    else:
        verdict = "delivered"
    return run_launcher.LaunchOutcome(outcome=verdict, data=data)


def _store_question(conn, item: dict, *, batch, run_id: str, ctx, seen: set[str]) -> str | None:
    """Verify one generated item and write it, or answer with the drop reason."""
    import json as jsonlib

    kind = item.get("kind")
    prompt = (item.get("question") or "").strip()
    answer = (item.get("answer") or "").strip()
    quote = item.get("quote") or ""
    index = item.get("source_index")
    if kind not in ("choice", "cloze", "qa") or not prompt or not answer or not quote:
        return "schema_invalid"
    if not isinstance(index, int) or not (1 <= index <= len(batch)):
        return "bad_source_index"
    source = batch[index - 1]
    position = source["text_nfc"].find(quote)
    if position < 0:
        return "quote_not_found"
    choices = None
    if kind == "choice":
        choices = [c for c in (item.get("choices") or []) if c]
        if len(set(choices)) < 4 or answer not in choices:
            return "bad_choices"
    if kind == "cloze" and "____" not in prompt:
        return "no_blank"
    normalized = prompt.lower()
    if normalized in seen or repo.question_prompt_exists(conn, normalized_prompt=normalized):
        return "duplicate"
    seen.add(normalized)
    repo.insert_question(
        conn,
        id=f"question:{ctx.ids.uuid7()}",
        run_id=run_id,
        kind=kind,
        entity_id=repo.entity_id_for_name(conn, name=answer),
        prompt=prompt,
        choices=jsonlib.dumps(choices) if choices else None,
        answer=answer,
        grounding_article_id=source["id"],
        grounding_quote=quote,
        grounding_span=jsonlib.dumps([position, position + len(quote)]),
        now=ctx.clock.now(),
    )
    return None
