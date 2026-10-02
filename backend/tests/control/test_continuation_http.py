from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from fastapi.testclient import TestClient

from .test_runtime import settings
from .test_worker_http import model_result


def complete_model(client, worker, expected_run_id):
    response = client.post("/internal/claims", json=worker)
    assert response.status_code == 200
    assignment = response.json()
    assert assignment["run_id"] == expected_run_id
    response = client.post(
        f"/internal/attempts/{assignment['attempt_id']}/report",
        json={
            **worker,
            "lease_generation": assignment["lease_generation"],
            "outcome": {"status": "succeeded", "result": model_result()},
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "recorded"}
    return assignment


def test_follow_up_http_survives_restart_and_replays_without_fetching_configuration(
    postgres_engine,
):
    seed, calls = str(uuid4()), []

    def tools(request):
        calls.append(request.url.path)
        return httpx.Response(200 if len(calls) == 1 else 503, json={"workspace_version_id": seed})

    configured = settings(postgres_engine)
    transport = httpx.MockTransport(tools)
    worker = {"worker_id": str(uuid4())}
    initial = {"submission_key": str(uuid4()), "prompt": "First question"}
    with TestClient(create_app(configured, transport=transport)) as client:
        created = client.post("/api/conversations", json=initial)
        assert created.status_code == 200
        conversation_id = created.json()["id"]
        first_id = created.json()["latest_run_id"]
        first = complete_model(client, worker, first_id)
        first_view = client.get(f"/api/runs/{first_id}").json()

    # Startup settings may change; an existing conversation keeps its frozen config.
    configured.model = "changed-model"
    with TestClient(create_app(configured, transport=transport)) as client:
        path = f"/api/conversations/{conversation_id}/runs"
        body = {
            "submission_key": str(uuid4()),
            "prompt": "Follow up",
            "after_run_id": first_id,
        }
        created = client.post(path, json=body)
        assert created.status_code == 200
        second_id = created.json()["id"]
        assert created.json()["status"] == "queued"
        assert created.json()["model"] == first["config"]["model"]
        assert "operations" not in created.json()  # RunSummary response.
        assert client.post(path, json=body).json() == created.json()
        assert client.get(f"/api/runs/{first_id}").json() == first_view
        second = complete_model(client, worker, second_id)
        inherited = second["config"]["agent_history"]
        assert {**second["config"], "agent_history": []} == first["config"]
        assert len(inherited) == 1
        assert inherited[0]["operation_id"] == first["operation_id"]
        assert inherited[0]["workspace_version_id"] == seed
        assert inherited[0]["value"] == {
            **model_result()["output"],
            "native_context": model_result()["native_context"],
        }
        assert second["attempt_no"] == second["lease_generation"] == 1
        assert second["operation"]["request"]["explicit_input"] == [
            {"role": "user", "content": initial["prompt"]},
            *model_result()["native_context"],
            {"role": "user", "content": body["prompt"]},
        ]
        replay = client.post(path, json=body)
        assert replay.status_code == 200
        assert replay.json()["id"] == second_id
        assert replay.json()["status"] == "succeeded"
        saved = client.get(f"/api/conversations/{conversation_id}").json()
        assert saved["latest_run_id"] == second_id
        assert saved["status"] == "succeeded"
        assert [run["id"] for run in saved["runs"]] == [first_id, second_id]
        assert all(run["workspace_version_id"] == seed for run in saved["runs"])
        assert calls == ["/tools/workspace/initial"]


def test_follow_up_http_returns_conflicts_and_validates_predecessor(postgres_engine):
    app = create_app(
        settings(postgres_engine),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        ),
    )
    with TestClient(app) as client:
        initial = {"submission_key": str(uuid4()), "prompt": "First"}
        created = client.post("/api/conversations", json=initial)
        assert created.status_code == 200
        conversation_id = created.json()["id"]
        first_id = created.json()["latest_run_id"]
        path = f"/api/conversations/{conversation_id}/runs"
        body = {"submission_key": str(uuid4()), "prompt": "Next", "after_run_id": first_id}

        for submitted, code in [
            (body, "conversation_busy"),
            ({**body, "after_run_id": str(uuid4())}, "stale_conversation"),
            ({**initial, "after_run_id": first_id}, "submission_conflict"),
        ]:
            response = client.post(path, json=submitted)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == code
        unknown = client.post(f"/api/conversations/{uuid4()}/runs", json=body)
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "conversation_not_found"
        assert client.post(path, json={**body, "after_run_id": "invalid"}).status_code == 422
        assert client.post(path, json={**body, "model": "other"}).status_code == 422

        complete_model(client, {"worker_id": str(uuid4())}, first_id)
        follow_up = client.post(path, json=body)
        assert follow_up.status_code == 200
        changed = client.post(path, json={**body, "prompt": "Different"})
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "submission_conflict"
        for entry_path in ["/api/conversations", "/api/runs"]:
            response = client.post(
                entry_path, json={key: body[key] for key in ("submission_key", "prompt")}
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "submission_conflict"
        saved = client.get(f"/api/conversations/{conversation_id}").json()
        assert [run["id"] for run in saved["runs"]] == [first_id, follow_up.json()["id"]]
