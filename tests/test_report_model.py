"""
Unit tests for the tk-free Quality Report model (``gui/report_model``).

The tested seam is ``QualityReportModel`` (and its pure ``build_quality_report_view``
helper): folder / shape / subject-subset in; discovered shapes, per-shape subject
lists, and a :class:`QualityReportView` (grouped rows, ``.6f``/blank cells, empty
Radial-Asymmetry group under LAB-only) out -- never a widget detail. This mirrors
``tests/test_viewer_model.py`` and reuses the *unchanged* ``processing/report.py``
compute leaf, so the in-app numbers are the CLI's numbers.

Fixtures are synthetic NIfTI volumes in ``tmp_path`` (prior art:
``tests/test_viewer_model.py``, ``tests/test_reanalysis_seam.py``). A single ROI
voxel with hand-chosen FA/V1/L2/L3 makes every metric a known constant, so the
formatted cells can be asserted exactly. Imports neither Qt nor tkinter.
"""

import nibabel as nib
import numpy as np

from dti_alps.gui.report_model import (
    FolderScan,
    LoadError,
    QualityReportModel,
    QualityReportView,
    build_quality_report_view,
)
from dti_alps.processing import report
from dti_alps.processing.results_layout import roi_dir_name

_ROI_NAMES = ("left_proj", "left_assoc", "right_proj", "right_assoc")

# The metric groups / ROI sub-columns the view exposes, in CSV order.
_GROUPS = (
    "Directional Alignment (V1)",
    "Angular Dispersion (V1)",
    "Fractional Anisotropy",
    "Radial Asymmetry (λ2/λ3)",
)
_ROI_COLUMNS = ("l_proj", "l_assoc", "r_proj", "r_assoc")


def _save_nii(path, arr):
    nib.save(nib.Nifti1Image(arr.astype(np.float32), np.eye(4)), str(path))


def _make_subject(folder, sid, tokens, with_images=True, with_l2l3=False):
    """Build one subject dir with FA/V1 (+ optional L2/L3) and ROI masks per token.

    A single ROI voxel at (1,1,1) carries FA=0.5 and V1=(0, 0.6, 0.8) so:
    proj directional (|V1_z|)=0.8, assoc directional (|V1_y|)=0.6,
    angular dispersion (single vector)=0.0, fa_mean=0.5. With L2/L3 present,
    L2=0.4/L3=0.2 gives radial asymmetry=2.0.
    """
    sub = folder / sid
    sub.mkdir()

    if with_images:
        fa = np.zeros((4, 4, 4))
        fa[1, 1, 1] = 0.5
        _save_nii(sub / f"{sid}_FA.nii.gz", fa)

        v1 = np.zeros((4, 4, 4, 3))
        v1[1, 1, 1] = (0.0, 0.6, 0.8)
        _save_nii(sub / f"{sid}_V1.nii.gz", v1)

        if with_l2l3:
            l2 = np.zeros((4, 4, 4))
            l2[1, 1, 1] = 0.4
            _save_nii(sub / f"{sid}_L2.nii.gz", l2)
            l3 = np.zeros((4, 4, 4))
            l3[1, 1, 1] = 0.2
            _save_nii(sub / f"{sid}_L3.nii.gz", l3)

    for token in tokens:
        roi_dir = sub / roi_dir_name(token)
        roi_dir.mkdir()
        for roi_name in _ROI_NAMES:
            mask = np.zeros((4, 4, 4))
            mask[1, 1, 1] = 1.0
            _save_nii(roi_dir / f"{sid}_{roi_name}.nii.gz", mask)

    return sub


def _cell(view: QualityReportView, subject_id: str, group: str, roi: str) -> str:
    """The formatted cell for one (subject, metric group, ROI column)."""
    g = view.metric_groups.index(group)
    r = view.roi_columns.index(roi)
    row = next(row for row in view.rows if row.subject_id == subject_id)
    return row.cells[g * len(view.roi_columns) + r]


