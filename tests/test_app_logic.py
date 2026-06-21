"""
Unit tests for the tk-free decisions extracted from gui/app.py (PRD 0004).

These exercise the pure functions the GUI delegates to — no Tkinter object is
named and the window is never instantiated. Each test asserts the structured
return value of the extracted decision, not a widget effect.
"""

import os

from dti_alps.processing.discovery import (
    SubjectFiles,
    discover_with_subdir_fallback,
    new_unique_runs,
)
from dti_alps.processing.validators import (
    resolve_readout_time,
    validate_runnable,
    validate_synb0_output_dir,
)


def _touch(path: str) -> None:
    """Create an empty file, making parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _make_dwi_set(folder: str, stem: str) -> None:
    """Create a matching DWI + bvec + bval file set in folder."""
    _touch(os.path.join(folder, f"{stem}.nii.gz"))
    _touch(os.path.join(folder, f"{stem}.bvec"))
    _touch(os.path.join(folder, f"{stem}.bval"))


class TestValidateSynb0OutputDir:
    """Tests for validate_synb0_output_dir()."""

    def test_all_present_in_outputs(self, tmp_path):
        """All required files in OUTPUTS itself → ok, empty missing."""
        out = tmp_path / "OUTPUTS"
        out.mkdir()
        _touch(str(out / "topup_fieldcoef.nii.gz"))
        _touch(str(out / "topup_movpar.txt"))
        _touch(str(out / "acqparams.txt"))

        ok, missing = validate_synb0_output_dir(str(out))

        assert ok is True
        assert missing == []

    def test_acqparams_fallback_to_inputs(self, tmp_path):
        """acqparams.txt in sibling ../INPUTS counts as found."""
        out = tmp_path / "OUTPUTS"
        out.mkdir()
        _touch(str(out / "topup_fieldcoef.nii.gz"))
        _touch(str(out / "topup_movpar.txt"))
        _touch(str(tmp_path / "INPUTS" / "acqparams.txt"))

        ok, missing = validate_synb0_output_dir(str(out))

        assert ok is True
        assert missing == []

    def test_all_missing(self, tmp_path):
        """Empty directory → not ok, all three descriptions listed in order."""
        out = tmp_path / "OUTPUTS"
        out.mkdir()

        ok, missing = validate_synb0_output_dir(str(out))

        assert ok is False
        assert missing == [
            "topup_fieldcoef.nii.gz (topup field coefficients)",
            "topup_movpar.txt (topup movement parameters)",
            "acqparams.txt (acquisition parameters)",
        ]

    def test_only_acqparams_missing(self, tmp_path):
        """topup files present but no acqparams anywhere → only acqparams missing."""
        out = tmp_path / "OUTPUTS"
        out.mkdir()
        _touch(str(out / "topup_fieldcoef.nii.gz"))
        _touch(str(out / "topup_movpar.txt"))

        ok, missing = validate_synb0_output_dir(str(out))

        assert ok is False
        assert missing == ["acqparams.txt (acquisition parameters)"]


class TestResolveReadoutTime:
    """Tests for resolve_readout_time()."""

    def test_auto_returns_none(self):
        """auto=True → None regardless of the raw string."""
        assert resolve_readout_time(True, "0.04", 0.05) is None
        assert resolve_readout_time(True, "garbage", 0.05) is None

    def test_valid_string_parsed(self):
        """A parseable raw string is returned as a float."""
        assert resolve_readout_time(False, "0.04", 0.05) == 0.04

    def test_unparseable_falls_back_to_default(self):
        """A non-numeric raw string falls back to the default."""
        assert resolve_readout_time(False, "", 0.05) == 0.05
        assert resolve_readout_time(False, "abc", 0.05) == 0.05

    def test_no_range_rejection(self):
        """Out-of-range values are passed through unchanged (no validation)."""
        # A range-checking validator would reject these; resolve_readout_time must not.
        assert resolve_readout_time(False, "999", 0.05) == 999.0
        assert resolve_readout_time(False, "-1", 0.05) == -1.0
        assert resolve_readout_time(False, "0", 0.05) == 0.0


class TestDiscoverWithSubdirFallback:
    """Tests for discover_with_subdir_fallback()."""

    def test_top_level_hit(self, tmp_path):
        """A DWI set directly in the folder is returned without a subdir scan."""
        subject = tmp_path / "10_1003"
        subject.mkdir()
        _make_dwi_set(str(subject), "DTI64")

        runs = discover_with_subdir_fallback(str(subject))

        assert len(runs) == 1
        assert runs[0].subject_id == "10_1003"

    def test_subdir_fallback(self, tmp_path):
        """No DWI at top level → immediate subdirectories are scanned, in order."""
        parent = tmp_path / "cohort"
        parent.mkdir()
        _make_dwi_set(str(parent / "sub-a"), "DTI64")
        _make_dwi_set(str(parent / "sub-b"), "DTI64")

        runs = discover_with_subdir_fallback(str(parent))

        assert [r.subject_id for r in runs] == ["sub-a", "sub-b"]

    def test_nothing_anywhere(self, tmp_path):
        """No DWI at top level or in subdirs → empty list."""
        empty = tmp_path / "empty"
        (empty / "junk").mkdir(parents=True)

        assert discover_with_subdir_fallback(str(empty)) == []


class TestNewUniqueRuns:
    """Tests for new_unique_runs()."""

    @staticmethod
    def _run(dwi_path: str) -> SubjectFiles:
        return SubjectFiles(folder_path="/f", subject_id=dwi_path, dwi_path=dwi_path)

    def test_dedup_against_existing(self):
        """Runs whose dwi_path is already in existing are dropped."""
        existing = [self._run("/a.nii.gz")]
        discovered = [self._run("/a.nii.gz"), self._run("/b.nii.gz")]

        unique = new_unique_runs(existing, discovered)

        assert [r.dwi_path for r in unique] == ["/b.nii.gz"]

    def test_intra_batch_dedup(self):
        """Duplicate dwi_paths within the discovered batch collapse to one."""
        discovered = [self._run("/a.nii.gz"), self._run("/a.nii.gz")]

        unique = new_unique_runs([], discovered)

        assert [r.dwi_path for r in unique] == ["/a.nii.gz"]

    def test_order_preserved(self):
        """Surviving runs keep their discovered order."""
        discovered = [self._run("/c.nii.gz"), self._run("/a.nii.gz"), self._run("/b.nii.gz")]

        unique = new_unique_runs([], discovered)

        assert [r.dwi_path for r in unique] == ["/c.nii.gz", "/a.nii.gz", "/b.nii.gz"]


class TestValidateRunnable:
    """Tests for validate_runnable()."""

    @staticmethod
    def _valid(subject_id: str) -> SubjectFiles:
        return SubjectFiles(
            folder_path="/f",
            subject_id=subject_id,
            dwi_path=f"/{subject_id}.nii.gz",
            bvec_path=f"/{subject_id}.bvec",
            bval_path=f"/{subject_id}.bval",
        )

    @staticmethod
    def _invalid(subject_id: str) -> SubjectFiles:
        # Missing bvec/bval → is_valid is False
        return SubjectFiles(
            folder_path="/f", subject_id=subject_id, dwi_path=f"/{subject_id}.nii.gz"
        )

    def test_runnable(self):
        """Valid subjects + output dir → ok, no kind/payload."""
        assert validate_runnable([self._valid("s1")], "/out") == (True, None, None)

    def test_no_subjects(self):
        """Empty subject list → no_subjects."""
        assert validate_runnable([], "/out") == (False, "no_subjects", None)

    def test_invalid_subjects_returns_ids(self):
        """Any invalid subject → invalid_subjects with the invalid ids."""
        subjects = [self._valid("good"), self._invalid("bad1"), self._invalid("bad2")]

        assert validate_runnable(subjects, "/out") == (
            False,
            "invalid_subjects",
            ["bad1", "bad2"],
        )

    def test_no_output_dir(self):
        """Valid subjects but empty output dir → no_output_dir."""
        assert validate_runnable([self._valid("s1")], "") == (False, "no_output_dir", None)

    def test_first_failure_wins_no_subjects_before_output(self):
        """Empty subjects beats a missing output dir (no_subjects reported first)."""
        assert validate_runnable([], "") == (False, "no_subjects", None)

    def test_first_failure_wins_invalid_before_output(self):
        """Invalid subjects beat a missing output dir (invalid_subjects first)."""
        ok, kind, payload = validate_runnable([self._invalid("bad")], "")
        assert (ok, kind, payload) == (False, "invalid_subjects", ["bad"])
