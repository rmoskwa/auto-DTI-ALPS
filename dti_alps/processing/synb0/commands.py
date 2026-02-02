"""
Command builders for synB0-DISCO external tools.

This module provides functions to build command-line invocations for
FreeSurfer, FSL, ANTs, and c3d tools used in the synB0-DISCO pipeline.
"""

from pathlib import Path


def get_data_dir() -> Path:
    """Get the path to the bundled synb0 data directory."""
    return Path(__file__).parent.parent.parent / "data" / "synb0"


def get_mni_template_path() -> Path:
    """Get path to MNI T1 template (1mm)."""
    return get_data_dir() / "mni_icbm152_t1_tal_nlin_asym_09c.nii.gz"


def get_mni_template_2mm_path() -> Path:
    """Get path to MNI T1 template (2.5mm for inference)."""
    return get_data_dir() / "mni_icbm152_t1_tal_nlin_asym_09c_2_5.nii.gz"


def get_mni_mask_path() -> Path:
    """Get path to MNI brain mask (1mm)."""
    return get_data_dir() / "mni_icbm152_t1_tal_nlin_asym_09c_mask.nii.gz"


def get_mni_mask_2mm_path() -> Path:
    """Get path to MNI brain mask (2.5mm)."""
    return get_data_dir() / "mni_icbm152_t1_tal_nlin_asym_09c_mask_2_5.nii.gz"


def get_topup_config_path() -> Path:
    """Get path to topup configuration file optimized for synthetic b0."""
    return get_data_dir() / "synb0.cnf"


# =============================================================================
# FreeSurfer commands
# =============================================================================


def build_mri_convert_cmd(input_path: str, output_path: str) -> list[str]:
    """Build mri_convert command for format conversion."""
    return ["mri_convert", input_path, output_path]


def build_mri_nu_correct_cmd(input_path: str, output_path: str, iterations: int = 2) -> list[str]:
    """Build mri_nu_correct.mni command for bias field correction."""
    return ["mri_nu_correct.mni", "--i", input_path, "--o", output_path, "--n", str(iterations)]


def build_mri_normalize_cmd(input_path: str, output_path: str) -> list[str]:
    """Build mri_normalize command for intensity normalization."""
    return ["mri_normalize", "-g", "1", "-mprage", input_path, output_path]


# =============================================================================
# FSL commands
# =============================================================================


def build_bet_cmd(
    input_path: str, output_path: str, frac: float = 0.4, robust: bool = True, mask: bool = True
) -> list[str]:
    """
    Build FSL bet command for brain extraction.

    Parameters
    ----------
    input_path : str
        Input image path
    output_path : str
        Output brain-extracted image path
    frac : float
        Fractional intensity threshold (0->1); default=0.4
    robust : bool
        Robust brain centre estimation
    mask : bool
        Generate binary brain mask
    """
    cmd = ["bet", input_path, output_path, "-f", str(frac)]
    if robust:
        cmd.append("-R")
    if mask:
        cmd.append("-m")
    return cmd


def build_epi_reg_cmd(
    epi_path: str, t1_path: str, t1_brain_path: str, output_prefix: str
) -> list[str]:
    """
    Build FSL epi_reg command for EPI to T1 registration.

    Parameters
    ----------
    epi_path : str
        Path to EPI (b0) image
    t1_path : str
        Path to T1 image (full head)
    t1_brain_path : str
        Path to brain-extracted T1 image
    output_prefix : str
        Output prefix for registration files

    Returns
    -------
    list[str]
        Command arguments
    """
    return [
        "epi_reg",
        f"--epi={epi_path}",
        f"--t1={t1_path}",
        f"--t1brain={t1_brain_path}",
        f"--out={output_prefix}",
    ]


def build_topup_cmd(
    imain_path: str,
    datain_path: str,
    output_prefix: str,
    config_path: str | None = None,
    extra_options: dict | None = None,
) -> list[str]:
    """
    Build FSL topup command for susceptibility distortion estimation.

    Parameters
    ----------
    imain_path : str
        Path to 4D image with b0 volumes (distorted + synthetic)
    datain_path : str
        Path to acquisition parameters file
    output_prefix : str
        Output prefix for topup results
    config_path : str | None
        Path to topup config file (uses synb0.cnf if None)
    extra_options : dict | None
        Additional topup options

    Returns
    -------
    list[str]
        Command arguments
    """
    if config_path is None:
        config_path = str(get_topup_config_path())

    cmd = [
        "topup",
        f"--imain={imain_path}",
        f"--datain={datain_path}",
        f"--out={output_prefix}",
        f"--config={config_path}",
    ]

    if extra_options:
        for key, value in extra_options.items():
            if value is True:
                cmd.append(f"--{key}")
            elif value is not None and value is not False:
                cmd.append(f"--{key}={value}")

    return cmd


def build_eddy_cmd(
    imain_path: str,
    mask_path: str,
    acqp_path: str,
    index_path: str,
    bvecs_path: str,
    bvals_path: str,
    topup_prefix: str,
    output_path: str,
    extra_options: dict | None = None,
) -> list[str]:
    """
    Build FSL eddy command for eddy current and motion correction.

    Parameters
    ----------
    imain_path : str
        Path to 4D DWI image
    mask_path : str
        Path to brain mask
    acqp_path : str
        Path to acquisition parameters file
    index_path : str
        Path to index file
    bvecs_path : str
        Path to bvecs file
    bvals_path : str
        Path to bvals file
    topup_prefix : str
        Prefix for topup output files
    output_path : str
        Output path (without extension)
    extra_options : dict | None
        Additional eddy options (e.g., --repol, --cnr_maps, --slm)

    Returns
    -------
    list[str]
        Command arguments
    """
    cmd = [
        "eddy",
        f"--imain={imain_path}",
        f"--mask={mask_path}",
        f"--acqp={acqp_path}",
        f"--index={index_path}",
        f"--bvecs={bvecs_path}",
        f"--bvals={bvals_path}",
        f"--topup={topup_prefix}",
        f"--out={output_path}",
    ]

    if extra_options:
        for key, value in extra_options.items():
            if value is True:
                cmd.append(f"--{key}")
            elif value is not None and value is not False:
                cmd.append(f"--{key}={value}")

    return cmd


def build_fslmaths_smooth_cmd(input_path: str, output_path: str, sigma: float = 1.15) -> list[str]:
    """Build fslmaths command for Gaussian smoothing."""
    return ["fslmaths", input_path, "-s", str(sigma), output_path]


def build_fslmerge_cmd(output_path: str, input_paths: list[str], dimension: str = "t") -> list[str]:
    """Build fslmerge command to concatenate images."""
    return ["fslmerge", f"-{dimension}", output_path] + input_paths


def build_fslmaths_mul_cmd(input_path: str, mask_path: str, output_path: str) -> list[str]:
    """Build fslmaths command to multiply image by mask."""
    return ["fslmaths", input_path, "-mul", mask_path, output_path]


# =============================================================================
# ANTs commands
# =============================================================================


def build_ants_registration_cmd(
    fixed_path: str,
    moving_path: str,
    output_prefix: str,
    transform_type: str = "a",
    threads: int | None = None,
) -> list[str]:
    """
    Build antsRegistrationSyNQuick.sh command for image registration.

    Parameters
    ----------
    fixed_path : str
        Path to fixed (reference) image
    moving_path : str
        Path to moving image
    output_prefix : str
        Output prefix for transformation files
    transform_type : str
        Transform type: 'a' for affine only, 's' for SyN (deformable)
    threads : int | None
        Number of threads (default: system default)

    Returns
    -------
    list[str]
        Command arguments
    """
    cmd = [
        "antsRegistrationSyNQuick.sh",
        "-d",
        "3",
        "-f",
        fixed_path,
        "-m",
        moving_path,
        "-o",
        output_prefix,
        "-t",
        transform_type,
    ]
    if threads is not None:
        cmd.extend(["-n", str(threads)])
    return cmd


def build_ants_apply_transforms_cmd(
    input_path: str,
    reference_path: str,
    output_path: str,
    transforms: list[str],
    interpolation: str = "BSpline",
    invert_flags: list[bool] | None = None,
) -> list[str]:
    """
    Build antsApplyTransforms command for applying transformations.

    Parameters
    ----------
    input_path : str
        Path to input image
    reference_path : str
        Path to reference image (defines output space)
    output_path : str
        Path for output image
    transforms : list[str]
        List of transform file paths (applied in reverse order)
    interpolation : str
        Interpolation method: Linear, NearestNeighbor, BSpline, etc.
    invert_flags : list[bool] | None
        List of invert flags for each transform

    Returns
    -------
    list[str]
        Command arguments
    """
    cmd = [
        "antsApplyTransforms",
        "-d",
        "3",
        "-i",
        input_path,
        "-r",
        reference_path,
        "-o",
        output_path,
        "-n",
        interpolation,
    ]

    # Add transforms with optional invert flags
    if invert_flags is None:
        invert_flags = [False] * len(transforms)

    for transform, invert in zip(transforms, invert_flags):
        if invert:
            cmd.extend(["-t", f"[{transform},1]"])
        else:
            cmd.extend(["-t", transform])

    return cmd


# =============================================================================
# c3d commands
# =============================================================================


def build_c3d_affine_tool_cmd(
    reference_path: str,
    source_path: str,
    input_matrix_path: str,
    output_matrix_path: str,
    fsl2ras: bool = True,
) -> list[str]:
    """
    Build c3d_affine_tool command to convert FSL matrix to ITK/ANTs format.

    Parameters
    ----------
    reference_path : str
        Path to reference image
    source_path : str
        Path to source image
    input_matrix_path : str
        Path to input FSL affine matrix
    output_matrix_path : str
        Path for output ITK affine matrix
    fsl2ras : bool
        Convert from FSL to RAS format

    Returns
    -------
    list[str]
        Command arguments
    """
    cmd = [
        "c3d_affine_tool",
        "-ref",
        reference_path,
        "-src",
        source_path,
        input_matrix_path,
    ]
    if fsl2ras:
        cmd.append("-fsl2ras")
    cmd.extend(["-oitk", output_matrix_path])
    return cmd
