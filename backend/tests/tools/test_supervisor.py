import asyncio
import json
import shlex
import sys
import time
from pathlib import Path

import pytest
from durable_agent.tools import supervisor


def python(code):
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def test_nonzero_exit_retains_files_and_separate_logs(tmp_path):
    output = asyncio.run(
        supervisor.supervise(
            "printf saved > result; printf out; printf err >&2; exit 7",
            str(tmp_path),
            2,
            1024,
            deadline=time.time() + 10,
        )
    )
    assert output == {
        "status": "ok",
        "value": {
            "exit_code": 7,
            "stdout": "out",
            "stderr": "err",
            "truncated": False,
        },
    }
    assert (tmp_path / "result").read_text() == "saved"


@pytest.mark.parametrize(
    "code",
    [
        "import os; os.write(1, b'x' * 1000000); os.write(2, b'y' * 1000000)",
        "import os; os.write(1, b'\\xff' * 1000000)",
    ],
)
def test_output_is_drained_and_bounded_even_for_invalid_utf8(tmp_path, code):
    value = asyncio.run(
        supervisor.supervise(python(code), str(tmp_path), 5, 103, deadline=time.time() + 10)
    )["value"]
    assert value["exit_code"] == 0
    assert len((value["stdout"] + value["stderr"]).encode()) <= 103
    assert value["truncated"] is True


@pytest.mark.parametrize("outer_budget", [2, 0.05, 0.1])
def test_deadlines_kill_command_and_background_children(tmp_path, outer_budget):
    command = "(sleep 0.3; touch late) & wait"
    call = supervisor.supervise(
        command, str(tmp_path), 0.1, 1024, deadline=time.time() + outer_budget
    )
    if outer_budget <= 0.1:
        with pytest.raises(TimeoutError):
            asyncio.run(call)
    else:
        result = asyncio.run(call)
        assert result["status"] == "error"
        assert result["code"] == "command_timeout"
    asyncio.run(asyncio.sleep(0.4))
    assert not (tmp_path / "late").exists()


def test_normal_exit_stops_background_writers(tmp_path):
    output = asyncio.run(
        supervisor.supervise(
            "(sleep 0.3; touch late) & exit 0", str(tmp_path), 2, 1024, deadline=time.time() + 10
        )
    )
    assert output["value"]["exit_code"] == 0
    asyncio.run(asyncio.sleep(0.4))
    assert not (tmp_path / "late").exists()


def test_outer_deadline_during_timeout_cleanup_has_no_completion(tmp_path, monkeypatch):
    communicate = asyncio.subprocess.Process.communicate

    async def slow_cleanup(process, *args, **kwargs):
        result = await communicate(process, *args, **kwargs)
        await asyncio.sleep(0.3)
        return result

    monkeypatch.setattr(asyncio.subprocess.Process, "communicate", slow_cleanup)
    with pytest.raises(TimeoutError):
        asyncio.run(
            supervisor.supervise("sleep 5", str(tmp_path), 0.05, 100, deadline=time.time() + 0.2)
        )


def test_missing_cwd_is_a_completed_tool_error(tmp_path):
    result = asyncio.run(
        supervisor.supervise("true", str(tmp_path / "missing"), 1, 100, deadline=time.time() + 10)
    )
    assert result["status"] == "error"
    assert result["code"] == "invalid_cwd"


def test_standalone_protocol_distinguishes_command_exit_from_supervisor_failure(tmp_path):
    async def execute(command, outer_timeout, timeout=1):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(supervisor.__file__)),
            command,
            str(tmp_path),
            str(time.time() + outer_timeout),
            "100",
            str(timeout),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return process.returncode, stdout

    code, stdout = asyncio.run(execute("exit 137", 2))
    assert code == 0
    assert json.loads(stdout)["value"]["exit_code"] == 137
    code, stdout = asyncio.run(execute("sleep 10", 2, timeout=0.05))
    assert code == 0
    assert json.loads(stdout)["code"] == "command_timeout"
    code, stdout = asyncio.run(execute("sleep 10", 0.05))
    assert code != 0
    assert not stdout
    code, _ = asyncio.run(execute("touch too-late", -1))
    assert code != 0
    assert not (tmp_path / "too-late").exists()
