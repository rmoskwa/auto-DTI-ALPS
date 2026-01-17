#!/usr/bin/env python3
"""
Test script to run DTI-ALPS pipeline components individually.

Usage:
    python -m pytest tests/test_pipeline.py
    python tests/test_pipeline.py --step preproc   # Test preprocessing only
    python tests/test_pipeline.py --step dti       # Test DTI fitting only
    python tests/test_pipeline.py --step alps      # Test ALPS calculation only
    python tests/test_pipeline.py --step all       # Run full pipeline

Note: ROI placement is now done via template-based registration in the full pipeline.
      Use the GUI for complete DTI-ALPS processing with automatic ROI placement.
"""

import argparse
import os
import subprocess
import time

# Test case parameters
TEST_PARAMS = {
    "dwi": "testCase/Original.nii.gz",
    "bvecs": "testCase/bvecs.bvec",
    "bvals": "testCase/bvals.bval",
    "reverse_pe": "testCase/b0_all.nii.gz",
    "pe_dir": "PA",
    "readout_time": "0.089",
    "output_dir": "testCase/output",
    "prefix": "test",
}


def run_command(cmd, description):
    """Run a command and stream output."""
    print(f"\n{'=' * 60}")
    print(f"STEP: {description}")
    print(f"{'=' * 60}")
    print(f"Command: {' '.join(cmd)}\n")

    start_time = time.time()

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        print(line, end="")

    process.wait()
    elapsed = time.time() - start_time

    if process.returncode == 0:
        print(f"\n[SUCCESS] {description} completed in {elapsed:.1f}s")
        return True
    else:
        print(f"\n[FAILED] {description} failed with code {process.returncode}")
        return False


def test_preprocessing():
    """Test dwifslpreproc."""
    os.makedirs(TEST_PARAMS["output_dir"], exist_ok=True)

    output_dwi = os.path.join(
        TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_dwi_preproc.nii.gz"
    )
    bvecs_out = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_bvecs_preproc")
    bvals_out = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_bvals_preproc")

    cmd = [
        "dwifslpreproc",
        TEST_PARAMS["dwi"],
        output_dwi,
        "-fslgrad",
        TEST_PARAMS["bvecs"],
        TEST_PARAMS["bvals"],
        "-export_grad_fsl",
        bvecs_out,
        bvals_out,
        "-pe_dir",
        TEST_PARAMS["pe_dir"],
        "-readout_time",
        TEST_PARAMS["readout_time"],
        "-rpe_pair",
        "-se_epi",
        TEST_PARAMS["reverse_pe"],
        "-align_seepi",
        "-info",  # More verbose output
    ]

    return run_command(cmd, "Preprocessing (dwifslpreproc)")


def test_dti_fitting():
    """Test dwi2tensor and tensor2metric."""
    input_dwi = os.path.join(
        TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_dwi_preproc.nii.gz"
    )
    bvecs_in = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_bvecs_preproc")
    bvals_in = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_bvals_preproc")
    tensor_out = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_tensor.nii.gz")
    fa_out = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_FA.nii.gz")
    v1_out = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_V1.nii.gz")

    if not os.path.exists(input_dwi):
        print(f"ERROR: Preprocessed DWI not found: {input_dwi}")
        print("Run --step preproc first")
        return False

    # Step 1: dwi2tensor
    cmd1 = ["dwi2tensor", input_dwi, tensor_out, "-fslgrad", bvecs_in, bvals_in, "-info"]

    if not run_command(cmd1, "Tensor fitting (dwi2tensor)"):
        return False

    # Step 2: tensor2metric
    cmd2 = [
        "tensor2metric",
        tensor_out,
        "-fa",
        fa_out,
        "-vector",
        v1_out,
        "-num",
        "1",
        "-modulate",
        "none",
        "-info",
    ]

    return run_command(cmd2, "Metric extraction (tensor2metric)")


