"""
Entry point for ``python -m dti_alps`` and the ``dti-alps`` console script.

The command grammar lives in :mod:`dti_alps.cli.main`; this module only re-exports
its ``main`` so both entry paths resolve to one implementation. ``main`` returns
an exit code -- the generated console script wraps it in ``sys.exit(...)``, and
the ``__main__`` guard below does the same for ``python -m``.
"""

import sys

from .cli.main import main

__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
