"""
Unit tests for the tk-free Results Viewer model (``gui/viewer_model``).

Two seams, both value-in/value-out, no display instantiated:

* ``render_dec_slice`` over hand-built (4,4,4) FA/V1/mask arrays, with the
  expected RGB and the post-orientation pixel locations computed by hand as an
  independent oracle (the rot90 mapping ``out[i, j] = in[j, n-1-i]`` is derived,
  not taken from numpy);
* ``load_session`` / ``set_roi_type`` / ``current_metrics`` over a synthetic
  output folder built in ``tmp_path`` from ``nib.Nifti1Image`` volumes and tiny
  CSVs.

Prior art: ``tests/test_alps_calculation.py`` (pure array oracle) and
``tests/test_reanalysis_seam.py`` (synthetic NIfTI fixtures in ``tmp_path``).
"""

import csv

import nibabel as nib
import numpy as np
import pytest

from dti_alps.gui.viewer_model import (
    ROI_NAMES,
    LoadError,
    MetricsView,
    SessionView,
    SubjectRecord,
    ViewerModel,
    discover_roi_options,
    render_dec_slice,
    roi_display_name,
)
from dti_alps.processing.results_layout import (
    REGISTRATION_DIR,
    brain_mask_name,
    roi_dir_name,
)

# --------------------------------------------------------------------------- #
# render_dec_slice: pure array oracle
# --------------------------------------------------------------------------- #
# All render tests use the sagittal view, whose orientation is a single
# rot90(k=1) with no flip: a voxel at slice position (j, k) lands at output
# pixel (3 - k, j) for a 4x4 slice.
#
# FA is now windowed, not per-slice auto-normalised (PRD 0021, Decision 3):
# fa_norm = clip((FA - (center - width/2)) / width, 0, 1). A window of
# (center=1.0, width=2.0) maps FA in [0, 2] linearly onto [0, 1] -- so FA=2
# gives fa_norm 1.0 and FA=1 gives 0.5, reproducing the old fixtures' intent
# without depending on the slice's own max.

# The window that maps FA in [0, 2] -> [0, 1] (center - width/2 == 0).
_WIN_0_2 = (1.0, 2.0)


def _empty_fa() -> np.ndarray:
    return np.zeros((4, 4, 4), dtype=float)


def _empty_v1() -> np.ndarray:
    return np.zeros((4, 4, 4, 3), dtype=float)


class TestRenderColorAndOrientation:
    """DEC colour, per-voxel normalisation, and the sagittal orientation map."""

    def test_four_directions_land_at_expected_pixels(self):
        fa = _empty_fa()
        v1 = _empty_v1()
        s = 1
        # (j, k) -> v1 vector; every bright voxel shares fa=2 so fa_norm == 1.
        fa[s, 0, 0] = 2.0
        v1[s, 0, 0] = (0.5, 0.0, 0.0)  # pure R
        fa[s, 1, 1] = 2.0
        v1[s, 1, 1] = (0.0, 0.5, 0.0)  # pure G
        fa[s, 2, 2] = 2.0
        v1[s, 2, 2] = (0.0, 0.0, 0.5)  # pure B
        fa[s, 3, 3] = 2.0
        v1[s, 3, 3] = (0.3, 0.4, 0.0)  # normalises to (0.75, 1, 0)

        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            s,
            show_rois=False,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )

        assert out.shape == (4, 4, 3)
        assert out.dtype == np.uint8
        # (j=0,k=0) -> (3,0); (1,1) -> (2,1); (2,2) -> (1,2); (3,3) -> (0,3)
        assert tuple(out[3, 0]) == (255, 0, 0)
        assert tuple(out[2, 1]) == (0, 255, 0)
        assert tuple(out[1, 2]) == (0, 0, 255)
        assert tuple(out[0, 3]) == (191, 255, 0)
        # Everything else is black.
        bright = {(3, 0), (2, 1), (1, 2), (0, 3)}
        for i in range(4):
            for j in range(4):
                if (i, j) not in bright:
                    assert tuple(out[i, j]) == (0, 0, 0)


