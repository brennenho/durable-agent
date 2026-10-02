"""Local file tools over immutable checkpoints. Execute off the event loop."""

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from durable_agent.contracts import (
    ListFilesArguments,
    ReadFileArguments,
    ToolError,
    ToolOutput,
    ToolSuccess,
    WriteFileArguments,
)
from durable_agent.tools.archives import (
    InvalidWorkspace,
    capture_workspace,
    restore_workspace,
    workspace_path,
)
from durable_agent.tools.workspaces import list_workspace, read_workspace_file

TEXT_LIMIT = 64 * 1024


class InvalidToolCall(ValueError):
    pass


def execute_file_tool(
    archive: bytes, name: str, arguments: dict, max_bytes: int
) -> tuple[ToolOutput, bytes | None]:
    """Return the output and an updated checkpoint, or None when files are unchanged."""
    schemas = {
        "list_files": ListFilesArguments,
        "read_file": ReadFileArguments,
        "write_file": WriteFileArguments,
    }
    if name not in schemas:
        raise InvalidToolCall(f"Unknown file tool: {name}")
    try:
        args = schemas[name].model_validate(arguments, strict=True)
    except ValidationError as exc:
        raise InvalidToolCall(f"Invalid arguments for {name}: {exc}") from exc
    try:
        path = (
            PurePosixPath(".")
            if name == "list_files" and args.path == "."
            else workspace_path(args.path)
        )
    except InvalidWorkspace as exc:
        return ToolError(status="error", code="invalid_path", message=str(exc)), None
    try:
        if name == "list_files":
            return _list_files(archive, path, max_bytes), None
        if name == "read_file":
            return _read_file(archive, path, max_bytes), None
        return _write_file(archive, path, args.content, max_bytes)
    except FileNotFoundError:
        return ToolError(
            status="error", code="not_found", message=f"File or directory not found: {path}"
        ), None
    except (NotADirectoryError, IsADirectoryError, FileExistsError):
        return ToolError(
            status="error", code="invalid_path", message=f"Wrong file type in path: {path}"
        ), None
    except PermissionError:
        return ToolError(
            status="error", code="permission_denied", message=f"Cannot access: {path}"
        ), None
    except UnicodeError:
        return ToolError(
            status="error", code="invalid_encoding", message="File content must be UTF-8"
        ), None


def _list_files(archive: bytes, path: PurePosixPath, max_bytes: int) -> ToolSuccess:
    entries = list_workspace(archive, max_bytes).entries
    if path != PurePosixPath("."):
        selected = next((entry for entry in entries if entry.path == str(path)), None)
        if selected is None:
            raise FileNotFoundError(str(path))
        if selected.kind != "directory":
            raise NotADirectoryError(str(path))
    children = [
        entry.model_dump(mode="json", exclude_none=True)
        for entry in entries
        if PurePosixPath(entry.path).parent == path
    ]
    return ToolSuccess(status="ok", value={"entries": children})


def _read_file(archive: bytes, path: PurePosixPath, max_bytes: int) -> ToolOutput:
    content = read_workspace_file(archive, str(path), max_bytes)
    if len(content) > TEXT_LIMIT:
        return ToolError(
            status="error", code="too_large", message="Text reads are limited to 64 KiB"
        )
    return ToolSuccess(status="ok", value={"content": content.decode("utf-8")})


def _write_file(
    archive: bytes, path: PurePosixPath, content: str, max_bytes: int
) -> tuple[ToolSuccess, bytes]:
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        raise InvalidWorkspace("Workspace exceeds its file-size limit")
    with TemporaryDirectory() as directory:
        root = Path(directory) / "workspace"
        restore_workspace(archive, root, max_bytes)
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        updated = capture_workspace(root, max_bytes, allow_permission_changes=True)
    return ToolSuccess(status="ok", value={"bytes_written": len(data)}), updated
