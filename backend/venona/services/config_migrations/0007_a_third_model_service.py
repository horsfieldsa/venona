"""Grok Build becomes selectable, which is two keys and no decisions.

Additive only. `llm.harness` is unchanged, so an instance that is reading on one
service keeps reading on it -- this migration makes a third row SELECTABLE at
`/admin/model` and chooses nothing on an operator's behalf.

`grok_pinned_version` ships EMPTY, and that is the one judgement here. The other
two pins name a version somebody ran; nobody has run this one, so a number would
be a claim rather than a pin, and `/admin/model` compares the installed version
against whatever is set -- so an invented value would report every correct
install as a mismatch. Empty reads as "no pin", which is true.
"""

from venona.services.config_migrations import AddKey, Migration

MIGRATION = Migration(
    version=7,
    description="a third model service, selectable but unpinned",
    ops=(
        AddKey(
            group="llm",
            key="grok_cli_path",
            block=("  grok_cli_path: grok",),
            value="grok",
        ),
        AddKey(
            group="llm",
            key="grok_pinned_version",
            block=(
                "  # Empty until somebody runs this service and records what they ran.",
                "  # A version here is compared against the installed one, so a guess",
                "  # would report a working install as the wrong version.",
                '  grok_pinned_version: ""',
            ),
            value="",
        ),
    ),
)
