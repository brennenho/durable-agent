from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from durable_agent.db.models import Operation, Run
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from .test_runtime import settings


def test_conversation_creation_replays_without_tools_and_preserves_run_urls(postgres_engine):
    seed, calls = uuid4(), []

    def tools(request):
        calls.append(request.url.path)
        return httpx.Response(
            200 if len(calls) == 1 else 503, json={"workspace_version_id": str(seed)}
        )

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        assert client.get("/api/conversations").json() == []
        body = {"submission_key": str(uuid4()), "prompt": "Build\n  a calculator"}
        created = client.post("/api/conversations", json=body)
        assert created.status_code == 200
        conversation = created.json()
        run_id = conversation["latest_run_id"]
        assert conversation["id"] == run_id
        assert conversation["title"] == "Build a calculator"
        assert client.post("/api/conversations", json=body).json() == conversation
        assert client.post("/api/runs", json=body).json()["id"] == run_id
        assert len(calls) == 1
        assert client.get("/api/conversations").json() == [conversation]
        saved = client.get(f"/api/conversations/{run_id}").json()
        assert saved == {**conversation, "runs": [client.get(f"/api/runs/{run_id}").json()]}
        assert saved["runs"][0]["workspace_version_id"] == str(seed)
        assert (
            client.post("/api/conversations", json={**body, "prompt": "Changed"}).status_code == 409
        )
        assert client.get(f"/api/conversations/{uuid4()}").status_code == 404
        assert (
            client.post(
                "/api/conversations", json={**body, "submission_key": str(uuid4())}
            ).status_code
            == 503
        )
        assert client.get("/api/conversations").json() == [conversation]


def test_conversation_views_order_turns_and_sort_by_latest_progress(postgres_engine):
    app = create_app(
        settings(postgres_engine),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        ),
    )
    with TestClient(app) as client:
        first, second = [
            client.post("/api/runs", json={"submission_key": str(uuid4()), "prompt": prompt}).json()
            for prompt in ["Initial prompt", "Another chat"]
        ]
        assert [c["id"] for c in client.get("/api/conversations").json()] == [
            second["id"],
            first["id"],
        ]
        followup_id, later = uuid4(), datetime.now(UTC) + timedelta(minutes=1)
        with postgres_engine.begin() as connection:
            config = connection.scalar(select(Run.config).where(Run.id == first["id"]))
            connection.execute(
                update(Run)
                .where(Run.id == first["id"])
                .values(status="succeeded", finished_at=later, next_attempt_at=None)
            )
            connection.execute(
                update(Operation)
                .where(Operation.run_id == first["id"])
                .values(
                    result={
                        "kind": "model",
                        "output": {"status": "completed", "text": "First answer", "tool_calls": []},
                        "native_context": [],
                    },
                    completed_at=later,
                )
            )
            connection.execute(
                insert(Run).values(
                    id=followup_id,
                    conversation_id=first["id"],
                    turn_index=1,
                    submission_key=uuid4(),
                    prompt="Follow up",
                    config=config,
                    status="queued",
                    next_attempt_at=later,
                    created_at=later,
                )
            )
        listing = client.get("/api/conversations").json()
        assert [c["id"] for c in listing] == [first["id"], second["id"]]
        assert listing[0]["title"] == "Initial prompt"
        assert listing[0]["status"] == "queued"
        saved = client.get(f"/api/conversations/{first['id']}").json()
        assert {k: v for k, v in saved.items() if k != "runs"} == listing[0]
        assert [run["id"] for run in saved["runs"]] == [first["id"], str(followup_id)]
        assert saved["runs"][0]["final_answer"] == "First answer"
        assert saved["runs"][1]["workspace_version_id"] == config["initial_workspace_version_id"]
