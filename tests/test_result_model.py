"""
Tests for the result-dispatch model and the batch-results presenter.

Two layers, both value-in/value-out — no Tkinter object is named and the window
is never instantiated:

- ``build_batch_results_table`` — a pure suite over the three ALPS methods,
  asserting columns, title, summary, and formatted rows (the ``.4f`` precision
  and ``None -> ""`` rules) against a hand-built ``BatchState``.
- A golden replay that feeds representative worker-message sequences through
  ``ResultModel`` and asserts the entire concatenated view-intent stream,
  including that ``ShowBatchResults`` wraps the builder output.
"""

from dataclasses import dataclass

from dti_alps.gui.result_model import (
    AppendLog,
    ResetStageButtons,
    ResultColumn,
    ResultModel,
    SetRowStatus,
    ShowBatchResults,
    UpdateStageStatus,
    build_batch_results_table,
)
from dti_alps.processing.discovery import SubjectFiles
from dti_alps.processing.state import BatchConfig, BatchState, SubjectResult


@dataclass
class FakeResult:
    """Stand-in for a SubjectResult in subject_complete messages — only .status is read."""

    status: str


def _batch(method: str, results: list[SubjectResult], output_dir: str = "/out") -> BatchState:
    """A real BatchState whose subject list is sized 1:1 with ``results``."""
    config = BatchConfig(alps_method=method, output_dir=output_dir)
    subjects = [SubjectFiles(folder_path=r.folder_path, subject_id=r.subject_id) for r in results]
    return BatchState(config=config, subjects=subjects, results=list(results))


# ---------------------------------------------------------------------------
# build_batch_results_table — pure data-in/data-out
# ---------------------------------------------------------------------------


def test_build_both_columns_title_summary_and_rows():
    """'Both' yields 8 columns; rows carry .4f LAB+PAS cells, with None -> ''."""
    results = [
        SubjectResult(
            subject_id="sub-a",
            folder_path="/d/a",
            status="completed",
            alps_lab_left=1.0,
            alps_lab_right=2.0,
            alps_lab_bilateral=1.5,
            alps_pas_left=0.5,
            alps_pas_right=0.6,
            alps_pas_bilateral=0.55,
        ),
        SubjectResult(subject_id="sub-b", folder_path="/d/b", status="failed"),
    ]
    view = build_batch_results_table(_batch("Both", results, output_dir="/out/both"))

    assert view.title == "Batch Processing Results (Both)"
    assert view.summary == "1/2 succeeded, 1 failed"
    assert view.output_dir == "/out/both"
    assert view.columns == (
        ResultColumn("subject", "Subject ID"),
        ResultColumn("lab_left", "Left LAB"),
        ResultColumn("lab_right", "Right LAB"),
        ResultColumn("lab_combined", "Combined LAB"),
        ResultColumn("pas_left", "Left PAS"),
        ResultColumn("pas_right", "Right PAS"),
        ResultColumn("pas_combined", "Combined PAS"),
        ResultColumn("status", "Status"),
    )
    assert view.rows == (
        {
            "subject": "sub-a",
            "lab_left": "1.0000",
            "lab_right": "2.0000",
            "lab_combined": "1.5000",
            "pas_left": "0.5000",
            "pas_right": "0.6000",
            "pas_combined": "0.5500",
            "status": "completed",
        },
        {
            "subject": "sub-b",
            "lab_left": "",
            "lab_right": "",
            "lab_combined": "",
            "pas_left": "",
            "pas_right": "",
            "pas_combined": "",
            "status": "failed",
        },
    )


def test_build_alps_lab_uses_lab_metrics():
    """'ALPS-LAB' yields 5 LAB-labelled columns sourced from the alps_lab_* fields."""
    results = [
        SubjectResult(
            subject_id="s1",
            folder_path="/d/s1",
            status="completed",
            alps_lab_left=0.1234,
            alps_lab_right=0.6789,
            alps_lab_bilateral=0.4,
            # PAS values must be ignored by the LAB column set:
            alps_pas_left=9.9,
            alps_pas_right=9.9,
            alps_pas_bilateral=9.9,
        ),
    ]
    view = build_batch_results_table(_batch("ALPS-LAB", results, output_dir="/out/lab"))

    assert view.title == "Batch Processing Results (ALPS-LAB)"
    assert view.summary == "1/1 succeeded, 0 failed"
    assert view.columns == (
        ResultColumn("subject", "Subject ID"),
        ResultColumn("alps_left", "Left LAB"),
        ResultColumn("alps_right", "Right LAB"),
        ResultColumn("alps_combined", "Combined LAB"),
        ResultColumn("status", "Status"),
    )
    assert view.rows == (
        {
            "subject": "s1",
            "alps_left": "0.1234",
            "alps_right": "0.6789",
            "alps_combined": "0.4000",
            "status": "completed",
        },
    )


