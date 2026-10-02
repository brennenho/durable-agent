"""Run a separately authored module through the package's public imports."""

import subprocess
import sys
from pathlib import Path

SMOKE_APP = """\
import asyncio
import sys
from uuid import uuid4

import durable_agent
from durable_agent import (
    AgentApp, AgentContext, ApiError, ChildBatch, ChildTask, Client, Done, Fail,
    Limits, ModelCall, Next, RunFailed, RunHandle, StepResult, ToolCall,
    ToolContext, ToolError, Workspace, agent, load_app, tool,
)


@tool(name="count_words", version="1")
async def count_words(text: str) -> dict:
    return {"words": len(text.split())}


@agent(name="writer", version="1")
class Writer:
    def start(self, ctx: AgentContext):
        return Next(state={"phase": "count"}, step=ToolCall(
            name="count_words", arguments={"text": ctx.input},
        ))

    def advance(self, ctx: AgentContext, result: StepResult):
        return Done(output=result.value)


app = AgentApp(agents=[Writer], tools=[count_words])


def verify():
    assert load_app("smoke_app:app") is app
    assert app.agents["writer"].agent_type is Writer
    assert app.tools["count_words"].entrypoint == "smoke_app:count_words"
    assert app.tools["count_words"].parameters["required"] == ["text"]
    context = AgentContext(
        run_id=uuid4(), agent="writer", version="1", input="Two words",
        workspace_version_id=uuid4(),
    )
    pending = Writer().start(context)
    result = asyncio.run(count_words(**pending.step.arguments))
    completed = Writer().advance(
        context, StepResult(operation_id=uuid4(), kind="tool", value=result),
    )
    assert completed.output == {"words": 2}
    error = ToolError(code="invalid_document", message="Needs a title")
    assert error.model_dump(mode="json")["status"] == "error"
    assert not any(name in sys.modules for name in (
        "fastapi", "sqlalchemy", "openai", "durable_agent.control", "durable_agent.tools",
    ))
    return durable_agent.__file__
"""


def test_public_authoring_api_in_separate_module(tmp_path):
    (tmp_path / "smoke_app.py").write_text(SMOKE_APP)
    result = subprocess.run(
        [sys.executable, "-c", "from smoke_app import verify; print(verify())"],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).name == "__init__.py"
