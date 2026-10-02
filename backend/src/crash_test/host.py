"""Internal controller startup used by the Crashboard launcher."""

import os
import secrets
import subprocess
from pathlib import Path

import uvicorn


def ensure_token() -> Path:
    root = Path(os.environ.get("CRASH_TEST_STATE_DIR", ".crash-test")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    token_file = root / "token"
    try:
        with open(token_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            file.write(secrets.token_hex(32))
    except FileExistsError:
        pass  # Development startup and Crashboard share the existing token.
    return token_file


def repository(directory: Path) -> Path:
    return Path(
        subprocess.check_output(
            [
                "git",
                "-C",
                str(directory),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            text=True,
            timeout=5,
        ).strip()
    ).parent


def installed_projects(directory: Path) -> set[str]:
    return set(
        subprocess.check_output(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project.working_dir={directory}",
                "--format",
                '{{.Label "com.docker.compose.project"}}',
            ],
            text=True,
            timeout=15,
        ).split()
    )


def serve():
    repo = repository(Path.cwd())
    selected_directory = os.environ.get("CRASH_TEST_COMPOSE_DIRECTORY")
    directory = (
        Path(selected_directory).resolve()
        if selected_directory
        else Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True, timeout=5
            ).strip()
        )
    )
    if repository(directory) != repo:
        raise ValueError("CRASH_TEST_COMPOSE_DIRECTORY must belong to this Git repository")
    projects = installed_projects(directory)
    if not projects and not selected_directory and directory != repo:
        directory = repo
        projects = installed_projects(directory)
    project = os.environ.get("CRASH_TEST_PROJECT") or (
        next(iter(projects)) if len(projects) == 1 else None
    )
    if not project or project not in projects:
        raise ValueError(
            "Select an existing project with CRASH_TEST_PROJECT and CRASH_TEST_COMPOSE_DIRECTORY"
        )
    token_file = ensure_token()
    os.environ.update(
        {
            "CRASH_TEST_PROJECT": project,
            "CRASH_TEST_COMPOSE_DIRECTORY": str(directory),
            "CRASH_TEST_HOOK_TOKEN": token_file.read_text().strip(),
            "CRASH_TEST_LOG_DIR": str(token_file.parent / "reports"),
            "CRASH_TEST_CONTROL_URL": os.environ.get("CONTROL_URL", "http://127.0.0.1:8000"),
        }
    )
    print(f"Crash Test controls {project} in {directory}.", flush=True)
    uvicorn.run(
        "crash_test.app:app", host="0.0.0.0", port=int(os.environ.get("CRASH_TEST_PORT", 9020))
    )


if __name__ == "__main__":
    serve()
