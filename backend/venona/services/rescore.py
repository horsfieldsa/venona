"""Recompute cluster scores from stored per-axis scores. Zero model calls.

This is why the model is never asked for an overall score. Each analyze call
stores the per-axis 0-5 values it assigned; the rollup into 0-100 happens in
Python. Change a weight in `profile/scoring_rubric.yaml` and every historical
cluster can be re-ranked instantly and for free.

The distinction that matters, and that the API surfaces to the user:

  * changing a WEIGHT      -> free. This function.
  * changing a DESCRIPTION -> changes what the model was asked, so the stored
                              axis scores answer a different question. That
                              needs re-analysis and real spend.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from venona.db import core as db
from venona.db.repositories import rescore as repo
from venona.llm.schemas import axis_ids, axis_weights, compute_overall, load_rubric, tier_for


@dataclass
class RescoreReport:
    clusters: int = 0
    changed: int = 0
    tier_changes: int = 0
    missing_axes: int = 0
    weights: dict[str, float] | None = None

    def as_dict(self) -> dict:
        return {
            "clusters": self.clusters,
            "changed": self.changed,
            "tier_changes": self.tier_changes,
            "missing_axes": self.missing_axes,
            "weights": self.weights or {},
        }


def rubric_fingerprint() -> str:
    """Identifies the rubric a cluster was scored under.

    Only the axis IDS and DESCRIPTIONS go into it, deliberately -- not the
    weights. A weight change is free to apply, so it must not invalidate stored
    scores; a description change means the model answered a different question,
    so it must.
    """
    rubric = load_rubric()
    payload = "\0".join(
        f"{axis['id']}:{(axis.get('description') or '').strip()}" for axis in rubric["axes"]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def rescore_all(conn: db.Conn) -> RescoreReport:
    report = RescoreReport(weights=axis_weights())
    known = set(axis_ids())

    rows = repo.select_clusters(conn).fetchall()

    for row in rows:
        stored = db.jload(row["axes"], {}) or {}
        scores = {k: float((v or {}).get("score", 0)) for k, v in stored.items()}
        report.clusters += 1

        # An axis added to the rubric since this cluster was analysed has no
        # stored score. It contributes 0 rather than being guessed at, and the
        # count is surfaced so the UI can offer a re-analysis.
        if known - set(scores):
            report.missing_axes += 1

        overall = compute_overall(scores)
        tier = tier_for(overall)

        if overall != (row["relevance_overall"] or 0):
            report.changed += 1
        if tier != row["tier"]:
            report.tier_changes += 1

        repo.update_clusters_by_relevance_overall_tier(conn, overall, tier, row["id"])

    return report
