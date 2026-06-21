"""
Architectural guard: the analysis engine must not import the GUI package.

The domain constants the engine consumes were lifted out of
``dti_alps.gui.config`` into ``dti_alps.processing.constants`` so the dependency
arrow points one way only: ``gui -> processing``, never the reverse. This test
pins that invariant at the package import boundary.

It runs in a *fresh child interpreter* rather than in-process: another test (or
a conftest) may already have imported ``dti_alps.gui`` and polluted this
process's ``sys.modules``, which would mask a real regression. A child gives a
clean module table every run, needs no external binaries, and catches
*transitive* re-introductions of a ``processing -> gui`` dependency, not just a
literal ``from ..gui import ...`` string.

Model: ``tests/test_discovery.py`` / ``tests/test_alps_calculation.py`` -- a
single focused, pure module with no external tools. It deliberately does *not*
name which module holds which constant; it asserts only the external,
architectural fact, so it stays green however the constants are organized inside
``processing`` and goes red only if a real engine -> gui dependency returns.
"""

import subprocess
import sys

# Importing any of these must not, directly or transitively, import dti_alps.gui.
ENGINE_MODULES = [
    "dti_alps.processing",
    "dti_alps.processing.alps_calculation",
    "dti_alps.processing.validators",
    "dti_alps.processing.batch",
    "dti_alps.processing.state",
]


def _gui_resident_after_importing(module: str) -> bool:
    """
    Import ``module`` in a fresh interpreter and report whether ``dti_alps.gui``
    ended up resident in that child's ``sys.modules``.

    Raises ``CalledProcessError`` if the import itself fails (non-zero exit),
    which surfaces as a test failure with the child's traceback.
    """
    code = f"import {module}, sys; print('dti_alps.gui' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "True"


def test_importing_engine_does_not_import_gui():
    """Each engine entry point imports cleanly without dragging in the GUI."""
    for module in ENGINE_MODULES:
        assert not _gui_resident_after_importing(module), (
            f"Importing {module} left dti_alps.gui resident in sys.modules; "
            "the engine must not depend on the GUI package."
        )
