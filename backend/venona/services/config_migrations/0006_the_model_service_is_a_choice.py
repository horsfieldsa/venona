"""Which service does the thinking, and what pays for it, become settings.

`llm.backend` answers two questions at once -- how the model is reached (a CLI,
or recorded fixtures) and which vendor's CLI that is. Only the first is a
deployment fact; the second is a choice an administrator should be able to make
from the app without a shell. So `backend` narrows to `cli | replay` and
`harness` carries the vendor.

Splitting rather than adding is what keeps the pair from disagreeing: with both
`backend: claude_cli` and `harness: codex` representable, an instance could
name one vendor and run another, and nothing would say which one won.

`backend` is overwritten rather than left alone because it is already a field
`config_service.FROZEN_FIELDS` refuses over HTTP -- there is no operator intent
here to respect, and `claude_cli` has no meaning under the new pair. Every
other key here is additive, so an instance that has tuned its models keeps
every value it chose.
"""

from venona.services.config_migrations import AddKey, Migration, SetValue

MIGRATION = Migration(
    version=6,
    description="the model service and how it is paid for, as settings rather than a deploy",
    ops=(
        SetValue(group="llm", key="backend", to="cli", force=True),
        AddKey(
            group="llm",
            key="harness",
            block=(
                "  # Which service does Venona's thinking. Change it under Model in Admin,",
                "  # which checks there is something to authenticate with first. The name",
                "  # resolves to a program Venona ships with; it is never a path.",
                '  harness: "claude_code"',
            ),
            value="claude_code",
        ),
        AddKey(
            group="llm",
            key="billing_mode",
            block=(
                "  # subscription | api. On a subscription what binds is a usage window and",
                "  # the cost figures are what the same tokens would have cost at published",
                "  # rates. Under an API key the money is real, there is no window, and",
                "  # max_spend_usd_per_run applies.",
                '  billing_mode: "subscription"',
            ),
            value="subscription",
        ),
        AddKey(
            group="llm",
            key="codex_cli_path",
            block=("  codex_cli_path: codex",),
            value="codex",
        ),
        AddKey(
            group="llm",
            key="codex_pinned_version",
            block=('  codex_pinned_version: "0.151.0"',),
            value="0.151.0",
        ),
        AddKey(
            group="llm",
            key="max_spend_usd_per_run",
            block=(
                "  # Only under billing_mode: api. Hitting it defers the rest of the work and",
                "  # ends the run partial, exactly like the call cap -- never failed. A",
                "  # subscription ignores it: capping a run against money nobody was charged",
                "  # is the confusion that put a spend cap on a flat-rate plan once already.",
                "  max_spend_usd_per_run: 25",
            ),
            value=25,
        ),
        AddKey(
            group="llm",
            key="confine",
            block=(
                "  # auto | off. Whether a model process is shut into its own files before",
                "  # it runs, where the machine can do that. `auto` confines the services",
                "  # that need it and runs the rest normally; `off` is for diagnosing a",
                "  # service a restriction broke. Admin, Model reports which happened.",
                '  confine: "auto"',
            ),
            value="auto",
        ),
    ),
)
