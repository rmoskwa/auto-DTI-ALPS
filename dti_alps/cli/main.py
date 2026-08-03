"""
Subparser wiring and dispatch for the ``dti-alps`` command.

Every verb contributes its own flags through an ``add_arguments(parser)`` hook
and does its work in ``execute(args) -> int``, so ``dti-alps run --help`` lists
exactly the ``run`` flags without polluting ``dti-alps reanalyze --help``. The
top-level ``--help`` is argparse's generated verb list rather than a module
docstring that had to be hand-synced with a per-verb epilog.

Bare ``dti-alps`` launches the GUI. That is a product requirement -- the
AppImage double-click path -- not a compatibility shim: the ``--viewer`` /
``--report`` / ``--reanalyze`` flag spellings this replaced are gone with no
aliases.

Exit codes are the CLI's contract with a job script:

===== =============================================================
  0   every subject completed
  1   finished with at least one failure
  2   usage or configuration error (argparse's own convention)
  3   preflight failure -- a required external tool is missing
130   interrupted by SIGINT
===== =============================================================
"""

import argparse
import sys

from . import reanalyze, report, view

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_USAGE = 2
EXIT_PREFLIGHT = 3
EXIT_INTERRUPTED = 130


def _launch_gui(args: argparse.Namespace) -> int:
    """Launch the main GUI application (the bare-command default)."""
    from ..gui import main as gui_main

    gui_main()
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with one subparser per verb."""
    parser = argparse.ArgumentParser(
        prog="dti-alps",
        description=(
            "Automatic DTI-ALPS ROI detection and analysis. "
            "Run with no arguments to launch the GUI."
        ),
    )

    verbs = parser.add_subparsers(dest="verb", metavar="VERB")

    gui_parser = verbs.add_parser("gui", help="Launch the main GUI application (default)")
    gui_parser.set_defaults(_execute=_launch_gui)

    view_parser = verbs.add_parser("view", help="Launch the results viewer")
    view.add_arguments(view_parser)
    view_parser.set_defaults(_execute=view.execute)

    report_parser = verbs.add_parser("report", help="Generate ROI quality reports")
    report.add_arguments(report_parser)
    report_parser.set_defaults(_execute=report.execute)

    reanalyze_parser = verbs.add_parser(
        "reanalyze",
        help="Re-run ROI placement and ALPS calculation over existing output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=reanalyze.EPILOG,
    )
    reanalyze.add_arguments(reanalyze_parser)
    reanalyze_parser.set_defaults(_execute=reanalyze.execute)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the selected verb.

    Returns the verb's exit code. ``__main__`` passes it to :func:`sys.exit`;
    tests call this directly and assert on the returned int.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # No verb at all -- the bare `dti-alps` GUI launch.
    if args.verb is None:
        return _launch_gui(args)

    return args._execute(args)


if __name__ == "__main__":
    sys.exit(main())
