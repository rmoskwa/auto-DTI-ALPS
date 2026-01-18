"""
FSL-based registration for automatic ROI placement in DTI-ALPS pipeline.

This module registers subject FA images to the JHU-ICBM-FA-1mm template
and transforms pre-defined ROI masks from template space to subject native space.
After transformation, spherical ROIs are created at the centroid of each
transformed region with a user-configurable radius.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import nibabel as nib
import numpy as np

if TYPE_CHECKING:
    from .pipeline import PipelineState


def get_fsldir() -> str | None:
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


def get_fsl_bin_dir() -> Path | None:
    """
    Get path to FSL bin directory.

    Returns
    -------
    Path or None
        Path to FSL bin directory, or None if not found
    """
    fsldir = get_fsldir()
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


def check_fsl_registration_available() -> tuple[bool, list[str]]:
    """
    Check if FSL registration tools are available.

    Returns
    -------
    tuple of (bool, list)
        (all_available, list of missing commands)
    """
    required_commands = ["bet2", "flirt", "fnirt", "invwarp", "applywarp"]
    missing = []

    fsl_bin = get_fsl_bin_dir()

    for cmd in required_commands:
        found = False
        # Check in FSL bin directory
        if fsl_bin and (fsl_bin / cmd).is_file():
            found = True
        # Also check PATH
        elif shutil.which(cmd) is not None:
            found = True

        if not found:
            missing.append(cmd)

    return (len(missing) == 0, missing)


def get_jhu_template_path() -> Path | None:
    """
    Get path to JHU-ICBM-FA-1mm.nii.gz template.

    Returns
    -------
    Path or None
        Path to template, or None if not found
    """
    fsldir = get_fsldir()
    if not fsldir:
        return None

    template_path = Path(fsldir) / "data" / "atlases" / "JHU" / "JHU-ICBM-FA-1mm.nii.gz"
    if template_path.exists():
        return template_path

    return None


def get_roi_template_paths() -> dict[str, Path] | None:
    """
    Get paths to pre-defined ROI templates shipped with the package.

    Returns
    -------
    dict or None
        Dictionary mapping ROI names to template paths, or None if any missing.
        Keys: 'left_proj', 'left_assoc', 'right_proj', 'right_assoc'
    """
    # Look relative to this module
    module_dir = Path(__file__).parent.parent.parent
    templates_dir = module_dir / "templates"

    roi_templates = {
        "left_proj": templates_dir / "JHU-labels-left_proj.nii.gz",
        "left_assoc": templates_dir / "JHU-labels-left_assoc.nii.gz",
        "right_proj": templates_dir / "JHU-labels-right_proj.nii.gz",
        "right_assoc": templates_dir / "JHU-labels-right_assoc.nii.gz",
    }

    # Check all templates exist
    for path in roi_templates.values():
        if not path.exists():
            return None

    return roi_templates


def create_sphere_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[float, float, float],
    radius_mm: float,
    voxel_size: tuple[float, float, float],
) -> np.ndarray:
    """
    Create a spherical binary mask centered at given voxel coordinates.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of float
        Center of sphere in voxel coordinates
    radius_mm : float
        Radius of sphere in millimeters
    voxel_size : tuple of float
        Voxel dimensions in millimeters (x, y, z)

    Returns
    -------
    np.ndarray
        Binary mask with sphere
    """
    x, y, z = np.ogrid[: shape[0], : shape[1], : shape[2]]

    # Calculate squared distance from center in mm
    dist_sq = (
        ((x - center_voxel[0]) * voxel_size[0]) ** 2
        + ((y - center_voxel[1]) * voxel_size[1]) ** 2
        + ((z - center_voxel[2]) * voxel_size[2]) ** 2
    )

    return dist_sq <= radius_mm**2


def find_mask_centroid(mask_data: np.ndarray) -> tuple[int, int, int] | None:
    """
    Find the centroid of non-zero voxels in a mask, rounded to nearest integer.

    Parameters
    ----------
    mask_data : np.ndarray
        Binary mask array

    Returns
    -------
    tuple of int or None
        Centroid coordinates (x, y, z) rounded to nearest integer,
        or None if mask is empty
    """
    coords = np.where(mask_data > 0)
    if len(coords[0]) == 0:
        return None

    centroid = (
        int(round(coords[0].mean())),
        int(round(coords[1].mean())),
        int(round(coords[2].mean())),
    )
    return centroid


def calculate_roi_quality(
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    mask: np.ndarray,
    fiber_type: str,
) -> tuple[float, float, float, float]:
    """
    Calculate ROI quality based on fiber purity, direction strength, and FA.

    The quality score rewards ROIs that:
    1. Have high fiber purity (% of voxels with correct dominant direction)
    2. Have strong directional alignment (mean magnitude of target V1 component)
    3. Have high FA values (strong fiber signal)

    Parameters
    ----------
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    mask : np.ndarray
        Binary ROI mask
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)

    Returns
    -------
    tuple of (purity, direction_strength, mean_fa, combined_score)
        purity: fraction of voxels with correct fiber orientation
        direction_strength: mean magnitude of target V1 component
        mean_fa: mean FA value in ROI
        combined_score: purity * direction_strength * mean_fa
    """
    coords = np.where(mask)
    if len(coords[0]) == 0:
        return 0.0, 0.0, 0.0, 0.0

    n_correct = 0
    direction_strengths = []
    fa_values = []

    for i in range(len(coords[0])):
        x, y, z = coords[0][i], coords[1][i], coords[2][i]
        v1 = v1_data[x, y, z, :]
        fa = fa_data[x, y, z]
        abs_v1 = np.abs(v1)

        fa_values.append(fa)

        if fiber_type == "proj":
            # Projection fibers: Z-dominant (superior-inferior)
            is_correct = abs_v1[2] > abs_v1[1] and abs_v1[2] > abs_v1[0]
            direction_strength = abs_v1[2]
        else:
            # Association fibers: Y-dominant (anterior-posterior)
            is_correct = abs_v1[1] > abs_v1[2] and abs_v1[1] > abs_v1[0]
            direction_strength = abs_v1[1]

        if is_correct:
            n_correct += 1
        direction_strengths.append(direction_strength)

    purity = n_correct / len(coords[0])
    mean_direction_strength = np.mean(direction_strengths)
    mean_fa = np.mean(fa_values)

    # Combined score rewards ROIs with high purity, strong direction, and high FA
    combined_score = purity * mean_direction_strength * mean_fa

    return purity, mean_direction_strength, mean_fa, combined_score


def refine_roi_placement(
    original_centroid: tuple[int, int, int],
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    shape: tuple[int, int, int],
    voxel_size: tuple[float, float, float],
    fiber_type: str,
    radius_mm: float = 3.0,
    search_xy: int = 2,
    search_z: int = 1,
) -> tuple[tuple[int, int, int], float, float]:
    """
    Refine ROI placement by searching nearby positions for better fiber purity.

    Starting from the template-based centroid, search a small neighborhood
    (±search_xy voxels in X/Y, ±search_z voxels in Z) to find the position
    that maximizes the combined quality score (purity * direction * FA).

    Parameters
    ----------
    original_centroid : tuple of int
        Initial centroid from template registration (x, y, z)
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    shape : tuple of int
        Shape of the image volume
    voxel_size : tuple of float
        Voxel dimensions in mm
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)
    radius_mm : float
        Sphere radius in millimeters
    search_xy : int
        Search range in X and Y directions (voxels)
    search_z : int
        Search range in Z direction (voxels)

    Returns
    -------
    tuple of (best_center, best_purity, best_score)
        best_center: optimal centroid position
        best_purity: fiber purity at optimal position
        best_score: combined quality score at optimal position
    """
    best_center = original_centroid
    best_score = -1.0
    best_purity = 0.0

    for dx in range(-search_xy, search_xy + 1):
        for dy in range(-search_xy, search_xy + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    original_centroid[0] + dx,
                    original_centroid[1] + dy,
                    original_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # Create sphere at test position
                sphere = create_sphere_mask(shape, test_center, radius_mm, voxel_size)

                # Calculate quality metrics
                purity, _, _, score = calculate_roi_quality(v1_data, fa_data, sphere, fiber_type)

                if score > best_score:
                    best_score = score
                    best_center = test_center
                    best_purity = purity

    return best_center, best_purity, best_score


def fix_nan_in_nifti(input_path: str, output_path: str) -> bool:
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
        import nibabel as nib
        import numpy as np

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


def run_fsl_command(
    cmd: list[str],
    log_callback: callable = None,
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


def register_fa_to_template(
    state: "PipelineState",
    log_callback: callable = None,
) -> bool:
    """
    Register subject FA to JHU template and transform ROI masks to native space.

    Steps:
    1. Fix NaN values in FA image
    2. Skull stripping with BET2
    3. Linear registration (FLIRT)
    4. Non-linear registration (FNIRT)
    5. Create inverse warp (INVWARP)
    6. Apply inverse warp to all 4 ROI masks (APPLYWARP)

    Parameters
    ----------
    state : PipelineState
        Pipeline state with fa_path set
    log_callback : callable, optional
        Function to call with log messages

    Returns
    -------
    bool
        True if registration succeeded
    """
    log = log_callback or (lambda x: None)

    # Get FSL paths
    fsl_bin = get_fsl_bin_dir()
    if not fsl_bin:
        log("ERROR: FSL bin directory not found")
        return False

    jhu_template = get_jhu_template_path()
    if not jhu_template:
        log("ERROR: JHU-ICBM-FA-1mm.nii.gz template not found")
        return False

    roi_templates = get_roi_template_paths()
    if not roi_templates:
        log("ERROR: ROI template files not found in templates/ directory")
        return False

    # Set up output paths
    reg_dir = Path(state.output_dir) / "registration"
    reg_dir.mkdir(parents=True, exist_ok=True)

    # Also create rois directory for final ROI masks
    roi_dir = Path(state.output_dir) / "rois"
    roi_dir.mkdir(parents=True, exist_ok=True)

    prefix = state.output_prefix
    fa_nonan = reg_dir / f"{prefix}_FA_nonan.nii.gz"
    fa_brain = reg_dir / f"{prefix}_FA_brain.nii.gz"
    affine_mat = reg_dir / f"{prefix}_subject2jhu_affine.mat"
    warp_coef = reg_dir / f"{prefix}_subject2jhu_warp_coef.nii.gz"
    inverse_warp = reg_dir / f"{prefix}_jhu2subject_warp_coef.nii.gz"

    # Step 1: Fix NaN values (bet2 may fail on NaN)
    log("Preparing FA image (fixing NaN values if present)...")
    if not fix_nan_in_nifti(state.fa_path, str(fa_nonan)):
        log("ERROR: Failed to prepare FA image")
        return False

    # Step 2: Skull stripping with bet2
    log("Running skull stripping (BET2)...")
    bet_cmd = [
        str(fsl_bin / "bet2"),
        str(fa_nonan),
        str(fa_brain),
        "-f",
        "0.3",  # Fractional intensity threshold (lower = larger brain)
    ]
    log(f"  Command: {' '.join(bet_cmd)}")
    if not run_fsl_command(bet_cmd, log):
        log("ERROR: BET2 skull stripping failed")
        return False

    if not fa_brain.exists():
        log("ERROR: Skull-stripped FA not created")
        return False

    # Step 3: Linear registration (FLIRT)
    # Use skull-stripped FA for FLIRT to get better initial alignment
    log("Running linear registration (FLIRT)...")
    flirt_cmd = [
        str(fsl_bin / "flirt"),
        "-in",
        str(fa_brain),
        "-ref",
        str(jhu_template),
        "-omat",
        str(affine_mat),
        "-dof",
        "12",
        "-cost",
        "corratio",  # Correlation ratio - good for same-modality
        "-searchrx",
        "-30",
        "30",  # Reduced search range for stability
        "-searchry",
        "-30",
        "30",
        "-searchrz",
        "-30",
        "30",
    ]
    log(f"  Command: {' '.join(flirt_cmd)}")
    if not run_fsl_command(flirt_cmd, log):
        log("ERROR: FLIRT failed")
        return False

    if not affine_mat.exists():
        log("ERROR: Affine matrix not created")
        return False

    # Step 4: Non-linear registration (FNIRT)
    # Use NON-skull-stripped FA for FNIRT (per FSL documentation)
    # Parameters optimized for FA registration with balanced speed/accuracy:
    # - intmod=none: FA is quantitative, no intensity modulation needed
    # - jacrange=0.2,5: Constrain Jacobian to prevent extreme warps
    # - lambda: Stronger regularization for stable warps
    # - subsamp/miter: Balanced profile (~15-20 min instead of ~30 min)
    log("Running non-linear registration (FNIRT)...")
    fnirt_cmd = [
        str(fsl_bin / "fnirt"),
        f"--in={fa_nonan}",  # Use non-skull-stripped FA
        f"--ref={jhu_template}",
        f"--aff={affine_mat}",
        f"--cout={warp_coef}",
        "--intmod=none",  # No intensity modulation for quantitative FA
        "--jacrange=0.2,5",  # Constrain Jacobian to prevent extreme warps
        "--lambda=300,150,100,50",  # Stronger regularization
        "--subsamp=4,2,2,1",  # Balanced: keep subsamp=2 longer for speed
        "--miter=5,5,3,3",  # Balanced: fewer iterations at full resolution
        "--warpres=10,10,10",  # 10mm warp resolution (FSL recommended)
    ]
    log(f"  Command: {' '.join(fnirt_cmd)}")
    if not run_fsl_command(fnirt_cmd, log):
        log("ERROR: FNIRT failed")
        return False

    if not warp_coef.exists():
        log("ERROR: Warp coefficients not created")
        return False

    # Step 5: Create inverse warp
    log("Creating inverse warp (INVWARP)...")
    invwarp_cmd = [
        str(fsl_bin / "invwarp"),
        f"--ref={state.fa_path}",
        f"--warp={warp_coef}",
        f"--out={inverse_warp}",
    ]
    log(f"  Command: {' '.join(invwarp_cmd)}")
    if not run_fsl_command(invwarp_cmd, log):
        log("ERROR: INVWARP failed")
        return False

    if not inverse_warp.exists():
        log("ERROR: Inverse warp not created")
        return False

    # Step 6: Apply inverse warp to all ROI masks and find centroids
    log("Transforming ROI masks to native space...")

    # Load reference image for shape and voxel size
    ref_img = nib.load(state.fa_path)
    ref_shape = ref_img.shape[:3]
    fa_data = ref_img.get_fdata()
    voxel_size = ref_img.header.get_zooms()[:3]

    # Get sphere radius from state (default 3.0mm)
    sphere_radius = getattr(state, "roi_sphere_radius", 3.0)
    log(f"  Sphere radius: {sphere_radius} mm")

    # Check if ROI refinement is enabled
    do_refinement = getattr(state, "refine_roi_placement", True)
    v1_data = None
    if do_refinement:
        log("  ROI refinement enabled (±2 X/Y, ±1 Z voxels)")
        # Load V1 data for fiber orientation analysis
        if state.v1_path and os.path.exists(state.v1_path):
            v1_img = nib.load(state.v1_path)
            v1_data = v1_img.get_fdata()
        else:
            log("  WARNING: V1 data not available, skipping refinement")
            do_refinement = False

    roi_centroids = {}
    roi_native_paths = {}

    for roi_name, roi_template in roi_templates.items():
        # First transform to get approximate location
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
        if not run_fsl_command(applywarp_cmd, log):
            log(f"ERROR: Failed to transform {roi_name}")
            return False

        if not roi_transformed.exists():
            log(f"ERROR: {roi_name} transformed ROI not created")
            return False

        # Load transformed mask and find centroid
        transformed_img = nib.load(str(roi_transformed))
        transformed_data = transformed_img.get_fdata()

        centroid = find_mask_centroid(transformed_data)
        if centroid is None:
            log(f"ERROR: No voxels found in transformed {roi_name}")
            return False

        log(f"    Template centroid: {centroid}")

        # Determine fiber type from ROI name
        fiber_type = "proj" if "proj" in roi_name else "assoc"

        # Apply refinement if enabled
        if do_refinement and v1_data is not None:
            # Calculate original purity
            orig_sphere = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
            orig_purity, _, _, _ = calculate_roi_quality(v1_data, fa_data, orig_sphere, fiber_type)

            # Refine placement
            refined_centroid, refined_purity, _ = refine_roi_placement(
                centroid,
                v1_data,
                fa_data,
                ref_shape,
                voxel_size,
                fiber_type,
                radius_mm=sphere_radius,
                search_xy=2,
                search_z=1,
            )

            # Calculate offset
            offset = (
                refined_centroid[0] - centroid[0],
                refined_centroid[1] - centroid[1],
                refined_centroid[2] - centroid[2],
            )

            if offset != (0, 0, 0):
                log(f"    Refined centroid: {refined_centroid} (offset: {offset})")
                log(f"    Purity: {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%")
            else:
                log(f"    No refinement needed (purity: {refined_purity * 100:.0f}%)")

            centroid = refined_centroid

        roi_centroids[roi_name] = centroid

        # Create spherical ROI at (possibly refined) centroid
        sphere_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
        n_voxels = int(np.sum(sphere_mask))
        log(f"    Created sphere with {n_voxels} voxels")

        # Save spherical ROI
        roi_sphere = roi_dir / f"{prefix}_{roi_name}.nii.gz"
        sphere_img = nib.Nifti1Image(sphere_mask.astype(np.float32), ref_img.affine, ref_img.header)
        nib.save(sphere_img, str(roi_sphere))

        roi_native_paths[roi_name] = str(roi_sphere)

    # Store ROI paths and centroids in state
    state.roi_mask_paths = roi_native_paths
    state.roi_centers = roi_centroids

    log("Registration and ROI creation completed successfully")
    log(f"  Left projection ROI: {roi_native_paths['left_proj']}")
    log(f"  Left association ROI: {roi_native_paths['left_assoc']}")
    log(f"  Right projection ROI: {roi_native_paths['right_proj']}")
    log(f"  Right association ROI: {roi_native_paths['right_assoc']}")
    return True
