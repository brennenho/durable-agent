import json
from pathlib import Path

import pytest
from durable_agent.control.app import app as control
from durable_agent.tools.app import app as tools

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name,app", [("control", control), ("tools", tools)])
def test_generated_openapi_matches_the_application(name, app):
    saved = json.loads((ROOT / "contracts" / f"{name}.openapi.json").read_text())
    assert saved == app.openapi()


@pytest.mark.parametrize(
    "app,path",
    [(control, "/api/runs/{run_id}/file"), (tools, "/tools/workspace-versions/{version_id}/file")],
)
def test_download_contracts_describe_bytes_not_json(app, path):
    success = app.openapi()["paths"][path]["get"]["responses"]["200"]
    assert success["content"] == {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
    }


def test_claim_contract_distinguishes_idle_from_unavailable():
    responses = control.openapi()["paths"]["/internal/claims"]["post"]["responses"]
    schema = responses["200"]["content"]["application/json"]["schema"]
    assert {"type": "null"} in schema["anyOf"]
    assert "503" in responses
