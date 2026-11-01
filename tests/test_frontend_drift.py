"""Literals the frontend duplicates from the backend, held to their source.

WHY GREP A `.vue` FILE FROM PYTHON. The alternative is generating these lists,
and for two of the three the generated form is worse: `COUNTER_LABELS` maps a
frozen name to English chosen for a narrow column, and `ROLES` orders a select
by what a visitor is most likely to be. Neither is derivable. What IS checkable is
that the set has not drifted -- so the duplication stays and the drift becomes a
failing test rather than a rendering bug.

The pattern is `tests/test_cli_run.py::test_the_ui_knows_every_stage`, which has
scraped the stage list out of `stores/runs.ts` since C6 shipped. It exists because
the store silently drops events for names it does not know, and every check here
guards a failure with the same shape: the wrong thing renders, or nothing does,
and neither raises.

Only two frontend/backend duplications were enforced before this file -- the stage
list and the error envelope. Roles became load-bearing when readers did, and
`COUNTER_LABELS`'s own contract entry (C6) names the file it used to live in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from venona.api import authz
from venona.db.repositories import access as access_repo
from venona.pipeline.types import RunCounters
from venona.services import run_types, user_service

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


def _literal(path: Path, pattern: str) -> str:
    text = path.read_text()
    found = re.search(pattern, text, re.S)
    assert found, f"could not find {pattern!r} in {path.relative_to(WEB.parent.parent)}"
    return found.group(1)


class TestAccessRequestRoles:
    """The form's role list comes from `/auth/mode` (the domain descriptor);
    its literal is only the fallback for a failed fetch.

    `POST /api/request-access` is PUBLIC and validates `role` against the
    descriptor, so a value the fallback offers and the gate does not is a 422
    on a stranger's first interaction with the product -- and the form has no
    way to show which field was wrong, because the endpoint is deliberately
    terse. Hence: the fallback must be a SUBSET of the descriptor's roles, and
    domain-neutral (lint-domain holds the neutrality half).
    """

    def test_the_fallback_offers_only_accepted_roles(self) -> None:
        from venona.domain import get_domain

        raw = _literal(WEB / "views/RequestAccessView.vue", r"const FALLBACK_ROLES = \[(.*?)\]")
        offered = tuple(re.findall(r"'([a-z]+)'", raw))
        accepted = set(get_domain().access_roles)
        # Same guard as `_labels`: a subset check against a non-empty set cannot
        # pass vacuously, but say so, because the next person to touch the regex
        # will not re-derive that.
        assert offered, "extraction found no roles; the regex has drifted"

        assert set(offered) <= accepted, (
            f"fallback offers {sorted(set(offered) - accepted)} that the gate rejects"
        )

    def test_other_is_last(self) -> None:
        """Not cosmetic: it is the escape hatch, and a list that buries it in the
        middle gets picked by people who did not read to the end."""
        raw = _literal(WEB / "views/RequestAccessView.vue", r"const FALLBACK_ROLES = \[(.*?)\]")
        assert tuple(re.findall(r"'([a-z]+)'", raw))[-1] == "other"


class TestRunCounterLabels:
    """C6 freezes thirteen counter names. `lib/runCopy.ts` labels them.

    The dangerous direction is a label for a counter that no longer exists:
    `shownCounters` filters on `value !== undefined`, so a renamed counter makes
    its column vanish from every run row with nothing logged and nothing thrown.
    The reverse -- a counter with no label -- degrades instead of breaking, via
    the `key.replace(/_/g, ' ')` fallback, so it is reported separately.
    """

    def _labels(self) -> tuple[str, ...]:
        raw = _literal(
            WEB / "lib/runCopy.ts",
            r"const COUNTER_LABELS: Record<string, string> = \{(.*?)\n\}",
        )
        names = tuple(re.findall(r"^\s*([a-z_]+):", raw, re.M))
        # A REGEX THAT STOPS MATCHING MUST FAIL, NOT PASS. `test_no_label_names_a_
        # counter_that_does_not_exist` checks a subtraction, and an empty left side
        # subtracts to nothing -- so a reformat that broke the extraction would
        # turn this whole class green while checking literally nothing.
        assert len(names) >= 10, f"extraction found only {names}; the regex has drifted"
        return names

    def test_no_label_names_a_counter_that_does_not_exist(self) -> None:
        frozen = set(RunCounters().as_dict())
        stale = [name for name in self._labels() if name not in frozen]
        assert not stale, (
            f"labelled but not a counter: {stale} -- the column silently disappears, "
            "because `shownCounters` filters on an undefined value"
        )

    def test_every_frozen_counter_has_a_reader_label(self) -> None:
        """The fallback opens the underscores, which is right for a counter
        nobody has got to yet and wrong as a resting state: `entities_seen`
        rendered as "entities seen" on every research run's page, and "entity"
        is this repo's word, not a reader's. The thirteen are frozen (C6), so
        the map can be complete and can be held complete."""
        labelled = set(self._labels())
        for name in RunCounters().as_dict():
            assert name in labelled, (
                f"{name} has no reader label in web/src/lib/runCopy.ts, so it "
                f"renders as its database spelling"
            )

    def test_every_counter_a_run_type_summarises_has_a_chosen_label(self) -> None:
        """The fallback is `entities_new` -> "entities new", which is worse than
        the chosen "entities" in a column three words wide. It only matters for
        counters something actually renders, which is what `summary` declares."""
        labelled = set(self._labels())
        summarised = {name for rt in run_types.RUN_TYPES.values() for name in rt.summary}
        missing = sorted(summarised - labelled)
        assert not missing, f"summarised by a run type with no chosen label: {missing}"

    def test_every_summarised_counter_is_a_frozen_counter(self) -> None:
        """Backend-only, but it belongs beside the two above: a `summary` naming
        something `RunCounters` does not have renders nothing at all."""
        frozen = set(RunCounters().as_dict())
        for kind, rt in run_types.RUN_TYPES.items():
            unknown = sorted(set(rt.summary) - frozen)
            assert not unknown, f"run type {kind!r} summarises unknown counters: {unknown}"


class TestOperatorNav:
    """A link the router will bounce you off is worse than no link.

    FOUR files decide whether a reader sees a destination: the route's
    `meta.operator`, `AppHeader::NAV`, `CommandPalette::ROUTES` and
    `useKeyboard::NAV_KEYS`. The router guard reads the first; the other three
    filter on their OWN copy of the flag. A route marked operator whose nav entry
    is not shows a reader a link that redirects them back to the brief with no
    explanation -- and nothing raises, on either side.

    The keyboard was the one with no copy at all, which is how `alt+3` and
    `alt+4` came to send a reader to `/curation` and `/runs` -- keys the `?`
    overlay listed for them, and the guard bounced. It is checked here rather
    than in the frontend suite because the fact being checked lives in a Python-
    parsed `.ts` file either way, and this is where the other three are.

    AN EARLIER DRAFT OF THIS TEST COMPARED COUNTS -- how many `isAdmin()` gates
    against how many `meta.operator` routes. There are six call sites across five
    files and about eight such routes, so the counts were never going to match
    and a comparison between them means nothing. The real invariant is per-entry
    and is what is written below.
    """

    ROUTER = WEB / "router/index.ts"

    def _operator_paths(self) -> dict[str, bool]:
        """Every declared route path, and whether its record carries `operator`.

        Each record runs from its own `path:` to the next one, which is what
        makes a flat regex enough here: the records are siblings in one array and
        none nests another.
        """
        # COMMENTS STRIPPED FIRST. The records are separated by long `//`
        # explanations, several of which discuss `operator: true` in prose --
        # including the one above `/admin`. Scanning the raw text attributed the
        # NEXT record's comment to the PREVIOUS record and marked `/graph`
        # admin-only, which is a test that fails for a reason that has nothing to
        # do with what it checks.
        text = re.sub(r"//.*", "", self.ROUTER.read_text())
        starts = [(m.start(), m.group(1)) for m in re.finditer(r"path: '([^']+)'", text)]
        assert len(starts) >= 15, f"extraction found {len(starts)} routes; the regex has drifted"
        out: dict[str, bool] = {}
        for i, (offset, path) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            out[path] = "operator: true" in text[offset:end]
        return out

    def _requires_admin(self, to: str) -> bool:
        """What the router answers for a link's `to`, param routes included.

        A link may point at the parent of a param route rather than at a
        declared path of its own -- `/admin/runs/:id` is reached from
        `/admin/runs`, and a
        comparison of literal paths alone would silently skip any link whose
        destination is only spelled out one segment down. Falling back to the
        param record's parent is what keeps those rows checked.
        """
        paths = self._operator_paths()
        if to in paths:
            return paths[to]
        for path, operator in paths.items():
            if ":" in path and path.rsplit("/", 1)[0] == to.rstrip("/"):
                return operator
        raise AssertionError(f"{to} resolves to no route in router/index.ts")

    def _entries(self, path, pattern: str) -> list[tuple[str, bool]]:
        """Each entry's `to` and whether it is marked operator.

        PER RECORD, NOT PER LINE. An entry with enough route names to wrap --
        `/admin` owns six -- puts `to:` and `operator: true` on different lines,
        and a line-at-a-time scan then reads every wrapped entry as a reader
        destination. That failure points at the entry rather than at the parser,
        which is the worst way for a drift test to be wrong.

        Comments are stripped first, for the reason `_operator_paths` strips
        them: the records are separated by prose that discusses `operator`, and
        an entry's chunk runs up to the next `to:`.
        """
        raw = re.sub(r"//.*", "", _literal(path, pattern))
        marks = [m.start() for m in re.finditer(r"to: '", raw)]
        found: list[tuple[str, bool]] = []
        for i, start in enumerate(marks):
            chunk = raw[start : marks[i + 1] if i + 1 < len(marks) else len(raw)]
            to = re.search(r"to: '([^']+)'", chunk)
            assert to
            found.append((to.group(1), "operator: true" in chunk))
        assert found, f"extraction found no entries in {path.name}; the regex has drifted"
        return found

    def _route_names(self) -> set[str]:
        """Every `name:` a route record declares.

        Same stripped text as `_operator_paths` and for the same reason: the
        records are separated by long `//` explanations, and several of those
        quote a route name in prose.
        """
        text = re.sub(r"//.*", "", self.ROUTER.read_text())
        names = set(re.findall(r"name: '([a-z-]+)'", text))
        assert len(names) >= 15, f"extraction found {len(names)} names; the regex has drifted"
        return names

    # Routes a nav entry deliberately does not claim, each with the reason it is
    # reached another way. A route absent from BOTH this table and every entry's
    # `names` renders the whole bar with nothing lit, which is what
    # `test_every_route_name_is_claimed` exists to catch.
    UNLISTED = {
        "login": "public, and rendered outside AppShell",
        "request-access": "public, and rendered outside AppShell",
        "welcome": "a one-time invitation, reached by redirect",
        "search": "reached from the palette's footer, not the nav",
        "entity": "reached from a card, a search result, the graph rail or a link somebody sent",
        "guide": "reached from the header's utility cluster",
        "account": "reached from the account menu",
        "settings-relevance": "reached from the settings rail",
        "settings-agents": "reached from the settings rail",
        "settings-notifications": "reached from the settings rail",
        "settings-billing": "reached from the settings rail, and from the dunning banner",
        "not-found": "the catch-all; nothing navigates to it on purpose",
    }

    def test_every_route_name_is_claimed(self) -> None:
        """A route no nav entry owns lights nothing in the bar.

        `AppHeader::isActive` is `names.includes(route.name)` and nothing more,
        so the failure is silent and total: the whole row renders unlit, on a
        page the reader navigated to from that very row. `/admin/agents`,
        `/setup` and a run's metrics all did this -- three routes that shipped
        after the entries listing their siblings, which is exactly the drift
        this file exists for. The count check in this class cannot see it,
        because a missing name is not a disagreement about `operator`.
        """
        raw = _literal(WEB / "components/ui/AppHeader.vue", r"const NAV = \[(.*?)\] as const")
        claimed = set(re.findall(r"'([a-z-]+)'", re.sub(r"to: '[^']+'", "", raw)))
        assert claimed, "extraction found no route names in AppHeader::NAV"
        unclaimed = sorted(self._route_names() - claimed - set(self.UNLISTED))
        assert not unclaimed, (
            f"{unclaimed} are declared routes that no AppHeader::NAV entry claims, "
            "so the nav renders with nothing active on them. Add the name to the "
            "owning entry's `names`, or to UNLISTED with the reason it is reached "
            "another way."
        )

    def test_no_nav_entry_claims_a_route_that_does_not_exist(self) -> None:
        """The other direction: a renamed route leaves a dead name behind, and a
        dead name never matches, so the entry simply stops lighting up."""
        raw = _literal(WEB / "components/ui/AppHeader.vue", r"const NAV = \[(.*?)\] as const")
        claimed = set(re.findall(r"'([a-z-]+)'", re.sub(r"to: '[^']+'", "", raw)))
        stale = sorted(claimed - self._route_names())
        assert not stale, f"{stale} appear in AppHeader::NAV but no route declares them"

    def test_every_header_nav_entry_agrees_with_its_route(self) -> None:
        wrong = [
            to
            for to, operator in self._entries(
                WEB / "components/ui/AppHeader.vue", r"const NAV = \[(.*?)\] as const"
            )
            if operator != self._requires_admin(to)
        ]
        assert not wrong, (
            f"AppHeader nav entries disagree with their routes: {wrong} -- a reader is "
            "either shown a link the guard bounces, or denied one they may use"
        )

    def test_every_palette_entry_agrees_with_its_route(self) -> None:
        wrong = [
            to
            for to, operator in self._entries(
                WEB / "components/nav/CommandPalette.vue", r"const ROUTES: Row\[\] = \[(.*?)\n\]"
            )
            if operator != self._requires_admin(to)
        ]
        assert not wrong, f"palette entries disagree with their routes: {wrong}"

    def test_every_keyboard_nav_binding_agrees_with_its_route(self) -> None:
        """`alt+1`..`alt+7`, which are the same destinations as the header nav.

        The failure this catches is quieter than the other two: there is no link
        to look wrong, just a key that navigates and is bounced straight back.
        """
        wrong = [
            to
            for to, operator in self._entries(
                WEB / "composables/useKeyboard.ts", r"const NAV_KEYS = \[(.*?)\] as const"
            )
            if operator != self._requires_admin(to)
        ]
        assert not wrong, (
            f"keyboard nav bindings disagree with their routes: {wrong} -- a reader is "
            "either given a key the guard bounces, or denied one they may use"
        )

    def test_the_keyboard_and_the_header_offer_the_same_destinations(self) -> None:
        """`useKeyboard.ts` calls `NAV_KEYS` "positional against `AppHeader::NAV`".

        It said that for a long time while it was false -- NAV grew from four
        entries to seven and the keys stayed at four, so `alt+3` went to Curation
        where the nav's third item is Archive. Order is the whole claim, so this
        compares SEQUENCES, not sets.
        """
        header = [
            to
            for to, _ in self._entries(
                WEB / "components/ui/AppHeader.vue", r"const NAV = \[(.*?)\] as const"
            )
        ]
        keys = [
            to
            for to, _ in self._entries(
                WEB / "composables/useKeyboard.ts", r"const NAV_KEYS = \[(.*?)\] as const"
            )
        ]
        assert keys == header, (
            f"alt+1.. goes {keys}, the header nav reads {header} -- the two are "
            "documented as positional against each other"
        )


class TestPeopleLiterals:
    """`lib/people.ts` duplicates four backend sets, and says so.

    Each is a Record lookup with a `?? 'neutral'` fallback, so a value the
    backend gained and the frontend did not renders as an unstyled chip -- the
    failure looks like a data problem, which is the shape every check in this
    file guards.
    """

    def _set(self, name: str) -> set[str]:
        raw = _literal(WEB / "lib/people.ts", rf"export const {name} = \[(.*?)\] as const")
        found = set(re.findall(r"'([a-z]+)'", raw))
        assert found, f"extraction found no members of {name}; the regex has drifted"
        return found

    def test_roles_match_authz(self) -> None:
        assert self._set("ROLES") == set(authz.ROLES)

    def test_user_statuses_match_the_service(self) -> None:
        assert self._set("USER_STATUSES") == {user_service.ACTIVE, user_service.SUSPENDED}

    def test_request_statuses_match_the_repository(self) -> None:
        assert self._set("REQUEST_STATUSES") == {
            access_repo.PENDING,
            access_repo.APPROVED,
            access_repo.DECLINED,
        }

    def test_every_role_has_a_tone(self) -> None:
        """The chip is how a reader tells an admin from a reader at a glance; an
        unmapped role renders in the neutral fallback, which is what `reader`
        already is."""
        raw = _literal(
            WEB / "lib/people.ts", r"export const ROLE_TONE: Record<string, Tone> = \{(.*?)\n\}"
        )
        toned = set(re.findall(r"^\s*([a-z]+):", raw, re.M))
        assert set(authz.ROLES) <= toned, f"no tone for {sorted(set(authz.ROLES) - toned)}"


class TestChromeGrammar:
    """One field for narrowing a list, and one place its parts are assembled.

    `Input.vue`'s docstring records five boxed fields written five different
    ways. The same divergence then happened to the assembly AROUND the field --
    the search glyph, the clear affordance, the in-flight spinner and the width
    -- across `ArchiveView`, `GraphSearch`, `RelevanceTailor` and `PathFinder`
    twice, none of which agreed and only some of which could be cleared at all.
    `FilterField` owns it now, and these two checks are what keep it owning it.

    BOTH HALVES ARE NEEDED and the second is the one that matters. A grep for
    `type="search"` catches a copy that starts from `ArchiveView`; it does not
    catch one that starts from `PathFinder`, whose fields never carried the
    attribute. What every copy DID share was an `Input` with a `Spinner` beside
    it, which is the shape below.
    """

    FIELD = WEB / "components/ui/FilterField.vue"

    def _templates(self) -> list[tuple[Path, str]]:
        found = [
            (path, path.read_text()) for path in sorted(WEB.rglob("*.vue")) if path != self.FIELD
        ]
        assert len(found) > 50, f"found only {len(found)} components; the glob has drifted"
        return found

    def test_the_search_input_type_lives_in_one_component(self) -> None:
        """`type="search"` is how a field says it filters. One field says it.

        `base.css` styles a bare `type="search"` by element selector, so the
        attribute is not cosmetic -- a second component carrying it inherits
        that treatment and then diverges from it locally, which is the whole
        pattern this test is here to stop.
        """
        offenders = [
            str(path.relative_to(WEB))
            for path, text in self._templates()
            if 'type="search"' in text
        ]
        assert not offenders, (
            f'{offenders} carry type="search" outside FilterField.vue. A field that '
            "narrows a list is a FilterField; a picker that feeds a two-argument "
            "operation is a FilterField too, with its dropdown in the default slot."
        )

    def test_no_component_pairs_an_input_with_its_own_spinner(self) -> None:
        """An `Input` with a `Spinner` for a sibling is a filter field being
        rebuilt by hand, whatever it is called locally.

        Matched on the SIBLING relationship rather than mere co-occurrence: a
        view may legitimately hold a form field and, elsewhere, a spinner for
        something else. What it may not do is assemble the pair.
        """
        offenders = []
        for path, text in self._templates():
            for match in re.finditer(r"<Input\b", text):
                rest = text[match.end() :]
                close = rest.find("/>")
                if close == -1:
                    continue
                after = rest[close + 2 :]
                # The next ELEMENT, skipping whitespace and comments.
                after = re.sub(r"\s+|<!--.*?-->", "", after, flags=re.S)
                if after.startswith("<Spinner"):
                    offenders.append(str(path.relative_to(WEB)))
        assert not offenders, (
            f"{offenders} pair an Input with a Spinner sibling, which is FilterField "
            "assembled by hand. Use the component: it puts the spinner inside the "
            "box, beside the text the request is for."
        )


class TestRunDetailAsksTheRegistry:
    """The run detail page renders every kind of run from one declaration.

    Contract C11 puts a run's LAYOUT in `run_types.py` -- label, stages,
    counters, `detail` -- and `RunType.facts`/`.lines` put its CONTENT there
    too. The page therefore never asks what kind of run it is looking at, and
    this is what stops the question growing back.

    The failure it guards has no other detector. A branch for one kind renders
    fine, typechecks fine, and is wrong only for the kinds that fall through it:
    a panel written for a lookup showed `subject` with nothing in it and
    `sourced: no` on a reader's brief that had just written a document, and
    every test in the suite passed while it did.
    """

    VIEW = Path("web/src/views/RunDetailView.vue")

    def test_the_page_does_not_branch_on_the_kind_of_run(self) -> None:
        source = self.VIEW.read_text()
        offenders = re.findall(r"\.kind\s*===?\s*['\"][a-z_]+['\"]", source)
        assert not offenders, (
            f"{self.VIEW} compares run.kind to a literal ({offenders}). "
            f"Declare it in run_types.RunType instead -- facts, lines or detail."
        )

    HEADER = Path("web/src/components/runs/RunResultHeader.vue")

    def test_every_kind_reaches_the_page_with_words_of_its_own(self) -> None:
        """The declaration is only load-bearing if the surfaces read it. Both
        halves of a run's words are resolved server-side -- the facts panel and
        the sentence under the verdict -- so if either binding leaves, something
        has gone back to working the answer out in the browser."""
        assert ".facts" in self.VIEW.read_text(), f"{self.VIEW} no longer reads the declared facts"
        assert "result_line" in self.HEADER.read_text(), (
            f"{self.HEADER} no longer reads the declared sentence"
        )

    def test_the_verdict_sentence_is_not_assembled_in_the_browser(self) -> None:
        """CLAUDE.md: never invent words for a run outcome at the render site.

        The chip has been server-resolved since ADR 0183 and the sentence under
        it now is too, so this component interpolates nothing. A template
        literal is what going back looks like -- it is how `Wrote about ${n}
        stories` and `Read ${pages} pages` were built out of keys no run type
        declared.

        Checked as `${`, the interpolation marker, rather than as words or as a
        bare backtick. Both of those fire on the file's own explanation of
        itself -- prose quotes code in backticks throughout this repo -- and a
        check that fires on its own documentation teaches the next person to
        delete the documentation. `lint_readonly.py` records the same trap.
        """
        source = self.HEADER.read_text()
        assert "${" not in source, (
            f"{self.HEADER} interpolates a string. A run's words belong in "
            f"run_types.RunType.lines, resolved server-side."
        )


class TestARailSitsInOnePlace:
    """Pages that share a rail must share a shell width.

    `RailLayout` puts the rail INSIDE `ViewShell`, so the shell's width decides
    where the rail starts. Two sibling pages with different widths therefore
    re-lay out the navigation you switched with: on an iPad in portrait,
    `/settings/billing` at `prose` drew the rail about 140px right of where the
    other four settings pages draw it, so tapping between them moved the thing
    being tapped.

    Grepped rather than generated for the reason at the top of this file: the
    width is a design choice per page and not derivable. What is checkable is
    that pages sharing a rail agree, and the failure is invisible in every
    per-page check -- each one is individually correct.
    """

    VIEWS = Path(__file__).resolve().parents[1] / "web" / "src" / "views"
    #: `SetupView` is exempt and says why in its own template: the wizard runs
    #: as the first-boot page with no chrome at all, so it carries no rail and
    #: switches width on purpose.
    EXEMPT = {"SetupView.vue"}

    def _width(self, path: Path) -> str | None:
        match = re.search(r'<ViewShell[^>]*?width="([a-z]+)"', path.read_text(), re.S)
        return match.group(1) if match else None

    def _pages_with(self, rail: str) -> dict[str, str | None]:
        return {
            path.name: self._width(path)
            for path in sorted(self.VIEWS.glob("*.vue"))
            if rail in path.read_text() and path.name not in self.EXEMPT
        }

    @pytest.mark.parametrize("rail", ["SettingsRail", "AdminRail"])
    def test_every_page_on_a_rail_agrees_on_the_shell_width(self, rail: str) -> None:
        widths = self._pages_with(rail)
        assert widths, f"no pages found carrying {rail}"
        distinct = {w for w in widths.values() if w is not None}
        assert len(distinct) == 1, (
            f"{rail} pages disagree on shell width: {widths}. The rail is inside the "
            f"shell, so this moves the navigation between sibling pages. Narrow "
            f"content belongs in a narrow column, not a narrow shell."
        )


class TestAnOutageIsSaidOnce:
    """One component says the API stopped answering, and nothing else does.

    The offline screen was written out longhand in three views and nowhere
    else, so every other page fell through to `ViewShell`'s raw error state and
    rendered whatever status line it happened to catch. A gateway failure in
    front of this API carries no reason phrase, so on the archive, the lookup
    list and the learn page that was the string ``502:`` and nothing after it,
    under a header still announcing zero briefs.

    Two halves, and the second is the one that keeps coming back: a page that
    supplies its own `#error` slot BYPASSES the shell's default entirely, and
    the sentence it puts there is a diagnosis. "No such run" and "nothing has
    been read yet" are both claims about a database nothing has been able to
    ask. Every such page must check for the outage first.
    """

    SHARED = Path("web/src/components/shared/ApiUnreachable.vue")
    #: The heading is the phrase to keep single-sourced. `StateNotice` is the
    #: primitive underneath and stays general: other outages (the graph
    #: database, the model) have their own headings and their own consequences.
    HEADING = "The API stopped answering"

    def test_the_component_owns_the_heading(self) -> None:
        source = (Path(__file__).resolve().parents[1] / self.SHARED).read_text()
        assert self.HEADING in source, f"{self.SHARED} no longer carries the heading"

    def test_no_view_writes_the_heading_itself(self) -> None:
        offenders = [
            path.name
            for path in sorted((WEB / "views").glob("*.vue"))
            if self.HEADING in path.read_text()
        ]
        assert not offenders, (
            f"{offenders} write the offline heading out longhand. Render "
            f"ApiUnreachable instead; the sentence about what is paused on that "
            f"page is its slot."
        )

    def test_the_default_error_state_checks_for_an_outage(self) -> None:
        """The fallback every view without an `#error` slot lands in."""
        ui = WEB / "components" / "ui"
        for path in (ui / "ViewShell.vue", ui / "PaneState.vue"):
            source = path.read_text()
            # The TAG, not the import: an import alone satisfied this while the
            # template had gone back to rendering the status line.
            assert "<ApiUnreachable" in source and "apiDown" in source, (
                f"{path.name} renders an error message without checking whether "
                f"the API is answering. Every view hands its store's failure "
                f"straight to it, so this is where a status line reaches a reader."
            )

    def test_a_view_that_supplies_its_own_error_state_checks_for_an_outage(self) -> None:
        offenders = [
            path.name
            for path in sorted((WEB / "views").glob("*.vue"))
            if "#error" in (source := path.read_text()) and "apiDown" not in source
        ]
        assert not offenders, (
            f"{offenders} supply an #error slot, which replaces ViewShell's "
            f"default, and never ask whether the API is down. Their own "
            f"diagnosis is a guess about a server that is not answering: check "
            f"apiDown first and render ApiUnreachable."
        )


def test_a_keyboard_first_button_carries_no_glyph() -> None:
    """ADR 0159 and `docs/DESIGN.md`: a row with a `Kbd` cap gets no icon.

    Both documents said "never both" while eight buttons across the two curation
    queues shipped both, which is the shape of rule that exists in prose and
    nowhere else. The cap and the glyph compete for the same leading position,
    and the reader learns neither.
    """
    offenders: list[str] = []
    for path in sorted(WEB.rglob("*.vue")):
        source = path.read_text(encoding="utf8")
        for block in re.split(r"(?=<Button\b)", source):
            if not block.startswith("<Button"):
                continue
            opening, _, rest = block.partition(">")
            slot = rest.split("</Button>")[0]
            if "<Kbd>" in slot and re.search(r'\bicon="', opening):
                label = re.search(r'label="([^"]*)"', opening)
                offenders.append(
                    f"{path.relative_to(WEB)}: {label.group(1) if label else opening[:40]}"
                )
    assert not offenders, "a Kbd cap beside an icon:\n" + "\n".join(offenders)
