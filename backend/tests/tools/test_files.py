import pytest
from durable_agent.tools.archives import InvalidWorkspace
from durable_agent.tools.files import TEXT_LIMIT, InvalidToolCall, execute_file_tool
from durable_agent.tools.workspaces import capture_initial_workspace, read_workspace_file


def test_write_read_and_list_preserve_input_and_create_parents(tmp_path):
    seed = capture_initial_workspace(tmp_path, 1024)
    result, saved = execute_file_tool(
        seed, "write_file", {"path": "dir/a.md", "content": "héllo"}, 1024
    )
    assert result.value == {"bytes_written": 6}
    assert read_workspace_file(saved, "dir/a.md", 1024) == "héllo".encode()
    result, version = execute_file_tool(saved, "read_file", {"path": "dir/a.md"}, 1024)
    assert result.value == {"content": "héllo"}
    assert version is None
    result, _ = execute_file_tool(saved, "list_files", {}, 1024)
    assert result.value == {"entries": [{"path": "dir", "kind": "directory"}]}
    result, _ = execute_file_tool(saved, "list_files", {"path": "dir"}, 1024)
    assert result.value == {"entries": [{"path": "dir/a.md", "kind": "file", "size_bytes": 6}]}
    result, _ = execute_file_tool(seed, "list_files", {}, 1024)
    assert result.value == {"entries": []}
    result, replacement = execute_file_tool(
        saved, "write_file", {"path": "dir/a.md", "content": "new"}, 1024
    )
    assert read_workspace_file(replacement, "dir/a.md", 1024) == b"new"
    assert read_workspace_file(saved, "dir/a.md", 1024) == "héllo".encode()


@pytest.mark.parametrize(
    "name,args,code",
    [
        ("read_file", {"path": "missing"}, "not_found"),
        ("read_file", {"path": "../secret"}, "invalid_path"),
        ("write_file", {"path": "/escape", "content": "x"}, "invalid_path"),
        ("write_file", {"path": "file/child", "content": "x"}, "invalid_path"),
        ("list_files", {"path": "file"}, "invalid_path"),
        ("read_file", {"path": "binary"}, "invalid_encoding"),
    ],
)
def test_business_errors_are_completed_outputs_without_a_new_checkpoint(tmp_path, name, args, code):
    (tmp_path / "file").write_text("x")
    (tmp_path / "binary").write_bytes(b"\xff")
    seed = capture_initial_workspace(tmp_path, 1024)
    result, updated = execute_file_tool(seed, name, args, 1024)
    assert result.status == "error"
    assert result.code == code
    assert updated is None


@pytest.mark.parametrize(
    "name,args",
    [
        ("unknown", {}),
        ("read_file", {}),
        ("read_file", {"path": 1}),
        ("write_file", {"path": "file"}),
        ("list_files", {"extra": True}),
    ],
)
def test_invalid_calls_fail_execution(name, args):
    seed = capture_initial_workspace(None, 0)
    with pytest.raises(InvalidToolCall):
        execute_file_tool(seed, name, args, 1024)


def test_read_limit_is_bytes_and_oversized_writes_never_publish(tmp_path):
    (tmp_path / "large").write_bytes(b"a" * TEXT_LIMIT)
    seed = capture_initial_workspace(tmp_path, TEXT_LIMIT)
    result, _ = execute_file_tool(seed, "read_file", {"path": "large"}, TEXT_LIMIT)
    assert len(result.value["content"]) == TEXT_LIMIT
    (tmp_path / "large").write_bytes(b"a" * (TEXT_LIMIT + 1))
    seed = capture_initial_workspace(tmp_path, TEXT_LIMIT + 1)
    result, updated = execute_file_tool(seed, "read_file", {"path": "large"}, TEXT_LIMIT + 1)
    assert result.code == "too_large"
    assert updated is None
    with pytest.raises(InvalidWorkspace):
        execute_file_tool(seed, "write_file", {"path": "new", "content": "x"}, TEXT_LIMIT + 1)
