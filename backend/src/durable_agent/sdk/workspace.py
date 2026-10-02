"""Async file access within a tool attempt's private workspace."""

import asyncio
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Workspace:
    root: Path

    async def read_text(self, path: str) -> str:
        """Read a UTF-8 file relative to the workspace."""
        return await asyncio.to_thread(self._read_text, path)

    async def write_text(self, path: str, content: str) -> None:
        """Replace a UTF-8 file, creating its parent directories as needed."""
        await asyncio.to_thread(self._write_text, path, content)

    async def list_files(self, path: str = ".") -> list[str]:
        """List files recursively, sorted by their workspace-relative paths."""
        return await asyncio.to_thread(self._list_files, path)

    def _path(self, name: str, *, directory: bool = False) -> Path:
        relative = PurePosixPath(name)
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or (name == "." and not directory)
        ):
            raise ValueError(f"Invalid workspace path: {name!r}")
        if not stat.S_ISDIR(self.root.lstat().st_mode):
            raise ValueError("Workspace root must be a directory, not a link")
        current = self.root
        for part in relative.parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f"Unsupported workspace file type: {name!r}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError(f"Hard links are unsupported: {name!r}")
        return current

    def _read_text(self, path: str) -> str:
        return self._path(path).read_text(encoding="utf-8")

    def _write_text(self, path: str, content: str) -> None:
        destination = self._path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _list_files(self, path: str) -> list[str]:
        directory = self._path(path, directory=True)
        if not directory.is_dir():
            raise NotADirectoryError(path)
        files = []
        for entry in directory.rglob("*"):
            name = entry.relative_to(self.root).as_posix()
            if self._path(name).is_file():
                files.append(name)
        return sorted(files)
