"""
Pure unit tests for the DTI-ALPS ROI-placement science.

These exercise the placement *science* at the pure-function seam in
``dti_alps.processing.roi_placement`` -- the mask creators, the quality score,
and the joint pair-adaptation all take pre-built NumPy arrays and return
masks/tuples, so no FSL, MRtrix3, or sample ``.nii.gz`` files are needed.
Expected values are computed *by hand* from the placement rules (corner
selection, purity, the crossing-fiber penalty, the drift constraint), making
each test an independent oracle -- it proves the rule is right rather than
freezing whatever the code currently emits.

Model: ``tests/test_alps_calculation.py`` -- pure, class-grouped, no external
tools, hand-computed oracles.
"""

import math

import numpy as np
import pytest

from dti_alps.processing.roi_placement import (
    adaptive_roi_pair_placement,
    calculate_roi_quality,
    create_sphere_mask,
    create_square_v4_mask,
    create_square_v9_mask,
    find_mask_centroid,
)


def _true_coords(mask: np.ndarray) -> set[tuple[int, int, int]]:
    """The set of (x, y, z) voxels that are True in a mask."""
    return {tuple(int(c) for c in coord) for coord in zip(*np.where(mask))}


class TestSphereMask:
    """create_sphere_mask -- mm-distance membership, inclusive boundary."""

    def test_isotropic_membership_and_inclusive_boundary(self):
        # 8^3 volume, centre (4,4,4), 1mm isotropic voxels, radius 2mm.
        mask = create_sphere_mask((8, 8, 8), (4, 4, 4), 2.0, (1.0, 1.0, 1.0))
        # A voxel at EXACTLY radius (dist 2.0mm) is included: membership is
        # dist_sq <= r^2, so this pins the <= (not <) and guards a silent
        # ROI-size change.
        assert mask[6, 4, 4]  # (x-4)=2 -> 2.0mm == radius -> present
        assert mask[4, 6, 4]
        assert mask[4, 4, 6]
        # Just beyond the radius is excluded.
        assert not mask[7, 4, 4]  # 3.0mm > 2.0mm
        # The centre is always present.
        assert mask[4, 4, 4]

    def test_anisotropic_voxels_use_mm_not_voxel_distance(self):
        # x voxels are 2mm wide, y/z are 1mm; radius 2mm.
        mask = create_sphere_mask((8, 8, 8), (4, 4, 4), 2.0, (2.0, 1.0, 1.0))
        # x: one voxel = 2mm -> on the boundary (present); two voxels = 4mm (out).
        assert mask[5, 4, 4]
        assert not mask[6, 4, 4]
        # y: two voxels = 2mm -> still on the boundary (present).
        assert mask[4, 6, 4]
        assert not mask[4, 7, 4]


class TestSquareV9Mask:
    """create_square_v9_mask -- a 3x3 in-plane block at one Z slice."""

    def test_full_block_is_nine_voxels(self):
        mask = create_square_v9_mask((8, 8, 8), (4, 4, 4))
        expected = {(x, y, 4) for x in (3, 4, 5) for y in (3, 4, 5)}
        assert _true_coords(mask) == expected
        assert int(mask.sum()) == 9

    def test_truncates_at_volume_edge(self):
        # Centre on the x=0 face: the x=-1 column is dropped, leaving 6 voxels.
        mask = create_square_v9_mask((8, 8, 8), (0, 4, 4))
        expected = {(x, y, 4) for x in (0, 1) for y in (3, 4, 5)}
        assert _true_coords(mask) == expected
        assert int(mask.sum()) == 6


