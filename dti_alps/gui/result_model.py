"""
tk-free presentation model for the live results dispatch.

``ResultModel`` maps a worker queue message to an ordered list of *view-intents*
— plain frozen dataclasses describing what should change in the GUI. The
``gui/app.py`` adapter interprets each intent into a widget call. The model
holds no Tkinter and addresses console-tree rows by index, so the dispatch can
be unit-tested as a value map rather than a sequence of imperative widget pokes.
"""

from dataclasses import dataclass

from ..processing.messages import (
    BatchCancelled,
    BatchComplete,
    BatchPartial,
    BatchStart,
    BatchSuccess,
    Error,
    Log,
    Stage,
    SubjectComplete,
    SubjectStart,
    WorkerMessage,
)
from ..processing.state import BatchState


@dataclass(frozen=True)
class AppendLog:
    """Append a line to the log console (and the log file)."""

    text: str


@dataclass(frozen=True)
class SetRowStatus:
    """Set the status text + tag of the console-tree row at ``index``."""

    index: int
    text: str
    tag: str


@dataclass(frozen=True)
class UpdateStageStatus:
    """Update the stage indicator (log + button colour) for a pipeline stage."""

    stage: str
    status: str


@dataclass(frozen=True)
class ResetStageButtons:
    """Reset all stage buttons to their default style."""


@dataclass(frozen=True)
class ResultColumn:
    """One column of the batch-results table: a stable key and its display label."""

    key: str
    label: str


@dataclass(frozen=True)
class BatchResultsView:
    """
    The finished batch-results screen as plain, render-ready data.

    Built by :func:`build_batch_results_table` and carried by
    :class:`ShowBatchResults`. Cells are already formatted strings (the ``.4f``
    precision and ``None -> ""`` rule are baked in), so the adapter inserts them
    verbatim and owns only widget chrome — column widths/anchors, the footer
    buttons, and the "Results saved to:" label (fed ``output_dir``).
    """

    title: str
    summary: str
    columns: tuple[ResultColumn, ...]
    rows: tuple[dict[str, str], ...]
    output_dir: str


@dataclass(frozen=True)
class ShowBatchResults:
    """Display the finished batch-results view for a completed batch."""

    view: BatchResultsView


# Union of all view-intents the adapter knows how to apply.
Intent = AppendLog | SetRowStatus | UpdateStageStatus | ResetStageButtons | ShowBatchResults


def _format_cell(value: float | None) -> str:
    """Format one metric cell: a 4-decimal float, or "" when the value is missing."""
    return f"{value:.4f}" if value is not None else ""


def _build_row(result, method: str) -> dict[str, str]:
    """Assemble one subject's row (keyed by column key) for the given ALPS method."""
    if method == "Both":
        return {
            "subject": result.subject_id,
            "lab_left": _format_cell(result.alps_lab_left),
            "lab_right": _format_cell(result.alps_lab_right),
            "lab_combined": _format_cell(result.alps_lab_bilateral),
            "pas_left": _format_cell(result.alps_pas_left),
            "pas_right": _format_cell(result.alps_pas_right),
            "pas_combined": _format_cell(result.alps_pas_bilateral),
            "status": result.status,
        }
    if method == "ALPS-LAB":
        return {
            "subject": result.subject_id,
            "alps_left": _format_cell(result.alps_lab_left),
            "alps_right": _format_cell(result.alps_lab_right),
            "alps_combined": _format_cell(result.alps_lab_bilateral),
            "status": result.status,
        }
    # ALPS-PAS
    return {
        "subject": result.subject_id,
        "alps_left": _format_cell(result.alps_pas_left),
        "alps_right": _format_cell(result.alps_pas_right),
        "alps_combined": _format_cell(result.alps_pas_bilateral),
        "status": result.status,
    }


