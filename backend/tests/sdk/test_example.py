import asyncio
import importlib
import time
from pathlib import Path
from uuid import uuid4

import pytest
from durable_agent import (
    AgentContext,
    Done,
    Fail,
    Next,
    StepResult,
    ToolContext,
    Workspace,
    load_app,
)


@pytest.fixture
def example(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "examples"))
    return importlib.import_module("sdk_agent.app")


def context(value):
    return AgentContext(
        run_id=uuid4(),
        agent="document_writer",
        version="1",
        input=value,
        workspace_version_id=uuid4(),
    )


def test_example_saves_a_document_and_finishes_from_saved_state(example, tmp_path):
    app = load_app("sdk_agent.app:app")
    assert app.tools["save_document"].entrypoint == "sdk_agent.app:save_document"
    agent = app.agents["document_writer"].agent_type()
    ctx = context({"title": "Résumé", "body": "A document written by a custom tool."})
    pending = agent.start(ctx)
    assert isinstance(pending, Next)
    assert list(tmp_path.iterdir()) == []

    tool_ctx = ToolContext(
        run_id=ctx.run_id,
        operation_id=uuid4(),
        attempt_id=uuid4(),
        workspace=Workspace(tmp_path),
        deadline=time.monotonic() + 10,
    )
    value = asyncio.run(example.save_document(tool_ctx, **pending.step.arguments))
    document = (tmp_path / "document.md").read_text()
    assert document == "# Résumé\n\nA document written by a custom tool.\n"
    assert value["bytes_written"] == len(document.encode("utf-8"))
    restored = AgentContext.model_validate_json(
        ctx.model_copy(update={"state": pending.state}).model_dump_json()
    )
    result = StepResult(operation_id=tool_ctx.operation_id, kind="tool", value=value)
    finished = agent.advance(restored, result)
    assert isinstance(finished, Done)
    assert finished.output == {"title": "Résumé", "file": value}


def test_example_business_error_does_not_write_partial_document(example, tmp_path):
    ctx = context({"title": "", "body": "Body"})
    agent = example.DocumentWriter()
    pending = agent.start(ctx)
    tool_ctx = ToolContext(
        run_id=ctx.run_id,
        operation_id=uuid4(),
        attempt_id=uuid4(),
        workspace=Workspace(tmp_path),
        deadline=time.monotonic() + 10,
    )
    error = asyncio.run(example.save_document(tool_ctx, **pending.step.arguments))
    result = StepResult(
        operation_id=tool_ctx.operation_id,
        kind="tool",
        error={"code": error.code, "message": error.message},
    )
    assert agent.advance(ctx.model_copy(update={"state": pending.state}), result) == Fail(
        code="empty_title",
        message="The document needs a title",
    )
    assert list(tmp_path.iterdir()) == []


def test_example_invalid_input_never_schedules_work(example):
    assert isinstance(example.DocumentWriter().start(context({"title": 42})), Fail)
