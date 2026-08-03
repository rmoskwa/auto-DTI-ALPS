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
from pathlib import Path

# The default subject-id depth: one path component, which is the folder basename.
# Reproduces the historical naming byte for byte (see :func:`subject_id_from_path`).
DEFAULT_ID_DEPTH = 1


class SubjectIdCollisionError(Exception):
    """
    Two or more discovered runs resolve to the same subject id.

    A hard error rather than a warning, because the id is the output directory
    name (``output_dir/<subject_id>``) *and* the key of every results-CSV row.
    Two runs sharing an id would write into the same folder and collapse to a
    single CSV row -- one subject's results silently overwritten by another's.

    Raised by :func:`check_unique_subject_ids`, which both front ends reach
    through ``BatchRunner.run_batch``.
    """


def subject_id_from_path(folder_path: str, id_depth: int = DEFAULT_ID_DEPTH) -> str:
    """
    Derive a subject id from the last ``id_depth`` components of ``folder_path``.

    ``id_depth=1`` (the default) is the folder basename -- the historical rule,
    reproduced byte for byte. Deeper values join the trailing components with
    ``_``, which is what makes a BIDS layout addressable: every
    ``sub-XX/ses-1/dwi`` folder is named ``dwi``, so at depth 1 an entire cohort
    would collapse onto one id.

    >>> subject_id_from_path("/bids/sub-01/ses-1/dwi")
    'dwi'
    >>> subject_id_from_path("/bids/sub-01/ses-1/dwi", 3)
    'sub-01_ses-1_dwi'
    >>> subject_id_from_path("/data/10_1003", 5)  # deeper than the path is long
    'data_10_1003'
    """
    parts = [p for p in Path(folder_path).parts if p not in ("/", "\\")]
    if not parts:
        return ""
    return "_".join(parts[-max(1, id_depth) :])


def check_unique_subject_ids(subjects: list["SubjectFiles"]) -> None:
    """
    Raise :class:`SubjectIdCollisionError` if any subject id appears twice.

    The id is both the output subdirectory name and the results-CSV row key, so
    a duplicate is unrecoverable data loss rather than cosmetic confusion. This
    is deliberately *not* auto-repaired by widening the id depth: doing so would
    make output folder names a function of cohort composition, so adding one
    subject could rename the output of subjects already processed -- breaking
    re-runs and breaking ``reanalyze`` against an existing output directory.

    The message names the colliding id and the DWI files behind it, and suggests
    the remedy (a deeper ``--id-depth``), because the common cause is a BIDS
    glob where every leaf folder is named ``dwi``.
    """
    by_id: dict[str, list[SubjectFiles]] = {}
    for subject in subjects:
        by_id.setdefault(subject.subject_id, []).append(subject)

    collisions = {sid: runs for sid, runs in by_id.items() if len(runs) > 1}
    if not collisions:
        return

    lines = [
        f"{len(collisions)} subject id(s) are used by more than one run. "
        "Each id names an output directory and a results-CSV row, so processing "
        "would overwrite one subject's results with another's."
    ]
    for sid, runs in sorted(collisions.items()):
        lines.append(f"  '{sid}':")
        for run in runs:
            lines.append(f"    {run.dwi_path}")
    lines.append(
        "Increase --id-depth so more of the path contributes to the id "
        "(e.g. --id-depth 3 turns sub-01/ses-1/dwi into sub-01_ses-1_dwi)."
    )
    raise SubjectIdCollisionError("\n".join(lines))


