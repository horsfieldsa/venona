"""How a notice leaves this machine: the shared ways out, plus the local one.

`DeliveryError` and the webhook channel are `notices.channels` -- the hub posts
to a webhook under exactly the same rules, and a second copy of "which status
codes are worth retrying" is two answers to one question. They are re-exported
here under the names the rest of this package already uses.

`email` stays. There is no transport for it yet and the hub has no inbox to
write to, so it is this service's own until that changes.
"""

from __future__ import annotations

from notices.channels import DeliveryError
from notices.channels import webhook as webhook

__all__ = ["DeliveryError", "webhook"]
