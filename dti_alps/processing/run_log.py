"""
The run log file: an engine-side, composable sink over the worker message stream.

A processing run leaves a timestamped ``dti_alps_<timestamp>.log`` beside its
results. That file used to be opened, teed into, and deleted by the Qt adapter,
which meant a headless run produced none -- and ``OutputConfig.log_file``, a key
the protocol schema exposes, would have been a knob the CLI silently ignored.

:class:`LogFileSink` puts the behaviour where both front ends can reach it. It
*wraps* a ``progress_callback`` rather than replacing it: every message passes
through to the inner callback unchanged, and ``Log`` messages are additionally
written to the file. So the GUI keeps its console and the CLI keeps its terminal
output, and neither owns the file.

Stdlib-only, no toolkit, no threads: testable without a Qt application.
"""

import os
from collections.abc import Callable
from datetime import datetime

from .messages import Log, WorkerMessage

# The run-log filename pattern. One home for the name the engine writes and any
# consumer looks for.
_LOG_TEMPLATE = "dti_alps_{timestamp}.log"

# How a timestamp is rendered into the filename, and how each line is stamped.
_FILENAME_STAMP = "%Y%m%d_%H%M%S"
_LINE_STAMP = "[%H:%M:%S]"


def log_file_name(when: datetime) -> str:
    """
    The run-log filename for a run started at ``when``.

    >>> log_file_name(datetime(2026, 8, 2, 14, 30, 5))
    'dti_alps_20260802_143005.log'
    """
    return _LOG_TEMPLATE.format(timestamp=when.strftime(_FILENAME_STAMP))


def format_log_line(text: str, when: datetime) -> str:
    """
    Stamp one log line exactly as the GUI console does.

    The same formatting on both front ends means a log file is byte-identical
    whichever one produced it.

    >>> format_log_line("Starting batch", datetime(2026, 8, 2, 14, 30, 5))
    '[14:30:05] Starting batch'
    """
    return f"{when.strftime(_LINE_STAMP)} {text}"


class LogFileSink:
    """
    A ``progress_callback`` wrapper that tees ``Log`` messages into a file.

    Use it as a callback:

        sink = LogFileSink(output_dir, output_config)
        runner = BatchRunner(state, progress_callback=sink.wrap(my_callback))
        try:
            runner.run_batch()
        finally:
            sink.close()

    or as a context manager, which closes (and honours the retention flag) on
    the way out even if the run raised:

        with LogFileSink(output_dir, config) as sink:
            BatchRunner(state, progress_callback=sink.wrap(cb)).run_batch()

    A file that cannot be opened is not fatal: the sink degrades to a
    pass-through and reports the reason through the wrapped callback, because
    losing the log is never a reason to lose the analysis.
    """

    def __init__(self, output_dir: str, output_config, when: datetime | None = None):
        """
        Open the run log in ``output_dir``.

        Parameters
        ----------
        output_dir : str
            Directory the log lands in (created if absent).
        output_config : OutputConfig
            Read at :meth:`close` time for ``log_file``; when false the log is
            written during the run (so a crash still leaves a record) and
            deleted at the end.
        when : datetime, optional
            The run's start time, used for the filename. Injectable so tests do
            not depend on the clock.
        """
        self._output_config = output_config
        self._when = when or datetime.now()
        self.path: str | None = os.path.join(output_dir, log_file_name(self._when))
        self._handle = None
        self._open_error: str | None = None

        try:
            os.makedirs(output_dir, exist_ok=True)
            self._handle = open(self.path, "w", encoding="utf-8")
        except OSError as err:
            self._open_error = f"Warning: Could not create log file: {err}"
            self.path = None

    def wrap(
        self, callback: Callable[[WorkerMessage], None] | None = None
    ) -> Callable[[WorkerMessage], None]:
        """
        Return a callback that forwards to ``callback`` and files ``Log`` lines.

        Wrapping also announces, through that same stream, whether the log was
        created or why it could not be -- so the notice reaches the GUI console
        and the terminal alike without this class knowing about either.
        """
        inner = callback or (lambda message: None)

        notice = self._open_error or f"Log file created: {self.path}"
        inner(Log(notice))
        self._write(notice)

        def _sink(message: WorkerMessage) -> None:
            inner(message)
            if isinstance(message, Log):
                self._write(message.text)

        return _sink

    def _write(self, text: str) -> None:
        """Append one stamped line, ignoring write errors (see class docstring)."""
        if self._handle is None:
            return
        try:
            self._handle.write(format_log_line(text, datetime.now()) + "\n")
            self._handle.flush()
        except OSError:
            pass

    def close(self) -> None:
        """
        Close the log, then delete it if ``output_config.log_file`` is false.

        The retention flag is honoured at close rather than at open so an
        aborted run still leaves the record behind: a log is most valuable
        exactly when the run did not finish.

        Idempotent -- a second call is a no-op.
        """
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

        if self.path and not self._output_config.log_file:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.path = None

    def __enter__(self) -> "LogFileSink":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
