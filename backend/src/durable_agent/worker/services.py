"""Worker-facing service calls. No database access or implicit retries."""

import asyncio
from uuid import UUID

import httpx
from pydantic import BaseModel

from durable_agent.contracts import (
    Assignment,
    ClaimRequest,
    ErrorResponse,
    ExecutionError,
    HeartbeatRequest,
    HeartbeatResponse,
    ReportRequest,
    ReportResponse,
    SuccessfulOutcome,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolResult,
)


class ServiceError(Exception):
    def __init__(self, error: ExecutionError, status_code: int | None = None):
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


class ServiceClient:
    def __init__(
        self, control_url: str, tools_url: str, *, http_client: httpx.AsyncClient | None = None
    ):
        self._control = control_url.rstrip("/")
        self._tools = tools_url.rstrip("/")
        self._http = http_client if http_client is not None else httpx.AsyncClient()

    async def close(self) -> None:
        await self._http.aclose()

    async def claim(self, worker_id: UUID) -> Assignment | None:
        body = await self._post(
            f"{self._control}/internal/claims", ClaimRequest(worker_id=worker_id)
        )
        return None if body is None else Assignment.model_validate(body)

    async def heartbeat(self, assignment: Assignment, worker_id: UUID) -> HeartbeatResponse:
        body = await self._post(
            f"{self._control}/internal/attempts/{assignment.attempt_id}/heartbeat",
            HeartbeatRequest(worker_id=worker_id, lease_generation=assignment.lease_generation),
        )
        return HeartbeatResponse.model_validate(body)

    async def report(self, assignment: Assignment, body: ReportRequest) -> ReportResponse:
        result = await self._post(
            f"{self._control}/internal/attempts/{assignment.attempt_id}/report", body
        )
        return ReportResponse.model_validate(result)

    async def execute_tool(
        self, assignment: Assignment, *, timeout_seconds: float
    ) -> SuccessfulOutcome:
        if assignment.operation.kind != "tool":
            raise ValueError("Expected a tool assignment")
        request = assignment.operation.request
        body = await self._post(
            f"{self._tools}/tools/execute",
            ToolExecuteRequest(
                operation_id=assignment.operation_id,
                input_version_id=request.input_version_id,
                name=request.call.name,
                arguments=request.call.arguments,
                lease_generation=assignment.lease_generation,
                deadline_at=assignment.deadline_at,
                sandbox=assignment.config.sandbox,
            ),
            timeout=timeout_seconds,
        )
        result = ToolExecuteResponse.model_validate(body)
        return SuccessfulOutcome(
            status="succeeded",
            result=ToolResult(
                kind="tool",
                call_id=request.call.call_id,
                output=result.output,
                workspace_version_id=result.workspace_version_id,
            ),
        )

    async def _post(self, url: str, body: BaseModel, *, timeout: float = 5):
        try:
            async with asyncio.timeout(timeout):
                response = await self._http.post(
                    url, json=body.model_dump(mode="json"), timeout=timeout
                )
        except (TimeoutError, httpx.TransportError) as exc:
            raise ServiceError(
                ExecutionError(
                    code="service_unavailable",
                    message=str(exc) or "Service request timed out",
                    retryable=True,
                )
            ) from exc
        if not response.is_success:
            try:
                error = ErrorResponse.model_validate_json(response.content).error
            except ValueError:
                error = ExecutionError(
                    code=f"service_http_{response.status_code}",
                    message=f"Service returned HTTP {response.status_code}",
                    retryable=response.status_code in {408, 429} or response.status_code >= 500,
                )
            raise ServiceError(error, response.status_code)
        return response.json()
