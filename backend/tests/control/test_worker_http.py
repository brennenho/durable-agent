import json
from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from .test_runtime import settings


def configured_app(postgres_engine):
    seed = str(uuid4())
    return create_app(
        settings(postgres_engine),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": seed})
        ),
    )


def model_result(text="Done", calls=None):
    return {
        "kind": "model",
        "output": {"status": "completed", "text": text, "tool_calls": calls or []},
        "native_context": (
            [{"type": "message", "role": "assistant", "content": text}] if text else []
        )
        + [
            {
                "type": "function_call",
                "call_id": call["call_id"],
                "name": call["name"],
                "arguments": json.dumps(call["arguments"]),
            }
            for call in calls or []
        ],
    }


def test_worker_http_commits_a_complete_run_and_deduplicates_reports(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        assert client.get("/health/ready").status_code == 200
        worker = {"worker_id": str(uuid4())}
        created = client.post(
            "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Write a note"}
        )
        run_id = created.json()["id"]
        version = str(uuid4())
        results = [
            model_result(
                "Editing",
                [
                    {
                        "call_id": "a",
                        "name": "write_file",
                        "arguments": {"path": "a", "content": "Hi"},
                    }
                ],
            ),
            {
                "kind": "tool",
                "call_id": "a",
                "output": {"status": "ok", "value": {"bytes_written": 2}},
                "workspace_version_id": version,
            },
            model_result(),
        ]
        for result in results:
            response = client.post("/internal/claims", json=worker)
            assert response.status_code == 200
            assignment = response.json()
            assert assignment["operation"]["kind"] == result["kind"]
            assert client.post("/internal/claims", json=worker).json() is None
            attempt_path = f"/internal/attempts/{assignment['attempt_id']}"
            lease = {**worker, "lease_generation": assignment["lease_generation"]}
            renewed = client.post(attempt_path + "/heartbeat", json=lease)
            assert renewed.status_code == 200
            assert renewed.json()["deadline_at"] == assignment["deadline_at"]
            view = client.get(f"/api/runs/{run_id}").json()
            assert view["last_heartbeat_at"] == renewed.json()["server_time"]
            report = {**lease, "outcome": {"status": "succeeded", "result": result}}
            assert client.post(attempt_path + "/report", json=report).json() == {
                "status": "recorded"
            }
            assert client.post(attempt_path + "/report", json=report).json() == {
                "status": "already_recorded"
            }
            assert client.post(attempt_path + "/heartbeat", json=lease).status_code == 409
        saved = client.get(f"/api/runs/{run_id}").json()
        assert saved["status"] == "succeeded"
        assert saved["final_answer"] == "Done"
        assert saved["workspace_version_id"] == version
        assert [op["result"] for op in saved["operations"]] == results
        assert all(len(op["attempts"]) == 1 for op in saved["operations"])
        assert client.post("/internal/claims", json=worker).json() is None


def test_worker_error_retries_the_operation_and_rejects_stale_results(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        worker = {"worker_id": str(uuid4())}
        run = client.post(
            "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Finish"}
        ).json()
        first = client.post("/internal/claims", json=worker).json()
        path = f"/internal/attempts/{first['attempt_id']}"
        body = {
            **worker,
            "lease_generation": first["lease_generation"],
            "outcome": {
                "status": "failed",
                "error": {
                    "code": "transport",
                    "message": "Connection lost",
                    "retryable": True,
                    "retry_after_seconds": 0,
                },
            },
        }
        assert client.post(path + "/report", json=body).status_code == 200
        second = client.post("/internal/claims", json=worker).json()
        assert second["operation_id"] == first["operation_id"]
        assert second["operation"] == first["operation"]
        assert second["lease_generation"] > first["lease_generation"]
        assert second["attempt_no"] == 2
        assert client.post(path + "/report", json=body).json() == {"status": "already_recorded"}
        body["outcome"] = {"status": "succeeded", "result": model_result()}
        assert client.post(path + "/report", json=body).status_code == 409
        assert client.post(f"/internal/attempts/{uuid4()}/report", json=body).status_code == 409
        body["lease_generation"] = second["lease_generation"]
        assert (
            client.post(f"/internal/attempts/{second['attempt_id']}/report", json=body).status_code
            == 200
        )
        saved = client.get(f"/api/runs/{run['id']}").json()
        assert saved["status"] == "succeeded"
        assert [attempt["status"] for attempt in saved["operations"][0]["attempts"]] == [
            "error",
            "accepted",
        ]


def test_database_unavailability_fails_claims_and_readiness(postgres_engine, monkeypatch):
    app = configured_app(postgres_engine)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        original_session = app.state.runtime.db.session

        def unavailable():
            raise OperationalError("connect", {}, OSError("database unavailable"))

        monkeypatch.setattr(app.state.runtime.db, "session", unavailable)
        for response in [
            client.get("/health/ready"),
            client.post("/internal/claims", json={"worker_id": str(uuid4())}),
        ]:
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "database_unavailable"
        monkeypatch.setattr(app.state.runtime.db, "session", original_session)
        assert client.get("/health/ready").status_code == 200
