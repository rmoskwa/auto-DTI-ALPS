"""
Byte-level characterization of the reanalysis ALPS-results CSV.

The safety net for ``reanalysis._write_reanalysis_csv``: it pins the *exact*
bytes the writer emits today (CRLF line terminators, ``.6f`` cells, blank cells
for missing values) for the LAB, PAS, and Both methods. Landed green on the
current hand-rolled writer (commit 2); held byte-for-byte when the writer is
repointed onto ``results_layout.write_alps_csv`` (commit 4), so "the file did
not change" is proven against what ships today.

Pure: no FSL, no nibabel, no sample volumes -- the writer is a free function
driven over constructed ``ReanalysisResult`` objects.
"""

from dti_alps.processing.reanalysis import ReanalysisResult, _write_reanalysis_csv


def _expected_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """The exact bytes csv.writer emits: comma-joined, CRLF-terminated."""
    lines = [",".join(header), *(",".join(r) for r in rows)]
    return "".join(line + "\r\n" for line in lines).encode()


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


def test_lab_bytes(tmp_path):
    results = [
        ReanalysisResult(
            subject_id="sub-01",
            status="completed",
            alps_lab_left=1.1,
            alps_lab_right=1.2,
            alps_lab_bilateral=1.15,
        )
    ]
    path = tmp_path / "alps_results_sphere3.csv"
    _write_reanalysis_csv(str(path), results, "ALPS-LAB")

    assert path.read_bytes() == _expected_bytes(
        _LAB_HEADER, [["sub-01", "1.100000", "1.200000", "1.150000", "completed", ""]]
    )


def test_pas_bytes(tmp_path):
    results = [
        ReanalysisResult(
            subject_id="sub-02",
            status="completed",
            alps_pas_left=0.9,
            alps_pas_right=0.95,
            alps_pas_bilateral=0.925,
        )
    ]
    path = tmp_path / "alps_results_squarev9.csv"
    _write_reanalysis_csv(str(path), results, "ALPS-PAS")

    assert path.read_bytes() == _expected_bytes(
        _PAS_HEADER, [["sub-02", "0.900000", "0.950000", "0.925000", "completed", ""]]
    )


def test_both_bytes(tmp_path):
    results = [
        ReanalysisResult(
            subject_id="sub-03",
            status="completed",
            alps_lab_left=1.1,
            alps_lab_right=1.2,
            alps_lab_bilateral=1.15,
            alps_pas_left=0.9,
            alps_pas_right=0.95,
            alps_pas_bilateral=0.925,
        )
    ]
    path = tmp_path / "alps_results.csv"
    _write_reanalysis_csv(str(path), results, "Both")

    assert path.read_bytes() == _expected_bytes(
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
            ]
        ],
    )


def test_failed_subject_blank_cells_and_error_message(tmp_path):
    # None metrics -> blank cells; error_message -> Error column; the bilateral
    # field lands in the Combined column.
    results = [
        ReanalysisResult(
            subject_id="sub-01",
            status="completed",
            alps_lab_left=1.1,
            alps_lab_right=1.2,
            alps_lab_bilateral=1.15,
        ),
        ReanalysisResult(subject_id="sub-bad", status="failed", error_message="boom"),
    ]
    path = tmp_path / "alps_results.csv"
    _write_reanalysis_csv(str(path), results, "ALPS-LAB")

    assert path.read_bytes() == _expected_bytes(
        _LAB_HEADER,
        [
            ["sub-01", "1.100000", "1.200000", "1.150000", "completed", ""],
            ["sub-bad", "", "", "", "failed", "boom"],
        ],
    )
