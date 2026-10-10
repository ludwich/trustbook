"""Phase 1 routes: browse the read-only home folder for "Link folder" (docs/ARCHITECTURE.md).

Paths are relative to the browse root. ``..``, absolute paths and symlinks leading out of the
root are refused; symlinks are not listed at all (SPEC §1: no symlinks).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.api.deps import ServicesDep
from app.ingest.browse_root import BrowsePathError, BrowseRootMissingError, resolve_in_root
from app.spaces.source_schemas import BrowseEntry, BrowseOut

router = APIRouter()

ROOT_MISSING = (
    "Your home folder is not available to Trustbook. Check TRUSTBOOK_BROWSE_ROOT in the .env file."
)


@router.get("/browse")
def browse(services: ServicesDep, path: str = "") -> BrowseOut:
    try:
        folder, relative = resolve_in_root(services.paths.browse_root, path)
    except BrowseRootMissingError as exc:
        raise HTTPException(status_code=404, detail=ROOT_MISSING) from exc
    except BrowsePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="This folder does not exist.") from exc
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="This is a file, not a folder.")

    entries: list[BrowseEntry] = []
    try:
        with os.scandir(folder) as items:
            for item in items:
                if item.is_symlink():
                    continue
                try:
                    is_dir = item.is_dir(follow_symlinks=False)
                    size = None if is_dir else item.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                child = f"{relative}/{item.name}" if relative else item.name
                entries.append(BrowseEntry(name=item.name, path=child, is_dir=is_dir, size=size))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Trustbook can't read this folder.") from exc
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower(), e.name))
    parent = None if not relative else (relative.rsplit("/", 1)[0] if "/" in relative else "")
    return BrowseOut(path=relative, parent=parent, entries=entries)
