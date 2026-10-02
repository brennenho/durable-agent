import asyncio

from durable_agent.contracts import FailedOutcome, ModelResult
from durable_agent.control.coordinator import REPORT_LIMIT, advance_delegations
from durable_agent.control.runtime import delegation_loop
from durable_agent.control.views import detail
from durable_agent.crash_test import HookInterrupted
from durable_agent.db.models import Run
from sqlalchemy import select

from .test_delegation import waiting
from .test_leases import claimed
from .test_pause import pause
from .test_reports import report, report_body


async def coordinate(db, *, commit=True, limit=100):
    async with db.session() as session:
        events = await advance_delegations(session, limit)
        if commit:
            await session.commit()
        return events


async def finish_children(db):
    for _ in range(2):
        assignment = await claimed(db)
        async with db.session() as session:
            child = await session.get(Run, assignment.run_id)
            index = child.child_index
        body = await report_body(db, assignment)
        if index == 0:
            body.outcome.result = ModelResult(
                kind="model",
                native_context=[],
                output={"status": "completed", "text": "A" * (REPORT_LIMIT + 1), "tool_calls": []},
            )
        else:
            body.outcome = FailedOutcome(
                status="failed",
                error={"code": "invalid_model", "message": "Cannot continue", "retryable": False},
            )
        await report(db, assignment, body)


def test_join_is_atomic_ordered_and_accepts_once_after_concurrent_scans(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            events = await coordinate(db)
            operation_id = events[0][2]
            assert events == [("control.after_children_commit", parent_id, operation_id)]
            assert await coordinate(db) == []
            await finish_children(db)
            await pause(db, parent_id)
            assert await coordinate(db) == []
            await pause(db, parent_id, False)
            assert await coordinate(db, commit=False) == [
                ("control.after_join_commit", parent_id, operation_id)
            ]
            async with db.session() as session:
                saved = await detail(session, parent_id)
                assert saved.status == "waiting" and saved.operations[-1].result is None
                child_ids = [child.id for child in saved.children]
            scans = await asyncio.gather(coordinate(db), coordinate(db))
            assert sorted(len(events) for events in scans) == [0, 1]
            async with db.session() as session:
                saved = await detail(session, parent_id)
                assert saved.status == "queued" and len(saved.operations) == 3
                joined = saved.operations[1]
                assert joined.attempts == []
                assert joined.result.workspace_version_id == config.initial_workspace_version_id
                results = joined.result.output.value["children"]
                assert [item["run_id"] for item in results] == [str(key) for key in child_ids]
                assert [item["name"] for item in results] == ["Code", "Tests"]
                assert results[0]["report"] == "A" * REPORT_LIMIT and results[0]["truncated"]
                assert results[1]["status"] == "failed" and results[1]["report"] is None
                assert results[1]["error"]["code"] == "invalid_model"
                assert not results[1]["truncated"]
            parent = await claimed(db)
            assert parent.run_id == parent_id
            assert parent.operation.request.history[-1] == joined.result
            await report(db, parent, await report_body(db, parent))
            assert await coordinate(db) == []

    asyncio.run(exercise())


def test_bounded_scan_skips_busy_batches(database, config):
    async def exercise():
        async with database() as db:
            first = await waiting(db, config.model_copy(deep=True))
            await coordinate(db)
            # Keep both older child attempts active while a newer parent delegates.
            assert (await claimed(db)).run_id != first
            assert (await claimed(db)).run_id != first
            second = await waiting(db, config.model_copy(deep=True))
            events = await coordinate(db, limit=1)
            assert len(events) == 1 and events[0][1] == second

    asyncio.run(exercise())


def test_coordinator_restart_rediscovers_work_and_hooks_run_outside_transactions(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            observed = []

            async def cycle(expected):
                reached = asyncio.Event()

                class Hooks:
                    async def hit(self, name, **scope):
                        assert name == expected
                        assert set(scope) == {"run_id", "operation_id"}
                        assert scope["run_id"] == parent_id
                        async with db.session() as session:
                            # A separate transaction can lock the row before the hook replies.
                            await session.scalar(
                                select(Run).where(Run.id == parent_id).with_for_update(nowait=True)
                            )
                        observed.append((name, scope["operation_id"]))
                        reached.set()
                        raise HookInterrupted("lost observer acknowledgment after commit")

                task = asyncio.create_task(delegation_loop(db, Hooks()))
                try:
                    async with asyncio.timeout(3):
                        await reached.wait()
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            await cycle("control.after_children_commit")
            await finish_children(db)
            await cycle("control.after_join_commit")
            assert observed[0][1] == observed[1][1]
            assert await coordinate(db) == []

    asyncio.run(exercise())


def test_coordinator_waits_through_database_outage_and_cancels_cleanly(database, monkeypatch):
    from durable_agent.crash_test import CrashTestHooks
    from sqlalchemy.exc import OperationalError

    async def exercise():
        reached = asyncio.Event()
        calls = 0

        async def recover(session, *, agents=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OperationalError("query", {}, OSError("offline"))
            reached.set()
            return []

        monkeypatch.setattr("durable_agent.control.runtime.advance_delegations", recover)
        async with database() as db:
            task = asyncio.create_task(delegation_loop(db, CrashTestHooks("control")))
            try:
                async with asyncio.timeout(3):
                    await reached.wait()
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            assert calls == 2

    asyncio.run(exercise())
