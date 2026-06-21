"""
Pure unit tests for the DTI-ALPS index calculation.

These exercise the *science* at the pure-function seam -- ``calculate_alps_lab``
and ``calculate_alps_pas`` take pre-loaded NumPy arrays, so no FSL, MRtrix3, or
sample ``.nii.gz`` files are needed. Expected values are computed by hand from
the published ALPS formula, making each test an independent oracle (it proves
the formula is correct) rather than a snapshot of current output.

Model: ``tests/test_discovery.py`` -- pure, class-grouped, no external tools.

ALPS index (Taoka):
    ALPS = mean(Dx_proj, Dx_assoc) / mean(Dperp_proj, Dperp_assoc)
where, per ROI:
    - projection ROI perpendicular = Dyy
    - association ROI perpendicular = Dzz
For ALPS-PAS, Dx/Dperp are the eigenvalues sorted per voxel by which eigenvector
(V2 vs V3) has the larger |X-component|.
"""

import math

import numpy as np
import pytest

from dti_alps.processing.alps_calculation import calculate_alps_lab, calculate_alps_pas

SHAPE = (4, 4, 4)

# One distinct voxel per ROI, so a region's mean is just that voxel's value.
COORDS = {
    "left_proj": (0, 0, 0),
    "left_assoc": (1, 0, 0),
    "right_proj": (2, 0, 0),
    "right_assoc": (3, 0, 0),
}


def _zeros() -> np.ndarray:
    return np.zeros(SHAPE, dtype=float)


def _mask_at(*coords: tuple[int, int, int]) -> np.ndarray:
    """Build a mask array with 1.0 at each given (i, j, k) coordinate."""
    m = _zeros()
    for c in coords:
        m[c] = 1.0
    return m


def _single_voxel_masks() -> dict[str, np.ndarray]:
    """The four ROI masks, each a single distinct voxel (see COORDS)."""
    return {name: _mask_at(coord) for name, coord in COORDS.items()}


class TestLabGoldenValue:
    """ALPS-LAB wiring against a hand-computed value."""

    def test_component_wiring_and_bilateral(self):
        """
        Prove which component lands in numerator vs denominator for projection
        vs association ROIs. A '99' sentinel sits in the component that must NOT
        be used for that ROI, so any miswiring changes the result.
        """
        masks = _single_voxel_masks()
        lp, la = COORDS["left_proj"], COORDS["left_assoc"]
        rp, ra = COORDS["right_proj"], COORDS["right_assoc"]

        dxx, dyy, dzz = _zeros(), _zeros(), _zeros()
        # left_proj: numerator uses Dxx; denominator uses Dyy (NOT Dzz)
        dxx[lp], dyy[lp], dzz[lp] = 10.0, 2.0, 99.0
        # left_assoc: numerator uses Dxx; denominator uses Dzz (NOT Dyy)
        dxx[la], dyy[la], dzz[la] = 20.0, 99.0, 4.0
        # right_proj
        dxx[rp], dyy[rp], dzz[rp] = 6.0, 1.0, 99.0
        # right_assoc
        dxx[ra], dyy[ra], dzz[ra] = 12.0, 99.0, 3.0

        fa = np.ones(SHAPE)  # all voxels pass the FA threshold
        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        # Per-component means feed the formula
        assert res["Dxx_proj_left"] == pytest.approx(10.0)
        assert res["Dyy_proj_left"] == pytest.approx(2.0)
        assert res["Dxx_assoc_left"] == pytest.approx(20.0)
        assert res["Dzz_assoc_left"] == pytest.approx(4.0)

        # ALPS_left  = mean(10, 20) / mean(2, 4) = 15 / 3   = 5.0
        # ALPS_right = mean(6, 12) / mean(1, 3) = 9  / 2   = 4.5
        assert res["ALPS_left"] == pytest.approx(5.0)
        assert res["ALPS_right"] == pytest.approx(4.5)
        # bilateral = mean(5.0, 4.5) = 4.75
        assert res["ALPS_bilateral"] == pytest.approx(4.75)


