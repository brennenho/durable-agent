"""Standard-library command supervisor, launched as PID 1 inside the sandbox."""

import asyncio
import json
import os
import signal
import sys
import time


def stop_children(pid: int) -> None:
    try:
        # PID 1 owns this container's process namespace, including detached children.
        os.kill(-1 if os.getpid() == 1 else -pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def supervise(
    command: str, cwd: str, timeout: float, log_limit: int, *, deadline: float
) -> dict:
    if timeout <= 0 or log_limit <= 0:
        raise ValueError("Timeout and log limit must be positive")
    remaining = deadline - time.time()
    if remaining <= 0:
        raise TimeoutError("Attempt deadline expired before command start")
    # Keep the outer watchdog active during launch and cleanup as well as execution.
    async with asyncio.timeout(remaining):
        return await _run_command(command, cwd, timeout, log_limit)


async def _run_command(command: str, cwd: str, timeout: float, log_limit: int) -> dict:
    command_timer = asyncio.timeout(timeout)
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        return {"status": "error", "code": "invalid_cwd", "message": str(exc)}

    buffers = [bytearray(), bytearray()]
    remaining = log_limit
    truncated = False

    async def drain(stream, buffer):
        nonlocal remaining, truncated
        while data := await stream.read(8192):
            kept = data[:remaining]
            buffer.extend(kept)
            remaining -= len(kept)
            truncated |= len(kept) < len(data)

    readers = [
        asyncio.create_task(drain(process.stdout, buffers[0])),
        asyncio.create_task(drain(process.stderr, buffers[1])),
    ]
    timed_out = False
    try:
        async with command_timer:
            # Process.wait can wait for inherited pipes; observe shell exit separately.
            while process.returncode is None:
                await asyncio.sleep(0.01)
            stop_children(process.pid)
            await asyncio.gather(*readers)
    except TimeoutError:
        if not command_timer.expired():
            raise
        timed_out = True
    finally:
        stop_children(process.pid)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Drain any buffered bytes after cancellation so pipe backpressure cannot block wait.
        await process.communicate()

    if timed_out:
        return {
            "status": "error",
            "code": "command_timeout",
            "message": (
                f"Command exceeded {timeout:g} seconds. Partial changes were discarded. "
                "Split the work into smaller commands or reissue with a larger timeout_seconds "
                "(maximum 900)."
            ),
        }

    logs = []
    remaining = log_limit
    for buffer in buffers:
        encoded = buffer.decode("utf-8", errors="replace").encode("utf-8")
        text = encoded[:remaining].decode("utf-8", errors="ignore")
        truncated |= len(encoded) > remaining
        remaining -= len(text.encode("utf-8"))
        logs.append(text)
    code = process.returncode
    return {
        "status": "ok",
        "value": {
            "exit_code": code if code >= 0 else 128 - code,
            "stdout": logs[0],
            "stderr": logs[1],
            "truncated": truncated,
        },
    }


if __name__ == "__main__":
    command, cwd, deadline, log_limit, timeout = sys.argv[1:]
    # A supervisor failure emits no completed result and exits nonzero.
    result = asyncio.run(
        supervise(command, cwd, float(timeout), int(log_limit), deadline=float(deadline))
    )
    print(json.dumps(result), flush=True)
