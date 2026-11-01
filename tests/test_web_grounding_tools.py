"""A web search with no web.

`pipeline/define.py::lookup_web` called the model without `tools=`, so
`call_and_record`'s default `()` became `--tools ""` while its system prompt
asked the model to "locate authoritative pages". Entity web-grounding has never
searched anything.

The existing harness could not have caught it. `test_acronyms_grounding.py`'s
fake is `async def fake(ctx, task, *, user_content, **_kw)` -- `tools` vanishes
into `**_kw` and is never asserted on. So this file tests the thing rather than
the outcome, at three levels, because the outcome (a lookup that finds nothing)
is indistinguishable from an acronym that genuinely has no authoritative page.
"""

from __future__ import annotations

import inspect

import pytest

# Every model task that MUST be given tools. Declared here rather than inferred,
# so a fourth grounding kind is covered the day it is written and not the day
# someone notices it returning nothing.
TOOL_USING_TASKS = frozenset({"acronym_search", "define_search"})


def _pipeline_source(module: str) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend" / "venona" / "pipeline"
    return (root / module).read_text()


def _grounding_kinds() -> dict[str, type]:
    """Every `WebGrounding` subclass, keyed by the search task it declares.

    Discovered rather than listed, so a fourth grounding kind is covered the day
    it is written. Importing both modules is what populates the subclass set.
    """
    from venona.pipeline import acronyms, define, web_grounding  # noqa: F401

    return {
        cls.search_task: cls
        for cls in web_grounding.WebGrounding.__subclasses__()
        if getattr(cls, "search_task", None)
    }


@pytest.mark.parametrize("task", sorted(TOOL_USING_TASKS))
def test_every_search_task_is_given_tools(task: str) -> None:
    """LAYER 1. The regression itself, for every grounding kind.

    The shape of this changed when the two `lookup_web` copies were unified.
    There is now ONE `call_and_record` for a search, in `web_grounding.ground`,
    and each kind reaches it by declaring `search_task` -- so a task is
    tool-using by construction and the omission that started this file is no
    longer expressible per-kind.

    That makes the assertion two halves: the task must belong to a real
    grounding kind, and the single shared site must pass both tools. An AST-free
    read of that site is deliberate -- the alternative is driving the loop
    through a fake, and the whole reason the bug survived is that a fake makes
    it easy to not look at this argument.
    """
    kinds = _grounding_kinds()
    assert task in kinds, f"{task} is not any WebGrounding.search_task -- did it get renamed?"

    text = _pipeline_source("web_grounding.py")
    start = text.index("kind.search_task")
    body = text[start : start + 1200]
    assert '"WebSearch"' in body, f"{task}'s shared search site is not passed WebSearch"
    assert '"WebFetch"' in body, f"{task}'s shared search site is not passed WebFetch"


def test_no_grounding_kind_names_its_own_task_at_a_call_site() -> None:
    """The unification, held.

    Two copies of this loop is what produced the bug: `define.py`'s was written
    without `tools=` and nothing compared them. Re-adding a local
    `call_and_record("acronym_search", ...)` would recreate exactly that, and
    would pass the test above while doing it.

    So: inside the two kind modules, a grounding task name may appear ONLY as a
    `search_task = "..."` / `read_task = "..."` class attribute. Anywhere else
    it is a call site. Deliberately not "no `call_and_record` in these files" --
    `define.py` legitimately makes the tier-2 `define_write` call, which never
    touches the web and is not what this guards.
    """
    import re as _re

    grounding_tasks = TOOL_USING_TASKS | {"acronym_read", "define_read"}
    declaration = _re.compile(r"(search_task|read_task)\s*=\s*$")

    for module in ("acronyms.py", "define.py"):
        text = _pipeline_source(module)
        for task in grounding_tasks:
            for match in _re.finditer(_re.escape(f'"{task}"'), text):
                before = text[: match.start()].rsplit("\n", 1)[-1]
                assert declaration.search(before), (
                    f'{module} mentions "{task}" outside a `search_task`/`read_task` '
                    f"declaration, at: {before.strip()!r}. Tier-3 grounding goes through "
                    f"`web_grounding.ground`; a second copy of that loop is how "
                    f"`define_search` lost its tools for the life of the project."
                )


def test_the_read_task_is_deliberately_toolless() -> None:
    """The complement, and the reason a blanket "always pass tools" would be
    wrong: the read step is handed page text that has ALREADY been fetched, and
    giving it WebFetch would let it wander off and quote something we never
    saw -- which the quote-in-page gate would then reject, confusingly."""
    text = _pipeline_source("web_grounding.py")
    start = text.index("kind.read_task")
    assert '"WebSearch"' not in text[start : start + 1200]


def test_call_and_record_defaults_tools_to_empty() -> None:
    """LAYER 2a. The default that made the omission silent rather than an error.

    Pins it deliberately: the fix is NOT to change this default to something
    permissive. A task that needs tools should say so.
    """
    from venona.llm.runner import call_and_record

    default = inspect.signature(call_and_record).parameters["tools"].default
    assert default == ()


def test_empty_tools_produces_an_explicitly_disabled_argv() -> None:
    """LAYER 2b. The mechanism the bug rode in on.

    The driver's `else` branch emits `--tools ""`, which is invisible at the
    call site and looks like "no opinion" rather than "web access removed".
    """
    import asyncio

    from venona.llm.harness import CallRequest
    from venona.llm.harness.claude_code import CLAUDE_CODE

    class _NoFiles:
        async def materialize(self, content, *, kind, suffix):
            return None

    def argv_for(tools):
        return asyncio.run(
            CLAUDE_CODE.argv(
                CallRequest(
                    task="acronym_search",
                    system_prompt="",
                    schema={"type": "object"},
                    model="sonnet",
                    effort="low",
                    tools=tools,
                    user_content="x",
                ),
                _NoFiles(),
            )
        )

    argv_off = argv_for(())
    argv_on = argv_for(("WebSearch", "WebFetch"))

    assert "--tools" in argv_off
    assert argv_off[argv_off.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv_off

    assert "WebSearch" in " ".join(argv_on)
    assert "--allowedTools" in argv_on


def test_the_declared_tool_using_set_matches_the_prompts_that_ask_for_search() -> None:
    """LAYER 3. The structural guard.

    Any system prompt that tells a model to go and find pages implies its task
    must carry tools. This catches the next `define_search_system()`: a prompt
    written for a search, wired to a call that cannot search.
    """
    from venona.llm import prompts as prompt_mod

    searching = {
        name
        for name in dir(prompt_mod)
        if name.endswith("_search_system") and callable(getattr(prompt_mod, name))
    }
    assert searching, "no *_search_system prompts found -- did they get renamed?"

    # Each one maps to a task by convention: acronym_search_system -> acronym_search.
    implied = {name.removesuffix("_system") for name in searching}
    missing = implied - TOOL_USING_TASKS
    assert not missing, (
        f"{sorted(missing)} have a search system prompt but are not declared as "
        f"tool-using. Either give the call `tools=`, or the prompt is lying to the model."
    )
