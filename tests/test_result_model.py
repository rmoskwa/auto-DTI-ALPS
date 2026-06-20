"""
Golden-replay tests for the result-dispatch model (PRD 0004, commit 5).

Feeds representative worker-message sequences through ResultModel and asserts
the entire concatenated stream of view-intents. This is the regression net for
the dispatch that used to live in gui/app.py::_handle_result — no Tkinter object
is named and the window is never instantiated.
"""

from dataclasses import dataclass

from dti_alps.gui.result_model import (
    AppendLog,
    ResetStageButtons,
    ResultModel,
    SetRowStatus,
    ShowBatchResults,
    ShowResults,
    UpdateStageStatus,
)


@dataclass
class FakeResult:
    """Stand-in for a SubjectResult — the model only reads .status."""

    status: str


@dataclass
class FakeBatchState:
    """Stand-in for BatchState — the model only reads these two counts."""

    success_count: int
    total_subjects: int


def _replay(model: ResultModel, messages: list) -> list:
    """Run every message through the model and concatenate the intent stream."""
    intents: list = []
    for msg in messages:
        intents.extend(model.handle(msg))
    return intents


def test_batch_lifecycle_golden():
    """A full batch run maps to the exact concatenated intent stream."""
    model = ResultModel(["sub-a", "sub-b"])
    bs_complete = FakeBatchState(success_count=1, total_subjects=2)
    bs_success = FakeBatchState(success_count=2, total_subjects=2)

    messages = [
        ("batch_start", 2),
        ("subject_start", (0, "sub-a")),
        ("stage", ("denoise", "running")),
        ("stage", ("denoise", "complete")),
        ("log", "  Auto-detected PE direction: AP"),
        ("subject_complete", (0, FakeResult("completed"))),
        ("subject_start", (1, "sub-b")),
        ("subject_complete", (1, FakeResult("failed"))),
        ("batch_complete", bs_complete),
        ("batch_success", bs_success),
    ]

    assert _replay(model, messages) == [
        AppendLog("Processing 0/2 subjects"),
        AppendLog("Processing 1/2: sub-a"),
        SetRowStatus(0, "Processing", "processing"),
        ResetStageButtons(),
        UpdateStageStatus("denoise", "running"),
        UpdateStageStatus("denoise", "complete"),
        AppendLog("  Auto-detected PE direction: AP"),
        AppendLog("Completed 1/2 subjects"),
        SetRowStatus(0, "Completed", "completed"),
        AppendLog("Processing 2/2: sub-b"),
        SetRowStatus(1, "Processing", "processing"),
        ResetStageButtons(),
        AppendLog("Completed 2/2 subjects"),
        SetRowStatus(1, "Failed", "failed"),
        AppendLog("Batch complete: 1/2 succeeded"),
        ShowBatchResults(bs_complete),
        AppendLog("All subjects processed successfully!"),
        ShowBatchResults(bs_success),
    ]


def test_batch_partial_and_cancelled_golden():
    """batch_partial logs the success ratio + shows results; batch_cancelled logs only."""
    model = ResultModel(["a", "b", "c"])
    bs_partial = FakeBatchState(success_count=2, total_subjects=3)

    messages = [
        ("batch_partial", bs_partial),
        ("batch_cancelled", None),
    ]

    assert _replay(model, messages) == [
        AppendLog("Batch completed with errors: 2/3 succeeded"),
        ShowBatchResults(bs_partial),
        AppendLog("Batch processing cancelled."),
    ]


def test_legacy_single_subject_and_terminal_golden():
    """The legacy single-subject path and the terminal branches are reproduced."""
    model = ResultModel(["only"])
    alps = {"ALPS_left": 1.23}

    messages = [
        ("complete", alps),
        ("failed", None),
        ("cancelled", None),
        ("error", "boom"),
    ]

    assert _replay(model, messages) == [
        AppendLog("Pipeline completed successfully!"),
        ShowResults(alps),
        AppendLog("Pipeline failed."),
        AppendLog("Pipeline cancelled."),
        AppendLog("Error: boom"),
    ]


def test_log_and_stage_passthrough():
    """A bare log message and a stage message map 1:1."""
    model = ResultModel([])
    assert model.handle(("log", "hello")) == [AppendLog("hello")]
    assert model.handle(("stage", ("roi", "running"))) == [UpdateStageStatus("roi", "running")]


def test_unknown_message_yields_no_intents():
    """An unrecognized message type produces no intents (and does not raise)."""
    model = ResultModel(["x"])
    assert model.handle(("totally_unknown", 42)) == []


def test_total_tracks_subject_count():
    """The 'i/N' strings use the subject count the model was constructed with."""
    model = ResultModel(["s1", "s2", "s3"])
    assert model.handle(("subject_start", (0, "s1"))) == [
        AppendLog("Processing 1/3: s1"),
        SetRowStatus(0, "Processing", "processing"),
        ResetStageButtons(),
    ]