@dataclass
class SubjectFiles:
    """
    Discovered files for a single subject.

    Attributes
    ----------
    folder_path : str
        Path to the subject's data folder
    subject_id : str
        Subject identifier, and the name of this subject's output directory. At
        the default id depth: the folder basename for single-run folders (e.g.
        "10_1003"), the DWI filename stem for multi-run folders (e.g.
        "DTI64_b1300") to differentiate runs within the same folder. Deeper ids
        prepend more of the path -- see :meth:`SubjectDiscovery.discover_files`.
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

    def __init__(self, folder_path: str, id_depth: int = DEFAULT_ID_DEPTH):
        """
        Initialize discovery for a folder.

        Parameters
        ----------
        folder_path : str
            Path to the subject's data folder
        id_depth : int
            How many trailing path components contribute to the subject id.
            ``1`` (the default) reproduces the historical naming exactly; see
            :meth:`discover_files` for how it composes with the multi-run rule.
        """
        self.folder_path = os.path.abspath(folder_path)
        self.id_depth = max(1, id_depth)

    def discover_files(self) -> list[SubjectFiles]:
        """
        Auto-discover all DWI runs in the folder.

        Each DWI file with matching bvec/bval files becomes a separate entry.

        Subject ids are built from ``id_depth`` components of identity, where the
        *deepest* component is the folder name when the folder holds one run and
        the DWI filename stem when it holds several -- because with several runs
        the folder no longer identifies the subject, the stem does:

        For ``/data/raw/10_1003``:

        ===== ===================== ================================
        depth 1 run                 2 runs
        ===== ===================== ================================
          1   ``10_1003``           ``DTI64_b1300``
          2   ``raw_10_1003``       ``10_1003_DTI64_b1300``
          3   ``data_raw_10_1003``  ``raw_10_1003_DTI64_b1300``
        ===== ===================== ================================

        At the default depth of 1 this is the historical naming byte for byte.
        Deeper values are what make a BIDS tree addressable: every leaf there is
        named ``dwi``, so ``--id-depth 3`` yields ``sub-01_ses-1_dwi``.

        Ids are *not* auto-disambiguated on collision -- see
        :func:`check_unique_subject_ids` for why widening silently would be worse
        than failing.

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

        if len(results) == 1:
            # Single run: the folder IS the subject, so the id is the trailing
            # path components. This prevents collisions when different subject
            # folders contain identically-named DWI files (e.g.
            # 10_1003/DTI64.nii.gz vs 10_1005/DTI64.nii.gz).
            results[0].subject_id = subject_id_from_path(self.folder_path, self.id_depth)
        else:
            # Multiple runs: the stem is the deepest identity component (it is
            # what distinguishes the runs), so any extra depth comes from the
            # folder's *parents*. At depth 1 that prefix is empty and the id is
            # the bare stem -- today's naming.
            prefix = self._parent_prefix()
            for subject in results:
                subject.subject_id = (
                    f"{prefix}_{subject.subject_id}" if prefix else subject.subject_id
                )

        # Look for reverse PE images for each result
        for subject in results:
            subject.reverse_pe_path = self._find_reverse_pe(matched_dwi_files, subject.dwi_path)

        return results

    def _parent_prefix(self) -> str:
        """
        The trailing ``id_depth - 1`` components of this folder's path.

        Empty at depth 1. Used only on the multi-run path, where the DWI stem
        already supplies the deepest identity component, so the folder name is
        the *second* component rather than the first.
        """
        if self.id_depth <= 1:
            return ""
        return subject_id_from_path(self.folder_path, self.id_depth - 1)

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


def discover_with_subdir_fallback(
    folder: str, id_depth: int = DEFAULT_ID_DEPTH
) -> list[SubjectFiles]:
    """
    Discover DWI runs in a folder, falling back to immediate subdirectories.

    Runs :meth:`SubjectDiscovery.discover_files` on ``folder``. If that finds
    nothing, each immediate subdirectory is scanned in sorted order and the
    results are concatenated. This lets a user select either a single subject
    folder or a parent folder containing several subject folders.

    The fallback is deliberately one level deep only. Deeper trees are reached
    by shell globbing on the CLI (``--subjects /bids/sub-*/ses-1/dwi``) rather
    than by a recursive walk here: shell expansion is more expressive than any
    ``--depth`` flag, and a recursive walk would be CLI-only behaviour, so GUI
    and CLI discovery would diverge.

    Parameters
    ----------
    folder : str
        Folder to scan (a subject folder or a parent of subject folders).
    id_depth : int
        Forwarded to :class:`SubjectDiscovery`; how many trailing path
        components contribute to each subject id.

    Returns
    -------
    list[SubjectFiles]
        Discovered runs. The subdirectory fallback fires only when the
        top-level scan is empty; an empty list means nothing was found at
        either level.
    """
    discovered = SubjectDiscovery(folder, id_depth).discover_files()
    if discovered:
        return discovered

    runs: list[SubjectFiles] = []
    subdirs = sorted(p for p in Path(folder).iterdir() if p.is_dir())
    for subdir in subdirs:
        runs.extend(SubjectDiscovery(str(subdir), id_depth).discover_files())
    return runs


def new_unique_runs(
    existing: list[SubjectFiles], discovered: list[SubjectFiles]
) -> list[SubjectFiles]:
    """
    Filter discovered runs down to those not already present, deduped by DWI path.

    A discovered run is dropped when its ``dwi_path`` matches that of an entry in
    ``existing`` or of an earlier run in ``discovered`` (so duplicates within the
    same scan are also collapsed). Order of the surviving runs is preserved.

    Parameters
    ----------
    existing : list[SubjectFiles]
        Runs already in the session list.
    discovered : list[SubjectFiles]
        Newly discovered runs to filter.

    Returns
    -------
    list[SubjectFiles]
        The subset of ``discovered`` whose DWI paths are new.
    """
    seen = {s.dwi_path for s in existing}
    unique: list[SubjectFiles] = []
    for run in discovered:
        if run.dwi_path in seen:
            continue
        seen.add(run.dwi_path)
        unique.append(run)
    return unique


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
