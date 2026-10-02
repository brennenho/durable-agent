import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def startup(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    shutil.copytree(ROOT / "scripts", checkout / "scripts")
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"
    uv = shutil.which("uv")
    assert uv is not None
    stub = (
        f"#!{sys.executable}\n"
        + f"""
import json, os, sys
from pathlib import Path

if "--env-file" in sys.argv:
    os.execv({uv!r}, sys.argv)
with open(os.environ["STARTUP_LOG"], "a") as output:
    output.write(json.dumps({{"args": sys.argv[1:], "mode": os.getenv("CRASH_TEST_MODE"),
        "port": os.getenv("CRASH_TEST_PORT"), "url": os.getenv("CONTROL_URL"),
        "literal": os.getenv("LITERAL")}}) + "\\n")
if "-c" in sys.argv:
    state = Path(os.environ["CRASH_TEST_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    (state / "token").write_text("test-token")
elif sys.argv[1:3] == ["image", "inspect"]:
    print("sha256:test")
elif sys.argv[1:3] == ["context", "inspect"]:
    print("unix:///test/docker.sock")
"""
    )
    for name in ["uv", "docker"]:
        executable = commands / name
        executable.write_text(stub)
        executable.chmod(0o755)
    environment = {
        "PATH": f"{commands}:{os.environ['PATH']}",
        "HOME": os.environ["HOME"],
        "STARTUP_LOG": str(log),
        "UV_OFFLINE": "1",
    }

    def run(script, dotenv=None, **overrides):
        if dotenv is not None:
            (checkout / ".env").write_text(dotenv)
        result = subprocess.run(
            [str(checkout / "scripts" / script)],
            cwd=tmp_path,
            env=environment | overrides,
            capture_output=True,
            text=True,
            timeout=20,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return run


@pytest.mark.parametrize("override", [None, "0"])
def test_dev_up_reads_dotenv_before_selecting_hooks(startup, override):
    result, calls = startup(
        "dev-up.sh",
        "CRASH_TEST=1\nCONTROL_URL='http://localhost:8010'\nLITERAL='$(exit 71)'\n",
        **({"CRASH_TEST": override} if override is not None else {}),
    )
    assert result.returncode == 0, result.stderr
    compose = [call for call in calls if call["args"][0] == "compose"]
    assert len(compose) == 2
    assert all(
        ("compose.crash-test.yaml" in call["args"]) == (override is None) for call in compose
    )
    assert all(call["url"] == "http://localhost:8010" for call in calls)
    assert all(call["literal"] == "$(exit 71)" for call in calls)


def test_dev_up_without_dotenv_keeps_hooks_disabled(startup):
    result, calls = startup("dev-up.sh")
    assert result.returncode == 0, result.stderr
    assert all("compose.crash-test.yaml" not in call["args"] for call in calls)


def test_crashboard_reads_mode_and_port_from_dotenv(startup):
    result, calls = startup("crash-test.sh", "CRASH_TEST_MODE=docker\nCRASH_TEST_PORT=9030\n")
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert calls[0]["args"][-2:] == ["-m", "crash_test.compose"]
    assert calls[0]["port"] == "9030"


def test_crashboard_shell_mode_overrides_dotenv(startup):
    result, calls = startup("crash-test.sh", "CRASH_TEST_MODE=invalid\n", CRASH_TEST_MODE="docker")
    assert result.returncode == 0, result.stderr
    assert calls[0]["args"][-2:] == ["-m", "crash_test.compose"]


def test_invalid_dotenv_mode_fails_before_starting_services(startup):
    result, calls = startup("crash-test.sh", "CRASH_TEST_MODE=invalid\n")
    assert result.returncode == 2
    assert "CRASH_TEST_MODE must be host or docker" in result.stderr
    assert calls == []
