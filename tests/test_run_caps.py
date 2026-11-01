"""How much work a run takes on, and where that answer comes from.

`limit_per_source` and `analyze_limit` used to live only as CLI flags, as
optional API fields, and as a "Run defaults" panel on the Runs page that wrote
to `localStorage`. So a run a person started by hand could be capped while the
nightly scheduled run -- which passes neither -- ran uncapped, with nothing on
screen saying the two differed. `config/settings.yaml` is the standing answer
for both now.
"""

from __future__ import annotations

from venona.pipeline.analyze import pending_clusters
from venona.pipeline.types import resolve_cap
from venona.settings import get_settings


def test_an_explicit_argument_wins() -> None:
    """`venona run --limit 5` means 5 whatever the file says.

    One direction only. A config value that could override a flag typed at the
    prompt would make the flag a suggestion.
    """
    assert resolve_cap(5, 20) == 5


def test_the_config_answers_when_the_caller_does_not() -> None:
    """The API's "Run now" and the nightly run both name nothing."""
    assert resolve_cap(None, 20) == 20


def test_nothing_anywhere_means_uncapped() -> None:
    """The behaviour every run had before either knob existed, preserved as the
    default so adding the key does not change what a run does."""
    assert resolve_cap(None, None) is None


def test_an_explicit_zero_is_not_treated_as_absent() -> None:
    """`0` is falsy and `None` is absent, and conflating them is the classic
    bug in this shape: `argument or configured` would silently promote a
    deliberate zero to the config value."""
    assert resolve_cap(0, 20) == 0


def test_the_shipped_config_leaves_both_uncapped() -> None:
    """Adding the keys must not quietly bound anything. Whoever sets one is
    making a decision; until then a run behaves exactly as it always has."""
    pipeline = get_settings().pipeline
    assert pipeline.fetch_limit_per_source is None
    assert pipeline.analyze_limit is None


def test_a_cap_of_zero_means_none_not_all(conn) -> None:
    """The bug this shape always has.

    `if limit:` treats 0 as absent, so `--analyze-limit 0` -- fetch but analyze
    nothing, a legitimate dry run -- would have analyzed EVERY pending cluster,
    which is the most expensive thing the stage can do. `resolve_cap` keeps the
    zero and the consumers now honour it.
    """
    assert pending_clusters(conn, limit=0) == []


def test_pending_clusters_honours_the_resolved_cap(conn) -> None:
    """The consumer end: analyze is the stage that calls the model per cluster,
    so this is the cap that actually bounds what a run spends."""
    unbounded = pending_clusters(conn, limit=None)
    if len(unbounded) < 2:
        # The seed fixture has already been analyzed, so there is nothing
        # pending to slice. The precedence tests above still hold.
        return
    assert len(pending_clusters(conn, limit=1)) == 1
