# Tools implementation

`archives.py` captures complete gzip-compressed tar checkpoints and restores them
into new directories. Stop all workspace writers before capture. Source and target
parents must be private to the runner; these functions do not synchronize live writers.
Private runner captures set `allow_permission_changes=True` to temporarily gain
read/traverse access to mode-000 entries, restoring their modes even on failure.
Imports leave this disabled and never change the configured source's permissions.

The configured workspace limit counts file bytes. Tar metadata has a separate 4 MiB
budget, checked during capture and decompression. Only regular files and directories
with ordinary permission bits are supported. Links, sparse files, special files,
duplicate paths, and invalid directory hierarchies are rejected.

`workspaces.py` imports an optional starting directory and reads saved files without
restoring them. Its database functions use a caller-owned async session: saving a
checkpoint flushes but does not commit. Existing versions are never updated.

`publication.py` registers immutable operation inputs and fences result publication
with a row lock and the latest registered generation. Callers commit registration
before executing, then commit the validated checkpoint and result together. A retry
returns the existing completion; publication never replaces it.

`files.py` implements the three file tools over saved inputs. Reads reuse the input;
writes return a complete new checkpoint. Business errors return model-facing output,
while invalid call schemas and invalid checkpoints raise execution failures.

The HTTP service imports `WORKSPACE_INPUT_DIR` once at startup (unset means empty),
using `DATABASE_URL` and an optional `WORKSPACE_MAX_BYTES` limit (default 20 MiB).
It serves saved versions and executes file tools outside database transactions.
Archive/file work runs off-loop; a deadline discards late thread results. Completed
responses survive service restarts. `run_command` uses the same registration and
atomic completion path, with sandbox failures leaving no completed result.

Set `SANDBOX_IMAGE_DIGEST` and an absolute `SANDBOX_WORKSPACE_ROOT` to enable commands.
Optional `DOCKER_BIN` selects the CLI; `SANDBOX_OWNER` identifies this service instance.
Startup removes that owner's abandoned containers, then clears its private
`tools-<owner>` directory under the configured root. Other directories are untouched.
Readiness checks PostgreSQL and the installed image. With no sandbox configuration,
file operations remain available but readiness returns 503. See `RUNNER.md` for wiring.