class TestFaThresholdFilter:
    """FA-threshold CSF filtering selects which voxels enter the means."""

    def test_subthreshold_voxels_excluded_from_mean(self):
        v_keep1, v_keep2, v_drop = (0, 0, 0), (0, 1, 0), (0, 2, 0)
        masks = {
            "left_proj": _mask_at(v_keep1, v_keep2, v_drop),
            "left_assoc": _mask_at(COORDS["left_assoc"]),
            "right_proj": _mask_at(COORDS["right_proj"]),
            "right_assoc": _mask_at(COORDS["right_assoc"]),
        }
        dxx, dyy, dzz = _zeros(), np.ones(SHAPE), np.ones(SHAPE)
        dxx[v_keep1], dxx[v_keep2], dxx[v_drop] = 2.0, 4.0, 100.0
        # benign values for the other (single-voxel) ROIs so they don't go nan
        for c in (COORDS["left_assoc"], COORDS["right_proj"], COORDS["right_assoc"]):
            dxx[c] = 1.0

        fa = np.ones(SHAPE)
        fa[v_drop] = 0.1  # below threshold 0.2 -> excluded

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)
        # Only the two surviving voxels contribute: mean(2, 4) = 3.0
        assert res["Dxx_proj_left"] == pytest.approx(3.0)

    def test_voxels_above_threshold_are_included(self):
        v1, v2, v3 = (0, 0, 0), (0, 1, 0), (0, 2, 0)
        masks = {
            "left_proj": _mask_at(v1, v2, v3),
            "left_assoc": _mask_at(COORDS["left_assoc"]),
            "right_proj": _mask_at(COORDS["right_proj"]),
            "right_assoc": _mask_at(COORDS["right_assoc"]),
        }
        dxx, dyy, dzz = _zeros(), np.ones(SHAPE), np.ones(SHAPE)
        dxx[v1], dxx[v2], dxx[v3] = 2.0, 4.0, 100.0
        for c in (COORDS["left_assoc"], COORDS["right_proj"], COORDS["right_assoc"]):
            dxx[c] = 1.0

        fa = np.ones(SHAPE)
        fa[v3] = 0.1  # 0.1 still passes a threshold of 0.05

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.05)
        # All three voxels included: mean(2, 4, 100) = 35.333...
        assert res["Dxx_proj_left"] == pytest.approx((2.0 + 4.0 + 100.0) / 3)


class TestPasEigenvectorSort:
    """The ALPS-PAS per-voxel eigenvector-sort (the most error-prone science)."""

    def test_diff_x_selects_eigenvalue_with_larger_x_aligned_eigenvector(self):
        masks = _single_voxel_masks()
        lp, la = COORDS["left_proj"], COORDS["left_assoc"]
        rp, ra = COORDS["right_proj"], COORDS["right_assoc"]

        l2, l3, v2_x, v3_x = _zeros(), _zeros(), _zeros(), _zeros()

        # left_proj: |v2_x| > |v3_x| -> diff_X = l2, diff_perp = l3
        l2[lp], l3[lp], v2_x[lp], v3_x[lp] = 5.0, 3.0, 0.9, 0.1
        # right_proj: |v3_x| > |v2_x| -> diff_X = l3, diff_perp = l2
        l2[rp], l3[rp], v2_x[rp], v3_x[rp] = 5.0, 3.0, 0.1, 0.9
        # left_assoc: NEGATIVE v2_x with larger magnitude -> proves |.| is used
        l2[la], l3[la], v2_x[la], v3_x[la] = 2.0, 1.0, -0.8, 0.2
        # right_assoc: filler so the ROI is non-empty
        l2[ra], l3[ra], v2_x[ra], v3_x[ra] = 2.0, 1.0, 0.9, 0.1

        fa = np.ones(SHAPE)
        res = calculate_alps_pas(l2, l3, v2_x, v3_x, fa, masks, fa_threshold=0.2)

        # left_proj: diff_X = l2 (5), diff_perp = l3 (3)
        assert res["Dxx_proj_left"] == pytest.approx(5.0)
        assert res["Dyy_proj_left"] == pytest.approx(3.0)
        # right_proj: diff_X = l3 (3), diff_perp = l2 (5)
        assert res["Dxx_proj_right"] == pytest.approx(3.0)
        assert res["Dyy_proj_right"] == pytest.approx(5.0)
        # left_assoc: |-0.8| > |0.2| -> diff_X = l2 (2)
        assert res["Dxx_assoc_left"] == pytest.approx(2.0)


