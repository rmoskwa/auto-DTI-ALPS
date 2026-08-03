"""
Architectural guard: the analysis engine must import no GUI toolkit.

The engine (``dti_alps.processing``) is the distributable analysis core -- it
must run on a display-less cluster with no GUI toolkit installed. So the property
this test protects is *toolkit-absence*: importing an engine module must leave no
GUI toolkit (``PySide6``, ``tkinter``) resident, and must not import
``dti_alps.gui`` -- the dependency arrow points one way only, ``gui -> processing``.

Asserting the *property* (no toolkit resident), not just the *proxy* (no
``dti_alps.gui`` resident), matters: a processing module could ``import PySide6``
*directly*, without ever importing ``dti_alps.gui``. A gui-package-only check
would stay green while the headless guarantee broke. This test catches that
direct import too.

It runs in a *fresh child interpreter* rather than in-process: another test (or
a conftest) may already have imported a toolkit and polluted this process's
``sys.modules``, which would mask a real regression. A child gives a clean module
table every run, needs no external binaries, and catches *transitive*
re-introductions, not just a literal ``import PySide6`` / ``from ..gui import ...``
string.

Model: ``tests/test_discovery.py`` / ``tests/test_alps_calculation.py`` -- a
single focused, pure module with no external tools. It deliberately does *not*
name which module holds which constant; it asserts only the external,
architectural fact, so it stays green however the engine is organized inside
``processing`` and goes red only if a real toolkit dependency returns.
"""

import subprocess
import sys

# Importing any of these must not, directly or transitively, pull in a GUI toolkit.
ENGINE_MODULES = [
    "dti_alps.processing",
    "dti_alps.processing.alps_calculation",
    "dti_alps.processing.validators",
    "dti_alps.processing.batch",
    "dti_alps.processing.state",
    # The Quality Report's background worker (PRD 0022) is an engine module: it
    # must run the subset compute with no GUI toolkit and never import the view.
    "dti_alps.processing.report_worker",
]

# Names whose presence in a fresh child's ``sys.modules`` after importing an
# engine module means the engine reached into the GUI layer or a GUI toolkit.
FORBIDDEN_MODULES = ["dti_alps.gui", "PySide6", "tkinter"]

# The CLI is the *second* front end over the same engine, and the same headless
# guarantee applies to it: `dti-alps run` must work on a display-less compute
# node. Every CLI module except ``view`` (which launches the viewer) and the
# ``gui`` verb inside ``main`` must import with no toolkit resident -- and both
# of those reach for PySide6 only inside a function body, so importing
# ``cli.main`` itself stays clean even though it can dispatch to them.
CLI_MODULES = [
    "dti_alps.cli",
    "dti_alps.cli.main",
    "dti_alps.cli.reanalyze",
    "dti_alps.cli.report",
    "dti_alps.cli.validators",
    # ``view`` is the toolkit seam, but its import must still be inert: only
    # calling ``execute`` may pull Qt in.
    "dti_alps.cli.view",
]

# The CLI is a front end, so it may name ``dti_alps.gui`` lazily (the ``view``
# verb does). What it must not do is leave a toolkit resident on import.
FORBIDDEN_FOR_CLI = ["PySide6", "tkinter"]


def _forbidden_modules_after_importing(
    module: str, forbidden: list[str] | None = None
) -> list[str]:
    """
    Import ``module`` in a fresh interpreter and report which of ``forbidden``
    (default :data:`FORBIDDEN_MODULES`) ended up resident in that child's
    ``sys.modules``.

    Raises ``CalledProcessError`` if the import itself fails (non-zero exit),
    which surfaces as a test failure with the child's traceback.
    """
    forbidden = FORBIDDEN_MODULES if forbidden is None else forbidden
    code = f"import {module}, sys; print([m for m in {forbidden!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    # The child prints a repr'd list of the forbidden names it found resident.
    return eval(result.stdout.strip())  # noqa: S307 -- our own repr, trusted input


def test_importing_engine_imports_no_gui_toolkit():
    """Each engine entry point imports cleanly without dragging in the GUI or a toolkit."""
    for module in ENGINE_MODULES:
        resident = _forbidden_modules_after_importing(module)
        assert not resident, (
            f"Importing {module} left {resident} resident in sys.modules; "
            "the engine must import no GUI package or toolkit."
        )


def test_importing_cli_imports_no_gui_toolkit():
    """The CLI front end imports cleanly with no toolkit resident."""
    for module in CLI_MODULES:
        resident = _forbidden_modules_after_importing(module, FORBIDDEN_FOR_CLI)
        assert not resident, (
            f"Importing {module} left {resident} resident in sys.modules; "
            "`dti-alps run` must work on a display-less node, so the CLI may "
            "import a toolkit only inside a function body."
        )


def test_cli_never_imports_the_gui_package_except_at_the_view_seam():
    """
    ``cli/`` is the *second* front end, not a client of the first.

    Only ``view`` (and the ``gui`` verb in ``main``) may name ``dti_alps.gui``,
    and only lazily -- so no CLI module leaves it resident on import. Where the
    CLI needs a vocabulary the GUI also has, it derives it from the engine.
    """
    for module in CLI_MODULES:
        resident = _forbidden_modules_after_importing(module, ["dti_alps.gui"])
        assert not resident, (
            f"Importing {module} left dti_alps.gui resident in sys.modules; "
            "the CLI must derive shared vocabulary from the engine, not the GUI."
        )
