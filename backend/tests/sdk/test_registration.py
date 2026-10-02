import asyncio
import importlib.util
import sys

import pytest
from durable_agent.sdk.registration import BUILTIN_TOOLS, agent, tool
from durable_agent.sdk.types import Done
from pydantic import ValidationError


class Writer:
    def __init__(self):
        raise AssertionError("Registration must not instantiate agents")

    def start(self, ctx):
        return Done(output=ctx.input)

    def advance(self, ctx, result):
        return Done(output=result.value)


def test_agent_keeps_class_identity_and_does_not_register_subclasses():
    class Draft(Writer):
        pass

    registered = agent(name="writer", version="1")(Draft)
    assert registered is Draft
    assert registered.__durable_agent__.agent_type is Draft
    assert registered.__durable_agent__.name == "writer"
    assert registered.__durable_agent__.version == "1"

    class Child(Draft):
        pass

    assert "__durable_agent__" not in vars(Child)
    with pytest.raises(ValueError, match="already registered"):
        agent(name="again", version="1")(Draft)


@pytest.mark.parametrize(
    "source",
    [
        "async def start(self, ctx): pass",
        "def start(self, ctx): yield ctx",
        "def start(self, ctx, extra): pass",
        "def advance(self, *args): pass",
        "async def advance(self, ctx, result): pass",
        "def __init__(self, required): pass",
        "start = staticmethod(lambda ctx: None)",
        "advance = None",
    ],
)
def test_agent_rejects_callbacks_the_runtime_cannot_call(source):
    namespace = {}
    exec(source, namespace)
    invalid = type("Invalid", (Writer,), namespace)
    with pytest.raises(TypeError):
        agent(name="invalid", version="1")(invalid)
    assert "__durable_agent__" not in vars(invalid)


@pytest.fixture
def module(tmp_path, monkeypatch):
    source = tmp_path / "example_tools.py"
    source.write_text(
        "from durable_agent.sdk.tool_context import ToolContext\n"
        "from durable_agent.sdk.registration import tool\n"
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "@tool(name='greet', version='1')\n"
        "async def greet(ctx: ToolContext, name: str, *, count: Annotated[int, Field(ge=1)] = 1):\n"
        "    '''Write a greeting.'''\n"
        "    return name * count\n"
    )
    spec = importlib.util.spec_from_file_location("example_tools", source)
    imported = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, imported)
    spec.loader.exec_module(imported)
    return imported


def test_tool_schema_excludes_context_and_preserves_defaults_and_constraints(module):
    function = module.greet
    definition = function.__durable_tool__
    assert definition.entrypoint == "example_tools:greet"
    assert definition.description == "Write a greeting."
    assert definition.takes_context
    assert definition.parameters["required"] == ["name"]
    assert set(definition.parameters["properties"]) == {"name", "count"}
    assert definition.parameters["properties"]["count"]["default"] == 1
    assert definition.parameters["properties"]["count"]["minimum"] == 1
    assert definition.parameters["additionalProperties"] is False
    assert function.__durable_arguments__.model_validate({"name": "Hi"}).model_dump() == {
        "name": "Hi",
        "count": 1,
    }
    assert asyncio.run(function(None, "Hi", count=2)) == "HiHi"
    with pytest.raises(ValueError, match="already registered"):
        tool(name="again", version="1")(function)


@pytest.mark.parametrize("arguments", [{}, {"name": "Hi", "count": 0}, {"name": "Hi", "ctx": {}}])
def test_tool_arguments_reject_missing_invalid_and_extra_fields(module, arguments):
    with pytest.raises(ValidationError):
        module.greet.__durable_arguments__.model_validate(arguments)


def test_tool_keeps_function_identity_without_executing_it(module):
    exec("async def untouched(value: str): raise AssertionError('executed')", vars(module))
    original = module.untouched
    assert tool(name="untouched", version="2")(original) is original
    assert not original.__durable_tool__.takes_context


@pytest.mark.parametrize("module_name", ["__main__", "uninstalled_example_tools"])
def test_tool_rejects_unimportable_modules(module, module_name):
    exec("async def invalid(value: str): pass", vars(module))
    module.invalid.__module__ = module_name
    with pytest.raises(ValueError, match="importable"):
        tool(name="invalid", version="1")(module.invalid)


@pytest.mark.parametrize(
    "source",
    [
        "def invalid(name: str): pass",
        "async def invalid(name: str): yield name",
        "async def invalid(name): pass",
        "async def invalid(*names: str): pass",
        "async def invalid(name: str, /): pass",
        "async def invalid(ctx: str): pass",
        "async def invalid(ctx: ToolContext = None): pass",
        "async def invalid(other: ToolContext): pass",
        "async def invalid(_hidden: str): pass",
    ],
)
def test_tool_rejects_unsupported_signatures(module, source):
    exec(source, vars(module))
    with pytest.raises(TypeError):
        tool(name="invalid", version="1")(module.invalid)
    assert "__durable_tool__" not in vars(module.invalid)


def test_tool_rejects_local_functions():
    async def nested(name: str):
        return name

    with pytest.raises(ValueError, match="importable"):
        tool(name="nested", version="1")(nested)


@pytest.mark.parametrize("name", sorted(BUILTIN_TOOLS))
def test_tool_reserves_builtin_names(name):
    with pytest.raises(ValueError, match="reserved"):
        tool(name=name, version="1")


@pytest.mark.parametrize("decorate", [agent, tool])
@pytest.mark.parametrize("name,version", [("bad name", "1"), ("valid", ""), ("valid", 1)])
def test_registration_requires_valid_names_and_explicit_versions(decorate, name, version):
    with pytest.raises(ValidationError):
        decorate(name=name, version=version)
