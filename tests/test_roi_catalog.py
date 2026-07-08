"""
Unit tests for the ROI shape catalog (``gui/config.ROI_SHAPES``, PRD 0015).

The catalog is the single ordered source for the *selectable* ROI shapes. These
tests pin the two invariants the design relies on:

* the catalog labels agree with the viewer's ``roi_display_name`` parser for
  every input token — the two label sources are deliberately *not* coupled at
  runtime (the viewer parses the open on-disk vocabulary), so this is the CI
  tripwire that keeps them from drifting;
* exactly one row is the default, so the pre-checked checkbox and the form
  model's empty-selection fallback (both driven by that row) cannot break.

No Tkinter or PySide6 object is instantiated.
"""

from dti_alps.gui.config import ROI_SHAPES
from dti_alps.gui.viewer_model import roi_display_name


def test_catalog_labels_match_viewer_display_name():
    """Every catalog label equals what the viewer parser produces for its token."""
    for shape in ROI_SHAPES:
        assert roi_display_name(shape.token) == shape.label, shape.token


def test_exactly_one_default_shape():
    """The catalog has exactly one default row (drives pre-check + fallback)."""
    assert sum(shape.default for shape in ROI_SHAPES) == 1


def test_default_shape_is_sphere_3mm():
    """The canonical default is the 3 mm sphere (behavior-preserving anchor)."""
    default = next(shape for shape in ROI_SHAPES if shape.default)
    assert default.geometry == {"type": "sphere", "radius": 3.0}
