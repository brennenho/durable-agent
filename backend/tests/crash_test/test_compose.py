import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from crash_test.compose import start

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8010", "http://host.docker.internal:8010"),
        ("http://localhost:8000", "http://host.docker.internal:8000"),
        ("http://control.test:8000", "http://control.test:8000"),
    ],
)
def test_docker_startup_preserves_target_and_reaches_host_control(
    monkeypatch, capsys, url, expected
):
    environment = {
        "CRASH_TEST_PROJECT": "existing-app",
        "CRASH_TEST_COMPOSE_DIRECTORY": "/checkout",
        "CRASH_TEST_CONTROL_URL": url,
        "CRASH_TEST_HOOK_TOKEN": "private-token",
    }
    monkeypatch.setattr("crash_test.compose.controller_environment", lambda: environment)
    monkeypatch.setenv("DOCKER_HOST", "unix:///private/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    calls = []
    monkeypatch.setattr("crash_test.compose.subprocess.run", lambda *a, **kw: calls.append((a, kw)))
    start()
    (command,), options = calls[0]
    assert command[command.index("--project-name") + 1] == "existing-app-crash-test"
    assert "--detach" in command and "--wait" in command
    child = options["env"]
    assert child["CRASH_TEST_PROJECT"] == "existing-app"
    assert child["CRASH_TEST_COMPOSE_DIRECTORY"] == "/checkout"
    assert child["CRASH_TEST_CONTROL_URL"] == expected
    assert child["DOCKER_SOCKET"] == "/private/docker.sock"
    assert "private-token" not in capsys.readouterr().out


def test_failed_startup_does_not_announce_a_ready_dashboard(monkeypatch, capsys):
    monkeypatch.setattr(
        "crash_test.compose.controller_environment",
        lambda: {
            "CRASH_TEST_PROJECT": "app",
            "CRASH_TEST_CONTROL_URL": "http://localhost:8000",
        },
    )
    monkeypatch.setenv("DOCKER_HOST", "unix:///docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("crash_test.compose.subprocess.run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        start()
    assert "Crashboard:" not in capsys.readouterr().out


def test_remote_context_is_rejected_even_with_a_local_docker_host(monkeypatch):
    monkeypatch.setattr(
        "crash_test.compose.controller_environment",
        lambda: {
            "CRASH_TEST_PROJECT": "app",
            "CRASH_TEST_CONTROL_URL": "http://localhost:8000",
        },
    )
    monkeypatch.setenv("DOCKER_HOST", "unix:///docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setattr(
        "crash_test.compose.subprocess.check_output", lambda *a, **kw: "ssh://remote"
    )
    monkeypatch.setattr(
        "crash_test.compose.subprocess.run", lambda *a, **kw: pytest.fail("Started Compose")
    )
    with pytest.raises(ValueError, match="local Docker daemon"):
        start()


def test_compose_contains_only_controls_and_limits_socket_access(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is required to validate Compose")
    environment = os.environ | {
        "CRASH_TEST_PROJECT": "existing-app",
        "CRASH_TEST_COMPOSE_DIRECTORY": str(ROOT),
        "CRASH_TEST_HOOK_TOKEN": "t" * 32,
        "CRASH_TEST_CONTROL_URL": "http://host.docker.internal:8000",
        "CRASH_TEST_STATE_DIR": str(tmp_path),
        "DOCKER_SOCKET": "/private/docker.sock",
    }
    configuration = json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "compose.crash-test-runner.yaml"),
                "config",
                "--format",
                "json",
            ],
            env=environment,
            text=True,
            timeout=10,
        )
    )
    services = configuration["services"]
    assert set(services) == {"controller", "dashboard"}
    assert services["controller"]["environment"]["CRASH_TEST_PROJECT"] == "existing-app"
    assert "/var/run/docker.sock" in [v["target"] for v in services["controller"]["volumes"]]
    assert [v["target"] for v in services["dashboard"]["volumes"]] == ["/state"]
    assert services["dashboard"]["volumes"][0]["read_only"]
    assert services["dashboard"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["dashboard"]["build"]["target"] == "crash-dashboard"
    assert all("OPENAI_API_KEY" not in service["environment"] for service in services.values())