class TestPasGoldenValue:
    """ALPS-PAS end-to-end against a hand-computed value, sort included."""

    def test_hand_computed_alps_through_full_pas_path(self):
        masks = _single_voxel_masks()
        lp, la = COORDS["left_proj"], COORDS["left_assoc"]
        rp, ra = COORDS["right_proj"], COORDS["right_assoc"]

        l2, l3, v2_x, v3_x = _zeros(), _zeros(), _zeros(), _zeros()
        # left_proj : V2 dominant -> diff_X = l2 = 6, diff_perp = l3 = 2
        l2[lp], l3[lp], v2_x[lp], v3_x[lp] = 6.0, 2.0, 0.8, 0.2
        # left_assoc: V3 dominant -> diff_X = l3 = 4, diff_perp = l2 = 1
        l2[la], l3[la], v2_x[la], v3_x[la] = 1.0, 4.0, 0.2, 0.8
        # right_proj : V2 dominant -> diff_X = l2 = 8, diff_perp = l3 = 2
        l2[rp], l3[rp], v2_x[rp], v3_x[rp] = 8.0, 2.0, 0.8, 0.2
        # right_assoc: V2 dominant -> diff_X = l2 = 4, diff_perp = l3 = 1
        l2[ra], l3[ra], v2_x[ra], v3_x[ra] = 4.0, 1.0, 0.8, 0.2

        fa = np.ones(SHAPE)
        res = calculate_alps_pas(l2, l3, v2_x, v3_x, fa, masks, fa_threshold=0.2)

        # ALPS_left  = mean(6, 4) / mean(2, 1) = 5   / 1.5 = 10/3
        # ALPS_right = mean(8, 4) / mean(2, 1) = 6   / 1.5 = 4.0
        assert res["ALPS_left"] == pytest.approx(10.0 / 3.0)
        assert res["ALPS_right"] == pytest.approx(4.0)
        assert res["ALPS_bilateral"] == pytest.approx((10.0 / 3.0 + 4.0) / 2)


class TestDegenerateCases:
    """Pinned (not fixed) behavior for degenerate inputs."""

    def test_empty_roi_after_fa_filter_yields_nan(self):
        masks = _single_voxel_masks()
        dxx, dyy, dzz = np.full(SHAPE, 2.0), np.ones(SHAPE), np.ones(SHAPE)

        fa = np.ones(SHAPE)
        fa[COORDS["left_proj"]] = 0.0  # left_proj's only voxel is filtered out

        # np.mean of an empty selection warns and returns nan (existing behavior)
        with pytest.warns(RuntimeWarning):
            res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        assert math.isnan(res["Dxx_proj_left"])
        assert math.isnan(res["ALPS_left"])  # nan propagates to the hemisphere
        assert not math.isnan(res["ALPS_right"])  # other hemisphere unaffected
        # bilateral is omitted entirely when a hemisphere is nan
        assert "ALPS_bilateral" not in res

    def test_zero_denominator_yields_nan(self):
        masks = _single_voxel_masks()
        # Dyy (proj perp) and Dzz (assoc perp) are zero -> denominator == 0
        dxx, dyy, dzz = np.full(SHAPE, 2.0), _zeros(), _zeros()
        fa = np.ones(SHAPE)

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        # numerator is finite (2.0) but the `denominator > 0` guard yields nan
        assert math.isnan(res["ALPS_left"])
        assert math.isnan(res["ALPS_right"])

    def test_nan_denominator_yields_nan(self):
        masks = _single_voxel_masks()
        dxx, dyy, dzz = np.full(SHAPE, 2.0), np.ones(SHAPE), np.ones(SHAPE)
        dyy[COORDS["left_proj"]] = np.nan  # -> Dyy_proj_left nan -> denom nan
        fa = np.ones(SHAPE)

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        # `nan > 0` is False, so the guard yields nan for the left hemisphere
        assert math.isnan(res["ALPS_left"])
        assert not math.isnan(res["ALPS_right"])


