"""A `distinct` verdict stops waiting for a person.

Additive, and the opposite risk from the switch above it. `auto_merge_model_
verdicts` is guarded because a wrong merge is invisible and poisons every
downstream query; keeping two entities apart leaves them as what they already
were, so a wrong distinction costs a missed merge that comes back. ADR 0230.

Ships ON, and is turned off in Admin, Configuration -- the same place the merge
half is turned off, because the two are one operator decision seen from two
sides: how much of this queue the machine may settle.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=8,
    description="apply a certain `distinct` verdict without a curator",
    ops=(
        AddKey(
            group="pipeline",
            key="auto_keep_apart_model_verdicts",
            block=(
                "  # And apply a `distinct` verdict, which is the opposite risk: keeping two",
                "  # entities apart leaves them as what they already were, so being wrong costs a",
                "  # missed merge that comes back rather than a change nobody can see. A pair the",
                "  # derivational rules would merge is never settled this way. ADR 0230.",
                "  auto_keep_apart_model_verdicts: true",
            ),
            value=True,
        ),
    ),
)
