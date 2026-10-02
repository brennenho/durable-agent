"""Reads identify an accepted workspace version before fetching bytes."""

from typing import TYPE_CHECKING
from uuid import UUID

from durable_agent.contracts import RunFileListing

if TYPE_CHECKING:
    from .api import Client


class RunFiles:
    def __init__(self, client: "Client", run_id: UUID):
        self._client = client
        self._run_id = run_id

    async def list(self, *, version_id: UUID | None = None) -> RunFileListing:
        params = {"version_id": str(version_id)} if version_id is not None else {}
        response = await self._client._request(
            "GET", f"/api/runs/{self._run_id}/files", params=params
        )
        return RunFileListing.model_validate(response.json())

    async def read(self, path: str, *, version_id: UUID | None = None) -> bytes:
        """Omitting version selects the latest accepted workspace at read start."""
        if version_id is None:
            version_id = (await self.list()).workspace_version_id
        response = await self._client._request(
            "GET",
            f"/api/runs/{self._run_id}/file",
            params={"version_id": str(version_id), "path": path},
        )
        return response.content
