"""
Test doubles for the ToolRunner seam.

``FakeToolRunner`` is a stateless, predicate-based recorder: it remembers every
argv it was handed (in order) and returns scripted :class:`ToolResult`s. Outcomes
are scripted by *what the command is* (a predicate over argv), never by position
in a sequence, so tests survive reordering of long command chains. The fake
produces no files -- end-to-end-with-real-files is the integration smoke's job,
not the fake's.

It satisfies the ``ToolRunner`` Protocol structurally, without subclassing it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from dti_alps.processing.tool_runner import TERMINATED_RETURNCODE, ToolResult

Predicate = Callable[[list[str]], bool]


class FakeToolRunner:
    """Recording, predicate-scripted stand-in for a real ``ToolRunner``."""

    def __init__(self) -> None:
        # Every argv handed to run(), in order -- for command-construction
        # assertions that don't care where in the chain a command landed.
        self.calls: list[list[str]] = []
        # (predicate, returncode, lines, cancel) rules, tried in order.
        self._rules: list[tuple[Predicate, int, tuple[str, ...], bool]] = []

    def on(
        self,
        pred: Predicate,
        *,
        returncode: int = 0,
        lines: Sequence[str] = (),
        cancel: bool = False,
    ) -> FakeToolRunner:
        """Register a rule: when ``pred(cmd)`` matches, return this outcome.

        Rules are tried in registration order; the first match wins. Returns
        ``self`` so rules can be chained.
        """
        self._rules.append((pred, returncode, tuple(lines), cancel))
        return self

    def run(
        self,
        cmd: list[str],
        *,
        on_line: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ToolResult:
        self.calls.append(list(cmd))

        returncode, lines, cancel = 0, (), False
        for pred, rule_rc, rule_lines, rule_cancel in self._rules:
            if pred(cmd):
                returncode, lines, cancel = rule_rc, rule_lines, rule_cancel
                break

        if cancel and cancel_check is not None:
            # Mirror a terminated process. The caller owns its own cancel flag
            # (it supplied cancel_check) and disambiguates cancel vs failure.
            return ToolResult(returncode=TERMINATED_RETURNCODE, output="", cmd=list(cmd))

        emitted: list[str] = []
        for line in lines:
            if on_line is not None:
                on_line(line)  # exercise streaming callers' log handling
            emitted.append(line)
        return ToolResult(returncode=returncode, output="\n".join(emitted), cmd=list(cmd))
