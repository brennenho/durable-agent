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

These filesystem functions are synchronous. Service handlers must offload them from
the event loop. Persistence, tool execution, and the sandbox runner are pending.
