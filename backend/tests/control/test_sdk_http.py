from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from durable_agent.sdk import Done, Next, ToolCall
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent
from fastapi.testclient import TestClient

from .test_runtime import settings


@agent(name="echo", version="1")
class Echo:
    def start(self, ctx):
        if ctx.input == "read":
            return Next(
                state={"started": True}, step=ToolCall(name="read_file", arguments={"path": "a"})
            )
        return Done(
            output={"input": ctx.input, "state": ctx.state, "history": len(ctx.history)},
            state={"previous": ctx.input},
        )

    def advance(self, ctx, result):
        return Done(output=result.value, state={"accepted": True})


app = AgentApp(agents=[Echo])


def configured_app(postgres_engine):
    configured = settings(postgres_engine).model_copy(
        update={"agent_app": f"{__name__}:app", "agent_build_id": "build-1"}
    )
    return create_app(
        configured,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        ),
    )


def test_custom_submission_is_idempotent_and_continues_with_inherited_state(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        body = {"submission_key": str(uuid4()), "agent": "echo", "input": {"value": True}}
        created = client.post("/api/runs", json=body)
        assert created.status_code == 200
        run = created.json()
        assert run["status"] == "succeeded"
        assert run["agent"]["name"] == "echo"
        assert client.post("/api/runs", json=body).json() == run
        assert client.post("/api/runs", json={**body, "input": {"value": 1}}).status_code == 409
        assert (
            client.post(
                "/api/runs", json={key: value for key, value in body.items() if key != "agent"}
            ).status_code
            == 409
        )
        saved = client.get(f"/api/runs/{run['id']}").json()
        assert saved["output"] == {"input": {"value": True}, "state": None, "history": 0}
        assert saved["operations"] == []
        follow = client.post(
            f"/api/conversations/{run['conversation_id']}/runs",
            json={"submission_key": str(uuid4()), "after_run_id": run["id"], "input": "next"},
        )
        assert follow.status_code == 200
        assert follow.json()["agent"]["name"] == "echo"
        output = client.get(f"/api/runs/{follow.json()['id']}").json()["output"]
        assert output == {"input": "next", "state": {"previous": {"value": True}}, "history": 0}
        unknown = client.post(
            "/api/runs", json={"submission_key": str(uuid4()), "agent": "missing", "input": None}
        )
        assert unknown.status_code == 400
        assert unknown.json()["error"]["code"] == "unknown_agent"


def test_custom_tool_result_can_be_forked_with_its_state_history_and_files(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        run = client.post(
            "/api/runs", json={"submission_key": str(uuid4()), "agent": "echo", "input": "read"}
        ).json()
        worker = {"worker_id": str(uuid4())}
        assignment = client.post("/internal/claims", json=worker).json()
        assert assignment["operation"]["kind"] == "tool"
        version = str(uuid4())
        result = {
            "kind": "tool",
            "call_id": assignment["operation"]["request"]["call"]["call_id"],
            "output": {"status": "ok", "value": {"text": "saved"}},
            "workspace_version_id": version,
        }
        report = client.post(
            f"/internal/attempts/{assignment['attempt_id']}/report",
            json={
                **worker,
                "lease_generation": assignment["lease_generation"],
                "outcome": {"status": "succeeded", "result": result},
            },
        )
        assert report.status_code == 200
        saved = client.get(f"/api/runs/{run['id']}").json()
        assert saved["output"] == {"text": "saved"}
        fork = client.post(
            f"/api/runs/{run['id']}/forks",
            json={
                "submission_key": str(uuid4()),
                "after_operation_id": assignment["operation_id"],
                "input": "fork",
            },
        )
        assert fork.status_code == 200
        child = client.get(f"/api/conversations/{fork.json()['id']}").json()["runs"][0]
        assert child["workspace_version_id"] == version
        assert child["output"] == {"input": "fork", "state": {"accepted": True}, "history": 1}


def test_prompt_submission_uses_the_builtin_sdk_loop(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        run = client.post(
            "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Hello"}
        ).json()
        assert run["agent"]["name"] == "builtin"
        assigned = client.post("/internal/claims", json={"worker_id": str(uuid4())}).json()
        assert assigned["operation"]["request"]["explicit_input"] == [
            {"role": "user", "content": "Hello"}
        ]
