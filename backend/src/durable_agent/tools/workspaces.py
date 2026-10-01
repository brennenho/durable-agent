"""Workspace persistence and reads. Call filesystem/archive functions off-loop."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import FileEntry, FileListing
from durable_agent.db.models import WorkspaceVersion
from durable_agent.tools.archives import capture_workspace, unpack_workspace, workspace_path


class WorkspaceNotFound(LookupError):
    pass


def capture_initial_workspace(source: Path | None, max_bytes: int) -> bytes:
    if source is not None:
        return capture_workspace(source, max_bytes)
    with TemporaryDirectory() as directory:
        return capture_workspace(Path(directory), max_bytes)


async def save_workspace(session: AsyncSession, archive: bytes) -> UUID:
    """Insert an already-validated checkpoint; the caller owns the transaction."""
    version = WorkspaceVersion(archive=archive)
    session.add(version)
    await session.flush()
    return version.id


async def load_workspace(session: AsyncSession, version_id: UUID) -> bytes:
    version = await session.get(WorkspaceVersion, version_id)
    if version is None:
        raise WorkspaceNotFound(f"Workspace version {version_id} does not exist")
    return version.archive


def list_workspace(archive: bytes, max_bytes: int) -> FileListing:
    entries = [
        FileEntry(
            path=member.name,
            kind="directory" if member.isdir() else "file",
            size_bytes=None if member.isdir() else member.size,
        )
        for member, _ in unpack_workspace(archive, max_bytes)
    ]
    return FileListing(entries=sorted(entries, key=lambda entry: entry.path))


def read_workspace_file(archive: bytes, path: str, max_bytes: int) -> bytes:
    workspace_path(path)
    for member, payload in unpack_workspace(archive, max_bytes):
        if member.name == path and member.isfile():
            return payload
    raise FileNotFoundError(path)
