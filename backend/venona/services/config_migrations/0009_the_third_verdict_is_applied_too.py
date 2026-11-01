"""A `part_of` verdict stops waiting for a person, under a tighter gate.

The third verdict is the one that carries the fact a conflict surfaced: `BAE
Space & Mission Systems` is not `BAE Systems`, and saying only that they are
different throws away the relationship between them.

Gated harder than the other two, because it is the only one that WRITES rather
than settles. The ontology must have a legal predicate for the pair, and the
child's name must contain the parent's -- a subsidiary carries its parent's
name and never the reverse, which is exactly the direction `adjudicate` records
the model inverting on three of twelve hand-checked pairs. ADR 0230.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=9,
    description="apply a certain `part_of` verdict the names corroborate",
    ops=(
        AddKey(
            group="pipeline",
            key="auto_relate_model_verdicts",
            block=(
                "  # And apply a `part_of` verdict, but only where the ontology has a predicate",
                "  # for it and the child's name contains the parent's -- a subsidiary carries",
                "  # its parent's name and never the reverse, which is the direction the model",
                "  # is known to invert. An edge is reversible and destroys no id. ADR 0230.",
                "  auto_relate_model_verdicts: true",
            ),
            value=True,
        ),
    ),
)
