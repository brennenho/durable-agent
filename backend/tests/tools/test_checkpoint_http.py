import asyncio

import httpx
from durable_agent.contracts import ToolExecuteRequest, ToolSuccess
from durable_agent.tools.app import app
from durable_agent.tools.publication import publish_tool, register_tool

from .test_api import configured as configured
from .test_api import tool_request


def test_checkpoint_read_only_observes_committed_publication(configured):
    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://tools"
            ) as client:
                seed = (await client.get("/tools/workspace/initial")).json()["workspace_version_id"]
                request = ToolExecuteRequest.model_validate(tool_request(seed, "list_files"))
                path = f"/tools/operations/{request.operation_id}/checkpoint"
                assert (await client.get(path)).json() is None
                async with app.state.database.session() as session:
                    await register_tool(session, request)
                    await session.commit()
                assert (await client.get(path)).json() is None
                async with app.state.database.session() as session:
                    result = await publish_tool(
                        session, request, ToolSuccess(status="ok", value={"entries": []}), None
                    )
                    assert (await client.get(path)).json() is None
                    await session.rollback()
                assert (await client.get(path)).json() is None
                async with app.state.database.session() as session:
                    result = await publish_tool(session, request, result.output, None)
                    await session.commit()
                saved = (await client.get(path)).json()
                assert saved["workspace_version_id"] == seed
                assert saved["completed_at"] is not None
                async with app.state.database.session() as session:
                    assert await register_tool(session, request) == result
                    await session.commit()
                assert (await client.get(path)).json() == saved

    asyncio.run(exercise())
