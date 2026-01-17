#!/usr/bin/env python3
"""
Standalone script to test FSL-based registration for automatic ROI placement.

This script registers a subject's FA image to the JHU-ICBM-FA-1mm template,
transforms pre-defined ROI templates to subject space, finds the centroid of
each transformed region, and creates spherical ROIs at those locations.

Usage:
    python test_registration.py <subject_fa_path> [--output-dir <dir>] [--sphere-radius <mm>]

Example:
    python test_registration.py subject_FA.nii.gz

    # With custom sphere radius:
    python test_registration.py subject_FA.nii.gz --sphere-radius 3.0

    # With V1 image transformation to JHU space:
    python test_registration.py subject_FA.nii.gz --v1 subject_V1.nii.gz

Required Environment:
    - FSL must be installed and FSLDIR set (or sourced via /etc/fsl/fsl.sh)
    - JHU-ICBM-FA-1mm.nii.gz must exist in $FSLDIR/data/atlases/JHU/
    - ROI templates must exist in templates/ directory
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_fsldir() -> str | None:
    """Get FSLDIR environment variable, attempting to source if not set."""
    fsldir = os.environ.get("FSLDIR")
    if fsldir:
        return fsldir

    # Try common locations
    common_paths = [
        "/usr/local/fsl",
        "/usr/share/fsl/6.0",
        "/opt/fsl",
        os.path.expanduser("~/fsl"),
    ]
    for path in common_paths:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "bin", "flirt")):
            return path

    return None


def check_fsl_available() -> tuple[bool, str]:
    """Check if FSL tools are available."""
    fsldir = get_fsldir()
    if not fsldir:
        return False, "FSLDIR not set and FSL not found in common locations"

    required_tools = ["bet2", "flirt", "fnirt", "invwarp", "applywarp"]
    bin_dir = os.path.join(fsldir, "bin")
    if not os.path.isdir(bin_dir):
        bin_dir = os.path.join(fsldir, "share", "fsl", "bin")

    missing = []
    for tool in required_tools:
        tool_path = os.path.join(bin_dir, tool)
        if not os.path.isfile(tool_path):
            # Also check if it's in PATH
            if shutil.which(tool) is None:
                missing.append(tool)

    if missing:
        return False, f"Missing FSL tools: {', '.join(missing)}"

    return True, fsldir


def fix_nan_in_nifti(input_path: Path, output_path: Path) -> bool:
    """
    Replace NaN values with 0 in a NIfTI image.

    FSL tools often fail on images with NaN values, so this is necessary
    for images like FA maps where voxels outside the brain may be NaN.
    """
    try:
        import nibabel as nib
        import numpy as np

        img = nib.load(str(input_path))
        data = img.get_fdata()

        nan_count = np.sum(np.isnan(data))
        if nan_count > 0:
            print(
                f"  Found {nan_count} NaN voxels ({100 * nan_count / data.size:.2f}%), replacing with 0"
            )
            data = np.nan_to_num(data, nan=0.0)

            # Create new image with same header
            new_img = nib.Nifti1Image(data.astype(np.float32), img.affine, img.header)
            new_img.header.set_data_dtype(np.float32)
            nib.save(new_img, str(output_path))
            return True
        else:
            # No NaN, just copy
            import shutil

            shutil.copy(str(input_path), str(output_path))
            return True

    except Exception as e:
        print(f"  ERROR: Failed to fix NaN values: {e}")
        return False


def run_command(cmd: list[str], description: str) -> bool:
    """Run a command and stream output."""
    print(f"\n{'=' * 60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in iter(process.stdout.readline, ""):
            print(line, end="")

        process.wait()

        if process.returncode != 0:
            print(f"\nERROR: Command failed with return code {process.returncode}")
            return False

        print(f"\nSUCCESS: {description} completed")
        return True

    except FileNotFoundError as e:
        print(f"\nERROR: Command not found: {e}")
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        return False


def create_sphere_mask(shape, center_voxel, radius_mm, voxel_size):
    """Create a spherical binary mask centered at given voxel coordinates."""
    import numpy as np

    x, y, z = np.ogrid[: shape[0], : shape[1], : shape[2]]
    dist_sq = (
        ((x - center_voxel[0]) * voxel_size[0]) ** 2
        + ((y - center_voxel[1]) * voxel_size[1]) ** 2
        + ((z - center_voxel[2]) * voxel_size[2]) ** 2
    )
    return dist_sq <= radius_mm**2


def find_mask_centroid(mask_data):
    """Find the centroid of non-zero voxels, rounded to nearest integer."""
    import numpy as np

    coords = np.where(mask_data > 0)
    if len(coords[0]) == 0:
        return None
    return (
        int(round(coords[0].mean())),
        int(round(coords[1].mean())),
        int(round(coords[2].mean())),
    )


def register_fa_to_template(
    subject_fa: Path,
    output_dir: Path,
    jhu_fa_template: Path,
    fsl_bin: Path,
    sphere_radius: float = 2.0,
    subject_v1: Path | None = None,
) -> dict[str, Path] | None:
    """
    Perform registration of subject FA to JHU template and create spherical ROIs.

    Steps:
    1. Fix NaN values in FA image
    2. Skull stripping with BET2
    3. Linear registration (FLIRT) - subject FA to JHU template
    4. Non-linear registration (FNIRT) - refine with warping
    5. Inverse warp (INVWARP) - create template-to-subject transform
    6. Transform ROI templates to subject space, find centroids, create spheres
    7. (Optional) Apply forward warp to V1 - transform V1 to JHU space

    Args:
        subject_fa: Path to subject's FA image
        output_dir: Directory for output files
        jhu_fa_template: Path to JHU-ICBM-FA-1mm.nii.gz
        fsl_bin: Path to FSL bin directory
        sphere_radius: Radius of spherical ROIs in mm (default: 2.0)
        subject_v1: Optional path to subject's V1 image for transformation to JHU space

    Returns:
        Dictionary with paths to output files, or None on failure
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Define output paths
    prefix = subject_fa.stem.replace(".nii", "").replace("_FA", "")

    # Step 1: Fix NaN values in FA image (FSL doesn't handle NaN)
    fa_fixed = output_dir / f"{prefix}_FA_nonan.nii.gz"
    print("\nStep 1: Preparing FA image (fixing NaN values if present)...")
    if not fix_nan_in_nifti(subject_fa, fa_fixed):
        print("ERROR: Failed to prepare FA image")
        return None

    # Step 2: Skull stripping with BET2
    fa_brain = output_dir / f"{prefix}_FA_brain.nii.gz"
    bet_cmd = [
        str(fsl_bin / "bet2"),
        str(fa_fixed),
        str(fa_brain),
        "-f",
        "0.3",  # Fractional intensity threshold (lower = larger brain)
    ]

    if not run_command(bet_cmd, "Step 2: Skull Stripping (BET2)"):
        return None

    if not fa_brain.exists():
        print(f"ERROR: Skull-stripped FA not created: {fa_brain}")
        return None

    # Use skull-stripped FA for registration
    subject_fa_for_reg = fa_brain

    outputs = {
        "fa_nonan": fa_fixed,
        "fa_brain": fa_brain,
        "affine_mat": output_dir / f"{prefix}_subject2jhu_affine.mat",
        "registered_fa": output_dir / f"{prefix}_FA_to_JHU.nii.gz",
        "warp_coef": output_dir / f"{prefix}_subject2jhu_warp_coef.nii.gz",
        "inverse_warp": output_dir / f"{prefix}_jhu2subject_warp_coef.nii.gz",
    }

    # Step 3: Linear Registration (FLIRT)
    flirt_cmd = [
        str(fsl_bin / "flirt"),
        "-in",
        str(subject_fa_for_reg),
        "-ref",
        str(jhu_fa_template),
        "-omat",
        str(outputs["affine_mat"]),
        "-out",
        str(outputs["registered_fa"]),
        "-dof",
        "12",  # Affine (12 DOF)
    ]

    if not run_command(flirt_cmd, "Step 3: Linear Registration (FLIRT)"):
        return None

    # Verify affine matrix was created
    if not outputs["affine_mat"].exists():
        print(f"ERROR: Affine matrix not created: {outputs['affine_mat']}")
        return None

    # Step 4: Non-linear Registration (FNIRT)
    fnirt_cmd = [
        str(fsl_bin / "fnirt"),
        f"--in={subject_fa_for_reg}",
        f"--ref={jhu_fa_template}",
        f"--aff={outputs['affine_mat']}",
        f"--cout={outputs['warp_coef']}",
    ]

    if not run_command(fnirt_cmd, "Step 4: Non-linear Registration (FNIRT)"):
        return None

    # Verify warp coefficient was created
    if not outputs["warp_coef"].exists():
        print(f"ERROR: Warp coefficients not created: {outputs['warp_coef']}")
        return None

    # Step 5: Create Inverse Warp
    invwarp_cmd = [
        str(fsl_bin / "invwarp"),
        f"--ref={subject_fa}",
        f"--warp={outputs['warp_coef']}",
        f"--out={outputs['inverse_warp']}",
    ]

    if not run_command(invwarp_cmd, "Step 5: Create Inverse Warp (INVWARP)"):
        return None

    # Verify inverse warp was created
    if not outputs["inverse_warp"].exists():
        print(f"ERROR: Inverse warp not created: {outputs['inverse_warp']}")
        return None

    # Step 6: Transform ROI templates, find centroids, and create spherical ROIs
    import nibabel as nib
    import numpy as np

    project_root = Path(__file__).parent.parent
    roi_templates = {
        "left_proj": project_root / "templates" / "JHU-labels-left_proj.nii.gz",
        "left_assoc": project_root / "templates" / "JHU-labels-left_assoc.nii.gz",
        "right_proj": project_root / "templates" / "JHU-labels-right_proj.nii.gz",
        "right_assoc": project_root / "templates" / "JHU-labels-right_assoc.nii.gz",
    }

    # Load reference image for shape and voxel size
    ref_img = nib.load(str(subject_fa))
    ref_shape = ref_img.shape[:3]
    voxel_size = ref_img.header.get_zooms()[:3]

    print(f"\nCreating spherical ROIs with {sphere_radius}mm radius...")
    print(f"Reference shape: {ref_shape}, voxel size: {voxel_size}")

    roi_centroids = {}

    for roi_name, roi_template in roi_templates.items():
        if not roi_template.exists():
            print(f"  WARNING: ROI template not found: {roi_template}")
            continue

        # Transform template to get approximate location
        roi_transformed = output_dir / f"{prefix}_{roi_name}_transformed.nii.gz"

        applywarp_roi_cmd = [
            str(fsl_bin / "applywarp"),
            f"--ref={subject_fa}",
            f"--in={roi_template}",
            f"--warp={outputs['inverse_warp']}",
            f"--out={roi_transformed}",
            "--interp=nn",
        ]

        if not run_command(applywarp_roi_cmd, f"Step 6a: Transform {roi_name} to Native Space"):
            return None

        if not roi_transformed.exists():
            print(f"ERROR: {roi_name} transformed ROI not created")
            return None

        # Find centroid of transformed mask
        transformed_img = nib.load(str(roi_transformed))
        transformed_data = transformed_img.get_fdata()
        centroid = find_mask_centroid(transformed_data)

        if centroid is None:
            print(f"ERROR: No voxels found in transformed {roi_name}")
            return None

        roi_centroids[roi_name] = centroid
        print(f"  {roi_name}: centroid at {centroid}")

        # Create spherical ROI at centroid
        sphere_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
        n_voxels = int(np.sum(sphere_mask))
        print(f"    Created sphere with {n_voxels} voxels")

        # Save spherical ROI
        roi_sphere = output_dir / f"{prefix}_{roi_name}.nii.gz"
        sphere_img = nib.Nifti1Image(sphere_mask.astype(np.float32), ref_img.affine, ref_img.header)
        nib.save(sphere_img, str(roi_sphere))
        outputs[roi_name] = roi_sphere

    # Step 7 (Optional): Apply forward warp to V1 image
    if subject_v1 is not None:
        v1_to_jhu = output_dir / f"{prefix}_V1_to_JHU.nii.gz"
        outputs["v1_to_jhu"] = v1_to_jhu

        applywarp_v1_cmd = [
            str(fsl_bin / "applywarp"),
            f"--ref={jhu_fa_template}",
            f"--in={subject_v1}",
            f"--warp={outputs['warp_coef']}",
            f"--out={v1_to_jhu}",
        ]

        if not run_command(applywarp_v1_cmd, "Step 7: Transform V1 to JHU Space (APPLYWARP)"):
            return None

        if not v1_to_jhu.exists():
            print(f"ERROR: V1 in JHU space not created: {v1_to_jhu}")
            return None

    return outputs


