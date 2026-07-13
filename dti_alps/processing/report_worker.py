"""
Background worker + message channel for the in-app Quality Report (PRD 0022).

The GUI companion to ``--report`` needs to run the (per-subject, file-loading)
quality compute off the GUI thread and stay cancellable, exactly as the pipeline
does -- but the quality report is *not* a batch lifecycle, so it does **not**
travel on the pipeline's closed :data:`~dti_alps.processing.messages.WorkerMessage`
union. This module is the report's own, separate channel:

* a small set of report-only messages (:class:`ReportProgress`,
  :class:`ReportComplete`, :class:`ReportError`, :class:`ReportCancelled`), and
* :class:`ReportWorker`, a ``threading.Thread`` (like ``BatchWorker``, *not* a
  ``QThread``) that composes the unchanged ``report.py`` leaf functions over a
  chosen **subject subset** and pushes those messages onto a ``queue.Queue`` the
  adapter drains.

Engine/GUI split: this is an engine module -- stdlib (``threading``/``queue``/
``dataclasses``) plus ``report.py`` only, no Qt and no ``dti_alps.gui`` import.
The messages carry engine-native :class:`~dti_alps.processing.report.SubjectReportData`
(never a GUI ``QualityReportView``); the display-name mapping and cell formatting
that turn those rows into a view live GUI-side, so the dependency arrow stays
``gui -> processing``.
"""

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import report
from .report import SubjectReportData


@dataclass(frozen=True)
class ReportProgress:
    """Subject ``index`` of ``total`` (``subject_id``) is about to be computed."""

    index: int
    total: int
    subject_id: str


@dataclass(frozen=True)
class ReportComplete:
    """The subset compute finished. Carries the engine-native per-subject rows.

    ``subjects_data`` is the list of :class:`SubjectReportData` that produced
    metrics (subjects whose required files were missing are dropped, exactly as
    the CLI drops them). The adapter turns these into a ``QualityReportView`` and
    can persist them via ``write_report_csv`` for byte-for-byte CLI parity.
    """

    shape_token: str
    subjects_data: list[SubjectReportData] = field(default_factory=list)


@dataclass(frozen=True)
class ReportError:
    """An unhandled exception aborted the compute."""

    message: str


@dataclass(frozen=True)
class ReportCancelled:
    """The user cancelled the compute before it finished."""


# The report's own, separate message set. Deliberately NOT part of the pipeline's
# closed ``WorkerMessage`` union (``processing/messages.py``): a report event can
# never leak into pipeline dispatch and the batch-lifecycle union stays pristine.
ReportMessage = ReportProgress | ReportComplete | ReportError | ReportCancelled


class ReportWorker(threading.Thread):
    """Run the Quality Report subset compute in the background, cancellably.

    Mirrors ``BatchWorker``'s discipline: a daemon ``threading.Thread`` that
    pushes typed messages onto ``result_queue`` and checks ``cancel_event`` at
    each subject boundary. The in-flight subject is allowed to finish; the run
    stops before the next one.
    """

    def __init__(
        self,
        output_dir: str | Path,
        shape_token: str,
        subject_ids: list[str],
        result_queue: queue.Queue,
        cancel_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.output_dir = Path(output_dir)
        self.shape_token = shape_token
        self.subject_ids = subject_ids
        self.result_queue = result_queue
        self.cancel_event = cancel_event

    def run(self):
        """Compute metrics for the chosen subset and emit the report messages."""
        try:
            # Resolve the subset against the shape's discoverable subjects, keeping
            # the on-disk (sorted) order so the rows match the CLI's ordering.
            pairs = report.discover_subjects_for_shape(self.output_dir, self.shape_token)
            wanted = set(self.subject_ids)
            selected = [(sid, subject_dir) for sid, subject_dir in pairs if sid in wanted]
            total = len(selected)

            subjects_data: list[SubjectReportData] = []
            for index, (subject_id, subject_dir) in enumerate(selected):
                if self.cancel_event.is_set():
                    self.result_queue.put(ReportCancelled())
                    return

                self.result_queue.put(ReportProgress(index, total, subject_id))
                data = report.calculate_subject_metrics(subject_id, subject_dir, self.shape_token)
                if data is not None:
                    subjects_data.append(data)

            if self.cancel_event.is_set():
                self.result_queue.put(ReportCancelled())
                return

            self.result_queue.put(ReportComplete(self.shape_token, subjects_data))

        except Exception as e:  # pragma: no cover - mirrors BatchWorker's guard
            self.result_queue.put(ReportError(str(e)))
