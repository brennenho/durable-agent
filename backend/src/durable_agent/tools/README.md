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

Filesystem/archive functions are synchronous; service handlers must offload them
from the event loop, outside database transactions. HTTP handlers, tool execution,
and the sandbox runner are pending.
