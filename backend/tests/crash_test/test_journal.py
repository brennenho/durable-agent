import asyncio
import json

import httpx
import pytest
from crash_test.contracts import ContainerAction
from crash_test.controller import Journal
from crash_test.room import ControlRoom

from .test_runner import Docker


def seed_history(journal, count):
    journal.events = [
        {"id": str(index), "kind": "autoscaling", "label": "Earlier setting"}
        for index in range(count)
    ]
    journal.path.write_text("".join(json.dumps(event) + "\n" for event in journal.events))


@pytest.mark.parametrize("previous_events", [1999, 2000])
def test_control_actions_keep_recording_after_snapshot_fills(tmp_path, previous_events):
    async def exercise():
        docker = Docker()
        docker.project = "crash-test-unit"
        async with httpx.AsyncClient() as client:
            room = ControlRoom(docker, client, tmp_path)
            seed_history(room.journal, previous_events)
            target = docker.containers["worker"]
            stopped = await room.action(
                ContainerAction(id=target.id, started_at=target.started_at, action="kill")
            )
            assert not stopped.running
            assert room.journal.events[-1]["kind"] == "kill_observed"
            started = await room.action(
                ContainerAction(id=stopped.id, started_at=stopped.started_at, action="start")
            )
            assert started.running
            assert await room.set_autoscaling(False) == {"enabled": False}
            assert not room.autoscaling_enabled

            saved = [json.loads(line) for line in room.journal.path.read_text().splitlines()]
            assert len(saved) == previous_events + 5
            assert saved[0]["id"] == "0"
            assert [event["kind"] for event in saved[-5:]] == [
                "manual_action",
                "kill_observed",
                "manual_action",
                "restart",
                "autoscaling",
            ]
            snapshot = await room.snapshot()
            assert len(snapshot["controls"]) == 2000
            assert snapshot["controls"] == saved[-2000:]

    asyncio.run(exercise())


def test_scenario_journal_still_rejects_observation_overflow(tmp_path):
    journal = Journal(tmp_path / "scenario.jsonl")
    seed_history(journal, 2000)
    original_events = journal.events.copy()
    original_file = journal.path.read_bytes()

    with pytest.raises(RuntimeError, match="Scenario observation limit exceeded"):
        asyncio.run(journal.record("hook", "tools.after_commit"))

    assert journal.events == original_events
    assert journal.path.read_bytes() == original_file


def test_failed_append_preserves_control_snapshot(tmp_path):
    journal = Journal(tmp_path / "controls.jsonl", rolling=True)
    seed_history(journal, 2000)
    original_events = journal.events.copy()
    journal.path = tmp_path / "blocked" / "controls.jsonl"
    journal.path.parent.write_text("Not a directory")

    with pytest.raises(OSError):
        asyncio.run(journal.record("autoscaling", "Autoscaling paused"))

    assert journal.events == original_events
