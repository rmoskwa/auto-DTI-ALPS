"""
Unit tests for the tk-free decisions extracted from gui/app.py (PRD 0004).

These exercise the pure functions the GUI delegates to — no Tkinter object is
named and the window is never instantiated. Each test asserts the structured
return value of the extracted decision, not a widget effect.
"""

import os

from dti_alps.processing.validators import (
    resolve_readout_time,
    validate_synb0_output_dir,
)


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
        # validate_readout_time would reject these; resolve_readout_time must not.
        assert resolve_readout_time(False, "999", 0.05) == 999.0
        assert resolve_readout_time(False, "-1", 0.05) == -1.0
        assert resolve_readout_time(False, "0", 0.05) == 0.0