def test_alps_calculation():
    """Test ALPS index calculation from tensor and existing ROI masks."""
    tensor_path = os.path.join(TEST_PARAMS["output_dir"], f"{TEST_PARAMS['prefix']}_tensor.nii.gz")
    roi_dir = os.path.join(TEST_PARAMS["output_dir"], "rois")

    print(f"\n{'=' * 60}")
    print("STEP: ALPS Index Calculation")
    print(f"{'=' * 60}")

    try:
        import nibabel as nib
        import numpy as np

        # Check for existing ROI masks (from template-based registration)
        roi_files = {
            "left_proj": os.path.join(roi_dir, f"{TEST_PARAMS['prefix']}_left_proj.nii.gz"),
            "left_assoc": os.path.join(roi_dir, f"{TEST_PARAMS['prefix']}_left_assoc.nii.gz"),
            "right_proj": os.path.join(roi_dir, f"{TEST_PARAMS['prefix']}_right_proj.nii.gz"),
            "right_assoc": os.path.join(roi_dir, f"{TEST_PARAMS['prefix']}_right_assoc.nii.gz"),
        }

        # Verify all ROI files exist
        missing = []
        for name, path in roi_files.items():
            if not os.path.exists(path):
                missing.append(name)

        if missing:
            print("ERROR: ROI masks not found.")
            print("Missing masks:", ", ".join(missing))
            print()
            print("ROI masks are created by the template-based registration step.")
            print("Use the GUI to run the full pipeline which includes:")
            print("  - FA to JHU template registration")
            print("  - ROI mask transformation to native space")
            print()
            print("  Launch GUI: dti-alps")
            return False

        # Load ROI masks from disk
        print("Loading ROI masks from disk...")
        masks = {}
        for name, path in roi_files.items():
            mask_img = nib.load(path)
            masks[name] = mask_img.get_fdata()
            voxel_count = np.sum(masks[name] > 0)
            print(f"  {name}: {voxel_count} voxels")

        # Load tensor
        print(f"\nLoading tensor: {tensor_path}")
        tensor_img = nib.load(tensor_path)
        tensor_data = tensor_img.get_fdata()
        print(f"Tensor shape: {tensor_data.shape}")

        # Extract directional diffusivities
        # MRtrix dwi2tensor format: D11, D22, D33, D12, D13, D23
        dxx = tensor_data[:, :, :, 0]  # D11
        dyy = tensor_data[:, :, :, 1]  # D22
        dzz = tensor_data[:, :, :, 2]  # D33

        print("\nCalculating ALPS index...")

        results = {}
        for side in ["left", "right"]:
            proj_mask = masks[f"{side}_proj"]
            assoc_mask = masks[f"{side}_assoc"]

            proj_idx = np.where(proj_mask > 0)
            assoc_idx = np.where(assoc_mask > 0)

            dxx_proj = np.mean(dxx[proj_idx])
            dyy_proj = np.mean(dyy[proj_idx])
            dxx_assoc = np.mean(dxx[assoc_idx])
            dzz_assoc = np.mean(dzz[assoc_idx])

            numerator = (dxx_proj + dxx_assoc) / 2
            denominator = (dyy_proj + dzz_assoc) / 2
            alps_index = numerator / denominator

            results[side] = alps_index

            print(f"\n{side.upper()} Hemisphere:")
            print(f"  Dxx_proj:  {dxx_proj:.6f}")
            print(f"  Dxx_assoc: {dxx_assoc:.6f}")
            print(f"  Dyy_proj:  {dyy_proj:.6f}")
            print(f"  Dzz_assoc: {dzz_assoc:.6f}")
            print(f"  ALPS Index: {alps_index:.4f}")

        # Bilateral average
        bilateral = (results["left"] + results["right"]) / 2
        print(f"\nBILATERAL ALPS Index: {bilateral:.4f}")

        print("\n[SUCCESS] ALPS calculation completed")
        return True

    except Exception as e:
        print(f"\n[FAILED] ALPS calculation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Test DTI-ALPS pipeline components")
    parser.add_argument(
        "--step",
        choices=["preproc", "dti", "alps", "all"],
        default="all",
        help="Which step to test",
    )
    args = parser.parse_args()

    print("DTI-ALPS Pipeline Test")
    print(f"Test data: {TEST_PARAMS['dwi']}")
    print(f"Output: {TEST_PARAMS['output_dir']}")
    print()
    print("Note: ROI placement requires the full pipeline with template registration.")
    print("      Use the GUI for complete processing: dti-alps")

    if args.step == "preproc":
        test_preprocessing()
    elif args.step == "dti":
        test_dti_fitting()
    elif args.step == "alps":
        test_alps_calculation()
    elif args.step == "all":
        if not test_preprocessing():
            return
        if not test_dti_fitting():
            return
        print()
        print("Note: Skipping ROI step - requires template registration via GUI.")
        print("      Run 'dti-alps' to launch the GUI for full pipeline.")


if __name__ == "__main__":
    main()
