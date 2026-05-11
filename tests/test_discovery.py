"""
Unit tests for SubjectDiscovery file discovery logic.

Focuses on subject_id derivation:
- Single DWI in folder → subject_id = folder basename
- Multiple DWIs in folder → subject_id = DWI filename stem
- Batch scenario with duplicate filenames → unique subject_ids
"""

import os

from dti_alps.processing.discovery import SubjectDiscovery


def _create_empty_file(path: str) -> None:
    """Create an empty file, making parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _make_dwi_set(folder: str, stem: str) -> None:
    """Create a matching DWI + bvec + bval file set."""
    _create_empty_file(os.path.join(folder, f"{stem}.nii.gz"))
    _create_empty_file(os.path.join(folder, f"{stem}.bvec"))
    _create_empty_file(os.path.join(folder, f"{stem}.bval"))


class TestSubjectIdDerivation:
    """Tests for subject_id assignment in discover_files()."""

    def test_single_dwi_uses_folder_name(self, tmp_path):
        """Single DWI run in folder → subject_id = folder basename."""
        subject_dir = tmp_path / "10_1003"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64_b1300_Gmax")

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert len(results) == 1
        assert results[0].subject_id == "10_1003"

    def test_multiple_dwis_use_dwi_stems(self, tmp_path):
        """Multiple DWI runs in folder → subject_id = DWI filename stem."""
        subject_dir = tmp_path / "sub-01"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64_b1300")
        _make_dwi_set(str(subject_dir), "DTI64_b2000")

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert len(results) == 2
        subject_ids = {r.subject_id for r in results}
        assert subject_ids == {"DTI64_b1300", "DTI64_b2000"}

    def test_batch_duplicate_filenames_get_unique_ids(self, tmp_path):
        """Different folders with same DWI filename → unique subject_ids."""
        folders = ["10_1003", "10_1005", "10_1007"]
        all_results = []

        for folder_name in folders:
            subject_dir = tmp_path / folder_name
            subject_dir.mkdir()
            _make_dwi_set(str(subject_dir), "DTI64_b1300_Gmax")

            discovery = SubjectDiscovery(str(subject_dir))
            results = discovery.discover_files()
            all_results.extend(results)

        subject_ids = [r.subject_id for r in all_results]
        assert len(subject_ids) == 3
        # All unique — no collisions
        assert len(set(subject_ids)) == 3
        assert set(subject_ids) == {"10_1003", "10_1005", "10_1007"}

    def test_empty_folder_returns_empty(self, tmp_path):
        """Folder with no DWI files → empty list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        discovery = SubjectDiscovery(str(empty_dir))
        results = discovery.discover_files()

        assert results == []

    def test_dwi_without_gradients_excluded(self, tmp_path):
        """DWI without matching bvec/bval → not included."""
        subject_dir = tmp_path / "sub-02"
        subject_dir.mkdir()
        _create_empty_file(os.path.join(str(subject_dir), "DTI64.nii.gz"))
        # No bvec/bval

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert results == []

    def test_single_dwi_nii_extension(self, tmp_path):
        """Single DWI with .nii (not .nii.gz) → folder name as subject_id."""
        subject_dir = tmp_path / "sub-03"
        subject_dir.mkdir()
        _create_empty_file(os.path.join(str(subject_dir), "dwi.nii"))
        _create_empty_file(os.path.join(str(subject_dir), "dwi.bvec"))
        _create_empty_file(os.path.join(str(subject_dir), "dwi.bval"))

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert len(results) == 1
        assert results[0].subject_id == "sub-03"

    def test_flywheel_style_json_sidecar_is_matched(self, tmp_path):
        """JSON sidecar with embedded .nii.gz (Flywheel convention) → matched to DWI."""
        subject_dir = tmp_path / "10_1042"
        subject_dir.mkdir()
        stem = "501_DTI64_b1300_Gmax"
        _make_dwi_set(str(subject_dir), stem)
        json_path = os.path.join(str(subject_dir), f"{stem}.nii.gz.flywheel.json")
        _create_empty_file(json_path)

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert len(results) == 1
        assert results[0].json_sidecar_path == json_path

    def test_single_dwi_preserves_file_paths(self, tmp_path):
        """Changing subject_id doesn't affect discovered file paths."""
        subject_dir = tmp_path / "10_1003"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64_b1300")

        discovery = SubjectDiscovery(str(subject_dir))
        results = discovery.discover_files()

        assert results[0].subject_id == "10_1003"
        assert results[0].dwi_path.endswith("DTI64_b1300.nii.gz")
        assert results[0].bvec_path.endswith("DTI64_b1300.bvec")
        assert results[0].bval_path.endswith("DTI64_b1300.bval")
        assert results[0].is_valid
