"""Neo4j constraints and indexes. Applied idempotently on startup.

Community Edition has unique constraints only -- node keys are Enterprise. The
design deliberately needs nothing beyond uniqueness, so this stays portable.
"""

from __future__ import annotations

from neo4j import AsyncDriver

CONSTRAINTS = [
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT article_id IF NOT EXISTS FOR (a:Article) REQUIRE a.id IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX entity_key IF NOT EXISTS FOR (e:Entity) ON (e.canonical_key)",
    "CREATE INDEX entity_status IF NOT EXISTS FOR (e:Entity) ON (e.status)",
    "CREATE INDEX entity_lastseen IF NOT EXISTS FOR (e:Entity) ON (e.last_seen)",
    "CREATE INDEX article_pub IF NOT EXISTS FOR (a:Article) ON (a.published_at)",
    "CREATE INDEX article_score IF NOT EXISTS FOR (a:Article) ON (a.relevance_overall)",
    "CREATE INDEX article_cluster IF NOT EXISTS FOR (a:Article) ON (a.cluster_id)",
]

FULLTEXT = [
    "CREATE FULLTEXT INDEX entity_search IF NOT EXISTS "
    "FOR (e:Entity) ON EACH [e.name, e.aliases_text]",
]


async def apply_schema(driver: AsyncDriver) -> None:
    async with driver.session() as session:
        for statement in CONSTRAINTS + INDEXES + FULLTEXT:
            await session.run(statement)


async def drop_all(driver: AsyncDriver) -> None:
    """Used by `venona graph rebuild`. Safe because Neo4j holds no original
    data -- everything here is reprojected from Postgres assertions."""
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
