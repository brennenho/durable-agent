import asyncio
import os
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest
from durable_agent.sdk.tool_context import ToolContext
from durable_agent.sdk.workspace import Workspace


def test_workspace_reads_writes_and_lists_relative_utf8_files(tmp_path):
    async def exercise():
        workspace = Workspace(tmp_path)
        await workspace.write_text("notes/b.md", "héllo")
        await workspace.write_text("a.txt", "first")
        await workspace.write_text("a.txt", "replacement")
        assert await workspace.read_text("notes/b.md") == "héllo"
        assert await workspace.read_text("a.txt") == "replacement"
        assert await workspace.list_files() == ["a.txt", "notes/b.md"]
        assert await workspace.list_files("notes") == ["notes/b.md"]
        with pytest.raises(FileNotFoundError):
            await workspace.read_text("missing")
        with pytest.raises(NotADirectoryError):
            await workspace.list_files("a.txt")

    asyncio.run(exercise())


@pytest.mark.parametrize("path", ["", ".", "../escape", "/escape", "a/../escape", "a\\b", "a\0b"])
def test_workspace_rejects_paths_outside_its_relative_namespace(tmp_path, path):
    async def exercise():
        workspace = Workspace(tmp_path)
        with pytest.raises(ValueError, match="Invalid workspace path"):
            await workspace.write_text(path, "bad")
        with pytest.raises(ValueError, match="Invalid workspace path"):
            await workspace.read_text(path)

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "hard_link", "fifo"])
def test_workspace_rejects_links_and_special_files_without_changing_targets(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret"
    target.write_text("unchanged")
    root = tmp_path / "workspace"
    root.mkdir()
    path = root / "unsafe"
    relative = "unsafe"
    if kind == "file_link":
        path.symlink_to(target)
    elif kind == "directory_link":
        path.symlink_to(outside, target_is_directory=True)
        relative += "/secret"
    elif kind == "hard_link":
        path.hardlink_to(target)
    else:
        os.mkfifo(path)

    async def exercise():
        workspace = Workspace(root)
        with pytest.raises(ValueError):
            await workspace.read_text(relative)
        with pytest.raises(ValueError):
            await workspace.write_text(relative, "bad")
        with pytest.raises(ValueError):
            await workspace.list_files()

    asyncio.run(exercise())
    assert target.read_text() == "unchanged"


def test_workspace_root_cannot_be_a_link(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "link"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="Workspace root"):
        asyncio.run(Workspace(root).write_text("new", "bad"))
    assert list(target.iterdir()) == []


def test_workspace_io_runs_off_the_event_loop(tmp_path, monkeypatch):
    event_loop_thread = threading.get_ident()
    original = Path.write_text
    threads = []

    def write_text(path, *args, **kwargs):
        threads.append(threading.get_ident())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    asyncio.run(Workspace(tmp_path).write_text("a", "saved"))
    assert threads and all(thread != event_loop_thread for thread in threads)


def test_context_identity_is_frozen_and_remaining_time_never_negative(tmp_path, monkeypatch):
    context = ToolContext(uuid4(), uuid4(), uuid4(), Workspace(tmp_path), deadline=100)
    monkeypatch.setattr("durable_agent.sdk.tool_context.time.monotonic", lambda: 95.5)
    assert context.remaining_seconds == 4.5
    monkeypatch.setattr("durable_agent.sdk.tool_context.time.monotonic", lambda: 101)
    assert context.remaining_seconds == 0
    with pytest.raises(FrozenInstanceError):
        context.attempt_id = uuid4()
