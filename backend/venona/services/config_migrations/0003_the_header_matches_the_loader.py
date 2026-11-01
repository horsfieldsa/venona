"""The header line promising environment overrides is not true of this build.

`VENONA_LLM__ANALYZE_MODEL=opus` is not read. The three tuning groups come from
this instance's own file and nowhere else, so that one knob the config rail
reports as saved cannot be outranked by something outside the document.

Every instance carries its own copy of that sentence, so correcting the shipped
file alone would leave the promise standing everywhere it is actually read.
"""

from venona.services.config_migrations import Migration, RewriteComment

MIGRATION = Migration(
    version=3,
    description="the header line matches what the loader does",
    ops=(
        RewriteComment(
            old="# Env vars override these: VENONA_LLM__ANALYZE_MODEL=opus, etc.",
            new="# Every value here belongs to this instance. Edit them in Admin, Config.",
        ),
    ),
)
