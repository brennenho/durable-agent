import asyncio
from uuid import uuid4

import pytest
from durable_agent.db.session import Database
from durable_agent.tools.archives import InvalidWorkspace
from durable_agent.tools.workspaces import (
    WorkspaceNotFound,
    capture_initial_workspace,
    list_workspace,
    load_workspace,
    read_workspace_file,
    save_workspace,
)


def test_import_is_a_complete_copy_with_sorted_recursive_listing(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir/binary").write_bytes(b"\0\xff")
    (tmp_path / "a.md").write_text("saved")
    (tmp_path / "empty").mkdir()
    archive = capture_initial_workspace(tmp_path, 1024)
    (tmp_path / "a.md").write_text("changed")
    assert [
        (entry.path, entry.kind, entry.size_bytes)
        for entry in list_workspace(archive, 1024).entries
    ] == [
        ("a.md", "file", 5),
        ("dir", "directory", None),
        ("dir/binary", "file", 2),
        ("empty", "directory", None),
    ]
    assert read_workspace_file(archive, "a.md", 1024) == b"saved"
    assert read_workspace_file(archive, "dir/binary", 1024) == b"\0\xff"
    for path in ("missing", "dir"):
        with pytest.raises(FileNotFoundError):
            read_workspace_file(archive, path, 1024)
    with pytest.raises(InvalidWorkspace):
        read_workspace_file(archive, "../a.md", 1024)


def test_unset_source_is_empty_but_invalid_configured_source_fails(tmp_path):
    assert list_workspace(capture_initial_workspace(None, 0), 0).entries == []
    with pytest.raises(FileNotFoundError):
        capture_initial_workspace(tmp_path / "missing", 1024)
    file = tmp_path / "file"
    file.write_text("not a directory")
    with pytest.raises(InvalidWorkspace):
        capture_initial_workspace(file, 1024)


def test_checkpoint_survives_database_replacement_and_rollback_publishes_nothing(postgres_engine):
    archive = capture_initial_workspace(None, 0)
    url = postgres_engine.url.render_as_string(hide_password=False)

    async def exercise():
        db = Database(url)
        try:
            async with db.session() as session:
                version_id = await save_workspace(session, archive)
                await session.commit()
            with pytest.raises(RuntimeError, match="interrupted"):
                async with db.session() as session:
                    rolled_back = await save_workspace(session, archive)
                    raise RuntimeError("interrupted")
        finally:
            await db.close()
        replacement = Database(url)
        try:
            async with replacement.session() as session:
                assert await load_workspace(session, version_id) == archive
                for missing in (rolled_back, uuid4()):
                    with pytest.raises(WorkspaceNotFound):
                        await load_workspace(session, missing)
        finally:
            await replacement.close()

    asyncio.run(exercise())