class TestSquareV4CornerSelection:
    """create_square_v4_mask -- the V1-optimized 2x2 corner choice."""

    # Configurations (centroid as a corner), in selection order:
    #   0 bottom-left, 1 bottom-right, 2 top-left, 3 top-right.
    def _config_voxels(self, cx, cy, cz):
        return {
            0: {(cx, cy, cz), (cx + 1, cy, cz), (cx, cy + 1, cz), (cx + 1, cy + 1, cz)},
            1: {(cx - 1, cy, cz), (cx, cy, cz), (cx - 1, cy + 1, cz), (cx, cy + 1, cz)},
            2: {(cx, cy - 1, cz), (cx + 1, cy - 1, cz), (cx, cy, cz), (cx + 1, cy, cz)},
            3: {(cx - 1, cy - 1, cz), (cx, cy - 1, cz), (cx - 1, cy, cz), (cx, cy, cz)},
        }

    def test_proj_picks_unique_max_v1z_config(self):
        # Make config 0 the strict unique max of mean |V1_z| for a projection
        # ROI by setting V1_z=1 at exactly config 0's four voxels.
        v1 = np.zeros((8, 8, 8, 3))
        for x, y, z in self._config_voxels(4, 4, 4)[0]:
            v1[x, y, z, 2] = 1.0
        mask = create_square_v4_mask((8, 8, 8), (4, 4, 4), v1, "proj")
        # config 0 mean=1.0; configs 1,2 share two voxels (0.5); config 3 (0.25).
        assert _true_coords(mask) == self._config_voxels(4, 4, 4)[0]

    def test_assoc_uses_v1y_component(self):
        # 'assoc' optimizes the Y component -- make config 2 the unique max of
        # mean |V1_y| and assert config 2 (not config 0) is chosen.
        v1 = np.zeros((8, 8, 8, 3))
        for x, y, z in self._config_voxels(4, 4, 4)[2]:
            v1[x, y, z, 1] = 1.0
        mask = create_square_v4_mask((8, 8, 8), (4, 4, 4), v1, "assoc")
        assert _true_coords(mask) == self._config_voxels(4, 4, 4)[2]

    def test_tie_breaks_to_lower_index_config(self):
        # Tie configs 0 and 3 (one unique voxel each): selection is strict '>'
        # in list order, so the LOWER-index config 0 must win.
        v1 = np.zeros((8, 8, 8, 3))
        v1[5, 5, 4, 2] = 1.0  # unique to config 0 (cx+1, cy+1)
        v1[3, 3, 4, 2] = 1.0  # unique to config 3 (cx-1, cy-1)
        mask = create_square_v4_mask((8, 8, 8), (4, 4, 4), v1, "proj")
        assert _true_coords(mask) == self._config_voxels(4, 4, 4)[0]

    def test_falls_back_to_config0_when_v1_absent(self):
        # No V1 -> documented default: centroid at bottom-left (config 0).
        mask = create_square_v4_mask((8, 8, 8), (4, 4, 4), None, "proj")
        assert _true_coords(mask) == self._config_voxels(4, 4, 4)[0]

    def test_falls_back_to_config0_when_all_corners_out_of_bounds(self):
        # In a 1^3 volume every configuration has <4 in-bounds voxels, so none
        # qualifies and selection falls back to config 0; only its in-bounds
        # voxel (0,0,0) is set. (Unreachable on real in-brain centroids -- this
        # pins the conservative fallback.)
        v1 = np.zeros((1, 1, 1, 3))
        mask = create_square_v4_mask((1, 1, 1), (0, 0, 0), v1, "proj")
        assert _true_coords(mask) == {(0, 0, 0)}
        assert int(mask.sum()) == 1


class TestFindMaskCentroid:
    """find_mask_centroid -- integer-rounded mean of set voxels."""

    def test_rounded_mean_of_set_voxels(self):
        mask = np.zeros((8, 8, 8), dtype=bool)
        for c in [(1, 1, 1), (2, 1, 1), (3, 1, 1)]:
            mask[c] = True
        assert find_mask_centroid(mask) == (2, 1, 1)

    def test_empty_mask_returns_none(self):
        # Clean sentinel: an empty/missing ROI has no centroid.
        assert find_mask_centroid(np.zeros((8, 8, 8), dtype=bool)) is None


