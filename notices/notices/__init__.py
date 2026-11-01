"""The notice core, shared by the instance and the hub.

WHY THIS IS A PACKAGE AND NOT A COPY. Both services tell somebody something:
the instance reports its own runs and sources to whoever the administrator
pointed it at, and the hub reports access requests and unreachable instances to
its operator. Two audiences, two registries, two databases -- and one set of
answers to the questions that are dangerous to get wrong twice.

Those questions are the whole contents:

  * `urlguard`  -- which addresses Venona will dial. A second copy of an SSRF
    guard is not a duplicated rule, it is a hole, because the copies drift and
    only one of them gets the next fix.
  * `untrust`   -- making text Venona did not write visible to whoever reads it.
  * `render`    -- a notice in the words each channel can carry, with a
    stranger's paragraph escaped for the markup it is about to land in.
  * `channels`  -- posting it, and saying whether trying again is worth it.

WHAT IS DELIBERATELY NOT HERE. The registry of notice types, the destinations
and subscriptions tables, the outbox and its retry policy. Those are each
service's own: the two databases speak different placeholder dialects and the
hub answers to one operator where the instance answers to a readership.

NOTHING BEYOND THE STANDARD LIBRARY. The hub's small dependency set is
deliberate and this package must never be the reason it grows, so the webhook
channel takes its http client as an argument instead of importing one.
"""
