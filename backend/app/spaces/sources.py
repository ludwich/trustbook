"""Sources and chunks in a space's ``space.db`` (SPEC §5)."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.engine import get_engine
from app.db.models import Chunk, Source, SourceFile
from app.spaces.service import SpaceRecord
from app.timeutil import utc_now_iso

FINAL_STATUSES = ("ready", "failed")


class SourceNotFoundError(LookupError):
    """No source with this id in the space."""


class ChunkNotFoundError(LookupError):
    """No chunk with this id in the space."""


def new_id() -> str:
    return uuid.uuid4().hex


def load_stats(source: Source) -> dict[str, Any]:
    try:
        stats = json.loads(source.stats or "{}")
    except ValueError:
        return {}
    return stats if isinstance(stats, dict) else {}


class SourceRepository:
    """CRUD for one space's sources and chunks."""

    def __init__(self, record: SpaceRecord) -> None:
        self.record = record
        self._engine = get_engine(record.db_path)

    def folder(self, source_id: str) -> Path:
        """``sources/<id>/`` of this space (created on demand by writers)."""
        return self.record.folder / "sources" / source_id

    # --- sources ---------------------------------------------------------------------------

    def list_all(self) -> list[Source]:
        with Session(self._engine) as session:
            query = select(Source).order_by(col(Source.created_at), col(Source.id))
            return list(session.exec(query).all())

    def find(self, source_id: str) -> Source | None:
        with Session(self._engine) as session:
            return session.get(Source, source_id)

    def get(self, source_id: str) -> Source:
        source = self.find(source_id)
        if source is None:
            raise SourceNotFoundError(source_id)
        return source

    def add(self, source: Source) -> Source:
        with Session(self._engine) as session:
            session.add(source)
            session.commit()
            session.refresh(source)
            return source

    def update(self, source_id: str, **values: Any) -> Source:
        """Set columns on a source (``stats`` may be a dict) and bump ``updated_at``."""
        with Session(self._engine) as session:
            source = session.get(Source, source_id)
            if source is None:
                raise SourceNotFoundError(source_id)
            for key, value in values.items():
                if key == "stats" and isinstance(value, dict):
                    value = json.dumps(value)
                setattr(source, key, value)
            source.updated_at = utc_now_iso()
            session.add(source)
            session.commit()
            session.refresh(source)
            return source

    def set_enabled(self, source_ids: list[str], enabled: bool) -> list[Source]:
        with Session(self._engine) as session:
            sources = list(session.exec(select(Source).where(col(Source.id).in_(source_ids))))
            found = {s.id for s in sources}
            missing = [s for s in source_ids if s not in found]
            if missing:
                raise SourceNotFoundError(missing[0])
            now = utc_now_iso()
            for source in sources:
                source.enabled = enabled
                source.updated_at = now
                session.add(source)
            session.commit()
        return [self.get(s) for s in source_ids]

    def delete(self, source_id: str) -> None:
        """Remove the source, its chunks and its folder."""
        with Session(self._engine) as session:
            session.exec(delete(Chunk).where(col(Chunk.source_id) == source_id))
            session.exec(delete(SourceFile).where(col(SourceFile.source_id) == source_id))
            session.exec(delete(Source).where(col(Source.id) == source_id))
            session.commit()
        shutil.rmtree(self.folder(source_id), ignore_errors=True)

    # --- chunks ----------------------------------------------------------------------------

    def replace_chunks(self, source_id: str, chunks: list[Chunk]) -> None:
        """Swap a source's chunks in one transaction."""
        with Session(self._engine) as session:
            session.exec(delete(Chunk).where(col(Chunk.source_id) == source_id))
            session.add_all(chunks)
            session.commit()

    def chunks(self, source_id: str) -> list[Chunk]:
        """A source's chunks in reading order."""
        with Session(self._engine) as session:
            query = select(Chunk).where(col(Chunk.source_id) == source_id)
            return list(session.exec(query.order_by(col(Chunk.ordinal))).all())

    def chunk_count(self, source_id: str) -> int:
        with Session(self._engine) as session:
            return len(session.exec(select(Chunk.id).where(Chunk.source_id == source_id)).all())

    def chunk(self, chunk_id: str) -> tuple[Chunk, Source]:
        with Session(self._engine) as session:
            chunk = session.get(Chunk, chunk_id)
            source = session.get(Source, chunk.source_id) if chunk is not None else None
            if chunk is None or source is None:
                raise ChunkNotFoundError(chunk_id)
            return chunk, source

    # --- files of linked folders (incremental refresh) -------------------------------------

    def files(self, source_id: str) -> list[SourceFile]:
        """The files of a folder source as last indexed, by path."""
        with Session(self._engine) as session:
            query = select(SourceFile).where(col(SourceFile.source_id) == source_id)
            return list(session.exec(query.order_by(col(SourceFile.path))).all())

    def replace_files(self, source_id: str, files: list[SourceFile]) -> None:
        """Swap a source's file records in one transaction (``[]`` forgets them)."""
        with Session(self._engine) as session:
            session.exec(delete(SourceFile).where(col(SourceFile.source_id) == source_id))
            session.add_all(files)
            session.commit()
