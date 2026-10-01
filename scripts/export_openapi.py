"""Export contracts without starting services or contacting external systems."""

import json
from pathlib import Path

from durable_agent.control.app import app as control
from durable_agent.tools.app import app as tools

destination = Path(__file__).resolve().parents[1] / "contracts"
destination.mkdir(exist_ok=True)

for name, app in (("control", control), ("tools", tools)):
    (destination / f"{name}.openapi.json").write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    )