class TestRenderFaModulation:
    """FA modulation scales the DEC intensity by the windowed FA."""

    def test_windowed_fa_scales_intensity(self):
        fa = _empty_fa()
        v1 = _empty_v1()
        s = 0
        fa[s, 0, 0] = 2.0  # top of the [0, 2] window -> fa_norm 1.0
        v1[s, 0, 0] = (1.0, 0.0, 0.0)
        fa[s, 1, 0] = 1.0  # middle of the window -> fa_norm 0.5
        v1[s, 1, 0] = (1.0, 0.0, 0.0)

        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            s,
            show_rois=False,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )

        # k=0 for both -> output row i = 3 - 0 = 3; j = the slice's j index.
        assert tuple(out[3, 0]) == (255, 0, 0)  # full
        assert tuple(out[3, 1]) == (127, 0, 0)  # 0.5 * 255 truncated

    def test_window_is_stable_regardless_of_slice_max(self):
        # The key behaviour change: brightness is set by the window, NOT the
        # slice's own max. A slice whose max FA is 1.0 (not 2.0) still renders
        # FA=1 at fa_norm 0.5 under the [0, 2] window -- the old per-slice
        # normalise would have made it fully bright.
        fa = _empty_fa()
        v1 = _empty_v1()
        fa[0, 0, 0] = 1.0  # this slice's max is 1.0
        v1[0, 0, 0] = (1.0, 0.0, 0.0)

        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            0,
            show_rois=False,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[3, 0]) == (127, 0, 0)  # windowed to 0.5, not 1.0

    def test_narrow_window_brightens_and_clips(self):
        # A window narrowed to [0.4, 0.6] (center 0.5, width 0.2): FA=0.7 is above
        # the top -> saturates to 1.0; FA=0.5 is the middle -> 0.5.
        fa = _empty_fa()
        v1 = _empty_v1()
        fa[0, 0, 0] = 0.7
        v1[0, 0, 0] = (1.0, 0.0, 0.0)
        fa[0, 1, 0] = 0.5
        v1[0, 1, 0] = (1.0, 0.0, 0.0)

        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            0,
            show_rois=False,
            show_brain_mask=False,
            wl_center=0.5,
            wl_width=0.2,
        )
        assert tuple(out[3, 0]) == (255, 0, 0)  # clipped bright
        assert tuple(out[3, 1]) == (127, 0, 0)  # mid-window

    def test_hue_is_untouched_by_window(self):
        # Window/level operates on FA only; the |V1| hue is byte-identical across
        # two different windows for a voxel that stays inside both.
        fa = _empty_fa()
        v1 = _empty_v1()
        fa[0, 0, 0] = 2.0
        v1[0, 0, 0] = (0.3, 0.4, 0.0)  # normalises to (0.75, 1.0, 0.0)

        wide = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            0,
            show_rois=False,
            show_brain_mask=False,
            wl_center=1.0,
            wl_width=2.0,
        )
        narrow = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            0,
            show_rois=False,
            show_brain_mask=False,
            wl_center=1.0,
            wl_width=1.0,  # FA=2 still clips to 1.0 at the top
        )
        # Both fully modulated (fa_norm 1.0), so the hue ratio R:G is identical.
        assert tuple(wide[3, 0]) == tuple(narrow[3, 0]) == (191, 255, 0)


