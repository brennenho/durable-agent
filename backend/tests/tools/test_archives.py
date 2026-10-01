import gzip
import io
import os
import stat
import tarfile

import pytest
from durable_agent.tools.archives import (
    METADATA_BUDGET,
    InvalidWorkspace,
    capture_workspace,
    restore_workspace,
)


def archive_bytes(*entries):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, kind, payload in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return gzip.compress(raw.getvalue())


def test_round_trip_preserves_nested_files_empty_directories_and_modes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "src").mkdir()
    (source / "src/run.py").write_bytes(b"print('hello')\n")
    (source / "src/run.py").chmod(0o751)
    (source / "src/data").write_bytes(bytes(range(256)))
    (source / "src").chmod(0o750)
    (source / ".hidden").write_text("included")
    saved = capture_workspace(source, 1024)
    (source / "src/run.py").unlink()
    (source / "new").write_text("not saved")
    destination = tmp_path / "restored"
    restore_workspace(saved, destination, 1024)
    assert (destination / "empty").is_dir()
    assert (destination / "src/run.py").read_bytes() == b"print('hello')\n"
    assert (destination / "src/data").read_bytes() == bytes(range(256))
    assert (destination / ".hidden").read_text() == "included"
    assert stat.S_IMODE((destination / "src/run.py").stat().st_mode) == 0o751
    assert stat.S_IMODE((destination / "src").stat().st_mode) == 0o750
    assert not (destination / "new").exists()


def test_empty_workspace_and_exact_size_limit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    restore_workspace(capture_workspace(source, 0), tmp_path / "empty", 0)
    assert list((tmp_path / "empty").iterdir()) == []
    (source / "file").write_bytes(b"1234")
    saved = capture_workspace(source, 4)
    restore_workspace(saved, tmp_path / "exact", 4)
    with pytest.raises(InvalidWorkspace, match="file-size"):
        capture_workspace(source, 3)
    with pytest.raises(InvalidWorkspace, match="file-size"):
        restore_workspace(saved, tmp_path / "too-big", 3)
    assert not (tmp_path / "too-big").exists()


def test_private_capture_preserves_unreadable_modes_and_restores_them_on_failure(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    directory = source / "private"
    directory.mkdir()
    file = directory / "secret"
    file.write_bytes(b"private")
    file.chmod(0)
    directory.chmod(0)
    try:
        data = capture_workspace(source, 7, allow_permission_changes=True)
        assert stat.S_IMODE(directory.stat().st_mode) == 0
        with pytest.raises(InvalidWorkspace, match="file-size"):
            capture_workspace(source, 6, allow_permission_changes=True)
        assert stat.S_IMODE(directory.stat().st_mode) == 0
    finally:
        directory.chmod(0o700)
    assert stat.S_IMODE(file.stat().st_mode) == 0
    assert file.stat().st_size == 7
    destination = tmp_path / "restored"
    restore_workspace(data, destination, 7)
    assert stat.S_IMODE((destination / "private").stat().st_mode) == 0
    (destination / "private").chmod(0o700)
    restored = destination / "private/secret"
    assert stat.S_IMODE(restored.stat().st_mode) == 0
    restored.chmod(0o600)
    assert restored.read_bytes() == b"private"


@pytest.mark.parametrize("kind", ["symlink", "directory-link", "hardlink", "fifo"])
def test_capture_rejects_links_and_special_files(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("private")
    if kind == "symlink":
        (source / "bad").symlink_to(outside)
    elif kind == "directory-link":
        (source / "bad").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        os.link(outside, source / "bad")
    else:
        os.mkfifo(source / "bad")
    with pytest.raises(InvalidWorkspace):
        capture_workspace(source, 1024)


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../../escape", "a\\b", "./file", "."]
)
def test_restore_rejects_unsafe_paths_before_writing(tmp_path, name):
    data = archive_bytes(("valid", tarfile.REGTYPE, b"ok"), (name, tarfile.REGTYPE, b"bad"))
    with pytest.raises(InvalidWorkspace):
        restore_workspace(data, tmp_path / "restored", 1024)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_restore_rejects_unsupported_types(tmp_path, kind):
    with pytest.raises(InvalidWorkspace):
        restore_workspace(archive_bytes(("bad", kind, b"")), tmp_path / "restored", 1024)


@pytest.mark.parametrize("names", [("same", "same"), ("file", "file/child"), ("missing/child",)])
def test_restore_rejects_duplicate_paths_and_invalid_parents(tmp_path, names):
    data = archive_bytes(*[(name, tarfile.REGTYPE, b"x") for name in names])
    with pytest.raises(InvalidWorkspace):
        restore_workspace(data, tmp_path / "restored", 1024)


@pytest.mark.parametrize("data", [b"not gzip", gzip.compress(b"not tar"), gzip.compress(b"x")[:-3]])
def test_restore_rejects_corruption(tmp_path, data):
    with pytest.raises(InvalidWorkspace):
        restore_workspace(data, tmp_path / "restored", 1024)


def test_restore_bounds_decompression_and_refuses_existing_targets(tmp_path):
    with pytest.raises(InvalidWorkspace, match="uncompressed"):
        restore_workspace(gzip.compress(b"\0" * (METADATA_BUDGET + 2)), tmp_path / "bomb", 1)
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing/keep").write_text("keep")
    (tmp_path / "link").symlink_to(tmp_path / "existing")
    for target in [tmp_path / "existing", tmp_path / "link"]:
        with pytest.raises(FileExistsError):
            restore_workspace(archive_bytes(), target, 1024)
    assert (tmp_path / "existing/keep").read_text() == "keep"
