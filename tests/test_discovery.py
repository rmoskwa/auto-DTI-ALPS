"""
Unit tests for SubjectDiscovery file discovery logic.

Focuses on subject_id derivation:
- Single DWI in folder → subject_id = folder basename
- Multiple DWIs in folder → subject_id = DWI filename stem
- Batch scenario with duplicate filenames → unique subject_ids
"""

import os

import pytest

from dti_alps.processing.batch import BatchRunner
from dti_alps.processing.discovery import (
    SubjectDiscovery,
    SubjectIdCollisionError,
    assign_subject_ids,
    check_unique_subject_ids,
    discover_with_subdir_fallback,
)
from dti_alps.processing.state import BatchConfig, BatchState


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


class TestIdDepth:
    """
    ``id_depth`` widens the subject id to more of the path.

    The motivating case is a BIDS tree: every leaf is named ``dwi``, so at the
    default depth an entire cohort collapses onto one id.
    """

    def test_default_depth_is_the_folder_basename(self, tmp_path):
        """Depth 1 reproduces the historical naming byte for byte."""
        subject_dir = tmp_path / "sub-01" / "ses-1" / "dwi"
        _make_dwi_set(str(subject_dir), "dwi")

        results = SubjectDiscovery(str(subject_dir)).discover_files()

        assert results[0].subject_id == "dwi"

    def test_depth_three_joins_the_trailing_components(self, tmp_path):
        subject_dir = tmp_path / "sub-01" / "ses-1" / "dwi"
        _make_dwi_set(str(subject_dir), "dwi")

        results = SubjectDiscovery(str(subject_dir), id_depth=3).discover_files()

        assert results[0].subject_id == "sub-01_ses-1_dwi"

    def test_multi_run_depth_one_is_the_bare_stem(self, tmp_path):
        """Today's multi-run naming: the stem alone, no folder prefix."""
        subject_dir = tmp_path / "10_1003"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64_b1300")
        _make_dwi_set(str(subject_dir), "DTI64_b2600")

        results = SubjectDiscovery(str(subject_dir)).discover_files()

        assert sorted(r.subject_id for r in results) == ["DTI64_b1300", "DTI64_b2600"]

    def test_multi_run_extra_depth_comes_from_the_parents(self, tmp_path):
        """
        With several runs the stem is already the deepest identity component,
        so depth 2 adds the folder above it -- not the folder *and* the stem.
        """
        subject_dir = tmp_path / "10_1003"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64_b1300")
        _make_dwi_set(str(subject_dir), "DTI64_b2600")

        results = SubjectDiscovery(str(subject_dir), id_depth=2).discover_files()

        assert sorted(r.subject_id for r in results) == [
            "10_1003_DTI64_b1300",
            "10_1003_DTI64_b2600",
        ]

    def test_depth_deeper_than_the_path_does_not_raise(self, tmp_path):
        subject_dir = tmp_path / "10_1003"
        subject_dir.mkdir()
        _make_dwi_set(str(subject_dir), "DTI64")

        results = SubjectDiscovery(str(subject_dir), id_depth=99).discover_files()

        assert results[0].subject_id.endswith("10_1003")

    def test_subdir_fallback_forwards_the_depth(self, tmp_path):
        cohort = tmp_path / "cohort"
        for sid in ("sub-01", "sub-02"):
            _make_dwi_set(str(cohort / sid), "dwi")

        results = discover_with_subdir_fallback(str(cohort), id_depth=2)

        assert sorted(r.subject_id for r in results) == ["cohort_sub-01", "cohort_sub-02"]


class TestReassignSubjectIds:
    """
    ``assign_subject_ids`` re-names an already-discovered list at a new depth.

    This is what lets the GUI's ID-depth control re-name subjects that are
    already in the list, instead of forcing a remove-and-re-add. It has to reach
    the same ids discovery would, which is why discovery delegates to it.
    """

    def test_reassigning_is_idempotent_not_cumulative(self, tmp_path):
        """
        Ids derive from the path, never from the id already there.

        A depth that compounded on each change would produce
        ``sub-01_ses-1_sub-01_ses-1_dwi`` after two spins of the control.
        """
        subject_dir = tmp_path / "sub-01" / "ses-1" / "dwi"
        _make_dwi_set(str(subject_dir), "dwi")
        runs = SubjectDiscovery(str(subject_dir)).discover_files()

        assign_subject_ids(runs, 3)
        assign_subject_ids(runs, 3)

        assert runs[0].subject_id == "sub-01_ses-1_dwi"

    def test_depth_can_be_narrowed_again(self, tmp_path):
        """Lowering the control undoes it; the id is a function of depth alone."""
        subject_dir = tmp_path / "sub-01" / "ses-1" / "dwi"
        _make_dwi_set(str(subject_dir), "dwi")
        runs = SubjectDiscovery(str(subject_dir), id_depth=3).discover_files()

        assign_subject_ids(runs, 1)

        assert runs[0].subject_id == "dwi"

    def test_each_folder_is_named_on_its_own_terms(self, tmp_path):
        """
        Runs from different folders are grouped, so a mixed list is named the
        way each folder would have been named alone: the single-run folder by
        its path, the two-run folder by its stems.
        """
        solo = tmp_path / "10_1003"
        _make_dwi_set(str(solo), "DTI64")
        pair = tmp_path / "10_1005"
        _make_dwi_set(str(pair), "DTI64_b1300")
        _make_dwi_set(str(pair), "DTI64_b2600")

        runs = SubjectDiscovery(str(solo)).discover_files()
        runs += SubjectDiscovery(str(pair)).discover_files()
        assign_subject_ids(runs, 1)

        assert sorted(r.subject_id for r in runs) == [
            "10_1003",
            "DTI64_b1300",
            "DTI64_b2600",
        ]

    def test_dropping_a_run_renames_the_survivor_to_its_folder(self, tmp_path):
        """
        The documented consequence of ids being a function of the *current*
        list: a folder left holding one run is again named after the folder,
        because a single-run folder is the subject.
        """
        subject_dir = tmp_path / "10_1003"
        _make_dwi_set(str(subject_dir), "DTI64_b1300")
        _make_dwi_set(str(subject_dir), "DTI64_b2600")
        runs = SubjectDiscovery(str(subject_dir)).discover_files()

        survivors = [runs[0]]
        assign_subject_ids(survivors, 1)

        assert survivors[0].subject_id == "10_1003"

    def test_reassigning_matches_discovery_at_the_same_depth(self, tmp_path):
        """The two paths to an id must not drift; discovery delegates here."""
        cohort = tmp_path / "cohort"
        for sid in ("sub-01", "sub-02"):
            _make_dwi_set(str(cohort / sid), "dwi")

        at_depth_one = discover_with_subdir_fallback(str(cohort))
        assign_subject_ids(at_depth_one, 3)
        discovered_at_three = discover_with_subdir_fallback(str(cohort), id_depth=3)

        assert [r.subject_id for r in at_depth_one] == [r.subject_id for r in discovered_at_three]


