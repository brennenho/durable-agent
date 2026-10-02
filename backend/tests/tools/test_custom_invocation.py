import asyncio
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from durable_agent.contracts import ToolExecuteRequest
from durable_agent.sdk.registration import tool
from durable_agent.tools.invoke import INVALID_TOOL_EXIT, invoke_tool
from pydantic import ValidationError

from . import custom_tool_fixture


def custom_request(**arguments):
    return ToolExecuteRequest(
        definition=custom_tool_fixture.save_note.__durable_tool__,
        run_id=uuid4(),
        operation_id=uuid4(),
        attempt_id=uuid4(),
        input_version_id=uuid4(),
        name="save_note",
        arguments=arguments,
        lease_generation=1,
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        sandbox={"image_digest": "sha256:test", "workspace_max_bytes": 1024, "log_max_bytes": 1024},
    )


def test_loaded_tool_receives_validated_defaults_identity_and_private_files(tmp_path, capsys):
    request = custom_request()
    output = asyncio.run(invoke_tool(request, tmp_path))
    assert output.value == {
        "content": "default",
        "run_id": str(request.run_id),
        "operation_id": str(request.operation_id),
        "attempt_id": str(request.attempt_id),
        "remaining": True,
    }
    assert (tmp_path / "note.txt").read_text() == "default"
    logs = capsys.readouterr()
    assert logs.out == ""
    assert logs.err == "developer log\n"


@pytest.mark.parametrize("arguments", [{"content": 123}, {"unexpected": True}])
def test_invalid_arguments_never_execute_the_tool(tmp_path, arguments):
    with pytest.raises(ValidationError):
        asyncio.run(invoke_tool(custom_request(**arguments), tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [{"version": "2"}, {"parameters": {}}, {"takes_context": False}])
def test_installed_definition_must_match_the_saved_call(tmp_path, change):
    request = custom_request()
    request.definition = request.definition.model_copy(update=change)
    with pytest.raises(ValueError, match="differs from the saved definition"):
        asyncio.run(invoke_tool(request, tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["run_id", "attempt_id", "definition"])
def test_custom_calls_require_definition_and_attempt_identity(tmp_path, field):
    request = custom_request()
    setattr(request, field, None)
    with pytest.raises(ValueError, match="matching definition and call identity"):
        asyncio.run(invoke_tool(request, tmp_path))


@pytest.mark.parametrize("mode", ["exception", "invalid", "nan"])
def test_exception_or_invalid_result_never_returns_a_completed_output(tmp_path, mode):
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(invoke_tool(custom_request(mode=mode), tmp_path))


def test_explicit_business_error_is_a_completed_output(tmp_path):
    result = asyncio.run(invoke_tool(custom_request(mode="error"), tmp_path))
    assert result.code == "needs_review"
    assert (tmp_path / "note.txt").read_text() == "default"


def cli(request):
    return subprocess.run(
        [sys.executable, "-m", "durable_agent.tools.invoke", request.model_dump_json()],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_cli_reports_invalid_code_without_writing_a_result():
    request = custom_request()
    request.definition = request.definition.model_copy(update={"entrypoint": "invalid"})
    output = cli(request)
    assert output.returncode == INVALID_TOOL_EXIT
    assert output.stdout == ""
    assert "Tool entrypoint must be module:qualified_name" in output.stderr


def test_cli_keeps_native_and_child_process_logs_out_of_result_json():
    request = custom_request(content="compiled", mode="native")
    request.name = "echo"
    request.definition = custom_tool_fixture.echo.__durable_tool__
    output = cli(request)
    assert output.returncode == 0, output.stderr
    assert json.loads(output.stdout) == {"status": "ok", "value": {"content": "compiled"}}
    assert "native log" in output.stderr
    assert "child log" in output.stderr


def test_tools_without_context_receive_only_declared_arguments(tmp_path):
    request = custom_request(content="hello")
    request.name = "echo"
    request.definition = custom_tool_fixture.echo.__durable_tool__
    assert asyncio.run(invoke_tool(request, tmp_path)).value == {"content": "hello"}


def test_custom_definition_cannot_replace_a_builtin(tmp_path):
    request = custom_request()
    request.name = "run_command"
    request.definition = request.definition.model_copy(update={"name": request.name})
    with pytest.raises(ValueError, match="matching definition and call identity"):
        asyncio.run(invoke_tool(request, tmp_path))
    assert list(tmp_path.iterdir()) == []


class ReviewState(StrEnum):
    DRAFT = "draft"


@tool(name="typed_arguments", version="1")
async def typed_arguments(identity: UUID, day: date, state: ReviewState, count: int = 1):
    return {"identity": identity.hex, "day": day.isoformat(), "state": state.value, "count": count}


def typed_request(**changes):
    request = custom_request()
    request.name = "typed_arguments"
    request.definition = typed_arguments.__durable_tool__
    request.arguments = {
        "identity": str(request.run_id),
        "day": "2026-10-02",
        "state": "draft",
        **changes,
    }
    return ToolExecuteRequest.model_validate_json(request.model_dump_json())


def test_json_wire_arguments_become_their_declared_python_types(tmp_path):
    request = typed_request()
    output = asyncio.run(invoke_tool(request, tmp_path))
    assert output.value == {
        "identity": request.run_id.hex,
        "day": "2026-10-02",
        "state": "draft",
        "count": 1,
    }


@pytest.mark.parametrize(
    "changes",
    [{"identity": "invalid"}, {"day": "2026-99-99"}, {"state": "unknown"}, {"count": "1"}],
)
def test_strict_json_arguments_reject_bad_values_and_numeric_strings(tmp_path, changes):
    with pytest.raises(ValidationError):
        asyncio.run(invoke_tool(typed_request(**changes), tmp_path))
