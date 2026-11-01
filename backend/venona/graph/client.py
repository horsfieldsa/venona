"""Neo4j driver lifecycle.

Neo4j is a *projection*, never the system of record (decision 2). Nothing here
may be the only copy of anything -- `venona graph rebuild` must always be able
to reconstruct the entire contents from the Postgres `assertions` table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase

from venona.settings import Settings

# Matches the CLI probe's TTL, and for the same reason -- see `health`.
HEALTH_TTL_SECONDS = 60.0


@dataclass
class GraphHealth:
    ok: bool
    version: str | None = None
    edition: str | None = None
    error: str | None = None


class GraphClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: AsyncDriver | None = None
        self._health_cache: tuple[float, GraphHealth] | None = None
        self._health_lock = asyncio.Lock()

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            # Explicit pool and timeout configuration. The defaults are tuned
            # for a database on the same host; every one of these matters more
            # once it is not.
            #
            # `liveness_check_timeout` is the one that actually bites: a pooled
            # connection idle across a NAT or load-balancer timeout is already
            # dead, and without this the FIRST query after the idle period is
            # the one that discovers it -- as a user-facing error, at random.
            #
            # Deliberately no `encrypted=` / `trust=`: passing either alongside
            # a `neo4j+s://` or `+ssc://` URI makes the driver raise
            # ConfigurationError, and that scheme is exactly how a managed
            # instance is addressed. Not passing them is what makes
            # NEO4J_URI=neo4j+s://... a pure configuration change.
            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
                max_connection_pool_size=20,
                connection_acquisition_timeout=30.0,
                connection_timeout=10.0,
                max_transaction_retry_time=15.0,
                liveness_check_timeout=30.0,
                keep_alive=True,
            )
        return self._driver

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def health(self) -> GraphHealth:
        """Cached and single-flight, for the same reason the CLI probe is: the
        frontend polls `/health` every 3 seconds while a light run is live, and
        `CALL dbms.components()` is a round trip -- a NETWORK round trip once
        the database is managed rather than a container on the same host."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._health_cache is not None and now - self._health_cache[0] < HEALTH_TTL_SECONDS:
            return self._health_cache[1]

        async with self._health_lock:
            now = loop.time()
            if self._health_cache is not None and now - self._health_cache[0] < HEALTH_TTL_SECONDS:
                return self._health_cache[1]
            probe = await self._health_uncached()
            self._health_cache = (loop.time(), probe)
            return probe

    async def _health_uncached(self) -> GraphHealth:
        try:
            async with self.driver.session() as session:
                result = await session.run(
                    "CALL dbms.components() YIELD name, versions, edition "
                    "RETURN versions[0] AS version, edition LIMIT 1"
                )
                record = await result.single()
                if record is None:
                    return GraphHealth(ok=True)
                return GraphHealth(ok=True, version=record["version"], edition=record["edition"])
        except Exception as exc:  # noqa: BLE001 - health must never raise
            # `dbms.components()` is restricted for some roles on managed
            # instances, so a failure here is NOT evidence that the database is
            # down. Ask it the simplest possible question before saying so --
            # otherwise the strip reports Neo4j down on a perfectly healthy Aura.
            try:
                async with self.driver.session() as session:
                    await (await session.run("RETURN 1")).single()
                return GraphHealth(ok=True)
            except Exception:  # noqa: BLE001 - now it really is down
                return GraphHealth(ok=False, error=f"{type(exc).__name__}: {exc}")
