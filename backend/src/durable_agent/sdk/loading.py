"""Load explicitly selected agents and tools from a deployed application."""

import importlib
import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from durable_agent.sdk.registration import AgentDefinition
from durable_agent.sdk.types import ToolDefinition


def _resolve(reference: str):
    module_name, separator, attribute = reference.partition(":")
    if (
        not separator
        or not all(part.isidentifier() for part in module_name.split("."))
        or not all(part.isidentifier() for part in attribute.split("."))
    ):
        raise ValueError("Expected an import reference in module:attribute form")
    try:
        value = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"Could not import module {module_name!r}") from exc
    for part in attribute.split("."):
        try:
            value = getattr(value, part)
        except AttributeError as exc:
            raise ValueError(f"Import reference {reference!r} does not exist") from exc
    return value


@dataclass(frozen=True, init=False)
class AgentApp:
    agents: Mapping[str, AgentDefinition]
    tools: Mapping[str, ToolDefinition]

    def __init__(self, *, agents: Iterable[type] = (), tools: Iterable[Callable] = ()):
        agent_definitions = {}
        for cls in agents:
            definition = vars(cls).get("__durable_agent__") if inspect.isclass(cls) else None
            if not isinstance(definition, AgentDefinition) or definition.agent_type is not cls:
                raise TypeError("Every agent must be explicitly decorated with @agent")
            if definition.name in agent_definitions:
                raise ValueError(f"Duplicate agent name {definition.name!r}")
            agent_definitions[definition.name] = definition

        tool_definitions = {}
        for function in tools:
            definition = getattr(function, "__durable_tool__", None)
            if not inspect.iscoroutinefunction(function) or not isinstance(
                definition, ToolDefinition
            ):
                raise TypeError("Every tool must be explicitly decorated with @tool")
            if definition.name in tool_definitions:
                raise ValueError(f"Duplicate tool name {definition.name!r}")
            if _resolve(definition.entrypoint) is not function:
                raise ValueError(
                    f"Tool entrypoint {definition.entrypoint!r} changed after registration"
                )
            tool_definitions[definition.name] = definition.model_copy(deep=True)

        object.__setattr__(self, "agents", MappingProxyType(agent_definitions))
        object.__setattr__(self, "tools", MappingProxyType(tool_definitions))


def load_app(reference: str) -> AgentApp:
    """Import an app object; factories and automatic discovery are unsupported."""
    app = _resolve(reference)
    if not isinstance(app, AgentApp):
        raise TypeError(f"{reference!r} must refer to an AgentApp instance")
    return app
