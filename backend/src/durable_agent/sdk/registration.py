"""Explicit registration metadata; decorators never execute user code."""

import importlib.util
import inspect
from dataclasses import dataclass
from typing import Annotated, get_type_hints

from pydantic import ConfigDict, Field, TypeAdapter, create_model

from durable_agent.sdk.tool_context import ToolContext
from durable_agent.sdk.types import Name, ToolDefinition

BUILTIN_TOOLS = frozenset({"delegate", "list_files", "read_file", "write_file", "run_command"})


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    version: str
    agent_type: type


def _identity(name: str, version: str) -> None:
    TypeAdapter(Name).validate_python(name)
    TypeAdapter(Annotated[str, Field(min_length=1)]).validate_python(version)


def agent(*, name: str, version: str):
    """Register a class with synchronous start(ctx) and advance(ctx, result) methods."""
    _identity(name, version)

    def register(cls):
        if not inspect.isclass(cls):
            raise TypeError("@agent requires a class")
        if "__durable_agent__" in vars(cls):
            raise ValueError("Agent is already registered")
        for method_name, count in (("start", 2), ("advance", 3)):
            method = inspect.getattr_static(cls, method_name, None)
            if (
                not inspect.isfunction(method)
                or inspect.iscoroutinefunction(method)
                or inspect.isgeneratorfunction(method)
                or inspect.isasyncgenfunction(method)
            ):
                raise TypeError(f"{method_name} must be a synchronous instance method")
            parameters = list(inspect.signature(method).parameters.values())
            if len(parameters) != count or any(
                p.kind not in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in parameters
            ):
                raise TypeError(f"{method_name} must accept {count - 1} positional arguments")
        try:
            inspect.signature(cls).bind()
        except TypeError as exc:
            raise TypeError("Agent must be constructible without arguments") from exc
        cls.__durable_agent__ = AgentDefinition(name, version, cls)
        return cls

    return register


def _entrypoint(function) -> str:
    module, qualname = function.__module__, function.__qualname__
    if module == "__main__" or not all(part.isidentifier() for part in qualname.split(".")):
        raise ValueError("Tool must have an importable module-level entrypoint")
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError) as exc:
        raise ValueError(f"Tool module {module!r} is not importable") from exc
    if spec is None:
        raise ValueError(f"Tool module {module!r} is not importable")
    return f"{module}:{qualname}"


def tool(*, name: str, version: str):
    """Describe an async tool without wrapping it or adding it to a global registry."""
    _identity(name, version)
    if name in BUILTIN_TOOLS:
        raise ValueError(f"Tool name {name!r} is reserved")

    def register(function):
        if not inspect.isfunction(function) or not inspect.iscoroutinefunction(function):
            raise TypeError("@tool requires an async function")
        if "__durable_tool__" in vars(function):
            raise ValueError("Tool is already registered")
        entrypoint = _entrypoint(function)
        hints = get_type_hints(function, include_extras=True)
        fields = {}
        takes_context = False
        for parameter in inspect.signature(function).parameters.values():
            if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
                raise TypeError(
                    "Tool parameters must be named arguments; variadics are unsupported"
                )
            annotation = hints.get(parameter.name)
            if annotation is None:
                raise TypeError(f"Tool parameter {parameter.name!r} needs a type annotation")
            if parameter.name == "ctx":
                if annotation is not ToolContext or parameter.default is not parameter.empty:
                    raise TypeError("ctx must be a required ToolContext parameter")
                takes_context = True
                continue
            if annotation is ToolContext or parameter.name.startswith("_"):
                raise TypeError(f"Unsupported tool parameter {parameter.name!r}")
            default = ... if parameter.default is parameter.empty else parameter.default
            fields[parameter.name] = (annotation, default)
        arguments = create_model(
            f"{name}Arguments",
            __config__=ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True),
            **fields,
        )
        definition = ToolDefinition(
            name=name,
            version=version,
            entrypoint=entrypoint,
            parameters=arguments.model_json_schema(),
            description=inspect.getdoc(function) or "",
            takes_context=takes_context,
        )
        function.__durable_tool__ = definition
        function.__durable_arguments__ = arguments
        return function

    return register
