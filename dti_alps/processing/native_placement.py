"""
The single IO shell that places ROIs in native space for DTI-ALPS.

The *paths in -> mask files on disk* twin of the pure ``roi_placement`` leaf
(*arrays in -> masks/tuples out*). ``place_rois_in_native`` composes the pure
kernels into the one native-placement body both callers share: it transforms the
four ROI templates into native space (cache-if-exists ``applywarp`` via the
injected :class:`ToolRunner`), finds each template centroid, loads V1/L2/L3 only
as the shape/refinement need them (with the V1-missing fallbacks), jointly
refines each projection/association pair, creates the masks, and saves them under
``results_layout.roi_mask_name``.

Both ``registration/fsl.py::place_rois`` and ``reanalysis.py::reanalyze_subject``
call this instead of each carrying its own copy of the loop (PRD 0014). This leaf
never imports the ``registration`` result dataclasses -- the dependency arrow
points ``registration -> placement``, never back -- so failure is a raised
:class:`ROIPlacementError` that each caller translates into its own envelope.
"""

import os
from collections.abc import Callable
from pathlib import Path

import nibabel as nib
import numpy as np

from . import results_layout
from .roi_placement import (
    calculate_roi_quality,
    create_sphere_mask,
    create_square_v4_mask,
    create_square_v9_mask,
    find_mask_centroid,
    refine_roi_pair_placement,
)
from .tool_runner import ToolRunner


class ROIPlacementError(Exception):
    """Raised on a failed template transform or an empty transformed ROI.

    The neutral failure signal of the placement shell: the shell lives in an
    engine leaf and must not import ``ROIPlacementResult`` from
    ``registration/base.py`` (that would invert the dependency arrow). Each
    caller catches this and builds its own failure envelope.
    """


