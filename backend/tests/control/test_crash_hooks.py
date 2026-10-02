import asyncio
import json

import httpx
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.models import Run
from fastapi.testclient import TestClient
from sqlalchemy import select

from .test_inspection_http import claim, submit
from .test_worker_http import configured_app, model_result


def test_interrupt_after_acceptance_loses_ack_but_duplicate_report_does_not_advance(
    postgres_engine,
):
    app = configured_app(postgres_engine)
    events = []

    async def controller(request):
        event = json.loads(request.content)
        events.append(event)
        async with app.state.runtime.db.session() as session:
            run = await session.scalar(
                select(Run).where(Run.id == event["run_id"]).with_for_update(nowait=True)
            )
            assert run.status == "succeeded"
        return httpx.Response(200, json={"action": "interrupt"})

    observer = httpx.AsyncClient(
        base_url="http://controller", transport=httpx.MockTransport(controller)
    )
    try:
        with TestClient(app) as client:
            app.state.runtime.hooks = CrashTestHooks("control", observer)
            run_id = submit(client)
            assignment, lease = claim(client)
            path = f"/internal/attempts/{assignment['attempt_id']}/report"
            body = {**lease, "outcome": {"status": "succeeded", "result": model_result()}}
            lost = client.post(path, json=body)
            assert lost.status_code == 503
            assert lost.json()["error"]["code"] == "crash_test_interrupted"
            saved = client.get(f"/api/runs/{run_id}").json()
            assert saved["status"] == "succeeded"
            assert saved["operations"][0]["result"] == model_result()
            assert client.post(path, json=body).json() == {"status": "already_recorded"}
            assert client.get(f"/api/runs/{run_id}").json() == saved
            assert len(events) == 1
            assert events[0]["name"] == "control.after_accept_commit"
            assert events[0]["operation_id"] == assignment["operation_id"]
            assert events[0]["attempt_id"] == assignment["attempt_id"]
    finally:
        asyncio.run(observer.aclose())
