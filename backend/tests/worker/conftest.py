import json
from pathlib import Path

import pytest


@pytest.fixture
def response_payload():
    """Saved Responses API fixture; tests never call a live provider."""
    return json.loads((Path(__file__).parent / "fixtures/response.json").read_text())
