"""
tk-free presentation model for the live results dispatch (PRD 0004).

``ResultModel`` maps a worker queue message to an ordered list of *view-intents*
— plain frozen dataclasses describing what should change in the GUI. The
``gui/app.py`` adapter interprets each intent into a widget call. The model
holds no Tkinter and addresses console-tree rows by index, so the dispatch can
be unit-tested as a value map rather than a sequence of imperative widget pokes.
"""

from dataclasses import dataclass

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
class ShowBatchResults:
    """Display the batch-results view for a finished batch."""

    batch_state: BatchState


@dataclass(frozen=True)
class ShowResults:
    """Display the single-subject (legacy) results view."""

    data: dict


# Union of all view-intents the adapter knows how to apply.
Intent = (
    AppendLog
    | SetRowStatus
    | UpdateStageStatus
    | ResetStageButtons
    | ShowBatchResults
    | ShowResults
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

    def handle(self, msg) -> list[Intent]:
        """
        Map one worker message ``(msg_type, data)`` to its view-intents.

        Reproduces every branch of the former ``_handle_result`` dispatch,
        including the legacy single-subject ``complete``/``failed``/``cancelled``
        path and the exact log phrasing. Unknown message types yield no intents.
        """
        msg_type = msg[0]
        data = msg[1] if len(msg) > 1 else None

        if msg_type == "log":
            return [AppendLog(data)]

        if msg_type == "stage":
            stage, status = data
            return [UpdateStageStatus(stage, status)]

        if msg_type == "batch_start":
            total = data
            return [AppendLog(f"Processing 0/{total} subjects")]

        if msg_type == "subject_start":
            index, subject_id = data
            return [
                AppendLog(f"Processing {index + 1}/{self.total}: {subject_id}"),
                SetRowStatus(index, "Processing", "processing"),
                ResetStageButtons(),
            ]

        if msg_type == "subject_complete":
            index, result = data
            completed = index + 1
            intents: list[Intent] = [AppendLog(f"Completed {completed}/{self.total} subjects")]
            if result.status == "completed":
                intents.append(SetRowStatus(index, "Completed", "completed"))
            else:
                intents.append(SetRowStatus(index, "Failed", "failed"))
            return intents

        if msg_type == "batch_complete":
            batch_state = data
            return [
                AppendLog(
                    f"Batch complete: {batch_state.success_count}/"
                    f"{batch_state.total_subjects} succeeded"
                ),
                ShowBatchResults(batch_state),
            ]

        if msg_type == "batch_success":
            batch_state = data
            return [
                AppendLog("All subjects processed successfully!"),
                ShowBatchResults(batch_state),
            ]

        if msg_type == "batch_partial":
            batch_state = data
            return [
                AppendLog(
                    f"Batch completed with errors: {batch_state.success_count}/"
                    f"{batch_state.total_subjects} succeeded"
                ),
                ShowBatchResults(batch_state),
            ]

        if msg_type == "batch_cancelled":
            return [AppendLog("Batch processing cancelled.")]

        if msg_type == "complete":
            # Single subject complete (legacy)
            return [AppendLog("Pipeline completed successfully!"), ShowResults(data)]

        if msg_type == "failed":
            return [AppendLog("Pipeline failed.")]

        if msg_type == "cancelled":
            return [AppendLog("Pipeline cancelled.")]

        if msg_type == "error":
            return [AppendLog(f"Error: {data}")]

        return []
