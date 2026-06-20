"""
The single seam for external command execution in the DTI-ALPS engine.

Every toolchain command (MRtrix3, FSL, FreeSurfer, ANTs, Convert3D) is meant to
flow through a :class:`ToolRunner`. Production injects :class:`SubprocessToolRunner`,
which runs a real subprocess; tests inject a recording fake. Because two
implementations exist from day one, the seam is structural, not speculative.

The runner exposes a single ``run()`` method that both streams (via ``on_line``)
and captures (via :attr:`ToolResult.output`) in one pass. It never raises: every
outcome -- success, non-zero exit, missing binary, cancellation -- is reported as
an integer return code on a :class:`ToolResult`, so control-flow callers never
need a ``try/except`` and tests never script exceptions.
"""

from __future__ import annotations

import select
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# A process killed by SIGTERM is reported by Popen as return code -15. A run
# cancelled via ``cancel_check`` surfaces this; the *caller's* own cancel flag
# (it supplied ``cancel_check``) disambiguates "cancelled" from "failed" -- the
# result type carries no special cancelled field.
TERMINATED_RETURNCODE = -15

# Return code used for a missing binary (today's FileNotFoundError), caught
# internally so no exception crosses the seam.
COMMAND_NOT_FOUND_RETURNCODE = 127

# Seconds of silence before the streaming loop emits a "still processing" line.
DEFAULT_HEARTBEAT_INTERVAL = 30.0


@dataclass
class ToolResult:
    """Outcome of a single command execution.

    Attributes
    ----------
    returncode : int
        Process exit code. ``0`` is success. ``127`` is a missing binary
        (the runner catches ``FileNotFoundError`` and reports it here rather
        than raising). A negative value indicates termination by signal
        (e.g. ``-15`` after a cancel).
    output : str
        Merged stdout+stderr, newline-joined, with each line right-stripped.
        Streaming callers pass ``on_line`` and typically ignore this; capture
        callers read it.
    cmd : list of str
        The argv that produced this result, echoed back for assertions.
    """

    returncode: int
    output: str
    cmd: list[str]


class ToolRunner(Protocol):
    """Structural interface for executing one external command.

    Implementations: :class:`SubprocessToolRunner` (production) and the test
    ``FakeToolRunner``. Being a ``Protocol``, the fake satisfies this without
    importing or subclassing anything in this module.
    """

    def run(
        self,
        cmd: list[str],
        *,
        on_line: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ToolResult:
        """Execute ``cmd`` and return its :class:`ToolResult` (never raises)."""
        ...


class SubprocessToolRunner:
    """Real :class:`ToolRunner` backed by ``subprocess.Popen``.

    Models the superset behaviour of the pipeline's former ``_run_command``:
    non-blocking line streaming via ``select``, cooperative cancellation, and a
    periodic heartbeat during long silent stretches. ``stderr`` is merged into
    ``stdout`` into a single :attr:`ToolResult.output` string.

    POSIX-only: ``select`` on pipes does not work on Windows. This is acceptable
    because the toolchain (FSL/MRtrix3/...) runs on Linux/macOS; the GUI's
    cross-platform desktop-open calls deliberately stay outside this seam.
    """

    def __init__(self, heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL) -> None:
        self._heartbeat_interval = heartbeat_interval

    def run(
        self,
        cmd: list[str],
        *,
        on_line: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ToolResult:
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # unbuffered, so streaming stays line-timely
            )
        except OSError as exc:
            # Any failure to *launch* the process becomes a return code, not an
            # exception, so every failure mode is uniform at the seam and run()
            # never raises. A truly absent binary surfaces as FileNotFoundError
            # on most systems, but PATH-resolution quirks (e.g. a non-executable
            # match) can raise PermissionError instead -- both must be contained.
            if isinstance(exc, FileNotFoundError):
                message = f"Command not found: {cmd[0]}"
            else:
                message = f"Could not execute {cmd[0]}: {exc}"
            if on_line is not None:
                on_line(message)
            return ToolResult(returncode=COMMAND_NOT_FOUND_RETURNCODE, output=message, cmd=cmd)

        captured: list[str] = []

        def emit(text: str) -> None:
            captured.append(text)
            if on_line is not None:
                on_line(text)

        heartbeat_label = cmd[0] if cmd else "tool"
        last_activity = time.time()

        while True:
            # Poll the caller's cancel signal before blocking on output.
            if cancel_check is not None and cancel_check():
                return self._terminate(process, captured, cmd)

            ready, _, _ = select.select([process.stdout], [], [], 1.0)

            if ready:
                line = process.stdout.readline()
                if line:
                    stripped = line.rstrip()
                    if stripped:
                        emit(stripped)
                    last_activity = time.time()
                elif process.poll() is not None:
                    # EOF and the process has exited: no more output.
                    break
            else:
                if process.poll() is not None:
                    break
                now = time.time()
                if now - last_activity > self._heartbeat_interval:
                    elapsed = int(now - last_activity)
                    emit(
                        f"  [{heartbeat_label}] Still processing... ({elapsed}s since last output)"
                    )
                    last_activity = now

        # Drain anything buffered after the process exited.
        remaining = process.stdout.read()
        if remaining:
            for line in remaining.strip().split("\n"):
                if line:
                    emit(line)

        return ToolResult(returncode=process.returncode, output="\n".join(captured), cmd=cmd)

    def _terminate(
        self, process: subprocess.Popen, captured: list[str], cmd: list[str]
    ) -> ToolResult:
        """Terminate a cancelled process and return its partial result."""
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        returncode = process.returncode if process.returncode is not None else TERMINATED_RETURNCODE
        return ToolResult(returncode=returncode, output="\n".join(captured), cmd=cmd)
