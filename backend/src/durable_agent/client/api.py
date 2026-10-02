"""The client submits work; the server owns its lifetime and retries."""

import asyncio
from types import TracebackType
from uuid import UUID

import httpx

from durable_agent.contracts import RunDetail, RunSummary, SubmitRunRequest


class ApiError(Exception):
    """A non-success HTTP response. Transport errors remain httpx exceptions."""

    def __init__(self, response: httpx.Response):
        self.status_code = response.status_code
        self.code = "http_error"
        self.message = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = None
        self.details = payload
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            self.code = payload["error"].get("code", self.code)
            self.message = payload["error"].get("message", self.message)
        super().__init__(f"{self.status_code} {self.code}: {self.message}")


class RunFailed(Exception):
    """A terminal run failure, including the server's persisted error."""

    def __init__(self, run: RunDetail):
        self.run_id = run.id
        self.error = run.terminal_error
        super().__init__(self.error.message if self.error else f"Run {run.id} failed")


class Client:
    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        request_timeout: float = 30,
    ):
        self._base_url = base_url.rstrip("/")
        self._http = http_client if http_client is not None else httpx.AsyncClient()
        self._owns_http = http_client is None
        self._request_timeout = request_timeout
        self.runs = Runs(self)

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self._http.request(
            method, f"{self._base_url}{path}", timeout=self._request_timeout, **kwargs
        )
        if not response.is_success:
            raise ApiError(response)
        return response


class Runs:
    def __init__(self, client: Client):
        self._client = client

    async def start(self, *, prompt: str, submission_key: UUID) -> "RunHandle":
        """Reuse submission_key after a lost response; changed input conflicts."""
        request = SubmitRunRequest(prompt=prompt, submission_key=submission_key)
        response = await self._client._request(
            "POST", "/api/runs", json=request.model_dump(mode="json", exclude_unset=True)
        )
        summary = RunSummary.model_validate(response.json())
        return RunHandle(self._client, summary.id)

    async def get(self, run_id: UUID) -> "RunHandle":
        handle = RunHandle(self._client, UUID(str(run_id)))
        await handle.status()
        return handle


class RunHandle:
    def __init__(self, client: Client, run_id: UUID):
        self._client = client
        self.id = run_id

    async def status(self) -> RunDetail:
        response = await self._client._request("GET", f"/api/runs/{self.id}")
        return RunDetail.model_validate(response.json())

    async def result(self, *, timeout: float | None = None, poll_interval: float = 1) -> str | None:
        """Wait locally; timeout or cancellation never stops the server's run."""
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        async with asyncio.timeout(timeout):
            while True:
                run = await self.status()
                if run.status == "succeeded":
                    return run.final_answer
                if run.status == "failed":
                    raise RunFailed(run)
                await asyncio.sleep(poll_interval)
