"""The public directory. Consumed by the marketing examples section and the
extension's "add from directory" (which maps `id=slug`, `label=display_name`,
`baseUrl=public_url + '/api'` -- extension/src/shared/client.ts::Instance)."""

from __future__ import annotations

from pydantic import BaseModel


class PublicInstance(BaseModel):
    slug: str
    display_name: str
    tagline: str
    public_url: str
    status: str  # live | offline -- other states are not listed at all


class PublicInstances(BaseModel):
    instances: list[PublicInstance]


class Health(BaseModel):
    ok: bool