class TestCalculateRoiQuality:
    """calculate_roi_quality -- purity, direction, FA, crossing-fiber penalty."""

    def _mask_at(self, shape, coords):
        m = np.zeros(shape, dtype=bool)
        for c in coords:
            m[c] = True
        return m

    def test_perfect_projection_roi(self):
        shape = (4, 4, 4)
        coords = [(0, 0, 0), (1, 0, 0)]
        v1 = np.zeros(shape + (3,))
        fa = np.zeros(shape)
        for c in coords:
            v1[c] = [0.0, 0.0, 1.0]  # Z-dominant -> proj-correct
            fa[c] = 0.5
        purity, direction, mean_fa, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, coords), "proj"
        )
        assert (purity, direction, mean_fa) == (1.0, 1.0, 0.5)
        assert combined == pytest.approx(0.5)  # 1 * 1 * 0.5

    def test_mixed_purity_and_direction(self):
        shape = (4, 4, 4)
        v1 = np.zeros(shape + (3,))
        fa = np.ones(shape)
        v1[0, 0, 0] = [0.0, 0.0, 1.0]  # proj-correct
        v1[1, 0, 0] = [1.0, 0.0, 0.0]  # X-dominant -> incorrect for proj
        purity, direction, mean_fa, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, [(0, 0, 0), (1, 0, 0)]), "proj"
        )
        assert purity == 0.5  # 1 of 2 voxels correct
        assert direction == pytest.approx(0.5)  # mean(|V1_z|) = (1 + 0)/2
        assert mean_fa == 1.0
        assert combined == pytest.approx(0.25)  # 0.5 * 0.5 * 1

    def test_association_roi_uses_y_component(self):
        shape = (4, 4, 4)
        coords = [(0, 0, 0)]
        v1 = np.zeros(shape + (3,))
        fa = np.zeros(shape)
        v1[0, 0, 0] = [0.0, 1.0, 0.0]  # Y-dominant -> assoc-correct
        fa[0, 0, 0] = 1.0
        purity, direction, _, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, coords), "assoc"
        )
        assert purity == 1.0
        assert direction == 1.0
        assert combined == pytest.approx(1.0)

    def _penalty_fixture(self, shape, coords, l2_val, l3_val):
        v1 = np.zeros(shape + (3,))
        fa = np.ones(shape)
        l2 = np.zeros(shape)
        l3 = np.zeros(shape)
        for c in coords:
            v1[c] = [0.0, 0.0, 1.0]
            l2[c] = l2_val
            l3[c] = l3_val
        return v1, fa, l2, l3

    def test_crossing_fiber_penalty_fires_above_threshold(self):
        # mean lambda2/lambda3 = 2.0 > 1.8 -> penalty factor sqrt(1.8/2.0).
        shape = (4, 4, 4)
        coords = [(0, 0, 0), (1, 0, 0)]
        v1, fa, l2, l3 = self._penalty_fixture(shape, coords, 2.0, 1.0)
        _, _, _, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, coords), "proj", l2, l3
        )
        # base = 1*1*1 = 1.0; penalized by sqrt(1.8/2.0).
        assert combined == pytest.approx(math.sqrt(1.8 / 2.0))

    def test_no_penalty_just_below_threshold(self):
        shape = (4, 4, 4)
        coords = [(0, 0, 0)]
        v1, fa, l2, l3 = self._penalty_fixture(shape, coords, 1.5, 1.0)  # ratio 1.5 < 1.8
        _, _, _, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, coords), "proj", l2, l3
        )
        assert combined == pytest.approx(1.0)

    def test_no_penalty_when_l2_l3_absent(self):
        shape = (4, 4, 4)
        coords = [(0, 0, 0)]
        v1, fa, _, _ = self._penalty_fixture(shape, coords, 9.0, 1.0)
        _, _, _, combined = calculate_roi_quality(
            v1,
            fa,
            self._mask_at(shape, coords),
            "proj",  # no l2/l3
        )
        assert combined == pytest.approx(1.0)

    def test_no_penalty_when_all_l3_zero(self):
        # All lambda3 == 0 -> no valid ratio voxels -> penalty skipped (also
        # guards the division by zero).
        shape = (4, 4, 4)
        coords = [(0, 0, 0)]
        v1, fa, l2, l3 = self._penalty_fixture(shape, coords, 5.0, 0.0)
        _, _, _, combined = calculate_roi_quality(
            v1, fa, self._mask_at(shape, coords), "proj", l2, l3
        )
        assert combined == pytest.approx(1.0)

    def test_empty_mask_returns_zeros(self):
        # Clean sentinel for a missing/degenerate ROI.
        shape = (4, 4, 4)
        out = calculate_roi_quality(
            np.zeros(shape + (3,)), np.zeros(shape), np.zeros(shape, dtype=bool), "proj"
        )
        assert out == (0.0, 0.0, 0.0, 0.0)


