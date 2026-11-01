"""One timeout for every call is one number too few.

The wizard's ontology document does not fit the 300s default and was killed at
it three times in a row, so a task can now carry its own ceiling. The knob
belongs in every instance's file, not only the shipped one: the instance that
most needs it is the one about to be scaffolded, and the operator who has to
raise a second task's ceiling should find the shape of the answer already
there. ADR 0190.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=4,
    description="a per-task call ceiling, for the calls the default does not fit",
    ops=(
        AddKey(
            group="llm",
            key="task_timeout_seconds",
            block=(
                "  # Ceilings for single calls the 300s default does not fit, keyed by task.",
                "  # Venona ships `setup_ontology: 900`; entries here win, per task, over that.",
                "  task_timeout_seconds: {}",
            ),
            value={},
        ),
    ),
)
