import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from durable_agent.contracts import AgentConfig, Assignment


@pytest.fixture
def response_payload():
    """Saved Responses API fixture; tests never call a live provider."""
    return json.loads((Path(__file__).parent / "fixtures/response.json").read_text())


@pytest.fixture
def config():
    return AgentConfig(
        model="test-model",
        model_params={"max_output_tokens": 4096},
        tool_schemas=[],
        initial_workspace_version_id="00000000-0000-0000-0000-000000000001",
        sandbox={"image_digest": "sha256:test", "workspace_max_bytes": 1000, "log_max_bytes": 100},
        max_attempts=5,
        max_turns=20,
        call_timeout_seconds=120,
    )


@pytest.fixture
def assignment(config):
    now = datetime.now(UTC)
    return Assignment(
        run_id=uuid4(),
        operation_id=uuid4(),
        attempt_id=uuid4(),
        attempt_no=1,
        lease_generation=2,
        lease_expires_at=now + timedelta(seconds=30),
        deadline_at=now + timedelta(seconds=120),
        server_time=now,
        config=config,
        operation={"kind": "model", "request": {"prompt": "Edit a file", "history": []}},
    )
