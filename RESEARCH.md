# Durable Agent Execution Research Brief

Researched October 1, 2026, to inform the [design proposal](/Users/4wt/durable-agent/DESIGN.md). The initial design now uses a shared tool runner and complete workspace checkpoints for coding and document editing. External adapters and their recovery policies discussed below are future work.

**Assessment:** our proposed direction matches established approaches: persist individual model and tool results, retain completed work through recovery, and make retries explicit. The crucial boundary is between a result durably recorded by the runtime and an external action that may have completed without that record. Temporal and DBOS explicitly require safe retry behavior for interrupted activities or steps. [Temporal activity semantics](https://docs.temporal.io/activity-definition), [DBOS architecture](https://docs.dbos.dev/architecture).

## Relevant approaches

| System | What it does | Implication for our proposal |
| --- | --- | --- |
| **Temporal** | Replays deterministic workflow logic from execution history. LLM requests and tool calls run as Activities, whose recorded results are reused during replay. Its agent reference architecture follows a model-call, tool-call, repeat loop. [Reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture). | Separate orchestration from external execution and preserve accepted results. Our explicit agent state machine can borrow these principles without implementing arbitrary workflow replay. |
| **LangGraph** | Saves graph state at super-step boundaries and retains successful node writes when another node in that step fails. Its `sync` durability mode persists checkpoints before the next step; `async` allows execution to advance while persistence is pending. [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers). | Checkpoint each tool separately so a partially completed batch can resume. Our result-and-next-operation transaction should provide the ordering associated with synchronous durability. |
| **DBOS** | Uses PostgreSQL for workflow inputs, step outputs, queues, and scheduling state. Recovery re-executes workflow code while substituting saved step results. Distributed recovery requires coordination, provided by Conductor or a custom mechanism. [Architecture](https://docs.dbos.dev/architecture). | The closest storage precedent for our design. A shared database supports durable scheduling, but we still need explicit worker failure detection and reassignment. |
| **Restate** | Journals durable operations and reuses their results during recovery. Its Python API wraps external work in `ctx.run`/`ctx.run_typed`. The authors distinguish recorded side effects from calls that may repeat because failure occurred before their result became durable. [Python durable steps](https://docs.restate.dev/develop/python/durable-steps), [external side-effect boundary](https://restate.dev/blog/why-we-built-restate#side-effects). | Keep logical operation identity stable across attempts. Never infer that an external action did not happen merely because its result is absent. |

The implications column is our interpretation for this trial, not a claim that these products share identical guarantees or architecture.

## Concrete implementations

**Temporal AI Agent** is a public reference implementation with a multi-turn agent loop, native and MCP tools, and human input or approval. It is useful reading for the separation between workflow state and tool execution; its README also identifies conversation-history growth as a production concern. [Repository](https://github.com/temporal-community/temporal-ai-agent).

**DBOS’s Vercel AI SDK integration** separately wraps model calls and tool calls for checkpointing. This is a concrete example of choosing the individual external call as the durability boundary. [Integration documentation](https://docs.dbos.dev/integrations/vercel-ai).

**Sherlocks AI** reports using Temporal for incident investigations involving 10–15 specialized agents. Its CTO describes separate workers, child workflows for fault isolation, heartbeats, partial-result synthesis, and a UI that exposes running and retrying activities. This is a practitioner account hosted by Temporal, not an independent benchmark. [Engineering account, July 7, 2026](https://temporal.io/blog/how-sherlocks-ai-uses-temporal-to-orchestrate-ai-agents-for-incident-resolution?trk=public_post_comment-text).

## Recommendations for our design

1. **Keep the current scope.** Python, FastAPI, PostgreSQL, explicit agent operations, and separate workers are a defensible small implementation. Use these systems as design references; the trial asks us to demonstrate our own agent-specific model.
2. **Test both commit boundaries.** Reuse a saved tool result and workspace after a lost response; separately verify atomic runtime acceptance. Future external adapters must pause when an effect cannot be determined safely.
3. **Give retries one owner.** Proposed refinement: disable or explicitly account for automatic HTTP-client and SDK retries. Otherwise one recorded attempt could hide several external calls and bypass the selected tool retry policy.
4. **Keep inference claims precise.** Reusing saved model output prevents repeated completed work. It cannot recover a synchronous response that was lost before persistence. Our decision to allow repeating that inference remains appropriate.
5. **Build the dashboard around recovery.** Show the logical operation, attempt history, last heartbeat, last accepted progress, and next retry. Heartbeat recency and useful progress should be separate fields.
6. **Keep recovery compatible.** Freeze adapter versions and sandbox images; initially require the same application build for unfinished runs. General workflow migration remains deferred. DBOS and Temporal document compatibility requirements around replay. [DBOS workflow upgrades](https://docs.dbos.dev/architecture#upgrading-workflow-code), [Temporal determinism](https://docs.temporal.io/workflow-definition).

## Tool effects and sandbox recovery

These are published product behaviors and engineering patterns, not claims about
undocumented company infrastructure. The recommendations below are our interpretation.

| Source | Published approach | Application here |
| --- | --- | --- |
| [Temporal](https://docs.temporal.io/activity-definition) | Completed activity results survive replay, but an activity interrupted before reporting may run partly or fully again. It recommends smaller activities and receiver-enforced idempotency. | Save each accepted tool result; require a recovery policy for unfinished work. |
| [AWS Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | Stable client tokens identify repeated intent. Token recording and owned mutations must be atomic; changed parameters and late requests need defined behavior. | Keep operation IDs stable. Our ledger protects local commits; it cannot make an unrelated external API transactional. |
| [E2B](https://docs.e2b.dev/sandbox/persistence) | Pause/resume can preserve both filesystem and memory, with a filesystem-only option. A killed sandbox cannot be resumed. | Environment recovery is separate from agent progress. A saved environment is useful but does not settle an external write's outcome. |
| [Vercel Sandbox](https://vercel.com/docs/sandbox/concepts/snapshots) | Snapshots preserve filesystem state and installed packages for later sessions. | Saved environments are an option for expensive setup. Our initial tools can rebuild from a pinned image and explicit artifacts. |

For this project, use a private workspace per attempt and complete immutable workspace
versions in PostgreSQL. If an edit crashes halfway, discard the partial workspace and
repeat that operation from its saved input version. Save the checkpoint and tool result
in one transaction. A lost response returns the existing completion. Capture the whole
workspace automatically so tools need not declare each file they change.

This avoids requiring arbitrary code to be idempotent: its mutations stay in disposable
scratch space, and publication is controlled. Keep network access and external credentials
out of sandbox code. External integrations need their own adapters, with stable request
keys where supported, target-side version checks, and reconciliation otherwise. If the
adapter cannot establish whether a write happened, pause. A timeout or an absent lookup
does not prove that a delayed request cannot still apply.

Save intermediate artifacts between long steps when restarting the whole step is costly.
Full sandbox snapshots, arbitrary process resumption, and a generic compensation engine
can wait. Compensating actions are tool-specific and may themselves fail; they do not
provide a universal rollback mechanism.