# --------------------------------------------------------------------------- #
# load_folder: shape discovery + errors-as-data
# --------------------------------------------------------------------------- #
class TestLoadFolder:
    def test_discovers_shapes_with_display_labels(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois", "squarev9"])
        _make_subject(tmp_path, "sub-02", ["rois"])

        result = QualityReportModel().load_folder(tmp_path)

        assert isinstance(result, FolderScan)
        assert result.folder == tmp_path
        assert result.shapes == [("rois", "Sphere 3.0mm"), ("squarev9", "Square 3x3")]

    def test_folder_missing(self, tmp_path):
        missing = tmp_path / "nope"
        result = QualityReportModel().load_folder(missing)
        assert isinstance(result, LoadError)
        assert result.kind == "folder_missing"
        assert result.payload == missing

    def test_no_shapes(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = QualityReportModel().load_folder(empty)
        assert isinstance(result, LoadError)
        assert result.kind == "no_shapes"
        assert result.payload == empty

    def test_load_error_leaves_model_unchanged(self, tmp_path):
        model = QualityReportModel()
        model.load_folder(tmp_path / "nope")
        assert model.folder is None
        assert model.shape_tokens == []


# --------------------------------------------------------------------------- #
# subjects_for_shape: the shape drives the subject list
# --------------------------------------------------------------------------- #
class TestSubjectsForShape:
    def test_only_subjects_with_that_shape(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois", "squarev9"])
        _make_subject(tmp_path, "sub-02", ["rois"])
        _make_subject(tmp_path, "sub-03", ["squarev9"])

        model = QualityReportModel()
        model.load_folder(tmp_path)

        assert model.subjects_for_shape("rois") == ["sub-01", "sub-02"]
        assert model.subjects_for_shape("squarev9") == ["sub-01", "sub-03"]

    def test_empty_when_no_folder_loaded(self):
        assert QualityReportModel().subjects_for_shape("rois") == []


# --------------------------------------------------------------------------- #
# generate: subset compute + cell formatting
# --------------------------------------------------------------------------- #
class TestGenerate:
    def test_subset_has_exactly_the_chosen_subjects(self, tmp_path):
        for sid in ("sub-01", "sub-02", "sub-03", "sub-04"):
            _make_subject(tmp_path, sid, ["rois"])

        model = QualityReportModel()
        model.load_folder(tmp_path)
        view = model.generate("rois", ["sub-02", "sub-04"])

        assert [row.subject_id for row in view.rows] == ["sub-02", "sub-04"]

    def test_rows_follow_on_disk_order_not_subset_order(self, tmp_path):
        for sid in ("sub-01", "sub-02", "sub-03"):
            _make_subject(tmp_path, sid, ["rois"])

        model = QualityReportModel()
        model.load_folder(tmp_path)
        # Subset given out of order; rows still come back sorted.
        view = model.generate("rois", ["sub-03", "sub-01"])
        assert [row.subject_id for row in view.rows] == ["sub-01", "sub-03"]

    def test_view_structure_and_labels(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["squarev9"])
        model = QualityReportModel()
        model.load_folder(tmp_path)
        view = model.generate("squarev9", ["sub-01"])

        assert view.shape_token == "squarev9"
        assert view.shape_label == "Square 3x3"
        assert view.metric_groups == _GROUPS
        assert view.roi_columns == _ROI_COLUMNS
        assert len(view.rows[0].cells) == len(_GROUPS) * len(_ROI_COLUMNS)

    def test_cells_are_known_formatted_values(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois"], with_l2l3=True)
        model = QualityReportModel()
        model.load_folder(tmp_path)
        view = model.generate("rois", ["sub-01"])

        # Directional alignment: proj reads |V1_z|=0.8, assoc reads |V1_y|=0.6.
        assert _cell(view, "sub-01", "Directional Alignment (V1)", "l_proj") == "0.800000"
        assert _cell(view, "sub-01", "Directional Alignment (V1)", "l_assoc") == "0.600000"
        assert _cell(view, "sub-01", "Directional Alignment (V1)", "r_proj") == "0.800000"
        assert _cell(view, "sub-01", "Directional Alignment (V1)", "r_assoc") == "0.600000"
        # Single-vector dispersion is 0; FA mean is the one voxel's 0.5.
        assert _cell(view, "sub-01", "Angular Dispersion (V1)", "l_proj") == "0.000000"
        assert _cell(view, "sub-01", "Fractional Anisotropy", "r_assoc") == "0.500000"
        # L2/L3 present -> radial asymmetry 0.4/0.2 = 2.0.
        assert _cell(view, "sub-01", "Radial Asymmetry (λ2/λ3)", "l_proj") == "2.000000"

    def test_lab_only_leaves_radial_asymmetry_blank(self, tmp_path):
        # No L2/L3 files (LAB-only run) -> the whole Radial-Asymmetry group is blank.
        _make_subject(tmp_path, "sub-01", ["rois"], with_l2l3=False)
        model = QualityReportModel()
        model.load_folder(tmp_path)
        view = model.generate("rois", ["sub-01"])

        for roi in _ROI_COLUMNS:
            assert _cell(view, "sub-01", "Radial Asymmetry (λ2/λ3)", roi) == ""
        # ...but the other groups are still populated.
        assert _cell(view, "sub-01", "Fractional Anisotropy", "l_proj") == "0.500000"

    def test_progress_callback_fires_per_subject(self, tmp_path):
        for sid in ("sub-01", "sub-02"):
            _make_subject(tmp_path, sid, ["rois"])
        model = QualityReportModel()
        model.load_folder(tmp_path)

        seen = []
        model.generate(
            "rois", ["sub-01", "sub-02"], progress_cb=lambda i, n, sid: seen.append((i, n, sid))
        )
        assert seen == [(0, 2, "sub-01"), (1, 2, "sub-02")]

    def test_cancel_stops_early(self, tmp_path):
        for sid in ("sub-01", "sub-02", "sub-03"):
            _make_subject(tmp_path, sid, ["rois"])
        model = QualityReportModel()
        model.load_folder(tmp_path)

        # Cancel before the second subject is computed.
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1

        view = model.generate("rois", ["sub-01", "sub-02", "sub-03"], cancel=cancel)
        assert [row.subject_id for row in view.rows] == ["sub-01"]

    def test_generate_writes_nothing_to_disk(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois"])
        model = QualityReportModel()
        model.load_folder(tmp_path)
        model.generate("rois", ["sub-01"])
        assert list(tmp_path.glob("quality_report_*.csv")) == []


# --------------------------------------------------------------------------- #
# save_csv: byte-for-byte parity with the CLI --report
# --------------------------------------------------------------------------- #
class TestSaveCsv:
    def test_save_matches_cli_whole_folder_report(self, tmp_path):
        # Two subjects, all selected -> the saved subset CSV must equal the CLI's
        # whole-folder quality_report_rois.csv for the same shape.
        _make_subject(tmp_path, "sub-01", ["rois"], with_l2l3=True)
        _make_subject(tmp_path, "sub-02", ["rois"], with_l2l3=True)

        model = QualityReportModel()
        model.load_folder(tmp_path)
        view = model.generate("rois", ["sub-01", "sub-02"])

        saved = tmp_path / "from_model.csv"
        model.save_csv(view, saved)

        # The CLI whole-folder driver writes quality_report_rois.csv into tmp_path.
        report.generate_reports(str(tmp_path), log_callback=lambda _m: None)
        cli = tmp_path / "quality_report_rois.csv"

        assert saved.read_text() == cli.read_text()

    def test_default_csv_name(self):
        model = QualityReportModel()
        assert model.default_csv_name("squarev9_refined") == "quality_report_squarev9_refined.csv"


# --------------------------------------------------------------------------- #
# build_quality_report_view: pure helper on empty input
# --------------------------------------------------------------------------- #
class TestBuildView:
    def test_empty_subjects_is_a_headered_but_rowless_view(self):
        view = build_quality_report_view("rois", [])
        assert isinstance(view, QualityReportView)
        assert view.shape_label == "Sphere 3.0mm"
        assert view.metric_groups == _GROUPS
        assert view.rows == ()