class TestRenderRoiOverlay:
    """ROI voxels paint solid white, gated by show_rois and a non-empty mask set."""

    def _setup(self):
        fa = _empty_fa()
        v1 = _empty_v1()
        roi = np.zeros((4, 4, 4), dtype=float)
        roi[1, 0, 2] = 1.0  # a dark voxel at (j=0, k=2) in slice 1
        return fa, v1, {"left_proj": roi}

    def test_overlay_painted_when_enabled(self):
        fa, v1, masks = self._setup()
        out = render_dec_slice(
            fa,
            v1,
            masks,
            None,
            "sagittal",
            1,
            show_rois=True,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        # (j=0, k=2) -> output (3-2, 0) = (1, 0)
        assert tuple(out[1, 0]) == (255, 255, 255)

    def test_no_overlay_when_disabled(self):
        fa, v1, masks = self._setup()
        out = render_dec_slice(
            fa,
            v1,
            masks,
            None,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[1, 0]) == (0, 0, 0)

    def test_empty_mask_set_is_a_noop(self):
        fa, v1, _ = self._setup()
        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            1,
            show_rois=True,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[1, 0]) == (0, 0, 0)

    def test_overlay_composites_on_top_of_windowed_image(self):
        # The ROI overlay is painted after windowing: even under a narrow window
        # that would brighten the underlying FA, the ROI voxel is pure white.
        fa, v1, masks = self._setup()
        fa[1, 0, 2] = 0.6  # give the ROI voxel some FA under a narrow window
        v1[1, 0, 2] = (1.0, 0.0, 0.0)
        out = render_dec_slice(
            fa,
            v1,
            masks,
            None,
            "sagittal",
            1,
            show_rois=True,
            show_brain_mask=False,
            wl_center=0.5,
            wl_width=0.2,
        )
        assert tuple(out[1, 0]) == (255, 255, 255)


