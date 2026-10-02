"""One bounded Responses API request per assignment; Control owns retries."""

import asyncio
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from json import JSONDecodeError

import httpx
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI

from durable_agent.contracts import (
    AgentConfig,
    AttemptOutcome,
    ExecutionError,
    FailedOutcome,
    ModelRequest,
)
from durable_agent.worker.context import InvalidModelInput, response_input
from durable_agent.worker.responses import map_response


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class OpenAIClient:
    def __init__(self, api_key: str, *, http_client: httpx.AsyncClient | None = None):
        self._client = AsyncOpenAI(api_key=api_key, max_retries=0, http_client=http_client)

    async def close(self) -> None:
        await self._client.close()

    async def execute(
        self, config: AgentConfig, request: ModelRequest, *, timeout_seconds: float
    ) -> AttemptOutcome:
        parameters = (
            request.model_params if request.model_params is not None else config.model_params
        )
        reserved = {
            "model",
            "input",
            "tools",
            "stream",
            "background",
            "store",
            "include",
            "timeout",
            "previous_response_id",
            "conversation",
            "extra_body",
            "extra_headers",
            "extra_query",
        }
        if reserved.intersection(parameters):
            return FailedOutcome(
                status="failed",
                error=ExecutionError(
                    code="invalid_model_config",
                    message="Model parameters override execution fields",
                    retryable=False,
                ),
            )
        request_id = None
        retry_after = None
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._client.responses.create(
                    **parameters,
                    model=request.model if request.model is not None else config.model,
                    input=request.explicit_input
                    if request.explicit_input is not None
                    else response_input(request),
                    tools=request.tool_schemas
                    if request.tool_schemas is not None
                    else config.tool_schemas,
                    stream=False,
                    background=False,
                    store=False,
                    include=["reasoning.encrypted_content"],
                    timeout=timeout_seconds,
                )
            return map_response(response)
        except TimeoutError:
            code, message, retryable = "call_timeout", "Model call exceeded its deadline", True
        except APIStatusError as exc:
            code, message = f"openai_http_{exc.status_code}", str(exc)
            request_id = exc.request_id
            retryable = exc.status_code in {408, 409, 429} or exc.status_code >= 500
            retry_after = retry_after_seconds(exc.response.headers.get("retry-after"))
        except APIConnectionError as exc:
            code, message, retryable = "openai_connection_error", str(exc), True
        except (APIError, JSONDecodeError) as exc:
            code, message, retryable = "invalid_model_response", str(exc), False
        except InvalidModelInput as exc:
            code, message, retryable = "invalid_model_input", str(exc), False
        except (TypeError, ValueError) as exc:
            code, message, retryable = "invalid_model_config", str(exc), False
        return FailedOutcome(
            status="failed",
            error=ExecutionError(
                code=code, message=message, retryable=retryable, retry_after_seconds=retry_after
            ),
            provider_request_id=request_id,
        )
