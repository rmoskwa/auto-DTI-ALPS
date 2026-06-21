"""
Byte-level characterization of the batch ALPS-results CSV (PRD 0007).

The safety net for ``BatchRunner``'s two CSV-writing paths -- the single-file
``_write_single_csv`` (backward-compat) and the per-shape ``_write_shape_csv``.
It pins the *exact* bytes they emit today (CRLF line terminators, ``.6f`` cells,
blank cells for missing values) for the LAB, PAS, and Both methods. Landed green
on the current hand-rolled writer (commit 2); held byte-for-byte when the writer
is repointed onto ``results_layout.write_alps_csv`` (commit 4).

The single-file path reads each ``SubjectResult``'s primary fields; the per-shape
path reads its ``alps_results_by_shape`` dict -- both pinned here so a wrong field
mapping surfaces as a byte diff.

Pure: no FSL, no nibabel, no pipeline run -- the writer methods are driven over a
hand-built ``BatchState``. Model: ``tests/test_result_model.py``.
"""

from dti_alps.processing.batch import BatchRunner
from dti_alps.processing.state import BatchConfig, BatchState, SubjectResult


def _expected_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """The exact bytes csv.writer emits: comma-joined, CRLF-terminated."""
    lines = [",".join(header), *(",".join(r) for r in rows)]
    return "".join(line + "\r\n" for line in lines).encode()


def _runner(method: str, results: list[SubjectResult], output_dir: str) -> BatchRunner:
    config = BatchConfig(alps_method=method, output_dir=output_dir)
    return BatchRunner(BatchState(config=config, results=list(results)))


_LAB_HEADER = [
    "Filename",
    "Left Hemisphere ALPS-LAB",
    "Right Hemisphere ALPS-LAB",
    "Combined ALPS-LAB",
    "Status",
    "Error",
]
_PAS_HEADER = [
    "Filename",
    "Left Hemisphere ALPS-PAS",
    "Right Hemisphere ALPS-PAS",
    "Combined ALPS-PAS",
    "Status",
    "Error",
]
_BOTH_HEADER = [
    "Filename",
    "Left Hemisphere ALPS-LAB",
    "Right Hemisphere ALPS-LAB",
    "Combined ALPS-LAB",
    "Left Hemisphere ALPS-PAS",
    "Right Hemisphere ALPS-PAS",
    "Combined ALPS-PAS",
    "Status",
    "Error",
]


# --- single-file path (_write_single_csv reads the primary fields) ----------


def test_single_csv_lab_bytes(tmp_path):
    results = [
        SubjectResult(
            subject_id="sub-01",
            folder_path="/d/01",
            status="completed",
            alps_lab_left=1.1,
            alps_lab_right=1.2,
            alps_lab_bilateral=1.15,
        )
    ]
    _runner("ALPS-LAB", results, str(tmp_path))._write_single_csv("ALPS-LAB")

    assert (tmp_path / "alps_results.csv").read_bytes() == _expected_bytes(
        _LAB_HEADER, [["sub-01", "1.100000", "1.200000", "1.150000", "completed", ""]]
    )


def test_single_csv_pas_bytes(tmp_path):
    results = [
        SubjectResult(
            subject_id="sub-02",
            folder_path="/d/02",
            status="completed",
            alps_pas_left=0.9,
            alps_pas_right=0.95,
            alps_pas_bilateral=0.925,
        )
    ]
    _runner("ALPS-PAS", results, str(tmp_path))._write_single_csv("ALPS-PAS")

    assert (tmp_path / "alps_results.csv").read_bytes() == _expected_bytes(
        _PAS_HEADER, [["sub-02", "0.900000", "0.950000", "0.925000", "completed", ""]]
    )


def test_single_csv_both_bytes_with_failed_row(tmp_path):
    results = [
        SubjectResult(
            subject_id="sub-03",
            folder_path="/d/03",
            status="completed",
            alps_lab_left=1.1,
            alps_lab_right=1.2,
            alps_lab_bilateral=1.15,
            alps_pas_left=0.9,
            alps_pas_right=0.95,
            alps_pas_bilateral=0.925,
        ),
        SubjectResult(
            subject_id="sub-bad",
            folder_path="/d/bad",
            status="failed",
            error_message="boom",
        ),
    ]
    _runner("Both", results, str(tmp_path))._write_single_csv("Both")

    assert (tmp_path / "alps_results.csv").read_bytes() == _expected_bytes(
        _BOTH_HEADER,
        [
            [
                "sub-03",
                "1.100000",
                "1.200000",
                "1.150000",
                "0.900000",
                "0.950000",
                "0.925000",
                "completed",
                "",
            ],
            ["sub-bad", "", "", "", "", "", "", "failed", "boom"],
        ],
    )


# --- per-shape path (_write_shape_csv reads alps_results_by_shape) -----------


def test_shape_csv_both_bytes(tmp_path):
    results = [
        SubjectResult(
            subject_id="sub-01",
            folder_path="/d/01",
            status="completed",
            alps_results_by_shape={
                "sphere3_refined": {
                    "alps_lab_left": 1.1,
                    "alps_lab_right": 1.2,
                    "alps_lab_bilateral": 1.15,
                    "alps_pas_left": 0.9,
                    "alps_pas_right": 0.95,
                    "alps_pas_bilateral": 0.925,
                }
            },
        )
    ]
    csv_path = tmp_path / "alps_results_sphere3_refined.csv"
    _runner("Both", results, str(tmp_path))._write_shape_csv(
        str(csv_path), "sphere3_refined", "Both"
    )

    assert csv_path.read_bytes() == _expected_bytes(
        _BOTH_HEADER,
        [
            [
                "sub-01",
                "1.100000",
                "1.200000",
                "1.150000",
                "0.900000",
                "0.950000",
                "0.925000",
                "completed",
                "",
            ]
        ],
    )


def test_shape_csv_missing_shape_yields_blank_cells(tmp_path):
    # A subject lacking the requested shape -> blank metric cells, status/error
    # still from the subject-level result.
    results = [
        SubjectResult(
            subject_id="sub-01",
            folder_path="/d/01",
            status="completed",
            alps_results_by_shape={},
        )
    ]
    csv_path = tmp_path / "alps_results_squarev9.csv"
    _runner("ALPS-LAB", results, str(tmp_path))._write_shape_csv(
        str(csv_path), "squarev9", "ALPS-LAB"
    )

    assert csv_path.read_bytes() == _expected_bytes(
        _LAB_HEADER, [["sub-01", "", "", "", "completed", ""]]
    )