def build_batch_results_table(batch_state: BatchState) -> BatchResultsView:
    """
    Turn a finished ``BatchState`` into a render-ready :class:`BatchResultsView`.

    The ALPS method chooses the column set ("Both" -> 8 columns; ALPS-LAB /
    ALPS-PAS -> 5) and the per-row metric source; cells are pre-formatted
    (``.4f``, ``None -> ""``). Pure: no Tkinter, no I/O — the live-panel twin of
    the viewer's ``render_dec_slice``.
    """
    method = batch_state.config.alps_method

    if method == "Both":
        columns = (
            ResultColumn("subject", "Subject ID"),
            ResultColumn("lab_left", "Left LAB"),
            ResultColumn("lab_right", "Right LAB"),
            ResultColumn("lab_combined", "Combined LAB"),
            ResultColumn("pas_left", "Left PAS"),
            ResultColumn("pas_right", "Right PAS"),
            ResultColumn("pas_combined", "Combined PAS"),
            ResultColumn("status", "Status"),
        )
    else:
        suffix = "LAB" if method == "ALPS-LAB" else "PAS"
        columns = (
            ResultColumn("subject", "Subject ID"),
            ResultColumn("alps_left", f"Left {suffix}"),
            ResultColumn("alps_right", f"Right {suffix}"),
            ResultColumn("alps_combined", f"Combined {suffix}"),
            ResultColumn("status", "Status"),
        )

    summary = (
        f"{batch_state.success_count}/{batch_state.total_subjects} succeeded, "
        f"{batch_state.failed_count} failed"
    )
    rows = tuple(_build_row(result, method) for result in batch_state.results)

    return BatchResultsView(
        title=f"Batch Processing Results ({method})",
        summary=summary,
        columns=columns,
        rows=rows,
        output_dir=batch_state.config.output_dir,
    )


class ResultModel:
    """
    Translate worker queue messages into ordered view-intents.

    A run has a lifecycle, so the model is constructed once per batch with the
    subject ids; it knows the subject count for the "i/N" progress strings and
    addresses rows by index. It holds no widgets.
    """

    def __init__(self, subject_ids: list[str]):
        self.subject_ids = list(subject_ids)

    @property
    def total(self) -> int:
        """Number of subjects in the run."""
        return len(self.subject_ids)

    def handle(self, msg: WorkerMessage) -> list[Intent]:
        """
        Map one :class:`WorkerMessage` to its view-intents.

        Covers the batch lifecycle plus the shared ``Log``, ``Stage``, and
        ``Error`` messages, reproducing the exact log phrasing. The union is
        closed, so ``case _`` is unreachable in the field — a fallthrough means a
        new message type was added without a handler, which the exhaustiveness
        test catches. Raising (rather than dropping) makes that a loud failure.
        """
        match msg:
            case Log(text):
                return [AppendLog(text)]

            case Stage(stage, status):
                return [UpdateStageStatus(stage, status)]

            case BatchStart(total):
                return [AppendLog(f"Processing 0/{total} subjects")]

            case SubjectStart(index, subject_id):
                return [
                    AppendLog(f"Processing {index + 1}/{self.total}: {subject_id}"),
                    SetRowStatus(index, "Processing", "processing"),
                    ResetStageButtons(),
                ]

            case SubjectComplete(index, result):
                completed = index + 1
                intents: list[Intent] = [AppendLog(f"Completed {completed}/{self.total} subjects")]
                if result.status == "completed":
                    intents.append(SetRowStatus(index, "Completed", "completed"))
                else:
                    intents.append(SetRowStatus(index, "Failed", "failed"))
                return intents

            case BatchComplete(batch_state):
                return [
                    AppendLog(
                        f"Batch complete: {batch_state.success_count}/"
                        f"{batch_state.total_subjects} succeeded"
                    ),
                    ShowBatchResults(build_batch_results_table(batch_state)),
                ]

            case BatchSuccess(batch_state):
                return [
                    AppendLog("All subjects processed successfully!"),
                    ShowBatchResults(build_batch_results_table(batch_state)),
                ]

            case BatchPartial(batch_state):
                return [
                    AppendLog(
                        f"Batch completed with errors: {batch_state.success_count}/"
                        f"{batch_state.total_subjects} succeeded"
                    ),
                    ShowBatchResults(build_batch_results_table(batch_state)),
                ]

            case BatchCancelled():
                return [AppendLog("Batch processing cancelled.")]

            case Error(message):
                return [AppendLog(f"Error: {message}")]

            case _:
                raise ValueError(f"unhandled worker message: {msg!r}")
