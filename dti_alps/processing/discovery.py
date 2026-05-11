"""
File discovery and JSON sidecar parsing for batch processing.

This module provides functionality to automatically discover DWI files and
associated gradient/metadata files within subject folders, as well as
extract acquisition parameters from BIDS-format JSON sidecars.
"""

import json
import os
from dataclasses import dataclass, field
from glob import glob


@dataclass
class SubjectFiles:
    """
    Discovered files for a single subject.

    Attributes
    ----------
    folder_path : str
        Path to the subject's data folder
    subject_id : str
        Subject identifier. For single-run folders, this is the folder basename
        (e.g., "10_1003"). For multi-run folders, this is the DWI filename stem
        (e.g., "DTI64_b1300") to differentiate runs within the same folder.
    dwi_path : str or None
        Path to 4D diffusion-weighted image
    bvec_path : str or None
        Path to gradient directions file
    bval_path : str or None
        Path to b-values file
    json_sidecar_path : str or None
        Path to BIDS JSON sidecar (optional)
    reverse_pe_path : str or None
        Path to reverse phase encoding image (optional)
    """

    folder_path: str
    subject_id: str
    dwi_path: str | None = None
    bvec_path: str | None = None
    bval_path: str | None = None
    json_sidecar_path: str | None = None
    reverse_pe_path: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if all required files are present."""
        return bool(self.dwi_path and self.bvec_path and self.bval_path)

    def get_files_summary(self) -> str:
        """Get a human-readable summary of discovered files."""
        parts = []
        if self.dwi_path:
            parts.append("DWI")
        if self.bvec_path:
            parts.append("bvec")
        if self.bval_path:
            parts.append("bval")
        if self.json_sidecar_path:
            parts.append("JSON")
        if self.reverse_pe_path:
            parts.append("RPE")

        if not parts:
            return "No files found"
        return " + ".join(parts)

    def get_missing_files(self) -> list[str]:
        """Get list of missing required files."""
        missing = []
        if not self.dwi_path:
            missing.append("DWI image")
        if not self.bvec_path:
            missing.append("bvec file")
        if not self.bval_path:
            missing.append("bval file")
        return missing


class SubjectDiscovery:
    """
    Discover and validate files within a subject folder.

    This class handles automatic file discovery using common naming patterns
    for neuroimaging data (BIDS and non-BIDS formats). Each DWI file with
    matching bvec/bval files is returned as a separate SubjectFiles entry.
    """

    # Supported file extensions
    DWI_EXTENSIONS = ["*.nii.gz", "*.nii"]
    BVEC_EXTENSIONS = ["*.bvec", "*.bvecs"]
    BVAL_EXTENSIONS = ["*.bval", "*.bvals"]
    JSON_EXTENSIONS = ["*.json"]

    def __init__(self, folder_path: str):
        """
        Initialize discovery for a folder.

        Parameters
        ----------
        folder_path : str
            Path to the subject's data folder
        """
        self.folder_path = os.path.abspath(folder_path)

    def discover_files(self) -> list[SubjectFiles]:
        """
        Auto-discover all DWI runs in the folder.

        Each DWI file with matching bvec/bval files becomes a separate entry.
        When a single DWI run is found, subject_id is set to the folder basename
        (e.g., "10_1003") since the folder identifies the subject. When multiple
        runs are found, subject_id uses the DWI filename stem to differentiate them.

        Returns
        -------
        list[SubjectFiles]
            List of discovered file sets, one per DWI run with matching gradients.
            Returns empty list if no valid DWI runs are found.
        """
        # Find all files of each type
        dwi_files = self._find_files(self.DWI_EXTENSIONS)
        bvec_files = self._find_files(self.BVEC_EXTENSIONS)
        bval_files = self._find_files(self.BVAL_EXTENSIONS)
        json_files = self._find_files(self.JSON_EXTENSIONS)

        if not dwi_files:
            return []

        # Build lookup dictionaries by stem for fast matching
        bvec_by_stem = {self._get_stem(f): f for f in bvec_files}
        bval_by_stem = {self._get_stem(f): f for f in bval_files}
        json_by_stem = {self._get_stem(f): f for f in json_files}

        # Find all DWI files with matching bvec/bval
        results: list[SubjectFiles] = []
        matched_dwi_files: list[str] = []

        for dwi_path in dwi_files:
            dwi_stem = self._get_stem(dwi_path)

            bvec_match = bvec_by_stem.get(dwi_stem)
            bval_match = bval_by_stem.get(dwi_stem)
            json_match = json_by_stem.get(dwi_stem)

            # Only include if we have matching bvec and bval
            if bvec_match and bval_match:
                subject = SubjectFiles(
                    folder_path=self.folder_path,
                    subject_id=dwi_stem,
                    dwi_path=dwi_path,
                    bvec_path=bvec_match,
                    bval_path=bval_match,
                    json_sidecar_path=json_match,
                )
                results.append(subject)
                matched_dwi_files.append(dwi_path)

        # Single run: use folder name as subject_id (the folder IS the subject)
        # This prevents collisions when different subject folders contain
        # identically-named DWI files (e.g., 10_1003/DTI64.nii.gz vs 10_1005/DTI64.nii.gz)
        if len(results) == 1:
            results[0].subject_id = os.path.basename(self.folder_path)

        # Look for reverse PE images for each result
        for subject in results:
            subject.reverse_pe_path = self._find_reverse_pe(matched_dwi_files, subject.dwi_path)

        return results

    def _find_files(self, extensions: list[str]) -> list[str]:
        """Find all files matching given extensions in the folder."""
        files = []
        for ext in extensions:
            pattern = os.path.join(self.folder_path, ext)
            files.extend(glob(pattern))
        return sorted(files)

    def _get_stem(self, filepath: str) -> str:
        """
        Get the filename stem (without extensions).

        Handles double extensions like .nii.gz, and sidecar files that embed
        the original .nii / .nii.gz inside their basename (e.g. Flywheel's
        `<dwi>.nii.gz.flywheel.json`), so the stem matches the corresponding
        DWI file.
        """
        filename = os.path.basename(filepath)
        if filename.endswith(".nii.gz"):
            return filename[:-7]
        base = os.path.splitext(filename)[0]
        for nii_ext in (".nii.gz", ".nii"):
            idx = base.find(nii_ext)
            if idx >= 0:
                return base[:idx]
        return base

    def _find_reverse_pe(self, all_dwi_files: list[str], current_dwi: str | None) -> str | None:
        """
        Look for reverse phase encoding images.

        Common patterns: *_AP.nii.gz/*_PA.nii.gz, *b0*.nii.gz, *RPE*.nii.gz

        Parameters
        ----------
        all_dwi_files : list[str]
            All matched DWI files (to exclude from RPE search)
        current_dwi : str or None
            The current subject's DWI file (to exclude)
        """
        folder = self.folder_path

        # Look for common reverse PE patterns
        rpe_patterns = [
            "*_PA.*",
            "*_AP.*",
            "*_RL.*",
            "*_LR.*",
            "*b0_pair*",
            "*b0_all*",
            "*RPE*",
            "*rpe*",
            "*reverse*",
        ]

        for pattern in rpe_patterns:
            for ext in self.DWI_EXTENSIONS:
                # Search with glob pattern
                search_pattern = os.path.join(folder, pattern[:-1] + ext[1:])
                matches = glob(search_pattern)
                # Filter to only include files not already selected as main DWI
                for match in matches:
                    if match not in all_dwi_files and match != current_dwi:
                        return match

        return None


def parse_json_sidecar(json_path: str) -> dict:
    """
    Parse BIDS JSON sidecar and return its contents.

    Parameters
    ----------
    json_path : str
        Path to JSON sidecar file

    Returns
    -------
    dict
        Parsed JSON contents, or empty dict if parsing fails
    """
    try:
        with open(json_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def extract_readout_time(json_data: dict, nifti_path: str | None = None) -> float | None:
    """
    Extract total readout time from JSON sidecar data.

    Supports multiple BIDS fields:
    - TotalReadoutTime: Direct value in seconds
    - EffectiveEchoSpacing + ReconMatrixPE: Calculated as EES * (ReconMatrixPE - 1)
    - EffectiveEchoSpacing + PhaseEncodingSteps: Calculated as EES * (PES - 1)
    - EffectiveEchoSpacing + NIfTI dimensions: Uses image matrix size along PE axis

    Parameters
    ----------
    json_data : dict
        Parsed JSON sidecar contents
    nifti_path : str, optional
        Path to corresponding NIfTI file (used to get matrix dimensions if needed)

    Returns
    -------
    float or None
        Total readout time in seconds, or None if not determinable
    """
    # Try direct TotalReadoutTime field
    if "TotalReadoutTime" in json_data:
        try:
            return float(json_data["TotalReadoutTime"])
        except (TypeError, ValueError):
            pass

    # Try calculating from EffectiveEchoSpacing
    ees = json_data.get("EffectiveEchoSpacing")
    if ees is not None:
        try:
            ees = float(ees)

            # Try ReconMatrixPE first
            recon_pe = json_data.get("ReconMatrixPE")
            if recon_pe is not None:
                return ees * (int(recon_pe) - 1)

            # Try PhaseEncodingSteps
            pe_steps = json_data.get("PhaseEncodingSteps")
            if pe_steps is not None:
                return ees * (int(pe_steps) - 1)

            # Try AcquisitionMatrixPE
            acq_pe = json_data.get("AcquisitionMatrixPE")
            if acq_pe is not None:
                return ees * (int(acq_pe) - 1)

            # Fall back to calculating from NIfTI dimensions
            if nifti_path is not None:
                pe_matrix_size = _get_pe_matrix_size_from_nifti(json_data, nifti_path)
                if pe_matrix_size is not None:
                    return ees * (pe_matrix_size - 1)

        except (TypeError, ValueError):
            pass

    return None


def _get_pe_matrix_size_from_nifti(json_data: dict, nifti_path: str) -> int | None:
    """
    Get the matrix size along the phase encoding direction from NIfTI file.

    Parameters
    ----------
    json_data : dict
        JSON sidecar data (to determine PE direction axis)
    nifti_path : str
        Path to NIfTI file

    Returns
    -------
    int or None
        Matrix size along PE axis, or None if cannot be determined
    """
    try:
        import nibabel as nib

        pe_dir = json_data.get("PhaseEncodingDirection", "")

        # Determine which axis is the PE axis
        # i/i- = axis 0, j/j- = axis 1, k/k- = axis 2
        if pe_dir.startswith("i"):
            pe_axis = 0
        elif pe_dir.startswith("j"):
            pe_axis = 1
        elif pe_dir.startswith("k"):
            pe_axis = 2
        else:
            # Default to axis 1 (j) which is most common for axial acquisitions
            pe_axis = 1

        img = nib.load(nifti_path)
        shape = img.shape
        if len(shape) >= 3:
            return shape[pe_axis]

    except Exception:
        pass

    return None


def extract_phase_encoding_direction(json_data: dict) -> str | None:
    """
    Extract phase encoding direction from JSON sidecar data.

    Parameters
    ----------
    json_data : dict
        Parsed JSON sidecar contents

    Returns
    -------
    str or None
        Phase encoding direction (AP, PA, LR, RL, SI, IS), or None if not found
    """
    # BIDS PhaseEncodingDirection field uses i, j, k notation
    # i = left-right, j = anterior-posterior, k = superior-inferior
    # Positive direction is i, j, k; negative is i-, j-, k-
    pe_dir = json_data.get("PhaseEncodingDirection")

    if pe_dir is None:
        return None

    # Map BIDS notation to common notation
    mapping = {
        "i": "LR",
        "i-": "RL",
        "j": "PA",
        "j-": "AP",
        "k": "SI",
        "k-": "IS",
    }

    return mapping.get(pe_dir)
