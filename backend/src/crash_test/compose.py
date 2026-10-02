"""Start only the controller and dashboard in a separate Compose project."""

import os
import subprocess
from urllib.parse import urlsplit

from crash_test.host import controller_environment


def start():
    environment = os.environ | controller_environment()
    control = urlsplit(environment["CRASH_TEST_CONTROL_URL"])
    if control.hostname in {"localhost", "127.0.0.1", "::1"}:
        port = f":{control.port}" if control.port else ""
        control = control._replace(netloc=f"host.docker.internal{port}")
    environment["CRASH_TEST_CONTROL_URL"] = control.geturl()
    endpoint = (
        environment.get("DOCKER_HOST") if not environment.get("DOCKER_CONTEXT") else None
    ) or subprocess.check_output(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        text=True,
        timeout=5,
    ).strip()
    if not endpoint.startswith("unix://"):
        raise ValueError("Use a local Docker daemon for Crash Test")
    environment["DOCKER_SOCKET"] = endpoint.removeprefix("unix://")
    project = environment["CRASH_TEST_PROJECT"] + "-crash-test"
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "-f",
            "compose.crash-test-runner.yaml",
            "up",
            "--build",
            "--detach",
            "--wait",
            "--wait-timeout",
            "120",
        ],
        env=environment,
        check=True,
    )
    port = environment.get("CRASH_TEST_DASHBOARD_PORT", "3020")
    print(f"Crashboard: http://127.0.0.1:{port}/", flush=True)
    print(f"Running in Docker project {project}. Stop it from Docker Desktop.", flush=True)


if __name__ == "__main__":
    try:
        start()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
