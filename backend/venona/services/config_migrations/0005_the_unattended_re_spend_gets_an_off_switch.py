"""The one scheduled behaviour that spends on its own initiative gets a knob.

A daily that met a provider outage re-fires itself, up to three times, half an
hour apart. The ceiling and the delay were module constants in `scheduler.py`,
so the only way to stop an unattended re-spend was a deploy -- and on an
instance whose config lives on a disk rather than in the repo, a deploy would
not even do it.

It belongs in every instance's file for the reason 0004's did: the operator who
wants tonight's retry off is not the one who can ship. `outage_retry_max: 0`
turns it off. ADR 0194 widened what earns a retry, which is what made the
absence of an off switch worth fixing now.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=5,
    description="the outage retry's ceiling and delay, so it can be turned off without a deploy",
    ops=(
        AddKey(
            group="schedule",
            key="outage_retry_max",
            block=(
                "  # A daily that met a provider outage is re-fired this long after it ends,",
                "  # this many times. The retry resumes by state, so it costs only the work the",
                "  # outage ate. This is the one scheduled behaviour that spends on its own",
                "  # initiative while nobody is watching. 0 turns it off.",
                "  outage_retry_max: 3",
            ),
            value=3,
        ),
        AddKey(
            group="schedule",
            key="outage_retry_delay_seconds",
            block=("  outage_retry_delay_seconds: 1800",),
            value=1800,
        ),
    ),
)
