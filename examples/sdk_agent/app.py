"""Write a Markdown document without making a model call."""

from durable_agent import (
    AgentApp,
    AgentContext,
    Done,
    Fail,
    Next,
    StepResult,
    ToolCall,
    ToolContext,
    ToolError,
    agent,
    tool,
)


@tool(name="save_document", version="1")
async def save_document(ctx: ToolContext, title: str, body: str) -> dict | ToolError:
    """Save a titled Markdown document in the private workspace."""
    if not title.strip():
        return ToolError(code="empty_title", message="The document needs a title")
    content = f"# {title}\n\n{body}\n"
    await ctx.workspace.write_text("document.md", content)
    return {"path": "document.md", "bytes_written": len(content.encode("utf-8"))}


@agent(name="document_writer", version="1")
class DocumentWriter:
    def start(self, ctx: AgentContext) -> Next | Fail:
        value = ctx.input
        if not isinstance(value, dict) or any(
            not isinstance(value.get(key), str) for key in ("title", "body")
        ):
            return Fail(code="invalid_input", message="Supply a title and body as strings")
        return Next(
            state={"title": value["title"]},
            step=ToolCall(
                name="save_document", arguments={"title": value["title"], "body": value["body"]}
            ),
        )

    def advance(self, ctx: AgentContext, result: StepResult) -> Done | Fail:
        if result.error:
            return Fail(code=result.error.code, message=result.error.message)
        return Done(output={"title": ctx.state["title"], "file": result.value})


app = AgentApp(agents=[DocumentWriter], tools=[save_document])
