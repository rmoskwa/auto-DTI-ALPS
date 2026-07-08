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
]

# Names whose presence in a fresh child's ``sys.modules`` after importing an
# engine module means the engine reached into the GUI layer or a GUI toolkit.
FORBIDDEN_MODULES = ["dti_alps.gui", "PySide6", "tkinter"]


def _forbidden_modules_after_importing(module: str) -> list[str]:
    """
    Import ``module`` in a fresh interpreter and report which of
    ``FORBIDDEN_MODULES`` ended up resident in that child's ``sys.modules``.

    Raises ``CalledProcessError`` if the import itself fails (non-zero exit),
    which surfaces as a test failure with the child's traceback.
    """
    code = f"import {module}, sys; print([m for m in {FORBIDDEN_MODULES!r} if m in sys.modules])"
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
