"""
The terminal presentation model -- the exact mirror of ``gui/result_model.py``.

Both dispatch over the same closed ``WorkerMessage`` union; neither owns the
other's wording. Where ``ResultModel`` emits view-intents for a Qt adapter to
poke into widgets, :class:`TerminalRenderer` emits lines of text. The message
contract is the only thing they share.

**Verbose by default, ``--quiet`` to reduce.** ``Log`` messages carry raw MRtrix3
and FSL stdout (the pipeline passes ``on_line=self._log`` straight through), and
researchers who know this pipeline diagnose an eddy failure by reading eddy's
output. Making them ask for it with a flag would mean re-running a three-hour
stage to find out what went wrong. ``--quiet`` drops to stage- and
subject-level lines.
"""

from collections.abc import Callable

from ..processing.messages import (
    BatchComplete,
    BatchStart,
    Log,
    Stage,
    SubjectComplete,
    SubjectStart,
    WorkerMessage,
)
from ..processing.state import BatchState

# Subject statuses that count as a clean finish, for the summary line.
_OK = "completed"


class TerminalRenderer:
    """
    Renders the worker message stream to a terminal.

    Construct with ``quiet=True`` to suppress raw tool output while keeping the
    structural lines (batch start, per-subject start/finish, the summary).

    ``write`` is injectable so tests capture output as a list instead of
    parsing stdout, and so a caller can redirect without this class knowing how.
    """

    def __init__(self, quiet: bool = False, write: Callable[[str], None] | None = None):
        self.quiet = quiet
        self._write = write or print
        self.total = 0

    def handle(self, message: WorkerMessage) -> None:
        """Render one message. Unknown message types are ignored, not fatal.

        The CLI calls ``BatchRunner.run_batch()`` synchronously rather than
        through ``BatchWorker``, so it never receives ``BatchSuccess`` /
        ``BatchPartial`` / ``BatchCancelled`` / ``Error`` -- those are
        ``BatchWorker``-emitted. Its verdict comes from the returned bool and
        ``batch_state.success_count`` instead. Ignoring rather than raising on
        an unhandled type keeps that asymmetry from being a landmine.
        """
        if isinstance(message, BatchStart):
            self.total = message.total
            self._write(f"Processing {message.total} subject(s)")
        elif isinstance(message, SubjectStart):
            self._write(f"[{message.index + 1}/{self.total}] {message.subject_id}")
        elif isinstance(message, SubjectComplete):
            self._write(self._subject_line(message))
        elif isinstance(message, Stage):
            if not self.quiet:
                self._write(f"    {message.stage}: {message.status}")
        elif isinstance(message, Log):
            if not self.quiet:
                self._write(f"    {message.text}")
        elif isinstance(message, BatchComplete):
            self._write(summarize(message.batch_state))

    def _subject_line(self, message: SubjectComplete) -> str:
        """One line per finished subject: status, timing, and the reason on failure."""
        result = message.result
        elapsed = f"{result.processing_time:.1f}s"
        if result.status == _OK:
            return f"    {result.subject_id}: completed in {elapsed}"
        reason = f" -- {result.error_message}" if result.error_message else ""
        return f"    {result.subject_id}: {result.status}{reason}"


def summarize(batch_state: BatchState) -> str:
    """
    The end-of-batch tally.

    Kept a module function rather than a method so ``run`` can print it after a
    SIGINT-shortened batch too, where no ``BatchComplete`` was rendered.
    """
    total = batch_state.total_subjects
    ok = batch_state.success_count
    failed = batch_state.failed_count
    skipped = sum(1 for r in batch_state.results if r.status == "skipped")

    parts = [f"{ok}/{total} completed"]
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    return ", ".join(parts)


def format_subject_table(subjects, output_dir: str, id_depth: int) -> list[str]:
    """
    The ``--dry-run`` table: what would be processed, and where it would land.

    In scope rather than deferred because discovery is heuristic -- the reverse-PE
    match in particular is a first-match guess -- and this replaces the GUI's
    file-summary column, which is the thing a headless user loses. A mis-typed
    glob should cost ten seconds, not a wasted weekend.
    """
    import os

    lines = [f"{len(subjects)} subject(s) resolved (--id-depth {id_depth}):", ""]
    for subject in subjects:
        lines.append(f"  {subject.subject_id}")
        lines.append(f"    files:  {subject.get_files_summary()}")
        lines.append(f"    dwi:    {subject.dwi_path}")
        if subject.reverse_pe_path:
            # A guess worth showing: _find_reverse_pe takes the first pattern
            # match, and it is inert unless --rpe pair is set.
            lines.append(f"    rpe:    {subject.reverse_pe_path}")
        if not subject.is_valid:
            lines.append(f"    MISSING: {', '.join(subject.get_missing_files())}")
        lines.append(f"    output: {os.path.join(output_dir, subject.subject_id)}")
        lines.append("")
    return lines