class TestRenderBrainMask:
    """Brain-mask blackening: out-of-brain voxels go black, in-brain untouched,
    ROI overlay wins over the mask, and None/mismatched masks are no-ops."""

    def _setup(self):
        # Two bright voxels in slice 1: (j=0,k=0) and (j=1,k=1). Mask keeps only
        # the first. Sagittal: (j,k) -> output (3-k, j).
        fa = _empty_fa()
        v1 = _empty_v1()
        fa[1, 0, 0] = 2.0
        v1[1, 0, 0] = (1.0, 0.0, 0.0)  # -> output (3, 0), red
        fa[1, 1, 1] = 2.0
        v1[1, 1, 1] = (1.0, 0.0, 0.0)  # -> output (2, 1), red
        mask = np.zeros((4, 4, 4), dtype=float)
        mask[1, 0, 0] = 1.0  # keep only the first voxel
        return fa, v1, mask

    def test_blackens_out_of_brain_and_keeps_in_brain(self):
        fa, v1, mask = self._setup()
        out = render_dec_slice(
            fa,
            v1,
            {},
            mask,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=True,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[3, 0]) == (255, 0, 0)  # in-brain: kept
        assert tuple(out[2, 1]) == (0, 0, 0)  # out-of-brain: blackened

    def test_in_brain_pixels_identical_toggle_on_vs_off(self):
        fa, v1, mask = self._setup()
        on = render_dec_slice(
            fa,
            v1,
            {},
            mask,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=True,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        off = render_dec_slice(
            fa,
            v1,
            {},
            mask,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=False,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        # The one in-brain voxel is byte-for-byte identical either way.
        assert tuple(on[3, 0]) == tuple(off[3, 0]) == (255, 0, 0)

    def test_roi_overlay_wins_over_mask(self):
        # An ROI voxel that falls outside the brain mask is still painted white:
        # blackening runs before the overlay.
        fa, v1, mask = self._setup()
        roi = np.zeros((4, 4, 4), dtype=float)
        roi[1, 1, 1] = 1.0  # the out-of-brain voxel -> output (2, 1)
        out = render_dec_slice(
            fa,
            v1,
            {"left_proj": roi},
            mask,
            "sagittal",
            1,
            show_rois=True,
            show_brain_mask=True,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[2, 1]) == (255, 255, 255)

    def test_none_mask_is_a_noop(self):
        fa, v1, _ = self._setup()
        out = render_dec_slice(
            fa,
            v1,
            {},
            None,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=True,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[2, 1]) == (255, 0, 0)  # nothing blackened

    def test_shape_mismatch_mask_is_a_noop(self):
        fa, v1, _ = self._setup()
        wrong = np.ones((3, 3, 3), dtype=float)  # off the FA/V1 grid
        out = render_dec_slice(
            fa,
            v1,
            {},
            wrong,
            "sagittal",
            1,
            show_rois=False,
            show_brain_mask=True,
            wl_center=_WIN_0_2[0],
            wl_width=_WIN_0_2[1],
        )
        assert tuple(out[2, 1]) == (255, 0, 0)  # unchanged, no raise


class TestRenderBounds:
    """Out-of-range slices return None rather than raising."""

    def test_out_of_range_returns_none(self):
        assert (
            render_dec_slice(
                _empty_fa(), _empty_v1(), {}, None, "sagittal", 4, False, False, 1.0, 2.0
            )
            is None
        )
        assert (
            render_dec_slice(
                _empty_fa(), _empty_v1(), {}, None, "axial", 99, False, False, 1.0, 2.0
            )
            is None
        )


# --------------------------------------------------------------------------- #
# Display-name mapping
# --------------------------------------------------------------------------- #
class TestRoiDisplayName:
    def test_known_tokens(self):
        assert roi_display_name("rois") == "Sphere 3.0mm"
        assert roi_display_name("sphere2p5") == "Sphere 2.5mm"
        assert roi_display_name("squarev9") == "Square 3x3"
        assert roi_display_name("squarev4") == "Square 2x2"

    def test_refined_suffix(self):
        assert roi_display_name("squarev9_refined") == "Square 3x3 (r)"
        assert roi_display_name("rois_refined") == "Sphere 3.0mm (r)"


# --------------------------------------------------------------------------- #
# Synthetic output folder for load_session / set_roi_type / current_metrics
# --------------------------------------------------------------------------- #
def _save_nii(path, arr):
    nib.save(nib.Nifti1Image(arr.astype(np.float32), np.eye(4)), str(path))


def _make_subject(folder, sid, tokens, with_images=True, with_mask=False):
    sub = folder / sid
    sub.mkdir()
    if with_images:
        _save_nii(sub / f"{sid}_FA.nii.gz", np.zeros((4, 4, 4)))
        _save_nii(sub / f"{sid}_V1.nii.gz", np.zeros((4, 4, 4, 3)))
    if with_mask:
        reg_dir = sub / REGISTRATION_DIR
        reg_dir.mkdir()
        _save_nii(reg_dir / brain_mask_name(sid), np.ones((4, 4, 4)))
    for token in tokens:
        roi_dir = sub / roi_dir_name(token)
        roi_dir.mkdir()
        for roi_name in ROI_NAMES:
            _save_nii(roi_dir / f"{sid}_{roi_name}.nii.gz", np.zeros((4, 4, 4)))
    return sub


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


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


class TestLoadSessionBasic:
    def test_single_roi_type(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois"])
        _make_subject(tmp_path, "sub-02", ["rois"])
        _write_csv(
            tmp_path / "alps_results.csv",
            _LAB_HEADER,
            [
                ["sub-01", "1.1", "1.2", "1.15", "completed", ""],
                ["sub-02", "1.3", "1.4", "1.35", "completed", ""],
            ],
        )

        result = ViewerModel().load_session(tmp_path)

        assert isinstance(result, SessionView)
        assert result.roi_options == [("rois", "Sphere 3.0mm")]
        assert result.method == "ALPS-LAB"
        assert [s.subject_id for s in result.subjects] == ["sub-01", "sub-02"]

        rec = result.subjects[0]
        assert isinstance(rec, SubjectRecord)
        assert rec.fa_path == tmp_path / "sub-01" / "sub-01_FA.nii.gz"
        assert rec.v1_path == tmp_path / "sub-01" / "sub-01_V1.nii.gz"
        assert set(rec.all_roi_paths["rois"].keys()) == set(ROI_NAMES)
        assert rec.status == "completed"

    def test_brain_mask_discovered_and_loaded(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois"], with_mask=True)
        _make_subject(tmp_path, "sub-02", ["rois"], with_mask=False)
        _write_csv(
            tmp_path / "alps_results.csv",
            _LAB_HEADER,
            [
                ["sub-01", "1", "1", "1", "ok", ""],
                ["sub-02", "1", "1", "1", "ok", ""],
            ],
        )

        model = ViewerModel()
        result = model.load_session(tmp_path)

        by_id = {r.subject_id: r for r in result.subjects}
        assert by_id["sub-01"].brain_mask_path == (
            tmp_path / "sub-01" / REGISTRATION_DIR / brain_mask_name("sub-01")
        )
        assert by_id["sub-02"].brain_mask_path is None

        # has_brain_mask follows the current subject's mask presence.
        model.select_subject("sub-01")
        assert model.has_brain_mask is True
        model.select_subject("sub-02")
        assert model.has_brain_mask is False

    def test_multiple_roi_types_ordered(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois", "squarev9"])
        _write_csv(
            tmp_path / "alps_results.csv", _LAB_HEADER, [["sub-01", "1", "1", "1", "ok", ""]]
        )
        _write_csv(
            tmp_path / "alps_results_squarev9.csv",
            _LAB_HEADER,
            [["sub-01", "2", "2", "2", "ok", ""]],
        )

        result = ViewerModel().load_session(tmp_path)

        assert result.roi_options == [
            ("rois", "Sphere 3.0mm"),
            ("squarev9", "Square 3x3"),
        ]


class TestLoadSessionErrors:
    def test_folder_missing(self, tmp_path):
        missing = tmp_path / "nope"
        result = ViewerModel().load_session(missing)
        assert isinstance(result, LoadError)
        assert result.kind == "folder_missing"
        assert result.payload == missing

    def test_no_results(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = ViewerModel().load_session(empty)
        assert isinstance(result, LoadError)
        assert result.kind == "no_results"
        assert result.payload == empty

    def test_csv_missing(self, tmp_path):
        # A discoverable rois/ directory but no alps_results.csv.
        _make_subject(tmp_path, "sub-01", ["rois"])
        result = ViewerModel().load_session(tmp_path)
        assert isinstance(result, LoadError)
        assert result.kind == "csv_missing"
        assert result.payload == tmp_path / "alps_results.csv"

    def test_empty_subjects_is_a_session(self, tmp_path):
        # rois/ dir makes the folder discoverable; the CSV exists; but no subject
        # folder carries FA/V1, so the session is valid with zero subjects.
        _make_subject(tmp_path, "sub-01", ["rois"], with_images=False)
        _write_csv(
            tmp_path / "alps_results.csv", _LAB_HEADER, [["sub-01", "1", "1", "1", "ok", ""]]
        )
        result = ViewerModel().load_session(tmp_path)
        assert isinstance(result, SessionView)
        assert result.subjects == []


class TestSetRoiTypeAndMetrics:
    def _build(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["rois", "squarev9"])
        # Default rois CSV: Both method, status "completed".
        _write_csv(
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
            [["sub-01", "1.1", "1.2", "1.15", "0.9", "0.95", "0.925", "completed", ""]],
        )
        # squarev9 CSV: PAS only, status "failed".
        _write_csv(
            tmp_path / "alps_results_squarev9.csv",
            _PAS_HEADER,
            [["sub-01", "0.5", "0.6", "0.55", "failed", "bad fit"]],
        )
        return ViewerModel()

    def test_metrics_none_before_selection(self, tmp_path):
        model = self._build(tmp_path)
        model.load_session(tmp_path)
        assert model.current_metrics() is None

    def test_initial_metrics_from_default_csv(self, tmp_path):
        model = self._build(tmp_path)
        model.load_session(tmp_path)
        assert model.select_subject("sub-01") is True

        m = model.current_metrics()
        assert isinstance(m, MetricsView)
        assert model.current_alps_method == "Both"
        assert (m.lab_left, m.lab_right, m.lab_combined) == (1.1, 1.2, 1.15)
        assert (m.pas_left, m.pas_right, m.pas_combined) == (0.9, 0.95, 0.925)
        assert m.status == "completed"

    def test_metrics_follow_roi_type_switch(self, tmp_path):
        model = self._build(tmp_path)
        session = model.load_session(tmp_path)
        model.select_subject("sub-01")

        # Tree status is frozen on the record from the initial (default) CSV.
        assert session.subjects[0].status == "completed"

        model.set_roi_type("squarev9")
        assert model.current_alps_method == "ALPS-PAS"

        m = model.current_metrics()
        assert (m.lab_left, m.lab_right, m.lab_combined) == (None, None, None)
        assert (m.pas_left, m.pas_right, m.pas_combined) == (0.5, 0.6, 0.55)
        # The metrics-panel status reflects the *current* ROI type's CSV.
        assert m.status == "failed"
        # ...while the record's tree status is unchanged.
        assert session.subjects[0].status == "completed"

    def test_switch_back_to_cached_type_restores_method(self, tmp_path):
        # Revisiting an already-cached ROI type must restore its detected method,
        # not leave the previously-loaded one in place.
        model = self._build(tmp_path)
        model.load_session(tmp_path)
        model.select_subject("sub-01")

        model.set_roi_type("squarev9")
        assert model.current_alps_method == "ALPS-PAS"

        model.set_roi_type("rois")  # 'rois' table is already cached from load
        assert model.current_alps_method == "Both"
        m = model.current_metrics()
        assert (m.lab_left, m.pas_left) == (1.1, 0.9)

    def test_render_and_slice_helpers_after_select(self, tmp_path):
        model = self._build(tmp_path)
        model.load_session(tmp_path)
        model.select_subject("sub-01")

        assert model.num_slices("axial") == 4
        assert model.default_slice("axial") == 2
        assert model.current_shape == (4, 4, 4)
        out = model.render_slice(
            "axial", 2, show_rois=True, show_brain_mask=False, wl_center=0.5, wl_width=1.0
        )
        assert out is not None
        assert out.shape == (4, 4, 3)

    def test_select_unknown_subject_returns_false(self, tmp_path):
        model = self._build(tmp_path)
        model.load_session(tmp_path)
        assert model.select_subject("ghost") is False


class TestDiscoverRoiOptions:
    def test_default_first_then_alphabetical(self, tmp_path):
        _make_subject(tmp_path, "sub-01", ["squarev9", "rois", "sphere2p5"])
        assert discover_roi_options(tmp_path) == ["rois", "sphere2p5", "squarev9"]


class TestDefaultWindow:
    """The volume-derived default window: center = max/2, width = max."""

    def _model_with_fa(self, tmp_path, fa: np.ndarray) -> ViewerModel:
        sub = tmp_path / "sub-01"
        sub.mkdir()
        _save_nii(sub / "sub-01_FA.nii.gz", fa)
        _save_nii(sub / "sub-01_V1.nii.gz", np.zeros((*fa.shape, 3)))
        roi_dir = sub / roi_dir_name("rois")
        roi_dir.mkdir()
        for roi_name in ROI_NAMES:
            _save_nii(roi_dir / f"sub-01_{roi_name}.nii.gz", np.zeros(fa.shape))
        _write_csv(
            tmp_path / "alps_results.csv", _LAB_HEADER, [["sub-01", "1", "1", "1", "ok", ""]]
        )
        model = ViewerModel()
        model.load_session(tmp_path)
        return model

    def test_no_subject_falls_back(self):
        # Before any subject is selected there is no FA volume.
        assert ViewerModel().default_window() == (0.5, 1.0)

    def test_center_half_width_full_for_known_volume(self, tmp_path):
        fa = np.zeros((4, 4, 4), dtype=float)
        fa[1, 1, 1] = 0.8  # the volume max
        fa[2, 2, 2] = 0.3
        model = self._model_with_fa(tmp_path, fa)
        model.select_subject("sub-01")
        center, width = model.default_window()
        assert width == pytest.approx(0.8, abs=1e-5)
        assert center == pytest.approx(0.4, abs=1e-5)

    def test_all_zero_volume_falls_back(self, tmp_path):
        model = self._model_with_fa(tmp_path, np.zeros((4, 4, 4), dtype=float))
        model.select_subject("sub-01")
        assert model.default_window() == (0.5, 1.0)