class TestSubjectIdCollisionGuard:
    """
    Duplicate subject ids are a hard error, not a warning.

    The id names the output directory *and* keys the results-CSV row, so two
    runs sharing one would send both to ``out/<id>/`` and collapse the CSV to a
    single row -- one subject's numbers silently replaced by another's.
    ``new_unique_runs`` does not catch this: it dedupes on ``dwi_path``, which
    is genuinely distinct.
    """

    def _bids_cohort(self, tmp_path):
        """Two BIDS subjects whose leaf folders are both named ``dwi``."""
        runs = []
        for sid in ("sub-01", "sub-02"):
            leaf = tmp_path / sid / "ses-1" / "dwi"
            _make_dwi_set(str(leaf), "dwi")
            runs.extend(SubjectDiscovery(str(leaf)).discover_files())
        return runs

    def test_unique_ids_pass(self, tmp_path):
        for sid in ("10_1003", "10_1005"):
            _make_dwi_set(str(tmp_path / sid), "DTI64")
        runs = [
            r
            for sid in ("10_1003", "10_1005")
            for r in SubjectDiscovery(str(tmp_path / sid)).discover_files()
        ]

        check_unique_subject_ids(runs)  # does not raise

    def test_empty_list_passes(self):
        check_unique_subject_ids([])

    def test_bids_glob_collision_is_rejected(self, tmp_path):
        runs = self._bids_cohort(tmp_path)
        assert [r.subject_id for r in runs] == ["dwi", "dwi"]

        with pytest.raises(SubjectIdCollisionError) as exc:
            check_unique_subject_ids(runs)

        message = str(exc.value)
        assert "'dwi'" in message
        assert "--id-depth" in message
        # Both offending DWI files are named, so the user can see what collided.
        assert message.count(".nii.gz") == 2

    def test_id_depth_resolves_the_collision(self, tmp_path):
        runs = []
        for sid in ("sub-01", "sub-02"):
            leaf = tmp_path / sid / "ses-1" / "dwi"
            _make_dwi_set(str(leaf), "dwi")
            runs.extend(SubjectDiscovery(str(leaf), id_depth=3).discover_files())

        check_unique_subject_ids(runs)  # does not raise
        assert sorted(r.subject_id for r in runs) == ["sub-01_ses-1_dwi", "sub-02_ses-1_dwi"]

    def test_multi_run_collision_across_folders_is_rejected(self, tmp_path):
        """
        The latent GUI bug: two folders each holding DTI64_b1300 + DTI64_b2600
        collide by exactly the mechanism the folder-name rule exists to prevent.
        """
        runs = []
        for sid in ("10_1003", "10_1005"):
            folder = tmp_path / sid
            folder.mkdir()
            _make_dwi_set(str(folder), "DTI64_b1300")
            _make_dwi_set(str(folder), "DTI64_b2600")
            runs.extend(SubjectDiscovery(str(folder)).discover_files())

        with pytest.raises(SubjectIdCollisionError):
            check_unique_subject_ids(runs)


class TestBatchRunnerChecksIdsBeforeProcessing:
    """
    The guard lives in the engine, so both front ends hit it -- and it fires
    before any data is touched.
    """

    def test_run_batch_raises_on_collision(self, tmp_path):
        runs = []
        for sid in ("sub-01", "sub-02"):
            leaf = tmp_path / sid / "ses-1" / "dwi"
            _make_dwi_set(str(leaf), "dwi")
            runs.extend(SubjectDiscovery(str(leaf)).discover_files())

        state = BatchState(config=BatchConfig(output_dir=str(tmp_path / "out")), subjects=runs)
        messages = []

        with pytest.raises(SubjectIdCollisionError):
            BatchRunner(state, progress_callback=messages.append).run_batch()

        # Nothing was announced and no output directory was created: the guard
        # fires before BatchStart, not partway through a cohort.
        assert messages == []
        assert not (tmp_path / "out").exists()
