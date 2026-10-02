"""Select an installed agent and reject incompatible saved deployments."""

from dataclasses import dataclass, field

from durable_agent.contracts import AgentConfig
from durable_agent.control.errors import ControlError
from durable_agent.harness.builtin import BuiltinAgent
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.types import AgentIdentity


@dataclass(frozen=True)
class AgentRuntime:
    app: AgentApp = field(default_factory=AgentApp)
    app_ref: str = "builtin"
    build_id: str = "builtin-v1"

    def __post_init__(self):
        if "builtin" in self.app.agents:
            raise ValueError("The agent name 'builtin' is reserved")

    def _identity(self, name: str) -> AgentIdentity:
        definition = self.app.agents.get(name)
        if name != "builtin" and definition is None:
            raise ControlError(400, "unknown_agent", f"Agent is not registered: {name}")
        return AgentIdentity(
            name=name,
            version="1" if name == "builtin" else definition.version,
            app_ref=self.app_ref,
            build_id=self.build_id,
        )

    def freeze(self, config: AgentConfig, name: str = "builtin") -> AgentConfig:
        schemas = [
            schema
            for schema in config.tool_schemas
            if schema["name"] not in config.tool_definitions
        ]
        schemas.extend(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": False,
            }
            for tool in self.app.tools.values()
        )
        return config.model_copy(
            update={
                "agent": self._identity(name),
                "tool_schemas": schemas,
                "tool_definitions": dict(self.app.tools),
            },
            deep=True,
        )

    def instance(self, config: AgentConfig):
        identity = config.agent
        if identity is None:
            raise ControlError(409, "agent_incompatible", "Run has no saved agent identity")
        try:
            expected = self._identity(identity.name)
        except ControlError:
            expected = None
        if identity != expected or config.tool_definitions != dict(self.app.tools):
            raise ControlError(409, "agent_incompatible", "Saved agent deployment is not installed")
        if identity.name == "builtin":
            return BuiltinAgent(config)
        return self.app.agents[identity.name].agent_type()
