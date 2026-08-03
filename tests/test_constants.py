"""
Unit tests for the domain-constant leaf (``processing/constants.py``).

Value-in/value-out over a dependency-free module: nothing here touches disk, a
toolkit, or an external tool.
"""

import pytest

from dti_alps.processing.constants import (
    DEFAULT_ROI_METHOD,
    ROI_METHOD_OPTIONS,
    placement_modes,
)


class TestPlacementModes:
    """
    The one expansion from the tri-state vocabulary to placement passes.

    It lives beside the vocabulary because the pipeline and reanalysis both
    decode it; reanalysis previously had no expansion at all and took a bool,
    which is why ``reanalyze`` could not express "Both".
    """

    def test_standard_runs_one_non_adaptive_pass(self):
        assert placement_modes("Standard") == (False,)

    def test_adaptive_runs_one_adaptive_pass(self):
        assert placement_modes("Adaptive") == (True,)

    def test_both_runs_standard_first(self):
        """Order is load-bearing: results land in the same order as the suffixes."""
        assert placement_modes("Both") == (False, True)

    @pytest.mark.parametrize("method", ROI_METHOD_OPTIONS)
    def test_every_vocabulary_member_expands(self, method):
        """No member of the closed vocabulary may be left undecodable."""
        modes = placement_modes(method)
        assert modes and all(isinstance(m, bool) for m in modes)

    def test_the_default_is_expandable(self):
        assert placement_modes(DEFAULT_ROI_METHOD)

    @pytest.mark.parametrize("junk", ["adaptive", "STANDARD", "", "refined", "None"])
    def test_unknown_value_raises_rather_than_defaulting_to_standard(self, junk):
        """
        A silent fallback would run a *different* analysis from the one asked
        for -- the failure mode the shared DEFAULT_ROI_METHOD exists to prevent.
        """
        with pytest.raises(ValueError, match="Unknown ROI placement method"):
            placement_modes(junk)

    def test_a_bool_is_no_longer_accepted(self):
        """The legacy bool branch is gone; only the vocabulary decodes."""
        with pytest.raises(ValueError):
            placement_modes(True)
