"""Opt-in image verification; no services, credentials, or model calls."""

import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_SDK_BACKEND_IMAGE") and os.environ.get("TEST_SDK_SANDBOX_IMAGE")),
    reason="Set TEST_SDK_BACKEND_IMAGE and TEST_SDK_SANDBOX_IMAGE to the built example images",
)

SCRIPT = """
import asyncio, importlib, importlib.util, json, os, sys, time
from pathlib import Path
from uuid import uuid4
import durable_agent
import sdk_agent.app
from durable_agent import ToolContext, Workspace, load_app

async def main():
    assert os.getuid() == 1000
    assert not Path('/proc/net/route').read_text().splitlines()[1:]
    assert not Path('/var/run/docker.sock').exists()
    assert not {'OPENAI_API_KEY', 'DATABASE_URL', 'CRASH_TEST_HOOK_TOKEN'} & os.environ.keys()
    assert 'site-packages' in durable_agent.__file__
    assert 'site-packages' in sdk_agent.app.__file__
    app = load_app('sdk_agent.app:app')
    definition = app.tools['save_document']
    module, name = definition.entrypoint.split(':')
    function = getattr(importlib.import_module(module), name)
    assert function.__durable_tool__ == definition
    ctx = ToolContext(
        uuid4(), uuid4(), uuid4(), Workspace(Path('/workspace')), time.monotonic() + 10
    )
    result = await function(ctx=ctx, title='Release notes', body='Saved in a private workspace.')
    content = await ctx.workspace.read_text(result['path'])
    assert content == '# Release notes\\n\\nSaved in a private workspace.\\n'
    services = ['fastapi', 'sqlalchemy', 'openai', 'psycopg']
    assert not any(name in sys.modules for name in services)
    print(json.dumps({
        'build_id': os.environ['AGENT_BUILD_ID'],
        'tool': definition.model_dump(mode='json'),
        'result': result,
        'service_dependencies': [name for name in services if importlib.util.find_spec(name)],
    }))
asyncio.run(main())
"""


def execute(image: str) -> dict:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=1000:1000",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs=/workspace:rw,nosuid,nodev,uid=1000,gid=1000,mode=700",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--workdir=/workspace",
            "--entrypoint=python",
            image,
            "-I",
            "-c",
            SCRIPT,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_installed_app_and_tool_match_in_backend_and_private_sandbox():
    backend = execute(os.environ["TEST_SDK_BACKEND_IMAGE"])
    sandbox = execute(os.environ["TEST_SDK_SANDBOX_IMAGE"])
    assert backend.pop("service_dependencies") == ["fastapi", "sqlalchemy", "openai", "psycopg"]
    assert sandbox.pop("service_dependencies") == []
    assert backend == sandbox
    assert backend["build_id"]
