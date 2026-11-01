"""Every knob this instance had when configuration first became migratable.

The document beside this file is `config/settings.yaml` as it stood on
2026-09-01, pinned rather than read from the repository at runtime. That is what
makes a migration a migration: a historical fact does not change when the
repository does, so replaying this on a five-year-old instance means today what
it meant the day it was written. `db/migrations/0001_baseline.sql` states its
starting schema in full for the same reason, and is a data file for the same
reason too.

It adds only what a document lacks. An instance scaffolded before a knob existed
gets that knob and the comment explaining it; every value its operator chose is
left exactly as they left it.
"""

from pathlib import Path

from venona.services.config_migrations import Baseline, Migration

SHIPPED = (Path(__file__).with_suffix(".yaml")).read_text(encoding="utf-8")

MIGRATION = Migration(
    version=1,
    description="every knob this instance had when configuration became migratable",
    ops=(Baseline(SHIPPED),),
)
