"""How long a run waits out a provider's bad minute, and how often it asks.

Additive, and it changes nothing on an instance that leaves it alone: the two
keys carry the same defaults the code does. They exist so the waiting can be
tuned where the waiting hurts, which is not always here -- a research run has
one lane and hours to spend, and an operator watching a lookup does not.

The pair is on the wire rather than only in code because the alternative was
what happened before: three attempts doubling from one second, so a call meeting
the same bad minute three times had exhausted a policy that never waited three
seconds, and nobody could raise it without a release.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=10,
    description="the retry policy becomes editable",
    ops=(
        AddKey(
            group="llm",
            key="retry_attempts",
            block=(
                "  # How many times a transient failure (5xx, 529, a timeout) is tried before",
                "  # the call gives up and reports what it could not get. The waits between",
                "  # them double from `retry_base_seconds`.",
                "  retry_attempts: 4",
            ),
            value=4,
        ),
        AddKey(
            group="llm",
            key="retry_base_seconds",
            block=(
                "  # The first of those waits, in seconds, doubled per attempt and capped at a",
                "  # minute. Jittered by half, so several calls retrying at once do not arrive",
                "  # as one burst on a service that is already struggling.",
                "  retry_base_seconds: 5.0",
            ),
            value=5.0,
        ),
    ),
)
