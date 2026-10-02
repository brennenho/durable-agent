"""The client submits work; the server owns its lifetime and retries."""

import asyncio
from types import TracebackType
from uuid import UUID

import httpx
from pydantic import JsonValue

from durable_agent.contracts import (
    ChildRunSummary,
    ConversationSummary,
    OperationInspection,
    OperationView,
    RunDetail,
    RunSummary,
    SubmitFollowUpRequest,
    SubmitForkRequest,
    SubmitRunRequest,
)

from .files import RunFiles


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


class _Unset:
    pass


_UNSET = _Unset()


def _input_fields(prompt: str | None, input: JsonValue | _Unset, agent: str | None = None) -> dict:
    if prompt is not None and input is not _UNSET:
        raise ValueError("Supply either prompt or input")
    fields = {}
    if prompt is not None:
        fields["prompt"] = prompt
    if input is not _UNSET:
        fields["input"] = input
    if agent is not None:
        fields["agent"] = agent
    return fields


class Runs:
    def __init__(self, client: Client):
        self._client = client

    async def list(self) -> list[RunSummary]:
        response = await self._client._request("GET", "/api/runs")
        return [RunSummary.model_validate(item) for item in response.json()]

    async def start(
        self,
        *,
        submission_key: UUID,
        agent: str | None = None,
        input: JsonValue | _Unset = _UNSET,
        prompt: str | None = None,
    ) -> "RunHandle":
        """Submit a named agent's JSON input, or a legacy prompt; never both."""
        request = SubmitRunRequest(
            submission_key=submission_key, **_input_fields(prompt, input, agent)
        )
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
        self.files = RunFiles(client, run_id)

    async def status(self) -> RunDetail:
        response = await self._client._request("GET", f"/api/runs/{self.id}")
        return RunDetail.model_validate(response.json())

    async def pause(self) -> RunSummary:
        """Prevent new claims; the in-progress call may still complete."""
        response = await self._client._request("POST", f"/api/runs/{self.id}/pause")
        return RunSummary.model_validate(response.json())

    async def resume(self) -> RunSummary:
        """Continue the same pending operation and retry budget."""
        response = await self._client._request("POST", f"/api/runs/{self.id}/resume")
        return RunSummary.model_validate(response.json())

    async def follow_up(
        self, *, submission_key: UUID, input: JsonValue | _Unset = _UNSET, prompt: str | None = None
    ) -> "RunHandle":
        """Continue after this run; an outdated predecessor remains a server conflict."""
        request = SubmitFollowUpRequest(
            submission_key=submission_key, after_run_id=self.id, **_input_fields(prompt, input)
        )
        conversation_id = (await self.status()).conversation_id
        if conversation_id is None:
            raise RuntimeError("Server must expose conversation_id to submit a follow-up")
        response = await self._client._request(
            "POST",
            f"/api/conversations/{conversation_id}/runs",
            json=request.model_dump(mode="json", exclude_unset=True),
        )
        summary = RunSummary.model_validate(response.json())
        return RunHandle(self._client, summary.id)

    async def fork(
        self,
        *,
        after_operation_id: UUID,
        submission_key: UUID,
        input: JsonValue | _Unset = _UNSET,
        prompt: str | None = None,
    ) -> "RunHandle":
        """Start a new conversation from an operation with an accepted checkpoint."""
        request = SubmitForkRequest(
            after_operation_id=after_operation_id,
            submission_key=submission_key,
            **_input_fields(prompt, input),
        )
        response = await self._client._request(
            "POST",
            f"/api/runs/{self.id}/forks",
            json=request.model_dump(mode="json", exclude_unset=True),
        )
        conversation = ConversationSummary.model_validate(response.json())
        return RunHandle(self._client, conversation.latest_run_id)

    async def history(self) -> list[OperationView]:
        """Accepted results and execution attempts in operation order."""
        return (await self.status()).operations

    async def children(self) -> list[ChildRunSummary]:
        return (await self.status()).children

    async def inspect(self, operation_id: UUID) -> OperationInspection:
        response = await self._client._request(
            "GET", f"/api/runs/{self.id}/operations/{UUID(str(operation_id))}"
        )
        return OperationInspection.model_validate(response.json())

    async def result(self, *, timeout: float | None = None, poll_interval: float = 1) -> JsonValue:
        """Wait locally; timeout or cancellation never stops the server's run."""
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        async with asyncio.timeout(timeout):
            while True:
                run = await self.status()
                if run.status == "succeeded":
                    return run.output if run.agent is not None else run.final_answer
                if run.status == "failed":
                    raise RunFailed(run)
                await asyncio.sleep(poll_interval)
