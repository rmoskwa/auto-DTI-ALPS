"""
Interface test for the unified native-placement shell (PRD 0014).

``place_rois_in_native`` is the one IO body both the pipeline
(``registration/fsl.py``) and reanalysis share. It is exercised here with **no
FSL installed**, in two parts:

- **Happy path (full body).** The four ``{prefix}_{roi}_transformed.nii.gz`` are
  pre-seeded as tiny real NIfTIs, so cache-if-exists (Decision 5) skips
  ``applywarp`` entirely and the body runs on real ``nibabel``: centroid ->
  joint adaptive placement (``adaptive=True`` with a crafted V1) -> mask creation -> save.
  Zero ``applywarp`` calls proves the cache path.
- **Seam path (argv + failure).** With no pre-seed the ``FakeToolRunner`` records
  the ``applywarp`` argv; a non-zero returncode maps to a raised
  ``ROIPlacementError``.
"""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from dti_alps.processing.native_placement import ROIPlacementError, place_rois_in_native
from tests.fakes import FakeToolRunner

ROI_NAMES = ("left_proj", "right_proj", "left_assoc", "right_assoc")
APPLYWARP = "/fsl/bin/applywarp"


def _blob(shape=(8, 8, 8)):
    """A tiny volume with a solid cube of voxels centered in the grid."""
    data = np.zeros(shape, dtype=np.float32)
    data[3:5, 3:5, 3:5] = 1.0
    return data


def _seed_common(tmp_path):
    """FA volume + a crafted V1 (+L2/L3); returns the resolved path kwargs."""
    reg_dir = tmp_path / "registration"
    roi_dir = tmp_path / "rois_sphere3_adaptive"
    reg_dir.mkdir()
    roi_dir.mkdir()

    affine = np.eye(4)
    fa_path = tmp_path / "sub-01_FA.nii.gz"
    nib.save(nib.Nifti1Image(_blob(), affine), str(fa_path))

    # V1 with a Z-dominant vector everywhere so adaptive placement has real data to score.
    v1 = np.zeros((8, 8, 8, 3), dtype=np.float32)
    v1[..., 2] = 1.0
    v1_path = tmp_path / "sub-01_V1.nii.gz"
    nib.save(nib.Nifti1Image(v1, affine), str(v1_path))

    l2_path = tmp_path / "sub-01_L2.nii.gz"
    l3_path = tmp_path / "sub-01_L3.nii.gz"
    nib.save(nib.Nifti1Image(np.ones((8, 8, 8), dtype=np.float32), affine), str(l2_path))
    nib.save(nib.Nifti1Image(np.ones((8, 8, 8), dtype=np.float32), affine), str(l3_path))

    return {
        "fa_path": str(fa_path),
        "inverse_warp": tmp_path / "warp.nii.gz",
        "roi_templates": {name: tmp_path / f"tpl_{name}.nii.gz" for name in ROI_NAMES},
        "reg_dir": reg_dir,
        "roi_dir": roi_dir,
        "prefix": "sub-01",
        "shape_type": "sphere",
        "sphere_radius": 3.0,
        "v1_path": str(v1_path),
        "l2_path": str(l2_path),
        "l3_path": str(l3_path),
    }


# --- Happy path: full body, cache skips applywarp, no FSL --------------------


def test_place_rois_full_body_uses_cache_and_writes_masks(tmp_path):
    kwargs = _seed_common(tmp_path)
    # Pre-seed the four transformed templates so cache-if-exists skips applywarp.
    for name in ROI_NAMES:
        nib.save(
            nib.Nifti1Image(_blob(), np.eye(4)),
            str(kwargs["reg_dir"] / f"sub-01_{name}_transformed.nii.gz"),
        )
    fake = FakeToolRunner()

    mask_paths, centroids = place_rois_in_native(
        runner=fake, applywarp_cmd=APPLYWARP, adaptive=True, **kwargs
    )

    # All four masks written and centroids returned.
    assert set(mask_paths) == set(ROI_NAMES)
    assert set(centroids) == set(ROI_NAMES)
    for path in mask_paths.values():
        assert Path(path).exists()
    # Cache-if-exists means applywarp never crossed the seam.
    assert not any(c and c[0].endswith("applywarp") for c in fake.calls)


# --- Seam path: applywarp argv + failure mapping ----------------------------


def test_place_rois_issues_applywarp_argv_when_not_cached(tmp_path):
    kwargs = _seed_common(tmp_path)  # no pre-seed -> applywarp runs
    fake = FakeToolRunner()  # returncode 0, but writes no file

    # Default fake writes nothing, so the run raises "transformed ROI not
    # created" right after -- the seam is asserted from fake.calls before that.
    with pytest.raises(ROIPlacementError):
        place_rois_in_native(runner=fake, applywarp_cmd=APPLYWARP, adaptive=False, **kwargs)

    applywarp_calls = [c for c in fake.calls if c and c[0] == APPLYWARP]
    assert applywarp_calls, "expected applywarp to cross the seam"
    cmd = applywarp_calls[0]
    assert cmd[0] == APPLYWARP  # the injected command, not a bare PATH lookup
    assert any(a.startswith("--ref=") for a in cmd)
    assert any(a.startswith("--in=") for a in cmd)
    assert any(a.startswith("--warp=") for a in cmd)
    assert any(a.startswith("--out=") for a in cmd)
    assert "--interp=nn" in cmd


def test_place_rois_nonzero_applywarp_raises(tmp_path):
    kwargs = _seed_common(tmp_path)  # no pre-seed -> applywarp runs
    fake = FakeToolRunner().on(
        lambda c: c[0].endswith("applywarp"), returncode=1, lines=["applywarp boom"]
    )

    with pytest.raises(ROIPlacementError, match="FSL applywarp failed"):
        place_rois_in_native(runner=fake, applywarp_cmd=APPLYWARP, adaptive=False, **kwargs)
