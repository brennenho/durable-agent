import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "scripts" / "crash-test.sh"


@pytest.mark.parametrize("startup_fails", [False, True])
def test_launcher_waits_for_controller_and_cleans_up_children(tmp_path, startup_fails):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    launcher = checkout / "scripts" / LAUNCHER.name
    shutil.copy2(LAUNCHER, launcher)
    (checkout / ".env").write_text("CRASH_TEST_MODE=host\n")
    uv = shutil.which("uv")
    assert uv is not None
    # Real child processes stand in for uvicorn/Next so the shell's signal handling is exercised.
    child = (
        f"#!{sys.executable}\n"
        + f"import os, sys\nif '--env-file' in sys.argv: os.execv({uv!r}, sys.argv)\n"
        + """
import os, signal, sys, time
from pathlib import Path

state = Path(os.environ["CRASH_TEST_STATE_DIR"])
if Path(sys.argv[0]).name == "uv":
    if "-c" in sys.argv:
        sys.exit(0 if (state / "controller.pid").exists() and not os.environ["FAIL_STARTUP"] else 1)
    assert sys.argv[-2:] == ["-m", "crash_test.host"]
    if os.environ["FAIL_STARTUP"]:
        sys.exit(17)
    name = "controller"
else:
    assert (state / "controller.pid").exists()
    name = "dashboard"
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
(state / (name + ".pid")).write_text(str(os.getpid()))
while True:
    time.sleep(0.05)
"""
    )
    for command in ["uv", "pnpm"]:
        executable = tmp_path / command
        executable.write_text(child)
        executable.chmod(0o755)
    state = tmp_path / "state"
    environment = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        CRASH_TEST_STATE_DIR=str(state),
        FAIL_STARTUP="1" if startup_fails else "",
    )
    process = subprocess.Popen(
        [str(launcher)], env=environment, start_new_session=True, stdout=subprocess.DEVNULL
    )
    try:
        if startup_fails:
            assert process.wait(timeout=10) == 17
            assert not (state / "dashboard.pid").exists()
        else:
            deadline = time.monotonic() + 10
            while not (state / "dashboard.pid").exists():
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            # Signal only the launcher: its traps must stop both children.
            process.terminate()
            assert process.wait(timeout=10) == 143
            for name in ["controller", "dashboard"]:
                with pytest.raises(ProcessLookupError):
                    os.kill(int((state / f"{name}.pid").read_text()), 0)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
