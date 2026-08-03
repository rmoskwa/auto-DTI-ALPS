"""
Unit tests for the external-tool preflight checks (``processing/commands.py``).

No toolchain is installed here: every test fakes ``shutil.which`` with an
explicit set of names, so the checkers are exercised against a PATH the test
fully controls -- including the pathological ones a real machine produces.
"""

import shutil

import pytest

from dti_alps.processing.commands import check_fsl_available, check_mrtrix3_available


@pytest.fixture
def fake_path(monkeypatch):
    """
    Install a fake PATH; returns a setter taking the names that resolve.

    The checkers import ``shutil`` inside their own bodies, so patching
    ``shutil.which`` itself is what takes effect -- and it keeps the tests
    independent of whatever is actually installed on the machine running them.
    """

    def _install(*names):
        present = set(names)
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None
        )

    return _install


ALL_MRTRIX = [
    "dwidenoise",
    "mrdegibbs",
    "dwifslpreproc",
    "dwi2tensor",
    "tensor2metric",
    "dwi2mask",
    "dwiextract",
    "mrmath",
    "mrconvert",
]
ALL_FSL = ["flirt", "fnirt", "invwarp", "applywarp", "fslmaths"]


class TestMrtrixCheck:
    """The list must match what the engine actually invokes."""

    def test_complete_install_passes(self, fake_path):
        fake_path(*ALL_MRTRIX)

        assert check_mrtrix3_available() == (True, [])

    def test_empty_path_reports_everything(self, fake_path):
        fake_path()

        available, missing = check_mrtrix3_available()

        assert available is False
        assert set(missing) == set(ALL_MRTRIX)

    @pytest.mark.parametrize("cmd", ["dwi2mask", "dwiextract", "mrmath", "mrconvert"])
    def test_commands_the_old_list_omitted_are_now_checked(self, fake_path, cmd):
        """
        b0 extraction and the registration backend invoke all four. Omitting
        them meant preflight passed and the run died at stage 7 instead.
        """
        fake_path(*[c for c in ALL_MRTRIX if c != cmd])

        available, missing = check_mrtrix3_available()

        assert available is False
        assert missing == [cmd]


class TestSynb0ChangesTheRequiredSet:
    """
    On the synB0 route the user has already run synB0-DISCO externally, so
    ``dwifslpreproc`` is never invoked -- demanding it would fail a valid setup.
    """

    def test_dwifslpreproc_not_required_in_synb0_mode(self, fake_path):
        fake_path(*[c for c in ALL_MRTRIX if c != "dwifslpreproc"])

        assert check_mrtrix3_available(use_synb0=True) == (True, [])
        assert check_mrtrix3_available(use_synb0=False)[0] is False

    def test_eddy_is_required_in_synb0_mode_only(self, fake_path):
        """The synB0 route runs eddy itself; the standard route delegates."""
        fake_path(*ALL_FSL)

        assert check_fsl_available(use_synb0=False) == (True, [])

        available, missing = check_fsl_available(use_synb0=True)
        assert available is False
        assert missing == ["eddy"]

    def test_synb0_mode_passes_with_eddy_present(self, fake_path):
        fake_path(*ALL_FSL, "eddy")

        assert check_fsl_available(use_synb0=True) == (True, [])


class TestFslCheck:
    """The registration tools this codebase calls directly are what matter."""

    def test_complete_install_passes(self, fake_path):
        fake_path(*ALL_FSL)

        assert check_fsl_available() == (True, [])

    @pytest.mark.parametrize("cmd", ALL_FSL)
    def test_each_registration_tool_is_required(self, fake_path, cmd):
        """
        The old list checked eddy/topup/applytopup -- commands dwifslpreproc
        calls internally -- and omitted every command we invoke ourselves.
        """
        fake_path(*[c for c in ALL_FSL if c != cmd])

        available, missing = check_fsl_available()

        assert available is False
        assert missing == [cmd]

    def test_topup_is_not_demanded_on_the_standard_route(self, fake_path):
        """dwifslpreproc finds topup by its own means; requiring it fails good installs."""
        fake_path(*ALL_FSL)

        assert check_fsl_available() == (True, [])


class TestVariantMatchingIsPerCommand:
    """
    The variant list used to include the literal ``eddy_openmp`` for *every*
    command, so `topup` and `applytopup` were reported present whenever
    `eddy_openmp` happened to be installed. Variants are per-command now.
    """

    def test_eddy_openmp_satisfies_eddy(self, fake_path):
        fake_path(*ALL_FSL, "eddy_openmp")

        assert check_fsl_available(use_synb0=True) == (True, [])

    def test_eddy_openmp_does_not_satisfy_another_command(self, fake_path):
        """The bug, in the direction that produced a false pass."""
        fake_path(*[c for c in ALL_FSL if c != "flirt"], "eddy_openmp")

        available, missing = check_fsl_available()

        assert available is False
        assert missing == ["flirt"]

    def test_cuda_variant_is_accepted(self, fake_path):
        fake_path(*ALL_FSL, "eddy_cuda")

        assert check_fsl_available(use_synb0=True) == (True, [])

    def test_fsl_prefixed_variant_is_accepted(self, fake_path):
        fake_path(*[c for c in ALL_FSL if c != "fslmaths"], "fslfslmaths")

        assert check_fsl_available() == (True, [])
