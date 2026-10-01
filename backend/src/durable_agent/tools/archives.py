"""Complete checkpoints of quiescent workspaces. Call blocking functions off-loop."""

import gzip
import io
import os
import shutil
import stat
import tarfile
import zlib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

METADATA_BUDGET = 4 * 1024 * 1024


class InvalidWorkspace(ValueError):
    """The workspace cannot be represented by a safe, bounded checkpoint."""


def workspace_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != name
        or name == "."
    ):
        raise InvalidWorkspace(f"Invalid workspace path: {name!r}")
    return path


def _walk(root: Path) -> Iterator[Path]:
    with os.scandir(root) as entries:
        for entry in entries:
            path = Path(entry.path)
            yield path
            if entry.is_dir(follow_symlinks=False):
                yield from _walk(path)


def capture_workspace(root: Path, max_bytes: int) -> bytes:
    """Capture files and directories after all writers have stopped."""
    if max_bytes < 0 or not stat.S_ISDIR(root.lstat().st_mode):
        raise InvalidWorkspace("Expected a directory and a nonnegative size limit")
    raw = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in _walk(root):
            name = path.relative_to(root).as_posix()
            workspace_path(name)
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise InvalidWorkspace(f"Unsupported file type: {name}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise InvalidWorkspace(f"Hard links are unsupported: {name}")
            if stat.S_IMODE(info.st_mode) & ~0o777:
                raise InvalidWorkspace(f"Special permission bits are unsupported: {name}")
            member = archive.gettarinfo(str(path), arcname=name)
            total += member.size
            if total > max_bytes:
                raise InvalidWorkspace("Workspace exceeds its file-size limit")
            if member.isfile():
                with path.open("rb") as source:
                    archive.addfile(member, source)
            else:
                archive.addfile(member)
            if raw.tell() - total > METADATA_BUDGET - tarfile.RECORDSIZE:
                raise InvalidWorkspace("Workspace exceeds its archive-metadata limit")
    return gzip.compress(raw.getvalue(), mtime=0)


def unpack_workspace(data: bytes, max_bytes: int) -> list[tuple[tarfile.TarInfo, bytes]]:
    """Validate the entire checkpoint before exposing any files to callers."""
    if max_bytes < 0 or len(data) > max_bytes + METADATA_BUDGET:
        raise InvalidWorkspace("Archive exceeds its size limit")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            raw = compressed.read(max_bytes + METADATA_BUDGET + 1)
        if len(raw) > max_bytes + METADATA_BUDGET:
            raise InvalidWorkspace("Archive exceeds its uncompressed size limit")
        entries = []
        seen: dict[PurePosixPath, bool] = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                path = workspace_path(member.name)
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
                    raise InvalidWorkspace(f"Unsupported archive entry: {member.name}")
                if member.sparse is not None or member.mode & ~0o777:
                    raise InvalidWorkspace("Sparse files and special permissions are unsupported")
                if path in seen or (
                    path.parent != PurePosixPath(".") and not seen.get(path.parent)
                ):
                    raise InvalidWorkspace("Duplicate entry or missing directory parent")
                if member.size < 0 or (member.isdir() and member.size != 0):
                    raise InvalidWorkspace("Invalid archive entry size")
                total += member.size
                if total > max_bytes:
                    raise InvalidWorkspace("Workspace exceeds its file-size limit")
                payload = b""
                if member.isfile():
                    with archive.extractfile(member) as source:
                        payload = source.read()
                    if len(payload) != member.size:
                        raise InvalidWorkspace("Truncated file")
                entries.append((member, payload))
                seen[path] = member.isdir()
        if len(raw) - total > METADATA_BUDGET:
            raise InvalidWorkspace("Workspace exceeds its archive-metadata limit")
        return entries
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise InvalidWorkspace("Invalid workspace archive") from exc


def restore_workspace(data: bytes, destination: Path, max_bytes: int) -> None:
    """Restore into a new directory inside a trusted, privately owned parent."""
    entries = unpack_workspace(data, max_bytes)
    destination.mkdir(mode=0o700)  # Never merge into an existing tree or follow a root symlink.
    try:
        directories = []
        for member, payload in entries:
            path = destination / member.name
            if member.isdir():
                path.mkdir(mode=0o700)
                directories.append((path, member.mode))
            else:
                with path.open("xb") as output:
                    output.write(payload)
                path.chmod(member.mode)
        # Apply restrictive directory modes only after their children have been written.
        for path, mode in reversed(directories):
            path.chmod(mode)
    except BaseException:
        shutil.rmtree(destination)
        raise
