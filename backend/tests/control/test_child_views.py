"""Children remain inspectable without becoming top-level chats or shared files."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from durable_agent.contracts import SubmitRunRequest
from durable_agent.control.conversations import conversation_detail, list_conversations
from durable_agent.control.runs import create_run
from durable_agent.control.views import detail, list_summaries
from durable_agent.db.models import Operation


def test_child_views_preserve_order_identity_failure_and_private_workspace(database, config):
    async def exercise():
        async with database() as db:
            async with db.session() as session:
                parent = await create_run(
                    session, SubmitRunRequest(submission_key=uuid4(), prompt="Review"), config
                )
                operation = Operation(
                    id=uuid4(),
                    run_id=parent.id,
                    position=1,
                    kind="tool",
                    request={
                        "call": {
                            "call_id": "delegate-1",
                            "name": "delegate",
                            "arguments": {
                                "tasks": [
                                    {"name": "Code", "prompt": "Inspect code"},
                                    {"name": "Tests", "prompt": "Inspect tests"},
                                ]
                            },
                        },
                        "input_version_id": str(config.initial_workspace_version_id),
                    },
                )
                session.add(operation)
                await session.flush()
                parent.status, parent.next_attempt_at = "waiting", None
                children = {}
                for index in (1, 0):
                    child_config = config.model_copy(
                        update={"initial_workspace_version_id": uuid4()}
                    )
                    child = await create_run(
                        session,
                        SubmitRunRequest(submission_key=uuid4(), prompt=f"Task {index}"),
                        child_config,
                    )
                    child.parent_operation_id, child.child_index = operation.id, index
                    children[index] = child
                failed = children[1]
                failed.status, failed.next_attempt_at = "failed", None
                failed.finished_at = datetime.now(UTC)
                failed.terminal_error = {
                    "code": "call_timeout",
                    "message": "Deadline exceeded",
                    "retryable": False,
                }
                await session.commit()
            async with db.session() as session:
                parent_view = await detail(session, parent.id)
                assert parent_view.parent is None
                assert parent_view.workspace_version_id == config.initial_workspace_version_id
                assert [child.id for child in parent_view.children] == [
                    children[0].id,
                    children[1].id,
                ]
                assert [child.name for child in parent_view.children] == ["Code", "Tests"]
                assert parent_view.children[1].terminal_error.code == "call_timeout"
                assert parent_view.operations[1].tool_name == "delegate"
                assert parent_view.operations[1].attempts == []
                for index, child in children.items():
                    view = await detail(session, child.id)
                    assert view.parent.run_id == parent.id
                    assert view.parent.conversation_id == parent.conversation_id
                    assert view.parent.operation_id == operation.id
                    assert view.parent.name == ("Code", "Tests")[index]
                    assert view.children == []
                    assert (
                        str(view.workspace_version_id)
                        == child.config["initial_workspace_version_id"]
                    )
                    chat = await conversation_detail(session, child.conversation_id)
                    assert chat.runs[0] == view
                assert [run.id for run in await list_summaries(session)] == [parent.id]
                assert [chat.id for chat in await list_conversations(session)] == [
                    parent.conversation_id
                ]

    asyncio.run(exercise())
