"""Space folders: create, list, get, update and delete.

Each space is one folder under ``<data>/spaces/<slug>/`` (SPEC §4). The folder slug is chosen at
creation and never changes; the space id is a uuid4 hex string stored in ``space.json``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.capabilities.bundled import read_models_lock
from app.config import Paths
from app.db.engine import dispose_engine, get_engine
from app.db.migrate import migrate
from app.db.models import Source
from app.fsutil import atomic_write_json
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.spaces")

SPACE_SUBFOLDERS = ("vectors", "sources", "ideas")
MAX_SLUG_LENGTH = 48
# Folder names Windows refuses (the data folder is often a Windows bind mount).
_WINDOWS_RESERVED = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class SpaceNotFoundError(LookupError):
    """No space with this id."""


class UnsafeSpacePathError(RuntimeError):
    """A space folder is not strictly inside ``<data>/spaces`` or is a symlink."""


@dataclass(frozen=True)
class SpaceRecord:
    """A space as read from its ``space.json`` plus its folder."""

    id: str
    slug: str
    name: str
    description: str
    created_at: str
    schema_version: int
    embedding_model: str
    embedding_revision: str
    folder: Path

    @property
    def db_path(self) -> Path:
        return self.folder / "space.db"


def slugify(name: str) -> str:
    """Turn a display name into a safe folder name (ASCII, lowercase, dashes)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].rstrip("-")
    if not slug:
        slug = "space"
    if slug in _WINDOWS_RESERVED:
        slug = f"{slug}-space"
    return slug


class SpaceService:
    """CRUD for space folders. Thread-safe."""

    def __init__(
        self,
        paths: Paths,
        *,
        cancel_jobs: Callable[[str], None] | None = None,
    ) -> None:
        self._paths = paths
        self._cancel_jobs = cancel_jobs
        self._lock = threading.RLock()

    def set_cancel_jobs(self, cancel_jobs: Callable[[str], None]) -> None:
        """Register the callback that cancels a space's jobs before deletion."""
        self._cancel_jobs = cancel_jobs

    @property
    def root(self) -> Path:
        return self._paths.spaces_dir

    # --- queries ---------------------------------------------------------------------------

    def list_all(self) -> list[SpaceRecord]:
        """Return all readable spaces, newest first."""
        records: list[SpaceRecord] = []
        if not self.root.is_dir():
            return records
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            record = self._read(folder)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: (r.created_at, r.name), reverse=True)
        return records

    def get(self, space_id: str) -> SpaceRecord:
        """Return the space with ``space_id`` or raise :class:`SpaceNotFoundError`."""
        for record in self.list_all():
            if record.id == space_id:
                return record
        raise SpaceNotFoundError(space_id)

    def find(self, space_id: str) -> SpaceRecord | None:
        """Return the space with ``space_id`` or ``None``."""
        try:
            return self.get(space_id)
        except SpaceNotFoundError:
            return None

    def names(self) -> dict[str, str]:
        """Map space id → name for every space."""
        return {record.id: record.name for record in self.list_all()}

    def source_count(self, record: SpaceRecord) -> int:
        """Number of sources in the space's database (0 if it cannot be read)."""
        if not record.db_path.exists():
            return 0
        try:
            with Session(get_engine(record.db_path)) as session:
                count = session.exec(select(func.count()).select_from(Source)).one()
            return int(count)
        except Exception:
            logger.warning("Could not count sources in space %s", record.id, exc_info=True)
            return 0

    # --- commands --------------------------------------------------------------------------

    def create(self, name: str, description: str = "") -> SpaceRecord:
        """Create a new space folder with its database and manifest."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            folder = self._reserve_folder(slugify(name))
            try:
                for sub in SPACE_SUBFOLDERS:
                    (folder / sub).mkdir()
                schema_version = migrate(folder / "space.db", "space")
                embedding = read_models_lock(self._paths.models_dir)["embeddings"]
                manifest: dict[str, Any] = {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "description": description,
                    "embedding": {"model": embedding.model_id, "revision": embedding.revision},
                    "created_at": utc_now_iso(),
                    "schema_version": schema_version,
                }
                atomic_write_json(folder / "space.json", manifest)
            except BaseException:
                dispose_engine(folder / "space.db")
                shutil.rmtree(folder, ignore_errors=True)
                raise
            record = self._read(folder)
            if record is None:  # pragma: no cover - we just wrote it
                raise RuntimeError("The new space could not be read back")
            logger.info("Created space %s in %s", record.id, folder.name)
            return record

    def update(
        self, space_id: str, *, name: str | None = None, description: str | None = None
    ) -> SpaceRecord:
        """Change name and/or description. The folder slug never changes."""
        with self._lock:
            record = self.get(space_id)
            manifest_path = record.folder / "space.json"
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if name is not None:
                manifest["name"] = name
            if description is not None:
                manifest["description"] = description
            atomic_write_json(manifest_path, manifest)
            updated = self._read(record.folder)
            if updated is None:  # pragma: no cover - we just wrote it
                raise RuntimeError("The space could not be read back")
            return updated

    def delete(self, space_id: str) -> None:
        """Cancel the space's jobs, then remove its folder (refusing anything unsafe)."""
        with self._lock:
            record = self.get(space_id)
            folder = self._checked_folder(record.folder)
            if self._cancel_jobs is not None:
                self._cancel_jobs(space_id)
            dispose_engine(record.db_path)
            shutil.rmtree(folder)
            logger.info("Deleted space %s (%s)", space_id, folder.name)

    def migrate_all(self) -> None:
        """Bring every space database up to the newest schema and record it in space.json."""
        with self._lock:
            for record in self.list_all():
                try:
                    version = migrate(record.db_path, "space")
                except Exception:
                    logger.exception("Could not migrate space %s", record.id)
                    continue
                if version != record.schema_version:
                    manifest_path = record.folder / "space.json"
                    manifest = json.loads(manifest_path.read_text("utf-8"))
                    manifest["schema_version"] = version
                    atomic_write_json(manifest_path, manifest)

    # --- helpers ---------------------------------------------------------------------------

    def _checked_folder(self, folder: Path) -> Path:
        """Return ``folder`` if it is a real directory directly inside the spaces root."""
        if folder.is_symlink():
            raise UnsafeSpacePathError(f"Refusing to delete a symlink: {folder}")
        root = self.root.resolve()
        resolved = folder.resolve()
        if resolved.parent != root or resolved == root or not resolved.is_dir():
            raise UnsafeSpacePathError(f"Refusing to delete outside the spaces folder: {folder}")
        return resolved

    def _reserve_folder(self, base: str) -> Path:
        """Create and return a new, unique folder for ``base`` (``base``, ``base-2``, …)."""
        for attempt in range(1, 10_000):
            slug = base if attempt == 1 else f"{base}-{attempt}"
            folder = self.root / slug
            try:
                folder.mkdir()
            except FileExistsError:
                continue
            return folder
        raise RuntimeError("Could not find a free folder name for the space")

    def _read(self, folder: Path) -> SpaceRecord | None:
        manifest_path = folder / "space.json"
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text("utf-8"))
            embedding = data.get("embedding") or {}
            return SpaceRecord(
                id=str(data["id"]),
                slug=folder.name,
                name=str(data["name"]),
                description=str(data.get("description") or ""),
                created_at=str(data["created_at"]),
                schema_version=int(data.get("schema_version") or 0),
                embedding_model=str(embedding.get("model") or ""),
                embedding_revision=str(embedding.get("revision") or ""),
                folder=folder,
            )
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Skipping %s: space.json is unreadable", folder.name)
            return None
