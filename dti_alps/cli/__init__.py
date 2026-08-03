"""
The command-line front end -- the second front end over the analysis engine.

``dti_alps.cli`` is to the terminal what ``dti_alps.gui`` is to the desktop: a
thin adapter that turns user input into engine domain objects and turns the
engine's typed ``WorkerMessage`` stream back into something a person can read.
Both front ends sit *above* ``dti_alps.processing`` and neither imports the
other -- where the CLI needs a vocabulary the GUI also has, it derives it from
the engine, which is the shared floor.

Two disciplines hold this package together:

* **Toolkit-free except at the view seam.** Only :mod:`dti_alps.cli.view` (and
  the ``gui`` verb in :mod:`dti_alps.cli.main`) touch PySide6, and only inside a
  function body. Importing ``dti_alps.cli`` on a display-less compute node must
  leave no Qt resident -- a guard test asserts exactly that.
* **``cli/`` never imports ``gui/``.** The point of the package is that it is
  the second front end, not a client of the first.

The layout mirrors the verbs: :mod:`~dti_alps.cli.main` owns the subparser
wiring and dispatch, one module per verb (``run``, ``reanalyze``, ``report``,
``view``), and :mod:`~dti_alps.cli.render` is the terminal presentation model --
the exact mirror of ``gui/result_model.py``.
"""