class TestAdaptiveRoiPairPlacement:
    """adaptive_roi_pair_placement -- joint search, drift constraint, geometric mean.

    Uses sphere shape with a sub-voxel radius (0.5mm in 1mm voxels) so each mask
    is a SINGLE voxel: a position scores > 0 only when centred exactly on a
    planted 'good' voxel, giving clean all-or-nothing reachability on every axis.
    """

    SHAPE = (8, 8, 8)
    VOX = (1.0, 1.0, 1.0)
    R = 0.5  # sub-voxel: mask is the single centre voxel

    def _good_voxel(self, v1, fa, coord, component, fa_val=1.0):
        """Plant a single 'good' voxel: dominant V1 along `component`, FA=fa_val."""
        vec = [0.0, 0.0, 0.0]
        vec[component] = 1.0
        v1[coord] = vec
        fa[coord] = fa_val

    def _empty_fields(self):
        return np.zeros(self.SHAPE + (3,)), np.zeros(self.SHAPE)

    def _adaptive(self, proj_c, assoc_c, v1, fa):
        return adaptive_roi_pair_placement(
            proj_c, assoc_c, v1, fa, self.SHAPE, self.VOX, radius_mm=self.R
        )

    def test_finds_geometric_mean_optimum(self):
        v1, fa = self._empty_fields()
        self._good_voxel(v1, fa, (2, 4, 4), 2, fa_val=1.0)  # proj, Z-dominant, score 1.0
        self._good_voxel(v1, fa, (6, 4, 4), 1, fa_val=0.5)  # assoc, Y-dominant, score 0.5
        # Starts offset in Z so the search must move to the optimum.
        rp, ra, pp, ap, combined = self._adaptive((2, 4, 3), (6, 4, 5), v1, fa)
        assert rp == (2, 4, 4)
        assert ra == (6, 4, 4)
        assert pp == 1.0 and ap == 1.0
        assert combined == pytest.approx(math.sqrt(1.0 * 0.5))  # geometric mean

    def test_rejects_constraint_violating_higher_scoring_pair(self):
        v1, fa = self._empty_fields()
        self._good_voxel(v1, fa, (2, 4, 4), 2, fa_val=1.0)  # only proj position, z=4
        self._good_voxel(v1, fa, (6, 4, 4), 1, fa_val=0.5)  # assoc near: drift ok, score 0.5
        self._good_voxel(v1, fa, (6, 4, 6), 1, fa_val=1.0)  # assoc far: higher score, z-drift 2
        rp, ra, _, _, combined = self._adaptive((2, 4, 4), (6, 4, 5), v1, fa)
        assert rp == (2, 4, 4)
        # The higher-scoring (6,4,6) violates |dz|<=1 against the proj ROI, so the
        # constraint-satisfying (6,4,4) wins despite its lower individual score.
        assert ra == (6, 4, 4)
        assert combined == pytest.approx(math.sqrt(1.0 * 0.5))

    def test_z_search_reaches_plus_or_minus_two(self):
        # Pin the +-2 Z reach: the only proj optimum is 2 voxels away in Z.
        v1, fa = self._empty_fields()
        self._good_voxel(v1, fa, (4, 4, 4), 2, fa_val=1.0)  # proj at z=4
        self._good_voxel(v1, fa, (2, 4, 4), 1, fa_val=1.0)  # assoc, drift ok
        rp, ra, _, _, _ = self._adaptive((4, 4, 2), (2, 4, 4), v1, fa)
        assert rp == (4, 4, 4)  # dz=+2 reached
        assert ra == (2, 4, 4)

    def test_z_search_does_not_reach_three(self):
        # +-3 Z is outside the window: the optimum is unreachable, so the search
        # finds nothing scoring > 0 and keeps the originals with score -1.
        v1, fa = self._empty_fields()
        self._good_voxel(v1, fa, (4, 4, 4), 2, fa_val=1.0)  # 3 voxels from start z
        rp, ra, _, _, combined = self._adaptive((4, 4, 1), (2, 4, 4), v1, fa)
        assert rp == (4, 4, 1)  # unchanged -> z=4 not reachable from z=1
        assert combined == -1.0

    def test_y_search_does_not_reach_two(self):
        # +-2 Y is outside the (narrower) Y window -> optimum unreachable.
        v1, fa = self._empty_fields()
        self._good_voxel(v1, fa, (4, 4, 4), 2, fa_val=1.0)  # 2 voxels from start y
        rp, _, _, _, combined = self._adaptive((4, 2, 4), (2, 4, 4), v1, fa)
        assert rp == (4, 2, 4)  # unchanged -> y=4 not reachable from y=2
        assert combined == -1.0

    def test_all_out_of_bounds_neighbourhood_keeps_originals(self):
        # Every search position is out of bounds -> conservative fallback: keep
        # the original centroids, score -1.
        v1, fa = self._empty_fields()
        rp, ra, _, _, combined = self._adaptive((100, 100, 100), (4, 4, 4), v1, fa)
        assert rp == (100, 100, 100)
        assert combined == -1.0

    def test_all_zero_score_neighbourhood_keeps_originals(self):
        # No voxel scores > 0 anywhere (empty fields) -> same conservative
        # fallback as the all-OOB case.
        v1, fa = self._empty_fields()
        rp, ra, _, _, combined = self._adaptive((4, 4, 4), (2, 4, 4), v1, fa)
        assert rp == (4, 4, 4)
        assert ra == (2, 4, 4)
        assert combined == -1.0
