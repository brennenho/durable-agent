import importlib
import sys
from dataclasses import FrozenInstanceError

import pytest
from durable_agent.sdk.loading import AgentApp, load_app
from durable_agent.sdk.registration import agent


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    source = tmp_path / "example_app.py"
    source.write_text(
        "from durable_agent.sdk.loading import AgentApp\n"
        "from durable_agent.sdk.registration import agent, tool\n"
        "@agent(name='writer', version='1')\n"
        "class Writer:\n"
        "    def __init__(self): raise AssertionError('constructed')\n"
        "    def start(self, ctx): raise AssertionError('started')\n"
        "    def advance(self, ctx, result): raise AssertionError('advanced')\n"
        "@tool(name='save', version='1')\n"
        "async def save(text: str = ''): raise AssertionError('called')\n"
        "@tool(name='unselected', version='1')\n"
        "async def unselected(): raise AssertionError('called')\n"
        "async def undecorated(text: str): pass\n"
        "class Subclass(Writer): pass\n"
        "class Container:\n"
        "    app = AgentApp(agents=[Writer], tools=[save])\n"
        "app = Container.app\n"
        "def factory(): raise AssertionError('factory called')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    # Restore the module cache after each real import, including failed imports.
    monkeypatch.delitem(sys.modules, "example_app", raising=False)
    yield "example_app"
    sys.modules.pop("example_app", None)


def test_loading_imports_the_selected_app_without_executing_callbacks(app_module):
    assert app_module not in sys.modules
    app = load_app(f"{app_module}:app")
    module = importlib.import_module(app_module)
    assert app is module.app
    assert load_app(f"{app_module}:Container.app") is app
    assert app.agents["writer"].agent_type is module.Writer
    assert app.agents["writer"].version == "1"
    assert list(app.tools) == ["save"]
    assert app.tools["save"].entrypoint == f"{app_module}:save"
    assert app.tools["save"].parameters["properties"]["text"]["default"] == ""
    assert AgentApp().agents == {} and AgentApp().tools == {}


def test_app_copies_membership_and_tool_schema(app_module):
    module = importlib.import_module(app_module)
    agents, tools = [module.Writer], [module.save]
    app = AgentApp(agents=agents, tools=tools)
    agents.clear()
    tools.clear()
    module.save.__durable_tool__.parameters["properties"].clear()
    assert list(app.agents) == ["writer"]
    assert list(app.tools) == ["save"]
    assert "text" in app.tools["save"].parameters["properties"]
    with pytest.raises(TypeError):
        app.agents["other"] = app.agents["writer"]
    with pytest.raises(TypeError):
        app.tools["other"] = app.tools["save"]
    with pytest.raises(FrozenInstanceError):
        app.agents = {}


@pytest.mark.parametrize("kind", ["agents", "tools"])
def test_duplicate_names_reject_even_repeated_objects(app_module, kind):
    module = importlib.import_module(app_module)
    item = module.Writer if kind == "agents" else module.save
    with pytest.raises(ValueError, match="Duplicate"):
        AgentApp(**{kind: [item, item]})


def test_different_agent_versions_cannot_share_a_name(app_module):
    module = importlib.import_module(app_module)
    version_two = agent(name="writer", version="2")(module.Subclass)
    with pytest.raises(ValueError, match="Duplicate agent name"):
        AgentApp(agents=[module.Writer, version_two])


@pytest.mark.parametrize("kind,attribute", [("agents", "Subclass"), ("tools", "undecorated")])
def test_inherited_or_missing_registration_is_not_explicit(app_module, kind, attribute):
    module = importlib.import_module(app_module)
    with pytest.raises(TypeError, match="explicitly decorated"):
        AgentApp(**{kind: [getattr(module, attribute)]})


def test_tool_reference_cannot_silently_point_to_different_code(app_module):
    module = importlib.import_module(app_module)
    original = module.save
    module.save = module.undecorated
    with pytest.raises(ValueError, match="changed after registration"):
        AgentApp(tools=[original])


@pytest.mark.parametrize("reference", ["example_app", ":app", "example_app:", "example_app:app()"])
def test_invalid_references_fail_before_importing(app_module, reference):
    with pytest.raises(ValueError, match="module:attribute"):
        load_app(reference)
    assert app_module not in sys.modules


def test_missing_module_and_attribute_have_explicit_errors(app_module):
    with pytest.raises(ValueError, match="Could not import module"):
        load_app("missing_agent_application:app")
    with pytest.raises(ValueError, match="does not exist"):
        load_app(f"{app_module}:missing")


@pytest.mark.parametrize("attribute", ["Writer", "factory", "save"])
def test_loading_requires_an_instance_and_never_calls_factories(app_module, attribute):
    with pytest.raises(TypeError, match="AgentApp instance"):
        load_app(f"{app_module}:{attribute}")
