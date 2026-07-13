"""Frozen-build entry point.

PyInstaller runs its entry script as the top-level ``__main__`` module, which
breaks the relative imports inside ``dti_alps/__main__.py``. This shim imports
the package's ``main`` with a real package context so those imports resolve.
"""

from dti_alps.__main__ import main

if __name__ == "__main__":
    main()