class TestPropertyInvariants:
    """Gross-wiring sanity checks that hold regardless of hand-computed goldens."""

    def test_lab_all_components_equal_gives_alps_one(self):
        masks = _single_voxel_masks()
        c = 3.0
        dxx, dyy, dzz = np.full(SHAPE, c), np.full(SHAPE, c), np.full(SHAPE, c)
        fa = np.ones(SHAPE)

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        assert res["ALPS_left"] == pytest.approx(1.0)
        assert res["ALPS_right"] == pytest.approx(1.0)
        assert res["ALPS_bilateral"] == pytest.approx(1.0)

    def test_pas_equal_eigenvalues_give_alps_one(self):
        """diff_X == diff_perp everywhere regardless of the sort -> ALPS = 1."""
        masks = _single_voxel_masks()
        l2 = l3 = np.full(SHAPE, 2.0)
        # arbitrary, conflicting eigenvector alignments -- must not matter
        v2_x, v3_x = np.full(SHAPE, 0.9), np.full(SHAPE, 0.1)

        res = calculate_alps_pas(l2, l3, v2_x, v3_x, np.ones(SHAPE), masks, fa_threshold=0.2)

        assert res["ALPS_left"] == pytest.approx(1.0)
        assert res["ALPS_right"] == pytest.approx(1.0)
        assert res["ALPS_bilateral"] == pytest.approx(1.0)

    def test_bilateral_is_mean_of_left_and_right(self):
        masks = _single_voxel_masks()
        lp, la = COORDS["left_proj"], COORDS["left_assoc"]
        rp, ra = COORDS["right_proj"], COORDS["right_assoc"]
        dxx, dyy, dzz = _zeros(), _zeros(), _zeros()
        dxx[lp], dyy[lp], dxx[la], dzz[la] = 10.0, 2.0, 20.0, 4.0
        dxx[rp], dyy[rp], dxx[ra], dzz[ra] = 6.0, 1.0, 12.0, 3.0
        fa = np.ones(SHAPE)

        res = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        assert res["ALPS_bilateral"] == pytest.approx((res["ALPS_left"] + res["ALPS_right"]) / 2)

    def test_left_and_right_are_independent(self):
        masks = _single_voxel_masks()
        lp, la = COORDS["left_proj"], COORDS["left_assoc"]
        rp, ra = COORDS["right_proj"], COORDS["right_assoc"]
        fa = np.ones(SHAPE)

        dxx, dyy, dzz = _zeros(), _zeros(), _zeros()
        dxx[lp], dyy[lp], dxx[la], dzz[la] = 10.0, 2.0, 20.0, 4.0
        dxx[rp], dyy[rp], dxx[ra], dzz[ra] = 6.0, 1.0, 12.0, 3.0
        res_full = calculate_alps_lab(dxx, dyy, dzz, fa, masks, fa_threshold=0.2)

        # Perturb ONLY the right-side inputs
        dxx2, dyy2, dzz2 = dxx.copy(), dyy.copy(), dzz.copy()
        dxx2[rp] = 999.0
        dzz2[ra] = 999.0
        res_perturbed = calculate_alps_lab(dxx2, dyy2, dzz2, fa, masks, fa_threshold=0.2)

        assert res_perturbed["ALPS_left"] == pytest.approx(res_full["ALPS_left"])
        assert res_perturbed["ALPS_right"] != pytest.approx(res_full["ALPS_right"])
