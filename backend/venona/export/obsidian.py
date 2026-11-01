"""The settings that make an unzipped folder open looking like Venona.

WHY THESE FOUR AND NOT A WHOLE CONFIGURATION. `core-plugins.json` is the
load-bearing one: without it Obsidian opens the folder with the graph view
switched OFF, and the graph is the reason the relationships were worth carrying
across at all. `app.json` turns wikilinks on, which is what the whole folder is
written in. `graph.json` colours the view by type. `appearance.json` is one
line and stops the first open being a theme prompt.

`workspace.json` is deliberately NOT written. It pins pane layout and window
geometry to whichever machine built the archive, so shipping one hands the
reader somebody else's window.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from venona.export.palette import NEUTRAL_HEX, SLOT_HEX

if TYPE_CHECKING:  # pragma: no cover -- import cycle at runtime, types only
    from venona.export.vault import Member

CONFIG_DIR = ".obsidian"


def _rgb(value: str) -> int:
    """`#4f7060` as the packed integer Obsidian stores."""
    return int(value.lstrip("#"), 16)


def _colour_group(query: str, hex_value: str) -> dict[str, Any]:
    return {"query": query, "color": {"a": 1, "rgb": _rgb(hex_value)}}


def _json(payload: Any) -> str:
    """Sorted and fixed-width, so two builds of one corpus agree byte for byte."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def config_members(ontology: Any, *, humanize: Callable[[str], str]) -> list[Member]:
    from venona.export.vault import BRIEFS_DIR, NAMES_DIR, SOURCES_DIR, TERMS_DIR, Member

    #: BY PATH, not by a property value. A path query needs no plugin and no
    #: index to have warmed up, so the colours are right on the first frame
    #: rather than after Obsidian has read every note.
    groups = [
        _colour_group(
            f'path:"{NAMES_DIR}/{humanize(name)}"',
            SLOT_HEX[(spec.color_slot - 1) % len(SLOT_HEX)],
        )
        for name, spec in ontology.entity_types.items()
    ]
    groups.extend(
        _colour_group(f'path:"{folder}"', NEUTRAL_HEX)
        for folder in (TERMS_DIR, SOURCES_DIR, BRIEFS_DIR)
    )

    return [
        Member(
            path=f"{CONFIG_DIR}/app.json",
            text=_json(
                {
                    # The folder is written in wikilinks throughout, and this is
                    # the switch that makes Obsidian write them too, so a note
                    # the reader adds matches the ones they were given.
                    "useMarkdownLinks": False,
                    "newLinkFormat": "shortest",
                    "alwaysUpdateLinks": True,
                    "showUnsupportedFiles": False,
                }
            ),
        ),
        Member(path=f"{CONFIG_DIR}/appearance.json", text=_json({"accentColor": ""})),
        Member(
            path=f"{CONFIG_DIR}/core-plugins.json",
            text=_json(
                [
                    "graph",
                    "backlink",
                    "outgoing-link",
                    "page-preview",
                    "properties",
                    "tag-pane",
                    "search",
                    "file-explorer",
                ]
            ),
        ),
        Member(
            path=f"{CONFIG_DIR}/graph.json",
            text=_json({"colorGroups": groups, "showTags": False, "showAttachments": False}),
        ),
    ]
