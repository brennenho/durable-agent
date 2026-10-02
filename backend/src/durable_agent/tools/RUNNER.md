# Sandbox runner

`DockerRunner.run(workspace, command, cwd, config, deadline, *, timeout_seconds=120)`
returns a tool output only after the container stops and removal succeeds.
`deadline` uses the caller's event-loop clock and bounds the entire attempt.
The requested command budget (1–900 seconds) starts after container setup; the
supervisor also enforces the outer wall deadline if the service disappears.
Only a confirmed command timeout returns `command_timeout`, discarding partial
files and saving the input version with the result. Outer deadline expiry, crashes,
and failed cleanup remain execution failures.
The caller restores and owns the private directory, assigns it to `runner.uid/gid`,
then captures or discards it. Command failure is a result; container failure raises
`SandboxFailure`. Cleanup runs before returning, including cancellation.

Integration supplies an installed image pinned by digest or image ID, containing
Python 3.12 and `/bin/sh` (plus the agreed pytest version). The trusted service needs
the Docker CLI/socket and a workspace root mounted at the same absolute path in the
service and Docker host. Only each private workspace is mounted into its sandbox.

Call `cleanup_abandoned()` once at startup and `check_image(image)` for readiness.
Give each live service instance a distinct, stable `owner`; cleanup removes only
containers with that owner's label. Sandbox containers get no service credentials.
Limits are one CPU, 512 MiB memory, 64 processes, 64 MiB scratch, and the configured
maximum file size. Total workspace size is validated when capturing the checkpoint.

Docker tests require `SANDBOX_TEST_IMAGE`; set `DOCKER_BIN` if Docker is outside PATH.
`commands.py` restores each input into a new private directory, calls the runner,
validates a complete checkpoint, and discards the directory on every exit path.
Only normal command completion returns updated files. HTTP execution saves those
files and the result in one fenced transaction; a retry reuses the saved completion.
