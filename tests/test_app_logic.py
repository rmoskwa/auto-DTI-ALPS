"""
Unit tests for the tk-free decisions extracted from gui/app.py (PRD 0004).

These exercise the pure functions the GUI delegates to — no Tkinter object is
named and the window is never instantiated. Each test asserts the structured
return value of the extracted decision, not a widget effect.
"""

import os

from dti_alps.processing.validators import validate_synb0_output_dir


def _touch(path: str) -> None:
    """Create an empty file, making parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


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