def place_rois_in_native(
    *,
    runner: ToolRunner,
    applywarp_cmd: str,
    fa_path: str,
    inverse_warp: Path,
    roi_templates: dict[str, Path],
    reg_dir: Path,
    roi_dir: Path,
    prefix: str,
    shape_type: str,
    sphere_radius: float | None,
    refine: bool,
    v1_path: str | None = None,
    l2_path: str | None = None,
    l3_path: str | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[dict[str, str], dict[str, tuple[int, int, int]]]:
    """
    Place the four ROIs in native space for one shape and one refinement mode.

    Parameters
    ----------
    runner : ToolRunner
        Seam for external command execution (real subprocess or a test fake).
    applywarp_cmd : str
        Resolved ``applywarp`` command. Required -- the caller resolves FSL's
        bin dir; the shell never falls back to ``PATH`` so FSLDIR-only installs
        keep working (PRD 0014 Decision 4).
    fa_path : str
        Native-space FA image; the reference grid, affine, header, and FA data.
    inverse_warp : Path
        JHU->subject inverse warp.
    roi_templates : dict of {roi_name: Path}
        The four template masks to transform (left/right proj/assoc).
    reg_dir : Path
        Where the transformed-template intermediates live (and are cached).
    roi_dir : Path
        Output directory for the ROI masks (caller pre-creates it).
    prefix : str
        Subject identifier; drives the transformed + mask filenames.
    shape_type : str
        "sphere", "squarev9", or "squarev4".
    sphere_radius : float or None
        Sphere radius in mm (only for the sphere shape).
    refine : bool
        Whether to jointly refine each projection/association pair.
    v1_path, l2_path, l3_path : str or None
        Eigenvector / eigenvalue paths, loaded only when the shape or refinement
        needs them.
    log : callable
        Sink for progress lines.

    Returns
    -------
    tuple of (roi_mask_paths, roi_centroids)
        The written mask paths and the (possibly refined) centroids, both keyed
        by ROI name.

    Raises
    ------
    ROIPlacementError
        On a failed template transform or a transformed ROI with no voxels.
    """
    # Load reference image for shape, affine/header, FA data, and voxel size.
    ref_img = nib.load(fa_path)
    ref_shape = ref_img.shape[:3]
    fa_data = ref_img.get_fdata()
    voxel_size = ref_img.header.get_zooms()[:3]

    # Log ROI shape info
    if shape_type == "sphere":
        log(f"  Shape: sphere, radius: {sphere_radius} mm")
    else:
        log(f"  Shape: {shape_type}")

    v1_data = None

    # Load V1 data if refinement is enabled OR if using squarev4 (needs V1 for config selection)
    needs_v1_data = refine or shape_type == "squarev4"
    if needs_v1_data:
        if refine:
            log("  ROI refinement enabled (±3 X, ±1 Y, ±2 Z voxels)")
            log("  Association ROIs constrained to ±1 Y, ±1 Z voxels from projection ROI")
        if shape_type == "squarev4":
            log("  Square 2x2: V1-optimized configuration selection enabled")
        # Load V1 data for fiber orientation analysis
        if v1_path and os.path.exists(v1_path):
            v1_data = nib.load(v1_path).get_fdata()
        else:
            log("  WARNING: V1 data not available")
            if refine:
                log("  Skipping refinement")
                refine = False
            if shape_type == "squarev4":
                log("  Squarev4 will use default configuration")

    # Load L2/L3 data for radial asymmetry penalty in refinement
    l2_data = None
    l3_data = None
    if refine:
        if l2_path and os.path.exists(l2_path) and l3_path and os.path.exists(l3_path):
            l2_data = nib.load(l2_path).get_fdata()
            l3_data = nib.load(l3_path).get_fdata()
            log("  L2/L3 data loaded for radial asymmetry penalty")

    roi_centroids: dict[str, tuple[int, int, int]] = {}
    roi_native_paths: dict[str, str] = {}
    template_centroids: dict[str, tuple[int, int, int]] = {}

    # First pass: Transform all ROIs (cache-if-exists) and get template centroids
    for roi_name, roi_template in roi_templates.items():
        roi_transformed = reg_dir / f"{prefix}_{roi_name}_transformed.nii.gz"

        # The transformed templates depend only on (inverse_warp, template, FA
        # reference grid) -- none of which vary by shape or refinement mode -- so
        # a previously-produced output is reused (PRD 0014 Decision 5).
        if not roi_transformed.exists():
            log(f"  Transforming {roi_name}...")
            cmd = [
                applywarp_cmd,
                f"--ref={fa_path}",
                f"--in={roi_template}",
                f"--warp={inverse_warp}",
                f"--out={roi_transformed}",
                "--interp=nn",
            ]
            # The runner never raises: a non-zero exit (including a missing
            # binary at 127) is reported as a return code, so failure is mapped
            # to a raised ROIPlacementError here rather than a caught exception.
            transform_result = runner.run(cmd, on_line=log)
            if transform_result.returncode != 0:
                raise ROIPlacementError(f"FSL applywarp failed: {transform_result.output}")
            if not roi_transformed.exists():
                raise ROIPlacementError(f"{roi_name} transformed ROI not created")

        # Load transformed mask and find centroid
        transformed_data = nib.load(str(roi_transformed)).get_fdata()
        centroid = find_mask_centroid(transformed_data)
        if centroid is None:
            raise ROIPlacementError(f"No voxels found in transformed {roi_name}")

        template_centroids[roi_name] = centroid
        log(f"    Template centroid: {centroid}")

    # Second pass: Jointly refine projection and association ROI pairs
    # This optimizes both ROIs together to find the best combined placement
    # while respecting the Y/Z drift constraint between paired ROIs
    for side in ["left", "right"]:
        proj_name = f"{side}_proj"
        assoc_name = f"{side}_assoc"
        proj_centroid = template_centroids[proj_name]
        assoc_centroid = template_centroids[assoc_name]

        if refine and v1_data is not None:
            # Calculate original purities for logging
            if shape_type == "sphere":
                orig_proj_mask = create_sphere_mask(
                    ref_shape, proj_centroid, sphere_radius, voxel_size
                )
                orig_assoc_mask = create_sphere_mask(
                    ref_shape, assoc_centroid, sphere_radius, voxel_size
                )
            elif shape_type == "squarev4":
                orig_proj_mask = create_square_v4_mask(ref_shape, proj_centroid, v1_data, "proj")
                orig_assoc_mask = create_square_v4_mask(ref_shape, assoc_centroid, v1_data, "assoc")
            else:
                orig_proj_mask = create_square_v9_mask(ref_shape, proj_centroid)
                orig_assoc_mask = create_square_v9_mask(ref_shape, assoc_centroid)

            orig_proj_purity, _, _, _ = calculate_roi_quality(
                v1_data, fa_data, orig_proj_mask, "proj"
            )
            orig_assoc_purity, _, _, _ = calculate_roi_quality(
                v1_data, fa_data, orig_assoc_mask, "assoc"
            )

            # Jointly refine both ROIs as a pair
            (
                refined_proj,
                refined_assoc,
                refined_proj_purity,
                refined_assoc_purity,
                _,
            ) = refine_roi_pair_placement(
                proj_centroid,
                assoc_centroid,
                v1_data,
                fa_data,
                ref_shape,
                voxel_size,
                radius_mm=sphere_radius or 3.0,
                search_x=3,
                search_y=1,
                search_z=2,
                max_y_drift=1,
                max_z_drift=1,
                shape_type=shape_type,
                l2_data=l2_data,
                l3_data=l3_data,
            )

            # Log projection ROI refinement
            proj_offset = (
                refined_proj[0] - proj_centroid[0],
                refined_proj[1] - proj_centroid[1],
                refined_proj[2] - proj_centroid[2],
            )
            if proj_offset != (0, 0, 0):
                log(f"    {proj_name} refined: {refined_proj} (offset: {proj_offset})")
                log(
                    f"    Purity: {orig_proj_purity * 100:.0f}% -> {refined_proj_purity * 100:.0f}%"
                )
            else:
                log(
                    f"    {proj_name} no refinement needed "
                    f"(purity: {refined_proj_purity * 100:.0f}%)"
                )

            # Log association ROI refinement
            assoc_offset = (
                refined_assoc[0] - assoc_centroid[0],
                refined_assoc[1] - assoc_centroid[1],
                refined_assoc[2] - assoc_centroid[2],
            )
            y_drift = abs(refined_assoc[1] - refined_proj[1])
            z_drift = abs(refined_assoc[2] - refined_proj[2])
            if assoc_offset != (0, 0, 0):
                log(f"    {assoc_name} refined: {refined_assoc} (offset: {assoc_offset})")
                log(
                    f"    Purity: {orig_assoc_purity * 100:.0f}% -> "
                    f"{refined_assoc_purity * 100:.0f}%"
                )
                log(f"    Drift from {proj_name}: Y={y_drift}, Z={z_drift} voxels")
            else:
                log(
                    f"    {assoc_name} no refinement needed "
                    f"(purity: {refined_assoc_purity * 100:.0f}%)"
                )

            proj_centroid = refined_proj
            assoc_centroid = refined_assoc

        roi_centroids[proj_name] = proj_centroid
        roi_centroids[assoc_name] = assoc_centroid

        # Create and save projection ROI mask
        if shape_type == "sphere":
            proj_mask = create_sphere_mask(ref_shape, proj_centroid, sphere_radius, voxel_size)
        elif shape_type == "squarev4":
            proj_mask = create_square_v4_mask(ref_shape, proj_centroid, v1_data, "proj")
        else:
            proj_mask = create_square_v9_mask(ref_shape, proj_centroid)
        n_voxels = int(np.sum(proj_mask))
        log(f"    Created {proj_name} with {n_voxels} voxels")

        proj_path = roi_dir / results_layout.roi_mask_name(prefix, proj_name)
        proj_img = nib.Nifti1Image(proj_mask.astype(np.float32), ref_img.affine, ref_img.header)
        nib.save(proj_img, str(proj_path))
        roi_native_paths[proj_name] = str(proj_path)

        # Create and save association ROI mask
        if shape_type == "sphere":
            assoc_mask = create_sphere_mask(ref_shape, assoc_centroid, sphere_radius, voxel_size)
        elif shape_type == "squarev4":
            assoc_mask = create_square_v4_mask(ref_shape, assoc_centroid, v1_data, "assoc")
        else:
            assoc_mask = create_square_v9_mask(ref_shape, assoc_centroid)
        n_voxels = int(np.sum(assoc_mask))
        log(f"    Created {assoc_name} with {n_voxels} voxels")

        assoc_path = roi_dir / results_layout.roi_mask_name(prefix, assoc_name)
        assoc_img = nib.Nifti1Image(assoc_mask.astype(np.float32), ref_img.affine, ref_img.header)
        nib.save(assoc_img, str(assoc_path))
        roi_native_paths[assoc_name] = str(assoc_path)

    log("ROI placement completed successfully")
    log(f"  Left projection ROI: {roi_native_paths['left_proj']}")
    log(f"  Left association ROI: {roi_native_paths['left_assoc']}")
    log(f"  Right projection ROI: {roi_native_paths['right_proj']}")
    log(f"  Right association ROI: {roi_native_paths['right_assoc']}")

    return roi_native_paths, roi_centroids
