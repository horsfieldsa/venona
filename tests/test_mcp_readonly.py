"""The MCP surface writes nothing. Three independent layers.

Independent because each catches what the others cannot: a grep sees code that
never runs, an import allowlist sees intent, and a row-count snapshot sees
effects. All three are cheap; the thing they are guarding against -- an agent
tool that quietly spends model budget or deletes a row -- is not.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from venona.api.mcp import registry, tools
from venona.api.mcp.tools import ToolContext
from venona.db import core as db

MCP_PACKAGE = Path(inspect.getfile(tools)).parent

# Every name `tools.py` may bind from `venona.*`. SYMBOL level, not module level,
# and that is the whole point: `brief_service` is imported for `search_briefs`,
# and the SAME MODULE holds `delete_brief`, which runs two DELETEs and a commit.
# A module-level allowlist would admit it and look perfectly fine.
ALLOWED_IMPORTS = {
    ("venona.api.mcp", "render"),
    ("venona.api.mcp.schemas", "AnnotateArgs"),
    ("venona.api.mcp.schemas", "BriefsArgs"),
    ("venona.api.mcp.schemas", "EntityArgs"),
    ("venona.api.mcp.schemas", "EvidenceArgs"),
    ("venona.api.mcp.schemas", "OntologyArgs"),
    ("venona.api.mcp.schemas", "PathArgs"),
    ("venona.api.mcp.schemas", "RelationsArgs"),
    ("venona.api.mcp.schemas", "SearchArgs"),
    ("venona.api.schemas.annotate", "MAX_BLOCKS"),
    ("venona.api.schemas.annotate", "MAX_BLOCK_CHARS"),
    # The TYPE only. `venona.db.core` also exposes `insert`, `upsert` and
    # `transaction`; admitting the module would put all three one attribute
    # access away from a tool, which is the whole reason this list is
    # symbol-level rather than module-level.
    ("venona.db.core", "Conn"),
    ("venona.graph", "queries"),
    ("venona.ontology", "get_ontology"),
    ("venona.services.annotate_service", "annotate_texts"),
    ("venona.services.brief_service", "get_brief"),
    ("venona.services.brief_service", "latest_brief_id"),
    ("venona.services.brief_service", "search_briefs"),
    ("venona.services.entity_service", "cards_batch"),
    ("venona.services.entity_service", "entity_card"),
    ("venona.services.entity_service", "glossary_card"),
    ("venona.services.entity_service", "neighborhood"),
    ("venona.services.entity_service", "search"),
    ("venona.services.graph_service", "describe_path"),
    ("venona.services.graph_service", "edge_provenance"),
}

# Tokens that must not appear anywhere under `api/mcp/`. Deliberately dumb and
# deliberately noisy, in `scripts/lint_determinism.py`'s style: a false positive
# costs one rename, a false negative costs the corpus.
BANNED = (
    "run_launcher",
    "config_service",
    "delete_brief",
    "purge",
    "executor",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "DROP ",
    "CREATE ",
    ".commit(",
    "write_text",
    "GraphWriter",
    "projector",
)


def _code_only(path: Path) -> str:
    """The file with comments and string literals removed.

    Grepping raw source cannot work here, because this package's PROSE names the
    things the code must not do -- the module docstrings explain why
    `run_launcher` is absent and what `delete_brief` would break. A naive grep
    fires on its own explanation, which would teach the next person to delete the
    explanation.
    """
    import io
    import tokenize

    out: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(f"{token.start[0]}:{token.string}")
    return "\n".join(out)


def test_no_module_under_api_mcp_mentions_a_write() -> None:
    """Layer 1. A grep over CODE, and it says so.

    Catches the shape before it runs -- including a helper added for "just this
    one case" that no test happens to call.
    """
    offenders: list[str] = []
    for path in sorted(MCP_PACKAGE.glob("*.py")):
        code = _code_only(path)
        for token in BANNED:
            # SQL keywords only ever reach the wire inside a string literal,
            # which `_code_only` has already dropped; what is left to catch is an
            # identifier, so compare on the bare name.
            needle = token.strip().rstrip("(")
            for line in code.splitlines():
                _number, _, text = line.partition(":")
                if needle and needle == text:
                    offenders.append(f"{path.name}: {needle!r}")
    assert not offenders, (
        "the MCP surface is read-only (contract C12). These names suggest otherwise:\n"
        + "\n".join(sorted(set(offenders)))
    )


def test_the_tool_module_imports_only_read_paths() -> None:
    """Layer 2. Intent, at symbol granularity.

    A future `from venona.services import run_launcher` fails here as a diff in
    review rather than as a line on a subscription bill.
    """
    tree = ast.parse((MCP_PACKAGE / "tools.py").read_text())
    bound: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("venona"):
            for alias in node.names:
                bound.add((node.module or "", alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import venona.services.brief_service` would let attribute
                # access reach anything in it, defeating the pairs above.
                assert not alias.name.startswith("venona"), (
                    f"`import {alias.name}` bypasses the symbol allowlist; "
                    f"use `from ... import <name>`"
                )
    unexpected = bound - ALLOWED_IMPORTS
    assert not unexpected, (
        f"tools.py binds names not on the read-only allowlist: {sorted(unexpected)}.\n"
        f"If one of these is genuinely a read, add it to ALLOWED_IMPORTS with a reason."
    )


def _table_counts(conn: db.Conn) -> dict[str, int]:
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    return {t: conn.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"] for t in tables}


@pytest.mark.anyio
async def test_no_tool_writes_anything(api_db: db.Conn) -> None:
    """Layer 3. Effects, swept off the REGISTRY rather than a hand-written list.

    That is the property `test_annotate_writes_nothing` lacks: a tool added
    tomorrow is covered the day it is added, not when someone remembers to add a
    case. `venona_path` is exercised with no graph, which is itself the
    interesting path -- it must return a readable result, not a 500.
    """
    ctx = ToolContext(conn=api_db, graph=None)
    entity = api_db.execute("SELECT id FROM entities LIMIT 1").fetchone()
    entity_id = entity["id"] if entity else "org:nobody"

    arguments = {
        "venona_search": {"q": "NGC2"},
        "venona_entity": {"entity_id": entity_id},
        "venona_annotate": {"text": "The Army selected MOSA for CJADC2."},
        "venona_relations": {"entity_id": entity_id},
        "venona_evidence": {"edge_id": f"{entity_id}|MANAGES|{entity_id}"},
        "venona_path": {"from_entity_id": entity_id, "to_entity_id": "org:other"},
        "venona_briefs": {"q": "army"},
        "venona_ontology": {},
    }
    assert set(arguments) == set(registry.TOOLS), (
        "every registered tool needs arguments here, or the sweep silently skips it"
    )

    before = _table_counts(api_db)
    for name, tool in registry.TOOLS.items():
        args = tool.args_model.model_validate(arguments[name])
        try:
            if tool.is_async:
                await tool.handler(ctx, args)
            else:
                tool.handler(ctx, args)
        except tools.ToolError:
            # A ToolError IS a result -- "venona does not hold that". It is not a
            # failure of this test, and the tool still must not have written.
            pass
    after = _table_counts(api_db)

    changed = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    assert not changed, f"an MCP tool wrote to the corpus: {changed}"


def test_every_registered_tool_has_a_budget_and_a_description() -> None:
    for name, tool in registry.TOOLS.items():
        assert tool.max_chars > 0, f"{name} has no BUDGET_CHARS entry"
        assert len(tool.description) > 80, f"{name}'s description is too thin to route on"
        assert tool.args_model.model_json_schema()["type"] == "object"


def test_the_renderers_never_dump_a_model() -> None:
    """`render.py` reads NAMED fields, so a field added to `Card` for the reading
    surface cannot reach a context window by default. Someone has to come here
    and name it, which is a reviewable diff."""
    # An AST walk rather than a grep, because `dict[str, Any]` as an ANNOTATION
    # is fine and `dict(card)` as a CALL is the bug. A textual check cannot tell
    # them apart, and the version that could would ban a legitimate type hint.
    tree = ast.parse((MCP_PACKAGE / "render.py").read_text())
    banned_calls = {"model_dump", "model_dump_json", "dict", "vars", "asdict"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in banned_calls:
            offenders.append(f"line {node.lineno}: {name}()")
    assert not offenders, (
        f"render.py calls {offenders} — that is how `mini_graph` geometry, "
        f"`relevance.axes` and `profile_hooks` leak back into an agent's context. "
        f"Read named fields instead."
    )
    # And no dict-unpacking, which is the other way a whole model gets through.
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Dict) and None in n.keys], (
        "render.py unpacks a mapping with `**`"
    )


def test_every_tool_has_a_human_summary_distinct_from_its_description() -> None:
    """Two readers, two fields.

    `description` is written for a model choosing whether to call the tool;
    `summary` is the line a person reads on Settings -> Agents. Deriving the
    second from the first was lossy in a way that showed: `venona_evidence`
    opens "THE CITATION TOOL." -- emphasis a model routes on, and a shouty
    fragment in a table.
    """
    for name, tool in registry.TOOLS.items():
        assert tool.summary, f"{name} has no summary"
        assert tool.summary.endswith("."), f"{name}'s summary is not a sentence"
        assert len(tool.summary) < 100, f"{name}'s summary is too long for a table cell"
        assert tool.summary != tool.description, f"{name} reuses its description"
        assert tool.summary.upper() != tool.summary, f"{name}'s summary is shouting"
