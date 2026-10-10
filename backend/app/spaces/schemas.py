"""API shapes for spaces (see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.spaces.service import SpaceRecord

SpaceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
SpaceDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class EmbeddingInfo(BaseModel):
    model: str
    revision: str


class SpaceOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    created_at: str
    schema_version: int
    embedding: EmbeddingInfo
    folder: str
    source_count: int

    @classmethod
    def build(cls, record: SpaceRecord, source_count: int) -> SpaceOut:
        return cls(
            id=record.id,
            slug=record.slug,
            name=record.name,
            description=record.description,
            created_at=record.created_at,
            schema_version=record.schema_version,
            embedding=EmbeddingInfo(
                model=record.embedding_model, revision=record.embedding_revision
            ),
            folder=str(record.folder),
            source_count=source_count,
        )


class SpaceCreate(BaseModel):
    name: SpaceName
    description: SpaceDescription = ""


class SpaceUpdate(BaseModel):
    name: SpaceName | None = None
    description: SpaceDescription | None = None


class ActiveSpace(BaseModel):
    space_id: str | None = None
