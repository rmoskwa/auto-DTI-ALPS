"""
Pure unit tests for the results-on-disk contract (``processing/results_layout``).

These exercise value-in/value-out seams: the naming helpers round-trip as plain
strings, and ``read_alps_csv`` is driven over tiny CSVs written to ``tmp_path``.
No FSL, MRtrix3, or sample ``.nii.gz`` are needed. The legacy no-suffix case
pins the drift the old viewer carried (``Left Hemisphere ALPS`` with no method
suffix) so the one typed reader can never silently lose it.

Model: ``tests/test_alps_calculation.py`` / ``tests/test_discovery.py`` -- pure,
class-grouped, no external tools.
"""

import csv

import pytest

from dti_alps.processing.results_layout import (
    METHOD_BOTH,
    METHOD_LAB,
    METHOD_PAS,
    ROI_NAMES,
    AlpsRow,
    AlpsTable,
    alps_columns,
    alps_csv_name,
    detect_method,
    parse_roi_dir,
    read_alps_csv,
    roi_dir_name,
    roi_mask_glob,
    roi_mask_name,
    write_alps_csv,
)


def _write_csv(path, header: list[str], rows: list[list]):
    """Write a results CSV exactly as the engine's csv.writer would."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
    return path


class TestNamingRoundTrip:
    """roi_dir_name / parse_roi_dir / alps_csv_name as a pure naming contract."""

    @pytest.mark.parametrize(
        "token",
        ["rois", "squarev9", "squarev4", "sphere2p5", "sphere3", "squarev9_refined"],
    )
    def test_dir_round_trips_for_whole_tokens(self, token):
        assert parse_roi_dir(roi_dir_name(token)) == token

    def test_default_token_is_the_bare_rois_dir(self):
        assert roi_dir_name("rois") == "rois"
        assert parse_roi_dir("rois") == "rois"

    def test_non_default_token_gets_the_rois_prefix(self):
        assert roi_dir_name("squarev9") == "rois_squarev9"
        assert parse_roi_dir("rois_squarev9") == "squarev9"

    def test_refined_flag_appends_suffix(self):
        assert roi_dir_name("squarev9", refined=True) == "rois_squarev9_refined"
        assert alps_csv_name("squarev9", refined=True) == "alps_results_squarev9_refined.csv"

    def test_parse_rejects_non_roi_dirs(self):
        assert parse_roi_dir("registration") is None
        assert parse_roi_dir("sub-01") is None

    def test_csv_name(self):
        assert alps_csv_name("rois") == "alps_results.csv"
        assert alps_csv_name("squarev9") == "alps_results_squarev9.csv"
        assert alps_csv_name("sphere2p5") == "alps_results_sphere2p5.csv"


class TestDetectMethod:
    """Method detection is a property of the column set."""

    def test_both_when_both_left_columns_present(self):
        assert (
            detect_method(["Left Hemisphere ALPS-LAB", "Left Hemisphere ALPS-PAS"]) == METHOD_BOTH
        )

    def test_pas_only(self):
        assert detect_method(["Left Hemisphere ALPS-PAS"]) == METHOD_PAS

    def test_lab_only(self):
        assert detect_method(["Left Hemisphere ALPS-LAB"]) == METHOD_LAB

    def test_legacy_no_suffix_reads_as_lab(self):
        assert detect_method(["Left Hemisphere ALPS"]) == METHOD_LAB


class TestReadAlpsCsvLab:
    """LAB-only CSV (as batch.py emits for method ALPS-LAB)."""

    def test_method_and_values(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results.csv",
            [
                "Filename",
                "Left Hemisphere ALPS-LAB",
                "Right Hemisphere ALPS-LAB",
                "Combined ALPS-LAB",
                "Status",
                "Error",
            ],
            [["sub-01", "1.10", "1.20", "1.15", "completed", ""]],
        )
        table = read_alps_csv(path)

        assert isinstance(table, AlpsTable)
        assert table.method == METHOD_LAB
        row = table.rows["sub-01"]
        assert (row.lab_left, row.lab_right, row.lab_combined) == (1.10, 1.20, 1.15)
        # PAS untouched for a LAB table.
        assert (row.pas_left, row.pas_right, row.pas_combined) == (None, None, None)
        assert row.status == "completed"
        assert row.error == ""


class TestReadAlpsCsvPas:
    """PAS-only CSV."""

    def test_method_and_values(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results_squarev9.csv",
            [
                "Filename",
                "Left Hemisphere ALPS-PAS",
                "Right Hemisphere ALPS-PAS",
                "Combined ALPS-PAS",
                "Status",
                "Error",
            ],
            [["sub-02", "0.90", "0.95", "0.925", "completed", ""]],
        )
        table = read_alps_csv(path)

        assert table.method == METHOD_PAS
        row = table.rows["sub-02"]
        assert (row.pas_left, row.pas_right, row.pas_combined) == (0.90, 0.95, 0.925)
        assert (row.lab_left, row.lab_right, row.lab_combined) == (None, None, None)


class TestReadAlpsCsvBoth:
    """Both-method CSV carries the full LAB and PAS column set."""

    def test_method_and_values(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results.csv",
            [
                "Filename",
                "Left Hemisphere ALPS-LAB",
                "Right Hemisphere ALPS-LAB",
                "Combined ALPS-LAB",
                "Left Hemisphere ALPS-PAS",
                "Right Hemisphere ALPS-PAS",
                "Combined ALPS-PAS",
                "Status",
                "Error",
            ],
            [["sub-03", "1.1", "1.2", "1.15", "0.9", "0.95", "0.925", "completed", ""]],
        )
        table = read_alps_csv(path)

        assert table.method == METHOD_BOTH
        row = table.rows["sub-03"]
        assert (row.lab_left, row.lab_right, row.lab_combined) == (1.1, 1.2, 1.15)
        assert (row.pas_left, row.pas_right, row.pas_combined) == (0.9, 0.95, 0.925)


class TestReadAlpsCsvLegacy:
    """The drifted legacy no-suffix fallback the old viewer carried."""

    def test_no_suffix_columns_resolve_as_lab(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results.csv",
            [
                "Filename",
                "Left Hemisphere ALPS",
                "Right Hemisphere ALPS",
                "Combined ALPS",
                "Status",
                "Error",
            ],
            [["sub-legacy", "1.30", "1.40", "1.35", "completed", ""]],
        )
        table = read_alps_csv(path)

        assert table.method == METHOD_LAB
        row = table.rows["sub-legacy"]
        assert (row.lab_left, row.lab_right, row.lab_combined) == (1.30, 1.40, 1.35)


class TestReadAlpsCsvEdges:
    """Blank cells, missing Filename, and multiple rows."""

    def test_blank_and_missing_values_become_none(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results.csv",
            [
                "Filename",
                "Left Hemisphere ALPS-LAB",
                "Right Hemisphere ALPS-LAB",
                "Combined ALPS-LAB",
                "Status",
                "Error",
            ],
            [["sub-bad", "", "n/a", "1.15", "failed", "boom"]],
        )
        table = read_alps_csv(path)

        row = table.rows["sub-bad"]
        assert row.lab_left is None  # blank
        assert row.lab_right is None  # non-numeric
        assert row.lab_combined == 1.15
        assert row.status == "failed"
        assert row.error == "boom"

    def test_rows_without_filename_are_skipped(self, tmp_path):
        path = _write_csv(
            tmp_path / "alps_results.csv",
            ["Filename", "Left Hemisphere ALPS-LAB", "Status", "Error"],
            [
                ["sub-01", "1.1", "completed", ""],
                ["", "9.9", "completed", ""],
            ],
        )
        table = read_alps_csv(path)

        assert list(table.rows.keys()) == ["sub-01"]


# --------------------------------------------------------------------------- #
# Writer twin: alps_columns / write_alps_csv (the inverse of read_alps_csv)
# --------------------------------------------------------------------------- #
class TestAlpsColumns:
    """The canonical ordered header is a property of the method."""

    def test_lab_header(self):
        assert alps_columns(METHOD_LAB) == [
            "Filename",
            "Left Hemisphere ALPS-LAB",
            "Right Hemisphere ALPS-LAB",
            "Combined ALPS-LAB",
            "Status",
            "Error",
        ]

    def test_pas_header(self):
        assert alps_columns(METHOD_PAS) == [
            "Filename",
            "Left Hemisphere ALPS-PAS",
            "Right Hemisphere ALPS-PAS",
            "Combined ALPS-PAS",
            "Status",
            "Error",
        ]

    def test_both_header_is_lab_then_pas(self):
        assert alps_columns(METHOD_BOTH) == [
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


def _expected_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """The exact bytes csv.writer emits: comma-joined, CRLF-terminated."""
    lines = [",".join(header), *(",".join(r) for r in rows)]
    return "".join(line + "\r\n" for line in lines).encode()


class TestWriteAlpsCsv:
    """write_alps_csv emits the suffixed schema with .6f cells and CRLF lines."""

    def test_lab_bytes(self, tmp_path):
        table = AlpsTable(
            method=METHOD_LAB,
            rows={
                "sub-01": AlpsRow(
                    subject_id="sub-01",
                    status="completed",
                    error="",
                    lab_left=1.1,
                    lab_right=1.2,
                    lab_combined=1.15,
                )
            },
        )
        path = tmp_path / "alps_results.csv"
        write_alps_csv(path, table)

        assert path.read_bytes() == _expected_bytes(
            alps_columns(METHOD_LAB),
            [["sub-01", "1.100000", "1.200000", "1.150000", "completed", ""]],
        )

    def test_pas_bytes(self, tmp_path):
        table = AlpsTable(
            method=METHOD_PAS,
            rows={
                "sub-02": AlpsRow(
                    subject_id="sub-02",
                    status="completed",
                    error="",
                    pas_left=0.9,
                    pas_right=0.95,
                    pas_combined=0.925,
                )
            },
        )
        path = tmp_path / "alps_results_squarev9.csv"
        write_alps_csv(path, table)

        assert path.read_bytes() == _expected_bytes(
            alps_columns(METHOD_PAS),
            [["sub-02", "0.900000", "0.950000", "0.925000", "completed", ""]],
        )

    def test_both_bytes(self, tmp_path):
        table = AlpsTable(
            method=METHOD_BOTH,
            rows={
                "sub-03": AlpsRow(
                    subject_id="sub-03",
                    status="completed",
                    error="",
                    lab_left=1.1,
                    lab_right=1.2,
                    lab_combined=1.15,
                    pas_left=0.9,
                    pas_right=0.95,
                    pas_combined=0.925,
                )
            },
        )
        path = tmp_path / "alps_results.csv"
        write_alps_csv(path, table)

        assert path.read_bytes() == _expected_bytes(
            alps_columns(METHOD_BOTH),
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

    def test_missing_values_become_empty_cells(self, tmp_path):
        table = AlpsTable(
            method=METHOD_LAB,
            rows={
                "sub-bad": AlpsRow(
                    subject_id="sub-bad",
                    status="failed",
                    error="boom",
                    lab_left=None,
                    lab_right=None,
                    lab_combined=1.15,
                )
            },
        )
        path = tmp_path / "alps_results.csv"
        write_alps_csv(path, table)

        assert path.read_bytes() == _expected_bytes(
            alps_columns(METHOD_LAB),
            [["sub-bad", "", "", "1.150000", "failed", "boom"]],
        )

    def test_rows_written_in_table_order(self, tmp_path):
        table = AlpsTable(
            method=METHOD_LAB,
            rows={
                "sub-02": AlpsRow(subject_id="sub-02", status="completed", error=""),
                "sub-01": AlpsRow(subject_id="sub-01", status="completed", error=""),
            },
        )
        path = tmp_path / "alps_results.csv"
        write_alps_csv(path, table)

        body = path.read_bytes().decode().splitlines()
        assert [line.split(",")[0] for line in body[1:]] == ["sub-02", "sub-01"]


class TestReadWriteRoundTrip:
    """AlpsTable is the single currency: read(write(table)) == table to .6f."""

    @pytest.mark.parametrize(
        "table",
        [
            AlpsTable(
                method=METHOD_LAB,
                rows={
                    "sub-01": AlpsRow(
                        subject_id="sub-01",
                        status="completed",
                        error="",
                        lab_left=1.1,
                        lab_right=1.2,
                        lab_combined=1.15,
                    )
                },
            ),
            AlpsTable(
                method=METHOD_PAS,
                rows={
                    "sub-02": AlpsRow(
                        subject_id="sub-02",
                        status="completed",
                        error="",
                        pas_left=0.9,
                        pas_right=0.95,
                        pas_combined=0.925,
                    )
                },
            ),
            AlpsTable(
                method=METHOD_BOTH,
                rows={
                    "sub-03": AlpsRow(
                        subject_id="sub-03",
                        status="failed",
                        error="boom",
                        lab_left=1.1,
                        lab_right=1.2,
                        lab_combined=1.15,
                        pas_left=0.9,
                        pas_right=0.95,
                        pas_combined=0.925,
                    )
                },
            ),
        ],
    )
    def test_round_trips(self, tmp_path, table):
        path = tmp_path / "alps_results.csv"
        write_alps_csv(path, table)
        assert read_alps_csv(path) == table


class TestRoiMaskNaming:
    """roi_mask_name / roi_mask_glob: a producer/consumer pair over one template."""

    def test_name_is_subject_prefixed(self):
        assert roi_mask_name("sub-01", "left_proj") == "sub-01_left_proj.nii.gz"

    def test_glob_wildcards_the_subject(self):
        assert roi_mask_glob("left_proj") == "*_left_proj.nii.gz"

    def test_glob_matches_what_name_writes(self):
        # The written name and the glob that finds it share one template.
        import fnmatch

        for roi_name in ROI_NAMES:
            written = roi_mask_name("sub-01", roi_name)
            assert fnmatch.fnmatch(written, roi_mask_glob(roi_name))

    def test_canonical_set_has_the_four_names(self):
        assert set(ROI_NAMES) == {"left_proj", "right_proj", "left_assoc", "right_assoc"}
