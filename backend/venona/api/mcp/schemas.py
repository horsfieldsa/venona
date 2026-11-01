"""Tool inputs. One pydantic model per tool; `model_json_schema()` IS the wire.

This is the project's "a pydantic model at every API boundary" convention doing
double duty. MCP wants a JSON Schema per tool in `tools/list`, and pydantic
already emits one -- which is most of what the `mcp` SDK would have been taken
for, and the reason not taking it costs so little.

Bounds are declared HERE and not in the handlers, matching `api/schemas/entity.py`
(`CardBatchRequest.ids: Field(max_length=100)`) and `api/schemas/annotate.py`.
The annotate limits are IMPORTED from that module rather than restated, for the
same reason `api/schemas/annotate.py` imports `TextRun` instead of declaring a
second one: two copies of a bound drift, and the drift is silent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from venona.api.schemas.annotate import MAX_TOTAL_CHARS
from venona.graph import queries


class SearchArgs(BaseModel):
    q: str = Field(min_length=1, max_length=200, description="A name, acronym or partial name.")
    limit: int = Field(default=5, ge=1, le=25)
    include_provisional: bool = Field(
        default=False,
        description=(
            "Include entities seen once and not yet corroborated from a second source. "
            "Off by default: a provisional entity may be a extraction artefact."
        ),
    )


class EntityArgs(BaseModel):
    entity_id: str = Field(
        min_length=1,
        max_length=200,
        description="A venona id, e.g. 'prog:next-generation-command-and-control' or 'gloss:ota'.",
    )


class AnnotateArgs(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=MAX_TOTAL_CHARS,
        description="Prose to check. Paste a draft; blank lines separate paragraphs.",
    )


class RelationsArgs(BaseModel):
    entity_id: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=15, ge=1, le=50)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EvidenceArgs(BaseModel):
    edge_id: str | None = Field(
        default=None,
        max_length=400,
        description="As printed by venona_relations: 'subject_id|PREDICATE|object_id'.",
    )
    subject_id: str | None = Field(default=None, max_length=200)
    predicate: str | None = Field(default=None, max_length=100)
    object_id: str | None = Field(default=None, max_length=200)
    max_quotes: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def _one_form_or_the_other(self) -> EvidenceArgs:
        """Either an edge_id or all three parts.

        The triple form exists so an agent never hand-builds a pipe-delimited
        string -- the server assembles it and `graph_service.parse_edge_id`
        validates the predicate against the LIVE ontology, which is why a
        guessed predicate returns "not a predicate" rather than a null result.
        """
        if self.edge_id:
            return self
        if self.subject_id and self.predicate and self.object_id:
            return self
        raise ValueError(
            "give either `edge_id`, or all three of `subject_id`, `predicate` and "
            "`object_id`. venona_relations prints an edge_id on every row."
        )


class PathArgs(BaseModel):
    from_entity_id: str = Field(min_length=1, max_length=200)
    to_entity_id: str = Field(min_length=1, max_length=200)
    max_depth: int = Field(default=4, ge=1, le=queries.MAX_PATH_DEPTH)


class BriefsArgs(BaseModel):
    q: str = Field(default="", max_length=200)
    entity_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Stronger than `q`: matches ids recorded in the brief's spans, so it finds "
            "mentions under aliases you would not think to type."
        ),
    )
    brief_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=25)

    @model_validator(mode="after")
    def _something_to_search_on(self) -> BriefsArgs:
        if not (self.q or self.entity_id or self.brief_id):
            raise ValueError("give `q`, `entity_id` or `brief_id` — this does not dump the archive")
        return self


class OntologyArgs(BaseModel):
    pass
