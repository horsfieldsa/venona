"""How a notice actually leaves the machine.

One protocol, one module per way out. A channel takes a rendered `Notice` and a
target and either returns or raises: it decides nothing about who should be
told, retries nothing, and writes no row. That is `outbox.py`'s job, and keeping
the split means adding a third way out is a file rather than an edit to the
policy.
"""

from __future__ import annotations


class DeliveryError(RuntimeError):
    """The send did not happen. The message is shown to a person.

    Carries `permanent` so the outbox can tell "try again in four minutes" from
    "this will never work". A 404 from a revoked Slack webhook and a refused
    connection look identical to a retry loop and are not the same thing:
    retrying the first forever is how a destination stays struck out with a
    misleading error, and giving up on the second is how a five-minute outage
    loses a day's alerts.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent
