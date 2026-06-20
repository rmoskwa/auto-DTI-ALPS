"""
B0 image extraction utilities for DTI-ALPS pipeline.

This module provides functions to extract and average b0 volumes from DWI data
for use in brain mask generation.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .tool_runner import SubprocessToolRunner, ToolRunner


@dataclass
class B0ExtractionResult:
    """Result of b0 extraction operation."""

    success: bool
    b0_path: str | None = None
    n_b0_volumes: int = 0
    b0_indices: list[int] | None = None
    error_message: str | None = None


def parse_bvals(bvals_path: str, b0_threshold: float = 50.0) -> tuple[list[int], list[float]]:
    """
    Parse a bvals file and identify b0 volume indices.

    Parameters
    ----------
    bvals_path : str
        Path to the bvals file
    b0_threshold : float
        Maximum b-value to be considered a b0 volume (default: 50)

    Returns
    -------
    tuple[list[int], list[float]]
        (list of b0 indices, list of all bvals)

    Raises
    ------
    FileNotFoundError
        If bvals file does not exist
    ValueError
        If bvals file is empty or contains invalid data
    """
    bvals_path = Path(bvals_path)
    if not bvals_path.exists():
        raise FileNotFoundError(f"bvals file not found: {bvals_path}")

    with open(bvals_path) as f:
        content = f.read().strip()

    if not content:
        raise ValueError(f"bvals file is empty: {bvals_path}")

    # Parse bvals (can be space or newline separated)
    try:
        bvals = [float(x) for x in content.split()]
    except ValueError as e:
        raise ValueError(f"Invalid bvals file format: {e}") from e

    if not bvals:
        raise ValueError(f"No bvals found in file: {bvals_path}")

    # Find b0 indices
    b0_indices = [i for i, bval in enumerate(bvals) if bval < b0_threshold]

    return b0_indices, bvals


def validate_b0_exists(bvals_path: str, b0_threshold: float = 50.0) -> tuple[bool, str, int]:
    """
    Validate that at least one b0 volume exists in the DWI data.

    Parameters
    ----------
    bvals_path : str
        Path to the bvals file
    b0_threshold : float
        Maximum b-value to be considered a b0 volume (default: 50)

    Returns
    -------
    tuple[bool, str, int]
        (is_valid, message, n_b0_volumes)
    """
    try:
        b0_indices, bvals = parse_bvals(bvals_path, b0_threshold)
    except (FileNotFoundError, ValueError) as e:
        return False, str(e), 0

    n_b0 = len(b0_indices)
    n_total = len(bvals)

    if n_b0 == 0:
        return (
            False,
            f"No b0 volumes found in DWI data. All {n_total} volumes have b-value >= {b0_threshold}",
            0,
        )

    return True, f"Found {n_b0} b0 volume(s) out of {n_total} total volumes", n_b0


def extract_and_average_b0(
    dwi_path: str,
    bvecs_path: str,
    bvals_path: str,
    output_path: str,
    b0_threshold: float = 50.0,
    log: Callable[[str], None] | None = None,
) -> B0ExtractionResult:
    """
    Extract b0 volumes from DWI and average them.

    Uses MRtrix3's dwiextract to extract b0 volumes and mrmath to average them.
    If only one b0 volume exists, it is used directly without averaging.

    Parameters
    ----------
    dwi_path : str
        Path to the 4D DWI NIfTI file
    bvecs_path : str
        Path to the bvecs file
    bvals_path : str
        Path to the bvals file
    output_path : str
        Path for the output averaged b0 image
    b0_threshold : float
        Maximum b-value to be considered a b0 volume (default: 50)
    log : Callable[[str], None] | None
        Optional logging callback

    Returns
    -------
    B0ExtractionResult
        Result containing success status, output path, and metadata
    """
    if log is None:
        log = lambda x: None  # noqa: E731

    # Validate b0 exists
    is_valid, message, n_b0 = validate_b0_exists(bvals_path, b0_threshold)
    if not is_valid:
        return B0ExtractionResult(
            success=False,
            error_message=message,
        )

    # Get b0 indices for reporting
    b0_indices, _ = parse_bvals(bvals_path, b0_threshold)
    log(f"  {message}")
    log(f"  B0 indices: {b0_indices}")

    # Create output directory if needed
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use dwiextract to get b0 volumes
    b0_extracted_path = str(Path(output_path).with_suffix("").with_suffix(".b0_all.nii.gz"))

    extract_cmd = [
        "dwiextract",
        dwi_path,
        b0_extracted_path,
        "-fslgrad",
        bvecs_path,
        bvals_path,
        "-bzero",
        "-force",
    ]

    log(f"  Extracting b0 volumes: {' '.join(extract_cmd)}")

    try:
        subprocess.run(
            extract_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        return B0ExtractionResult(
            success=False,
            error_message=f"dwiextract failed: {e.stderr}",
            n_b0_volumes=n_b0,
            b0_indices=b0_indices,
        )
    except FileNotFoundError:
        return B0ExtractionResult(
            success=False,
            error_message="dwiextract not found. Is MRtrix3 installed and in PATH?",
            n_b0_volumes=n_b0,
            b0_indices=b0_indices,
        )

    # If only one b0, just rename/copy the file
    if n_b0 == 1:
        log("  Single b0 volume found, using directly")
        # Use mrconvert to ensure proper format
        convert_cmd = ["mrconvert", b0_extracted_path, output_path, "-force"]
        try:
            subprocess.run(convert_cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            return B0ExtractionResult(
                success=False,
                error_message=f"mrconvert failed: {e.stderr}",
                n_b0_volumes=n_b0,
                b0_indices=b0_indices,
            )

        # Clean up intermediate file
        Path(b0_extracted_path).unlink(missing_ok=True)
    else:
        # Average multiple b0 volumes
        log(f"  Averaging {n_b0} b0 volumes")
        mean_cmd = [
            "mrmath",
            b0_extracted_path,
            "mean",
            output_path,
            "-axis",
            "3",
            "-force",
        ]

        log(f"  Command: {' '.join(mean_cmd)}")

        try:
            subprocess.run(mean_cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            return B0ExtractionResult(
                success=False,
                error_message=f"mrmath failed: {e.stderr}",
                n_b0_volumes=n_b0,
                b0_indices=b0_indices,
            )
        except FileNotFoundError:
            return B0ExtractionResult(
                success=False,
                error_message="mrmath not found. Is MRtrix3 installed and in PATH?",
                n_b0_volumes=n_b0,
                b0_indices=b0_indices,
            )

        # Clean up intermediate file
        Path(b0_extracted_path).unlink(missing_ok=True)

    # Verify output exists
    if not Path(output_path).exists():
        return B0ExtractionResult(
            success=False,
            error_message=f"B0 extraction completed but output file not found: {output_path}",
            n_b0_volumes=n_b0,
            b0_indices=b0_indices,
        )

    log(f"  B0 image saved to: {output_path}")

    return B0ExtractionResult(
        success=True,
        b0_path=output_path,
        n_b0_volumes=n_b0,
        b0_indices=b0_indices,
    )


def create_brain_mask_from_dwi(
    dwi_path: str,
    bvecs_path: str,
    bvals_path: str,
    output_mask_path: str,
    log: Callable[[str], None] | None = None,
    runner: ToolRunner | None = None,
) -> tuple[bool, str]:
    """
    Create a brain mask from DWI data using dwi2mask.

    Parameters
    ----------
    dwi_path : str
        Path to the 4D DWI NIfTI file
    bvecs_path : str
        Path to the bvecs file
    bvals_path : str
        Path to the bvals file
    output_mask_path : str
        Path for the output brain mask
    log : Callable[[str], None] | None
        Optional logging callback
    runner : ToolRunner | None
        Seam for external command execution. Defaults to a real
        subprocess-backed runner; tests inject a fake.

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """
    if log is None:
        log = lambda x: None  # noqa: E731
    if runner is None:
        runner = SubprocessToolRunner()

    # Create output directory if needed
    output_dir = Path(output_mask_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use dwi2mask with fslgrad
    mask_cmd = [
        "dwi2mask",
        dwi_path,
        output_mask_path,
        "-fslgrad",
        bvecs_path,
        bvals_path,
        "-force",
    ]

    log("  Creating brain mask with dwi2mask...")
    log(f"  Command: {' '.join(mask_cmd)}")

    # The runner never raises: a non-zero exit (including a missing binary,
    # which surfaces as returncode 127 with an explanatory output) is reported
    # here, so there is no CalledProcessError/FileNotFoundError to catch.
    result = runner.run(mask_cmd)
    if result.returncode != 0:
        return False, f"dwi2mask failed: {result.output}"

    # Verify output exists
    if not Path(output_mask_path).exists():
        return False, f"dwi2mask completed but output file not found: {output_mask_path}"

    log(f"  Brain mask saved to: {output_mask_path}")
    return True, "Brain mask created successfully"


def apply_mask_to_image(
    input_path: str,
    mask_path: str,
    output_path: str,
    log: Callable[[str], None] | None = None,
    runner: ToolRunner | None = None,
) -> tuple[bool, str]:
    """
    Apply a binary mask to an image using fslmaths.

    Parameters
    ----------
    input_path : str
        Path to the input image
    mask_path : str
        Path to the binary mask
    output_path : str
        Path for the output masked image
    log : Callable[[str], None] | None
        Optional logging callback
    runner : ToolRunner | None
        Seam for external command execution. Defaults to a real
        subprocess-backed runner; tests inject a fake.

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """
    if log is None:
        log = lambda x: None  # noqa: E731
    if runner is None:
        runner = SubprocessToolRunner()

    # Create output directory if needed
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use fslmaths to apply mask
    mask_cmd = ["fslmaths", input_path, "-mas", mask_path, output_path]

    log("  Applying brain mask to image...")
    log(f"  Command: {' '.join(mask_cmd)}")

    # The runner never raises: a non-zero exit (including a missing binary,
    # which surfaces as returncode 127 with an explanatory output) is reported
    # here, so there is no CalledProcessError/FileNotFoundError to catch.
    result = runner.run(mask_cmd)
    if result.returncode != 0:
        return False, f"fslmaths failed: {result.output}"

    # Verify output exists
    if not Path(output_path).exists():
        return False, f"fslmaths completed but output file not found: {output_path}"

    log(f"  Masked image saved to: {output_path}")
    return True, "Mask applied successfully"
