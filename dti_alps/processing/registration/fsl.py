"""
FSL-based registration backend for DTI-ALPS pipeline.

This module implements the RegistrationBackend interface using FSL tools
(FLIRT, FNIRT, invwarp, applywarp) for FA-to-template registration.

Brain extraction is performed using MRtrix3's dwi2mask on the preprocessed
DWI data, which provides more reliable results than BET2 on FA images.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import nibabel as nib
import numpy as np

from ..b0_extraction import (
    apply_mask_to_image,
    create_brain_mask_from_dwi,
    validate_b0_exists,
)
from .base import (
    RegistrationBackend,
    RegistrationResult,
    ROIPlacementResult,
    calculate_roi_quality,
    create_sphere_mask,
    create_square_v4_mask,
    create_square_v9_mask,
    find_mask_centroid,
    get_roi_template_paths,
    refine_roi_placement,
)

if TYPE_CHECKING:
    from ..state import PipelineState


class FSLRegistration(RegistrationBackend):
    """
    FSL-based registration backend.

    Uses FLIRT for linear registration and FNIRT for nonlinear registration
    to transform ROI templates from JHU space to subject native space.
    """

    @property
    def name(self) -> str:
        """Return the backend name."""
        return "fsl"

    def check_available(self) -> tuple[bool, list[str]]:
        """
        Check if FSL registration tools and MRtrix3 dwi2mask are available.

        Returns
        -------
        tuple of (bool, list)
            (all_available, list of missing commands)
        """
        # FSL commands (checked in FSL bin dir and PATH)
        fsl_commands = ["flirt", "fnirt", "invwarp", "applywarp", "fslmaths"]
        # MRtrix3 commands (checked in PATH only)
        mrtrix_commands = ["dwi2mask"]

        missing = []

        fsl_bin = self._get_fsl_bin_dir()

        # Check FSL commands
        for cmd in fsl_commands:
            found = False
            # Check in FSL bin directory
            if fsl_bin and (fsl_bin / cmd).is_file():
                found = True
            # Also check PATH
            elif shutil.which(cmd) is not None:
                found = True

            if not found:
                missing.append(cmd)

        # Check MRtrix3 commands (PATH only)
        for cmd in mrtrix_commands:
            if shutil.which(cmd) is None:
                missing.append(cmd)

        return (len(missing) == 0, missing)

    def get_template_path(self) -> Path | None:
        """
        Get path to JHU-ICBM-FA-1mm.nii.gz template.

        Returns
        -------
        Path or None
            Path to template, or None if not found
        """
        fsldir = self._get_fsldir()
        if not fsldir:
            return None

        template_path = Path(fsldir) / "data" / "atlases" / "JHU" / "JHU-ICBM-FA-1mm.nii.gz"
        if template_path.exists():
            return template_path

        return None

    def register(
        self,
        state: "PipelineState",
        log_callback: Callable[[str], None] | None = None,
    ) -> RegistrationResult:
        """
        Register subject FA to JHU template and create inverse warp.

        Steps:
        1. Validate b0 volumes exist in DWI data
        2. Create brain mask using dwi2mask on preprocessed DWI
        3. Fix NaN values in FA image
        4. Apply brain mask to FA
        5. Linear registration (FLIRT)
        6. Non-linear registration (FNIRT)
        7. Create inverse warp (INVWARP)

        Parameters
        ----------
        state : PipelineState
            Pipeline state with fa_path and preprocessed_dwi_path set
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        RegistrationResult
            Result containing inverse warp path
        """
        log = log_callback or (lambda x: None)

        # Get FSL paths
        fsl_bin = self._get_fsl_bin_dir()
        if not fsl_bin:
            return RegistrationResult(
                success=False,
                error_message="FSL bin directory not found",
            )

        jhu_template = self.get_template_path()
        if not jhu_template:
            return RegistrationResult(
                success=False,
                error_message="JHU-ICBM-FA-1mm.nii.gz template not found",
            )

        # Check that preprocessed DWI exists
        if not state.preprocessed_dwi_path or not Path(state.preprocessed_dwi_path).exists():
            return RegistrationResult(
                success=False,
                error_message="Preprocessed DWI not found. Run preprocessing first.",
            )

        # Get bvecs/bvals paths (preprocessed versions)
        bvecs_preproc = state.get_output_path("bvecs_preproc")
        bvals_preproc = state.get_output_path("bvals_preproc")

        if not Path(bvecs_preproc).exists() or not Path(bvals_preproc).exists():
            return RegistrationResult(
                success=False,
                error_message="Preprocessed bvecs/bvals not found.",
            )

        # Set up output paths
        reg_dir = Path(state.output_dir) / "registration"
        reg_dir.mkdir(parents=True, exist_ok=True)

        prefix = state.output_prefix
        brain_mask = reg_dir / f"{prefix}_brain_mask.nii.gz"
        fa_nonan = reg_dir / f"{prefix}_FA_nonan.nii.gz"
        fa_brain = reg_dir / f"{prefix}_FA_brain.nii.gz"
        affine_mat = reg_dir / f"{prefix}_subject2jhu_affine.mat"
        warp_coef = reg_dir / f"{prefix}_subject2jhu_warp_coef.nii.gz"
        inverse_warp = reg_dir / f"{prefix}_jhu2subject_warp_coef.nii.gz"

        # Get user-configured options (with defaults)
        flirt_opts = getattr(state, "flirt_options", {})
        fnirt_opts = getattr(state, "fnirt_options", {})

        # Step 1: Validate b0 volumes exist
        log("Validating DWI data contains b0 volumes...")
        is_valid, message, n_b0 = validate_b0_exists(bvals_preproc)
        if not is_valid:
            return RegistrationResult(
                success=False,
                error_message=f"B0 validation failed: {message}",
            )
        log(f"  {message}")

        # Step 2: Create brain mask using dwi2mask
        log("Creating brain mask using dwi2mask...")
        success, msg = create_brain_mask_from_dwi(
            dwi_path=state.preprocessed_dwi_path,
            bvecs_path=bvecs_preproc,
            bvals_path=bvals_preproc,
            output_mask_path=str(brain_mask),
            log=log,
        )
        if not success:
            return RegistrationResult(
                success=False,
                error_message=f"Brain mask creation failed: {msg}",
            )

        # Update state with brain mask path
        state.brain_mask_path = str(brain_mask)

        # Step 3: Fix NaN values in FA image
        log("Preparing FA image (fixing NaN values if present)...")
        if not self._fix_nan_in_nifti(state.fa_path, str(fa_nonan)):
            return RegistrationResult(
                success=False,
                error_message="Failed to prepare FA image",
            )

        # Step 4: Apply brain mask to FA
        log("Applying brain mask to FA image...")
        success, msg = apply_mask_to_image(
            input_path=str(fa_nonan),
            mask_path=str(brain_mask),
            output_path=str(fa_brain),
            log=log,
        )
        if not success:
            return RegistrationResult(
                success=False,
                error_message=f"Failed to apply brain mask to FA: {msg}",
            )

        if not fa_brain.exists():
            return RegistrationResult(
                success=False,
                error_message="Skull-stripped FA not created",
            )

        # Update state with registration paths
        state.fa_brain_path = str(fa_brain)
        state.affine_mat_path = str(affine_mat)

        # Step 3: Linear registration (FLIRT)
        log("Running linear registration (FLIRT)...")
        flirt_cmd = [
            str(fsl_bin / "flirt"),
            "-in",
            str(fa_brain),
            "-ref",
            str(jhu_template),
            "-omat",
            str(affine_mat),
        ]
        # Add FLIRT options (use defaults if not specified)
        dof = flirt_opts.get("-dof", "12")
        flirt_cmd.extend(["-dof", str(dof)])

        cost = flirt_opts.get("-cost", "corratio")
        flirt_cmd.extend(["-cost", str(cost)])

        # Search ranges - parse "min max" format
        searchrx = flirt_opts.get("-searchrx", "-30 30").split()
        if len(searchrx) == 2:
            flirt_cmd.extend(["-searchrx", searchrx[0], searchrx[1]])

        searchry = flirt_opts.get("-searchry", "-30 30").split()
        if len(searchry) == 2:
            flirt_cmd.extend(["-searchry", searchry[0], searchry[1]])

        searchrz = flirt_opts.get("-searchrz", "-30 30").split()
        if len(searchrz) == 2:
            flirt_cmd.extend(["-searchrz", searchrz[0], searchrz[1]])

        if "-interp" in flirt_opts and flirt_opts["-interp"]:
            flirt_cmd.extend(["-interp", str(flirt_opts["-interp"])])

        log(f"  Command: {' '.join(flirt_cmd)}")
        if not self._run_fsl_command(flirt_cmd, log):
            return RegistrationResult(
                success=False,
                error_message="FLIRT failed",
            )

        if not affine_mat.exists():
            return RegistrationResult(
                success=False,
                error_message="Affine matrix not created",
            )

        # Step 4: Non-linear registration (FNIRT)
        log("Running non-linear registration (FNIRT)...")
        fnirt_cmd = [
            str(fsl_bin / "fnirt"),
            f"--in={fa_nonan}",  # Use non-skull-stripped FA
            f"--ref={jhu_template}",
            f"--aff={affine_mat}",
            f"--cout={warp_coef}",
        ]
        # Add FNIRT options (use defaults if not specified)
        intmod = fnirt_opts.get("--intmod", "none")
        fnirt_cmd.append(f"--intmod={intmod}")

        jacrange = fnirt_opts.get("--jacrange", "0.2,5")
        fnirt_cmd.append(f"--jacrange={jacrange}")

        lambda_val = fnirt_opts.get("--lambda", "300,150,100,50")
        fnirt_cmd.append(f"--lambda={lambda_val}")

        subsamp = fnirt_opts.get("--subsamp", "4,2,2,1")
        fnirt_cmd.append(f"--subsamp={subsamp}")

        miter = fnirt_opts.get("--miter", "5,5,3,3")
        fnirt_cmd.append(f"--miter={miter}")

        warpres = fnirt_opts.get("--warpres", "10,10,10")
        fnirt_cmd.append(f"--warpres={warpres}")

        log(f"  Command: {' '.join(fnirt_cmd)}")
        if not self._run_fsl_command(fnirt_cmd, log):
            return RegistrationResult(
                success=False,
                error_message="FNIRT failed",
            )

        if not warp_coef.exists():
            return RegistrationResult(
                success=False,
                error_message="Warp coefficients not created",
            )

        # Update state with warp path
        state.warp_coef_path = str(warp_coef)

        # Step 5: Create inverse warp
        log("Creating inverse warp (INVWARP)...")
        invwarp_cmd = [
            str(fsl_bin / "invwarp"),
            f"--ref={state.fa_path}",
            f"--warp={warp_coef}",
            f"--out={inverse_warp}",
        ]
        log(f"  Command: {' '.join(invwarp_cmd)}")
        if not self._run_fsl_command(invwarp_cmd, log):
            return RegistrationResult(
                success=False,
                error_message="INVWARP failed",
            )

        if not inverse_warp.exists():
            return RegistrationResult(
                success=False,
                error_message="Inverse warp not created",
            )

        # Update state with inverse warp path
        state.inverse_warp_path = str(inverse_warp)

        log("Registration completed successfully")
        return RegistrationResult(
            success=True,
            inverse_warp_path=str(inverse_warp),
        )

    def place_rois(
        self,
        state: "PipelineState",
        log_callback: Callable[[str], None] | None = None,
    ) -> ROIPlacementResult:
        """
        Transform ROI templates to native space and create spherical ROIs.

        Requires that register() has been run first (inverse_warp_path must exist).

        Parameters
        ----------
        state : PipelineState
            Pipeline state with inverse_warp_path and ROI parameters
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        ROIPlacementResult
            Result containing ROI paths and centroids
        """
        log = log_callback or (lambda x: None)

        # Get FSL paths
        fsl_bin = self._get_fsl_bin_dir()
        if not fsl_bin:
            return ROIPlacementResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="FSL bin directory not found",
            )

        # Check inverse warp exists
        inverse_warp = state.inverse_warp_path
        if not inverse_warp or not Path(inverse_warp).exists():
            return ROIPlacementResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Inverse warp not found. Run registration first.",
            )

        roi_templates = get_roi_template_paths()
        if not roi_templates:
            return ROIPlacementResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="ROI template files not found in templates/ directory",
            )

        # Set up output paths
        reg_dir = Path(state.output_dir) / "registration"
        reg_dir.mkdir(parents=True, exist_ok=True)

        log("Transforming ROI masks to native space...")

        # Get ROI shapes from state (default to sphere 3mm if not set)
        roi_shapes = getattr(state, "roi_shapes", [{"type": "sphere", "radius": 3.0}])
        if not roi_shapes:
            roi_shapes = [{"type": "sphere", "radius": 3.0}]

        # Check if refinement is enabled
        do_refinement = getattr(state, "refine_roi_placement", True)

        # Process each ROI shape
        all_results = {}
        for shape_config in roi_shapes:
            shape_type = shape_config.get("type", "sphere")
            sphere_radius = shape_config.get("radius", 3.0) if shape_type == "sphere" else None

            # Create descriptive directory name
            if shape_type == "sphere":
                # Format: sphere3, sphere2p5, sphere3p5
                r_str = str(sphere_radius).replace(".", "p").rstrip("0").rstrip("p")
                shape_name = f"sphere{r_str}"
            else:
                shape_name = shape_type  # squarev9, squarev4

            # Add _refined suffix if refinement is enabled
            if do_refinement:
                dir_name = f"rois_{shape_name}_refined"
            else:
                dir_name = f"rois_{shape_name}"

            roi_dir = Path(state.output_dir) / dir_name
            roi_dir.mkdir(parents=True, exist_ok=True)

            log(
                f"Creating {shape_name} ROIs (refinement: {'enabled' if do_refinement else 'disabled'})..."
            )

            result = self._transform_rois_to_native(
                state=state,
                fsl_bin=fsl_bin,
                inverse_warp=Path(inverse_warp),
                roi_templates=roi_templates,
                reg_dir=reg_dir,
                roi_dir=roi_dir,
                shape_type=shape_type,
                sphere_radius=sphere_radius,
                log=log,
            )

            if not result.success:
                return result

            all_results[dir_name] = result

        # Return the first result (for backward compatibility with single-shape callers)
        # The roi_mask_paths will be from the first shape processed
        first_result = list(all_results.values())[0]
        return first_result

    def _transform_rois_to_native(
        self,
        state: "PipelineState",
        fsl_bin: Path,
        inverse_warp: Path,
        roi_templates: dict[str, Path],
        reg_dir: Path,
        roi_dir: Path,
        shape_type: str,
        sphere_radius: float | None,
        log: Callable[[str], None],
    ) -> ROIPlacementResult:
        """Transform ROI templates to native space and create ROI masks."""
        # Load reference image for shape and voxel size
        ref_img = nib.load(state.fa_path)
        ref_shape = ref_img.shape[:3]
        fa_data = ref_img.get_fdata()
        voxel_size = ref_img.header.get_zooms()[:3]

        prefix = state.output_prefix

        # Log ROI shape info
        if shape_type == "sphere":
            log(f"  Shape: sphere, radius: {sphere_radius} mm")
        else:
            log(f"  Shape: {shape_type}")

        # Check if ROI refinement is enabled
        do_refinement = getattr(state, "refine_roi_placement", True)
        v1_data = None

        # Load V1 data if refinement is enabled OR if using squarev4 (needs V1 for config selection)
        needs_v1_data = do_refinement or shape_type == "squarev4"
        if needs_v1_data:
            if do_refinement:
                log("  ROI refinement enabled (±3 X, ±2 Y, ±1 Z voxels)")
                log("  Association ROIs constrained to ±1 Y, ±1 Z voxels from projection ROI")
            if shape_type == "squarev4":
                log("  Square 2x2: V1-optimized configuration selection enabled")
            # Load V1 data for fiber orientation analysis
            if state.v1_path and os.path.exists(state.v1_path):
                v1_img = nib.load(state.v1_path)
                v1_data = v1_img.get_fdata()
            else:
                log("  WARNING: V1 data not available")
                if do_refinement:
                    log("  Skipping refinement")
                    do_refinement = False
                if shape_type == "squarev4":
                    log("  Squarev4 will use default configuration")

        roi_centroids = {}
        roi_native_paths = {}
        template_centroids = {}

        # First pass: Transform all ROIs and get template centroids
        for roi_name, roi_template in roi_templates.items():
            roi_transformed = reg_dir / f"{prefix}_{roi_name}_transformed.nii.gz"
            log(f"  Transforming {roi_name}...")

            applywarp_cmd = [
                str(fsl_bin / "applywarp"),
                f"--ref={state.fa_path}",
                f"--in={roi_template}",
                f"--warp={inverse_warp}",
                f"--out={roi_transformed}",
                "--interp=nn",
            ]
            if not self._run_fsl_command(applywarp_cmd, log):
                return ROIPlacementResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"Failed to transform {roi_name}",
                )

            if not roi_transformed.exists():
                return ROIPlacementResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"{roi_name} transformed ROI not created",
                )

            # Load transformed mask and find centroid
            transformed_img = nib.load(str(roi_transformed))
            transformed_data = transformed_img.get_fdata()

            centroid = find_mask_centroid(transformed_data)
            if centroid is None:
                return ROIPlacementResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"No voxels found in transformed {roi_name}",
                )

            template_centroids[roi_name] = centroid
            log(f"    Template centroid: {centroid}")

        # Second pass: Refine projection ROIs first (no constraint)
        # This establishes the Y-coordinate reference for association ROIs
        for roi_name in ["left_proj", "right_proj"]:
            centroid = template_centroids[roi_name]

            if do_refinement and v1_data is not None:
                # Calculate original purity using appropriate mask
                if shape_type == "sphere":
                    orig_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
                elif shape_type == "squarev4":
                    orig_mask = create_square_v4_mask(ref_shape, centroid, v1_data, "proj")
                else:
                    orig_mask = create_square_v9_mask(ref_shape, centroid)
                orig_purity, _, _, _ = calculate_roi_quality(v1_data, fa_data, orig_mask, "proj")

                # Refine placement (no reference constraint for projection ROIs)
                refined_centroid, refined_purity, _ = refine_roi_placement(
                    centroid,
                    v1_data,
                    fa_data,
                    ref_shape,
                    voxel_size,
                    "proj",
                    radius_mm=sphere_radius or 3.0,  # Use 3mm for square refinement search
                    search_x=3,
                    search_y=2,
                    search_z=2,
                    shape_type=shape_type,
                )

                offset = (
                    refined_centroid[0] - centroid[0],
                    refined_centroid[1] - centroid[1],
                    refined_centroid[2] - centroid[2],
                )

                if offset != (0, 0, 0):
                    log(f"    {roi_name} refined: {refined_centroid} (offset: {offset})")
                    log(f"    Purity: {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%")
                else:
                    log(
                        f"    {roi_name} no refinement needed (purity: {refined_purity * 100:.0f}%)"
                    )

                centroid = refined_centroid

            roi_centroids[roi_name] = centroid

            # Create and save ROI mask based on shape type
            if shape_type == "sphere":
                roi_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
            elif shape_type == "squarev4":
                roi_mask = create_square_v4_mask(ref_shape, centroid, v1_data, "proj")
            else:
                roi_mask = create_square_v9_mask(ref_shape, centroid)
            n_voxels = int(np.sum(roi_mask))
            log(f"    Created {roi_name} with {n_voxels} voxels")

            roi_path = roi_dir / f"{prefix}_{roi_name}.nii.gz"
            roi_img = nib.Nifti1Image(roi_mask.astype(np.float32), ref_img.affine, ref_img.header)
            nib.save(roi_img, str(roi_path))
            roi_native_paths[roi_name] = str(roi_path)

        # Third pass: Refine association ROIs with Y-constraint from paired projection ROI
        # This ensures proj and assoc ROIs stay aligned to sample same X-direction diffusion
        for roi_name, proj_name in [("left_assoc", "left_proj"), ("right_assoc", "right_proj")]:
            centroid = template_centroids[roi_name]
            proj_centroid = roi_centroids[proj_name]

            if do_refinement and v1_data is not None:
                # Calculate original purity using appropriate mask
                if shape_type == "sphere":
                    orig_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
                elif shape_type == "squarev4":
                    orig_mask = create_square_v4_mask(ref_shape, centroid, v1_data, "assoc")
                else:
                    orig_mask = create_square_v9_mask(ref_shape, centroid)
                orig_purity, _, _, _ = calculate_roi_quality(v1_data, fa_data, orig_mask, "assoc")

                # Refine placement with Y/Z-constraint relative to projection ROI
                refined_centroid, refined_purity, _ = refine_roi_placement(
                    centroid,
                    v1_data,
                    fa_data,
                    ref_shape,
                    voxel_size,
                    "assoc",
                    radius_mm=sphere_radius or 3.0,  # Use 3mm for square refinement search
                    search_x=3,
                    search_y=2,
                    search_z=2,
                    reference_centroid=proj_centroid,
                    max_y_drift=1,
                    max_z_drift=1,
                    shape_type=shape_type,
                )

                offset = (
                    refined_centroid[0] - centroid[0],
                    refined_centroid[1] - centroid[1],
                    refined_centroid[2] - centroid[2],
                )
                y_drift = abs(refined_centroid[1] - proj_centroid[1])
                z_drift = abs(refined_centroid[2] - proj_centroid[2])

                if offset != (0, 0, 0):
                    log(f"    {roi_name} refined: {refined_centroid} (offset: {offset})")
                    log(f"    Purity: {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%")
                    log(f"    Drift from {proj_name}: Y={y_drift}, Z={z_drift} voxels")
                else:
                    log(
                        f"    {roi_name} no refinement needed (purity: {refined_purity * 100:.0f}%)"
                    )

                centroid = refined_centroid

            roi_centroids[roi_name] = centroid

            # Create and save ROI mask based on shape type
            if shape_type == "sphere":
                roi_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
            elif shape_type == "squarev4":
                roi_mask = create_square_v4_mask(ref_shape, centroid, v1_data, "assoc")
            else:
                roi_mask = create_square_v9_mask(ref_shape, centroid)
            n_voxels = int(np.sum(roi_mask))
            log(f"    Created {roi_name} with {n_voxels} voxels")

            roi_path = roi_dir / f"{prefix}_{roi_name}.nii.gz"
            roi_img = nib.Nifti1Image(roi_mask.astype(np.float32), ref_img.affine, ref_img.header)
            nib.save(roi_img, str(roi_path))
            roi_native_paths[roi_name] = str(roi_path)

        log("ROI placement completed successfully")
        log(f"  Left projection ROI: {roi_native_paths['left_proj']}")
        log(f"  Left association ROI: {roi_native_paths['left_assoc']}")
        log(f"  Right projection ROI: {roi_native_paths['right_proj']}")
        log(f"  Right association ROI: {roi_native_paths['right_assoc']}")

        return ROIPlacementResult(
            success=True,
            roi_mask_paths=roi_native_paths,
            roi_centers=roi_centroids,
        )

    # =========================================================================
    # Private helper methods
    # =========================================================================

    def _get_fsldir(self) -> str | None:
        """
        Get FSLDIR from environment or common installation paths.

        Returns
        -------
        str or None
            Path to FSL directory, or None if not found
        """
        fsldir = os.environ.get("FSLDIR")
        if fsldir and os.path.isdir(fsldir):
            return fsldir

        # Try common locations
        common_paths = [
            "/usr/local/fsl",
            "/usr/share/fsl/6.0",
            "/opt/fsl",
            os.path.expanduser("~/fsl"),
        ]
        for path in common_paths:
            if os.path.isdir(path):
                # Verify it has bin directory with flirt
                bin_dir = os.path.join(path, "bin")
                if not os.path.isdir(bin_dir):
                    bin_dir = os.path.join(path, "share", "fsl", "bin")
                if os.path.isfile(os.path.join(bin_dir, "flirt")):
                    return path

        return None

    def _get_fsl_bin_dir(self) -> Path | None:
        """
        Get path to FSL bin directory.

        Returns
        -------
        Path or None
            Path to FSL bin directory, or None if not found
        """
        fsldir = self._get_fsldir()
        if not fsldir:
            return None

        bin_dir = Path(fsldir) / "bin"
        if bin_dir.exists():
            return bin_dir

        # Some installations have bin under share/fsl/
        alt_bin = Path(fsldir) / "share" / "fsl" / "bin"
        if alt_bin.exists():
            return alt_bin

        return None

    def _fix_nan_in_nifti(self, input_path: str, output_path: str) -> bool:
        """
        Replace NaN values with 0 in a NIfTI image.

        FSL tools fail on images with NaN values, so this is necessary
        for FA maps where voxels outside the brain may be NaN.

        Parameters
        ----------
        input_path : str
            Path to input NIfTI file
        output_path : str
            Path for output NIfTI file

        Returns
        -------
        bool
            True if successful
        """
        try:
            img = nib.load(input_path)
            data = img.get_fdata()

            nan_count = np.sum(np.isnan(data))
            if nan_count > 0:
                data = np.nan_to_num(data, nan=0.0)
                new_img = nib.Nifti1Image(data.astype(np.float32), img.affine, img.header)
                new_img.header.set_data_dtype(np.float32)
                nib.save(new_img, output_path)
            else:
                # No NaN, just copy
                shutil.copy(input_path, output_path)

            return True

        except Exception:
            return False

    def _run_fsl_command(
        self,
        cmd: list[str],
        log_callback: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Execute an FSL command and capture output.

        Parameters
        ----------
        cmd : list of str
            Command and arguments
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        bool
            True if command succeeded
        """
        log = log_callback or (lambda x: None)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    log(line)

            process.wait()
            return process.returncode == 0

        except FileNotFoundError:
            log(f"ERROR: Command not found: {cmd[0]}")
            return False
        except Exception as e:
            log(f"ERROR: {e}")
            return False


# =============================================================================
# Backward compatibility functions
# =============================================================================


def get_fsldir() -> str | None:
    """Get FSLDIR from environment or common installation paths."""
    return FSLRegistration()._get_fsldir()


def get_fsl_bin_dir() -> Path | None:
    """Get path to FSL bin directory."""
    return FSLRegistration()._get_fsl_bin_dir()


def check_fsl_registration_available() -> tuple[bool, list[str]]:
    """Check if FSL registration tools are available."""
    return FSLRegistration().check_available()


def get_jhu_template_path() -> Path | None:
    """Get path to JHU-ICBM-FA-1mm.nii.gz template."""
    return FSLRegistration().get_template_path()