def test_build_alps_pas_uses_pas_metrics_and_blanks_missing():
    """'ALPS-PAS' yields 5 PAS-labelled columns from alps_pas_*; a None cell renders ''."""
    results = [
        SubjectResult(
            subject_id="s1",
            folder_path="/d/s1",
            status="completed",
            # LAB values must be ignored by the PAS column set:
            alps_lab_left=9.9,
            alps_lab_right=9.9,
            alps_lab_bilateral=9.9,
            alps_pas_left=0.3,
            alps_pas_right=None,
            alps_pas_bilateral=0.30,
        ),
    ]
    view = build_batch_results_table(_batch("ALPS-PAS", results, output_dir="/out/pas"))

    assert view.title == "Batch Processing Results (ALPS-PAS)"
    assert view.columns == (
        ResultColumn("subject", "Subject ID"),
        ResultColumn("alps_left", "Left PAS"),
        ResultColumn("alps_right", "Right PAS"),
        ResultColumn("alps_combined", "Combined PAS"),
        ResultColumn("status", "Status"),
    )
    assert view.rows == (
        {
            "subject": "s1",
            "alps_left": "0.3000",
            "alps_right": "",
            "alps_combined": "0.3000",
            "status": "completed",
        },
    )


def test_build_empty_batch_has_columns_but_no_rows():
    """A batch with no results still resolves its method's columns; rows are empty."""
    view = build_batch_results_table(_batch("Both", [], output_dir="/out/empty"))

    assert view.rows == ()
    assert view.summary == "0/0 succeeded, 0 failed"
    assert tuple(c.key for c in view.columns) == (
        "subject",
        "lab_left",
        "lab_right",
        "lab_combined",
        "pas_left",
        "pas_right",
        "pas_combined",
        "status",
    )


# ---------------------------------------------------------------------------
# ResultModel golden replay — dispatch wiring (ShowBatchResults wraps the builder)
# ---------------------------------------------------------------------------


def _replay(model: ResultModel, messages: list) -> list:
    """Run every message through the model and concatenate the intent stream."""
    intents: list = []
    for msg in messages:
        intents.extend(model.handle(msg))
    return intents


def test_batch_lifecycle_golden():
    """A full batch run maps to the exact concatenated intent stream."""
    model = ResultModel(["sub-a", "sub-b"])
    bs_complete = _batch(
        "Both",
        [
            SubjectResult(subject_id="sub-a", folder_path="/d/a", status="completed"),
            SubjectResult(subject_id="sub-b", folder_path="/d/b", status="failed"),
        ],
    )
    bs_success = _batch(
        "Both",
        [
            SubjectResult(subject_id="sub-a", folder_path="/d/a", status="completed"),
            SubjectResult(subject_id="sub-b", folder_path="/d/b", status="completed"),
        ],
    )

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
        ShowBatchResults(build_batch_results_table(bs_complete)),
        AppendLog("All subjects processed successfully!"),
        ShowBatchResults(build_batch_results_table(bs_success)),
    ]


def test_batch_partial_and_cancelled_golden():
    """batch_partial logs the success ratio + shows results; batch_cancelled logs only."""
    model = ResultModel(["a", "b", "c"])
    bs_partial = _batch(
        "Both",
        [
            SubjectResult(subject_id="a", folder_path="/d/a", status="completed"),
            SubjectResult(subject_id="b", folder_path="/d/b", status="completed"),
            SubjectResult(subject_id="c", folder_path="/d/c", status="failed"),
        ],
    )

    messages = [
        ("batch_partial", bs_partial),
        ("batch_cancelled", None),
    ]

    assert _replay(model, messages) == [
        AppendLog("Batch completed with errors: 2/3 succeeded"),
        ShowBatchResults(build_batch_results_table(bs_partial)),
        AppendLog("Batch processing cancelled."),
    ]


def test_error_survives_and_legacy_trio_is_gone():
    """``error`` is batch-reachable and stays; the removed single-subject trio yields nothing."""
    model = ResultModel(["only"])

    assert model.handle(("error", "boom")) == [AppendLog("Error: boom")]
    # The legacy single-subject branches were deleted with their view.
    assert model.handle(("complete", {"ALPS_left": 1.23})) == []
    assert model.handle(("failed", None)) == []
    assert model.handle(("cancelled", None)) == []


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
