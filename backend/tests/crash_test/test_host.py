import os
import stat

import pytest
from crash_test.host import ensure_token, serve


def test_startup_creates_a_private_stable_token_without_printing_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CRASH_TEST_STATE_DIR", str(tmp_path))
    token_file = ensure_token()
    token = token_file.read_text()
    assert len(token) == 64 and token not in capsys.readouterr().out
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert ensure_token().read_text() == token


@pytest.mark.parametrize("local", [True, False])
def test_discovery_prefers_current_checkout_then_primary(tmp_path, monkeypatch, local):
    repo, checkout = tmp_path / "primary", tmp_path / "worktree"
    environment = {"CRASH_TEST_STATE_DIR": str(tmp_path), "CRASH_TEST_PORT": "9050"}
    calls, servers = [], []
    monkeypatch.setattr("crash_test.host.os.environ", environment)
    monkeypatch.setattr("crash_test.host.uvicorn.run", lambda *a, **kw: servers.append(kw))

    def output(command, **kwargs):
        if command[0] == "git":
            return str(checkout if "--show-toplevel" in command else repo / ".git")
        selector = command[command.index("--filter") + 1]
        calls.append(selector)
        return "durable-agent\n" if local or str(repo) in selector else ""

    monkeypatch.setattr("crash_test.host.subprocess.check_output", output)
    serve()
    assert environment["CRASH_TEST_COMPOSE_DIRECTORY"] == str(checkout if local else repo)
    assert environment["CRASH_TEST_PROJECT"] == "durable-agent"
    assert len(calls) == (1 if local else 2)
    assert servers == [{"host": "0.0.0.0", "port": 9050}]


@pytest.mark.parametrize("project", [None, "missing", "selected"])
def test_explicit_checkout_requires_an_unambiguous_existing_project(tmp_path, monkeypatch, project):
    environment = {
        "CRASH_TEST_STATE_DIR": str(tmp_path / "state"),
        "CRASH_TEST_COMPOSE_DIRECTORY": str(tmp_path),
    }
    if project:
        environment["CRASH_TEST_PROJECT"] = project
    monkeypatch.setattr("crash_test.host.os.environ", environment)
    monkeypatch.setattr("crash_test.host.repository", lambda directory: tmp_path)
    monkeypatch.setattr(
        "crash_test.host.installed_projects", lambda directory: {"selected", "other"}
    )
    servers = []
    monkeypatch.setattr("crash_test.host.uvicorn.run", lambda *a, **kw: servers.append(kw))
    if project == "selected":
        serve()
        assert os.environ["CRASH_TEST_PROJECT"] == "selected" and len(servers) == 1
    else:
        with pytest.raises(ValueError, match="Select an existing project"):
            serve()
        assert not servers and not (tmp_path / "state").exists()


def test_foreign_repository_is_rejected_before_docker_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASH_TEST_COMPOSE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("crash_test.host.repository", lambda directory: directory)
    monkeypatch.setattr(
        "crash_test.host.installed_projects",
        lambda directory: pytest.fail("Queried foreign Docker"),
    )
    with pytest.raises(ValueError, match="must belong to this Git repository"):
        serve()


def test_missing_app_explains_startup_without_creating_a_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASH_TEST_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CRASH_TEST_COMPOSE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("crash_test.host.repository", lambda directory: tmp_path)
    monkeypatch.setattr("crash_test.host.installed_projects", lambda directory: set())
    with pytest.raises(ValueError, match="CRASH_TEST=1 ./scripts/dev-up.sh"):
        serve()
    assert not (tmp_path / "state").exists()
