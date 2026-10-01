"""Local file tools over immutable checkpoints. Execute off the event loop."""

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, ValidationError

from durable_agent.contracts import ToolError, ToolOutput, ToolSuccess
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


class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str


class ListArgs(FileArgs):
    path: str = "."


class WriteArgs(FileArgs):
    content: str


def execute_file_tool(
    archive: bytes, name: str, arguments: dict, max_bytes: int
) -> tuple[ToolOutput, bytes | None]:
    """Return the output and an updated checkpoint, or None when files are unchanged."""
    schemas = {"list_files": ListArgs, "read_file": FileArgs, "write_file": WriteArgs}
    if name not in schemas:
        raise InvalidToolCall(f"Unknown file tool: {name}")
    try:
        args = schemas[name].model_validate(arguments)
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
            return ToolSuccess(status="ok", value={"entries": children}), None
        if name == "read_file":
            content = read_workspace_file(archive, str(path), max_bytes)
            if len(content) > TEXT_LIMIT:
                return ToolError(
                    status="error", code="too_large", message="Text reads are limited to 64 KiB"
                ), None
            return ToolSuccess(status="ok", value={"content": content.decode("utf-8")}), None
        content = args.content.encode("utf-8")
        if len(content) > max_bytes:
            raise InvalidWorkspace("Workspace exceeds its file-size limit")
        with TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            restore_workspace(archive, root, max_bytes)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            updated = capture_workspace(root, max_bytes, allow_permission_changes=True)
        return ToolSuccess(status="ok", value={"bytes_written": len(content)}), updated
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
