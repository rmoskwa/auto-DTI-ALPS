"""
Regression net for the real ``SubprocessToolRunner``.

These tests pin the adapter's streaming/exit/cancel/not-found behaviour using
only POSIX coreutils (``echo``, ``printf``, ``false``, ``sh``) -- no FSL/MRtrix3/
FreeSurfer/ANTs/Convert3D required. They are the safety net the pipeline refactor
leans on, so they exist before any caller is converted to the seam.

POSIX-only: the streaming adapter uses ``select`` on pipes.
"""

import os

import pytest

from dti_alps.processing.tool_runner import SubprocessToolRunner

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="SubprocessToolRunner streaming is POSIX-only"
)


def test_echo_streams_lines_and_exits_zero():
    runner = SubprocessToolRunner()
    streamed: list[str] = []

    result = runner.run(["echo", "hello world"], on_line=streamed.append)

    assert result.returncode == 0
    assert streamed == ["hello world"]
    assert result.output == "hello world"
    assert result.cmd == ["echo", "hello world"]


def test_capture_without_on_line_returns_merged_output():
    # Capture-style callers pass no on_line and read .output.
    runner = SubprocessToolRunner()

    result = runner.run(["printf", "line1\nline2\n"])

    assert result.returncode == 0
    assert result.output == "line1\nline2"


def test_stderr_is_merged_into_output():
    runner = SubprocessToolRunner()

    result = runner.run(["sh", "-c", "echo err 1>&2"])

    assert result.returncode == 0
    assert "err" in result.output


def test_false_reports_nonzero_returncode():
    runner = SubprocessToolRunner()

    result = runner.run(["false"])

    assert result.returncode == 1


def test_missing_binary_returns_127_without_raising():
    runner = SubprocessToolRunner()
    streamed: list[str] = []

    # An absolute, non-existent path raises FileNotFoundError deterministically
    # (a bare name can raise PermissionError under some PATH setups).
    result = runner.run(["/nonexistent/dir/dti-alps-missing"], on_line=streamed.append)

    assert result.returncode == 127
    assert "Command not found" in result.output
    # The not-found explanation is also streamed, so streaming callers see it.
    assert streamed == [result.output]


def test_unlaunchable_command_is_contained_as_127():
    # run() must never raise, whatever OSError the launch produces.
    runner = SubprocessToolRunner()

    result = runner.run(["dti-alps-no-such-binary-xyz"])

    assert result.returncode == 127
    assert "dti-alps-no-such-binary-xyz" in result.output


def test_cancel_terminates_running_process():
    runner = SubprocessToolRunner()

    # cancel_check is polled at the top of the loop, before select blocks, so a
    # process that would otherwise sleep 30s is terminated near-immediately.
    result = runner.run(["sh", "-c", "sleep 30"], cancel_check=lambda: True)

    # Terminated, not a clean exit.
    assert result.returncode != 0
