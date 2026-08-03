"""
Unit tests for the CLI's grammar and dispatch (``dti_alps/cli/main.py``).

These exercise the parser and the verb routing without running any verb's real
work: each test swaps the target module's ``execute`` for a recorder, so no
pipeline, viewer, or report is ever started. Importing this module leaves Qt
unloaded -- the point of the CLI package -- and nothing here needs MRtrix3 or
FSL on PATH.
"""

import pytest

from dti_alps.cli import main as cli_main
from dti_alps.cli import reanalyze, report, view


@pytest.fixture
def calls(monkeypatch):
    """Replace every verb's ``execute`` with a recorder returning exit code 0."""
    recorded = {}

    def _recorder(name):
        def _execute(args):
            recorded[name] = args
            return 0

        return _execute

    monkeypatch.setattr(reanalyze, "execute", _recorder("reanalyze"))
    monkeypatch.setattr(report, "execute", _recorder("report"))
    monkeypatch.setattr(view, "execute", _recorder("view"))
    monkeypatch.setattr(cli_main, "_launch_gui", _recorder("gui"))
    return recorded


class TestVerbDispatch:
    """Each verb routes to its own module; the bare command launches the GUI."""

    def test_bare_command_launches_the_gui(self, calls):
        """The AppImage double-click path: no arguments means the GUI."""
        assert cli_main.main([]) == 0
        assert "gui" in calls

    def test_explicit_gui_verb_launches_the_gui(self, calls):
        assert cli_main.main(["gui"]) == 0
        assert "gui" in calls

    def test_view_verb_routes_to_view(self, calls):
        assert cli_main.main(["view", "/data/out"]) == 0
        assert calls["view"].output_dir == "/data/out"

    def test_view_verb_output_dir_is_optional(self, calls):
        assert cli_main.main(["view"]) == 0
        assert calls["view"].output_dir is None

    def test_report_verb_routes_to_report(self, calls):
        assert cli_main.main(["report", "/data/out"]) == 0
        assert calls["report"].output_dir == "/data/out"

    def test_reanalyze_verb_routes_to_reanalyze(self, calls):
        assert cli_main.main(["reanalyze", "/data/out", "--sphere", "3"]) == 0
        assert calls["reanalyze"].output_dir == "/data/out"
        assert calls["reanalyze"].sphere == [3.0]


class TestCleanBreakFromFlagVerbs:
    """
    The old ``--viewer`` / ``--report`` / ``--reanalyze`` spellings are gone
    with no aliases (pre-release clean break, PRD 0024). They must be rejected
    as usage errors, not silently swallowed into a GUI launch -- the failure
    mode the hand-rolled ``sys.argv[1]`` switch was written to avoid.
    """

    @pytest.mark.parametrize("flag", ["--viewer", "--report", "--reanalyze", "--gui"])
    def test_retired_flag_spelling_is_a_usage_error(self, flag, calls):
        with pytest.raises(SystemExit) as exc:
            cli_main.main([flag])
        assert exc.value.code == cli_main.EXIT_USAGE
        assert calls == {}

    def test_unknown_verb_is_a_usage_error(self, calls):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["frobnicate"])
        assert exc.value.code == cli_main.EXIT_USAGE
        assert calls == {}


class TestHelp:
    """``--help`` is argparse-generated, not a hand-maintained docstring."""

    def test_top_level_help_lists_every_verb(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["--help"])
        assert exc.value.code == 0

        out = capsys.readouterr().out
        for verb in ("gui", "view", "report", "reanalyze"):
            assert verb in out

    def test_per_verb_help_does_not_leak_other_verbs_flags(self, capsys):
        """Subparsers exist so ``reanalyze --help`` shows only reanalyze flags."""
        with pytest.raises(SystemExit):
            cli_main.main(["reanalyze", "--help"])

        out = capsys.readouterr().out
        assert "--sphere" in out
        assert "--roi-method" in out


class TestValidators:
    """Flag validation is shared, so both verbs reject the same bad input."""

    def test_sphere_radius_out_of_range_is_rejected(self):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["reanalyze", "/out", "--sphere", "99"])
        assert exc.value.code == cli_main.EXIT_USAGE

    def test_search_value_out_of_range_is_rejected(self):
        with pytest.raises(SystemExit) as exc:
            cli_main.main(["reanalyze", "/out", "--search-x", "9"])
        assert exc.value.code == cli_main.EXIT_USAGE

    def test_comma_separated_sphere_radii_all_parse(self, calls):
        cli_main.main(["reanalyze", "/out", "--sphere", "2,3,4"])
        assert calls["reanalyze"].sphere == [2.0, 3.0, 4.0]


class TestExitCodes:
    """The verb's return value becomes the process exit code."""

    def test_main_returns_the_verbs_code(self, monkeypatch):
        monkeypatch.setattr(report, "execute", lambda args: 7)
        assert cli_main.main(["report", "/out"]) == 7

    def test_reanalyze_with_no_shape_returns_failure(self, capsys):
        """No ROI shape named is a user error the verb reports, not a crash."""
        assert cli_main.main(["reanalyze", "/out"]) == 1
        assert "At least one ROI shape" in capsys.readouterr().out
