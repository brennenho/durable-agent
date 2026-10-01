# Worker

Run with `uv run --project backend python -m durable_agent.worker`.
Set `OPENAI_API_KEY`; `CONTROL_URL` and `TOOLS_URL` default to localhost ports 8000
and 8001. The model and tool configuration come from each saved assignment.
Integration owns Compose startup and readiness wiring.

Each process has a fresh worker ID and executes one assignment at a time. Idle
polling and report retries default to one second; heartbeats run every five seconds.
Only Control chooses operation retries. Workers never connect to PostgreSQL.

- `responses.py`: pure output mapping and native context preservation.
- `openai_client.py`: one bounded model request, with SDK retries disabled.
- `services.py`: typed Control/Tools HTTP calls.
- `lease.py`: monotonic lease timing, renewal, and cancellation.
- `runner.py`: claim, execute once, and report the same outcome until acknowledged.

Report retries stop at the lease expiry known when execution completed. Renewal
does not extend that delivery budget or the fixed call deadline. If reporting or
the process fails, Control recovers after the lease expires; a lost model result
may require inference again. Tools can recover already committed results.

SIGTERM and Ctrl-C cancel work and close the HTTP clients. Tests use saved response
and HTTP fixtures: `uv run --project backend pytest backend/tests/worker -q`.