def verify_registration_quality(
    subject_fa: Path,
    roi_outputs: dict[str, Path],
) -> None:
    """
    Print summary statistics about the registration result.
    """
    try:
        import nibabel as nib
        import numpy as np

        print("\n" + "=" * 60)
        print("Registration Quality Check")
        print("=" * 60)

        # Load FA image
        fa_img = nib.load(str(subject_fa))
        fa_data = fa_img.get_fdata()

        print(f"\nSubject FA shape: {fa_data.shape}")
        print(f"Voxel size: {fa_img.header.get_zooms()[:3]}")

        roi_names = ["left_proj", "left_assoc", "right_proj", "right_assoc"]

        for roi_name in roi_names:
            if roi_name not in roi_outputs:
                continue

            roi_path = roi_outputs[roi_name]
            if not roi_path.exists():
                print(f"\n{roi_name}: NOT FOUND")
                continue

            roi_img = nib.load(str(roi_path))
            roi_data = roi_img.get_fdata()
            mask = roi_data > 0
            voxel_count = np.sum(mask)

            # Get FA values within ROI
            fa_in_roi = fa_data[mask]
            mean_fa = np.mean(fa_in_roi) if len(fa_in_roi) > 0 else 0

            # Get bounding box
            coords = np.where(mask)
            if len(coords[0]) > 0:
                bbox = f"{coords[0].max() - coords[0].min() + 1}x{coords[1].max() - coords[1].min() + 1}x{coords[2].max() - coords[2].min() + 1}"
            else:
                bbox = "N/A"

            print(f"\n{roi_name}:")
            print(f"  Voxel count: {voxel_count}")
            print(f"  Bounding box: {bbox}")
            print(f"  Mean FA in ROI: {mean_fa:.4f}")

        print("\n✓ Registration and ROI creation completed")

    except ImportError:
        print("\nNote: nibabel not available, skipping quality check")
    except Exception as e:
        print(f"\nWarning: Could not perform quality check: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Test FSL-based registration for DTI-ALPS ROI region limiting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "subject_fa",
        type=str,
        help="Path to subject's FA image (e.g., subject_FA.nii.gz)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: same as input FA)",
    )
    parser.add_argument(
        "--sphere-radius",
        type=float,
        default=2.0,
        help="Radius of spherical ROIs in mm (default: 2.0)",
    )
    parser.add_argument(
        "--v1",
        type=str,
        default=None,
        help="Path to subject's V1 (principal eigenvector) image to transform to JHU space",
    )

    args = parser.parse_args()

    # Validate subject FA
    subject_fa = Path(args.subject_fa).resolve()
    if not subject_fa.exists():
        print(f"ERROR: Subject FA not found: {subject_fa}")
        sys.exit(1)

    # Set output directory
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = subject_fa.parent / "registration"

    # Check FSL availability
    fsl_ok, fsl_result = check_fsl_available()
    if not fsl_ok:
        print(f"ERROR: {fsl_result}")
        sys.exit(1)

    fsldir = Path(fsl_result)
    fsl_bin = fsldir / "bin"
    if not fsl_bin.exists():
        fsl_bin = fsldir / "share" / "fsl" / "bin"

    print(f"FSLDIR: {fsldir}")
    print(f"FSL bin: {fsl_bin}")

    # Locate JHU FA template
    jhu_fa_template = fsldir / "data" / "atlases" / "JHU" / "JHU-ICBM-FA-1mm.nii.gz"
    if not jhu_fa_template.exists():
        print(f"ERROR: JHU FA template not found: {jhu_fa_template}")
        sys.exit(1)

    print(f"JHU FA template: {jhu_fa_template}")

    # Validate V1 image if provided
    subject_v1 = None
    if args.v1:
        subject_v1 = Path(args.v1).resolve()
        if not subject_v1.exists():
            print(f"ERROR: Subject V1 not found: {subject_v1}")
            sys.exit(1)

    sphere_radius = args.sphere_radius

    print(f"Subject FA: {subject_fa}")
    print(f"Sphere radius: {sphere_radius} mm")
    if subject_v1:
        print(f"Subject V1: {subject_v1}")
    print(f"Output directory: {output_dir}")

    # Run registration
    print("\n" + "=" * 60)
    print("Starting Registration Pipeline")
    print("=" * 60)

    outputs = register_fa_to_template(
        subject_fa=subject_fa,
        output_dir=output_dir,
        jhu_fa_template=jhu_fa_template,
        fsl_bin=fsl_bin,
        sphere_radius=sphere_radius,
        subject_v1=subject_v1,
    )

    if outputs is None:
        print("\n" + "=" * 60)
        print("REGISTRATION FAILED")
        print("=" * 60)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("REGISTRATION COMPLETED SUCCESSFULLY")
    print("=" * 60)
    print("\nOutput files:")
    for name, path in outputs.items():
        status = "✓" if path.exists() else "✗"
        print(f"  {status} {name}: {path}")

    # Verify registration quality
    verify_registration_quality(
        subject_fa=subject_fa,
        roi_outputs=outputs,
    )

    print("\n" + "=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("1. Visually inspect the spherical ROIs overlaid on the FA image")
    print("   using FSLeyes or similar viewer:")
    if "left_proj" in outputs:
        print(
            f"   fsleyes {subject_fa} {outputs['left_proj']} -cm red {outputs['left_assoc']} -cm blue"
        )
    print("\n2. Adjust --sphere-radius if needed (default: 2.0mm)")


if __name__ == "__main__":
    main()
