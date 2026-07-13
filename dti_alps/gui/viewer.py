"""
Results Viewer for DTI-ALPS processing output (PySide6 adapter).

Displays FA-modulated RGB direction-encoded color (DEC) images with ROI overlays.

This module is the **Qt adapter**: it reads widgets, calls
:class:`~dti_alps.gui.viewer_model.ViewerModel` (a tk-free presentation model)
and ``results_layout`` (the on-disk contract), and applies the returned plain
data and finished NumPy pictures to widgets. It owns all phrasing, dialog type,
zoom, and image placement; the session logic, the ALPS/CSV parsing, and the DEC
rendering math live in the model and the engine leaf, reused here
byte-for-byte from the Tkinter viewer.

The viewer content is :class:`ResultsViewerPanel`, a host-agnostic ``QWidget``
shared by two hosts: the standalone :class:`ResultsViewer` window wraps one as
its central widget, and the main app embeds one as a resident page. All controls
(load, view, slice, zoom, show-ROIs) live in the panel body — there is no menu
bar — so both hosts get every capability from the one implementation.

The image pane is a ``QGraphicsView`` (:class:`_ImageView`) with the standard
PACS mouse conventions (PRD 0021): left-drag = window/level, right-drag = zoom,
middle-drag = pan, and the wheel still changes the *slice*. Zoom is driven by a
geometric 10%-800% slider and right-drag sharing one center-anchored scalar; a
"Reset view" button re-fits the zoom and restores the default window. The
``AsNeeded`` scrollbars remain as a pan fallback for users without a middle
button. The window/level pixel math lives in the pure ``render_dec_slice``; this
adapter owns only the transient cursor state (slice, zoom, window/level).
"""

import math
import sys

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .user_config import UserConfig, get_user_config
from .viewer_model import LoadError, ViewerModel

# Geometric zoom band (Decision 5): the slider and right-drag drive one
# center-anchored zoom scalar spanning 10%-800%, mapped so equal slider steps
# are equal zoom *ratios*. Fit values are clamped into this band.
_ZOOM_MIN = 0.10
_ZOOM_MAX = 8.0
_ZOOM_SLIDER_MAX = 1000  # integer slider resolution over the geometric span

# Mouse drag sensitivities (Decision 1/8). Window/level is in abstract FA units
# (FA is bounded ~[0, 1]); zoom drag is geometric (each pixel multiplies zoom by
# a constant ratio). Tuned by manual smoke.
_WL_LEVEL_PER_PIXEL = 0.004  # vertical left-drag -> window center (brightness)
_WL_WIDTH_PER_PIXEL = 0.004  # horizontal left-drag -> window width (contrast)
_WL_MIN_WIDTH = 1e-3  # adapter clamp so a drag never reaches a zero-width window
_ZOOM_DRAG_PER_PIXEL = 0.01  # vertical right-drag -> geometric zoom exponent


def _clamp_zoom(zoom: float) -> float:
    """Clamp a zoom scalar into the 10%-800% band."""
    return min(_ZOOM_MAX, max(_ZOOM_MIN, zoom))


def _zoom_to_slider_pos(zoom: float) -> int:
    """Map a zoom scalar to its integer slider position (geometric)."""
    frac = math.log(_clamp_zoom(zoom) / _ZOOM_MIN) / math.log(_ZOOM_MAX / _ZOOM_MIN)
    return int(round(frac * _ZOOM_SLIDER_MAX))


def _slider_pos_to_zoom(pos: int) -> float:
    """Map an integer slider position back to a zoom scalar (geometric)."""
    frac = pos / _ZOOM_SLIDER_MAX
    return _ZOOM_MIN * (_ZOOM_MAX / _ZOOM_MIN) ** frac


def _bold(widget: QLabel) -> QLabel:
    """Make a label's font bold in place and return it (for inline use)."""
    font = widget.font()
    font.setBold(True)
    widget.setFont(font)
    return widget


def _clear_layout(layout) -> None:
    """Remove and delete every widget currently in ``layout``."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class _ImageView(QGraphicsView):
    """A ``QGraphicsView`` implementing the PACS mouse conventions (Decision 1).

    The raw mouse mechanics live here; the semantic state (window/level, zoom)
    is owned by the panel and mutated through callbacks, keeping this a thin
    interaction shell:

    * **wheel** changes the slice (``on_wheel``, unchanged from PRD 0010);
    * **left-drag** adjusts window/level -- horizontal delta = width, vertical
      delta = level -- via ``on_window_level(dx, dy)``;
    * **right-drag** zooms -- vertical delta, up = in -- via ``on_zoom(dy)``;
    * **middle-drag** pans, handled here by translating this view's own
      scrollbars (the left button is taken by window/level, so ``ScrollHandDrag``
      is unavailable).

    The ``AsNeeded`` scrollbars from PRD 0010 remain as a pan fallback for users
    without a middle button (Decision 6).
    """

    def __init__(self, on_wheel, on_window_level, on_zoom):
        super().__init__()
        self._on_wheel = on_wheel
        self._on_window_level = on_window_level
        self._on_zoom = on_zoom
        self._drag_button = Qt.NoButton
        self._last_pos = None

    def wheelEvent(self, event):  # noqa: N802 (Qt override name)
        dy = event.angleDelta().y()
        if dy:
            self._on_wheel(1 if dy > 0 else -1)
        event.accept()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override name)
        if event.button() in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton):
            self._drag_button = event.button()
            self._last_pos = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt override name)
        if self._drag_button == Qt.NoButton or self._last_pos is None:
            super().mouseMoveEvent(event)
            return
        pos = event.position()
        dx = pos.x() - self._last_pos.x()
        dy = pos.y() - self._last_pos.y()
        self._last_pos = pos

        if self._drag_button == Qt.LeftButton:
            self._on_window_level(dx, dy)
        elif self._drag_button == Qt.RightButton:
            self._on_zoom(dy)
        elif self._drag_button == Qt.MiddleButton:
            # Pan by translating the scrollbars (the same mechanism the fallback
            # scrollbars drive), moving the content with the cursor.
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(dx))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(dy))
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override name)
        if event.button() == self._drag_button:
            self._drag_button = Qt.NoButton
            self._last_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ResultsViewerPanel(QWidget):
    """
    Host-agnostic viewer surface for DTI-ALPS processing results.

    A self-sufficient ``QWidget`` (no menu bar) holding the subject list, DEC
    image pane and legend, navigation/zoom controls, the "Show ROIs" toggle, and
    the ALPS metrics panel. It is the adapter for the unchanged
    :class:`ViewerModel` and is shared by both hosts: the standalone
    :class:`ResultsViewer` window and the main app's docked "Results Viewing"
    page. Loading is on demand via :meth:`load_folder` (or the panel's own "Load
    Folder..." button).
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # The tk-free session model owns the loaded session, the per-ROI-type
        # CSV cache, the current selection, and the current subject's arrays.
        self.model = ViewerModel()

        # View cursor (adapter-owned, transient): the current slice, zoom, and
        # window/level. The current view / show-ROIs live in their widgets
        # below. Window/level is the twin of slice and zoom (Decision 8): it is
        # transient view-cursor state, never held in the model. Seeded from
        # ``model.default_window()`` at subject-select.
        self.current_slice = 0
        self.zoom_level = 1.0
        self.wl_center = 0.5
        self.wl_width = 1.0

        # ALPS method the metrics labels are currently laid out for.
        self.alps_method = "ALPS-LAB"
        self._labels_built_for_method = "ALPS-LAB"

        # Ordered (token, display label) ROI options for the combobox.
        self._roi_options: list[tuple[str, str]] = []
        # Subject ids present in the tree (defensive guard for selection).
        self._known_ids: set[str] = set()

        self._create_layout()

    def load_folder(self, folder_path: str):
        """Load a results folder into the panel (the public external entry point).

        Both hosts call this: the standalone wrapper on construction when given a
        folder, and the main app when a "View Results" button is clicked.
        """
        self._load_output_folder(folder_path)

    def _create_layout(self):
        """Create main layout."""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Left panel - Subject list
        left_panel = QWidget()
        left_panel.setFixedWidth(280)
        self._create_subject_panel(left_panel)
        main_layout.addWidget(left_panel)

        # Right panel - Image and controls
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(right_panel, stretch=1)

        # Top: Image view
        image_group = QGroupBox("Image View")
        image_layout = QVBoxLayout(image_group)
        self._create_image_pane(image_layout)
        right_layout.addWidget(image_group, stretch=1)

        # Bottom: Controls and metrics
        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        self._create_controls(bottom_layout)
        self._create_metrics_panel(bottom_layout)
        right_layout.addWidget(bottom)

    def _create_subject_panel(self, parent: QWidget):
        """Create subject list panel."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 5, 0)

        # Header with load button
        header = QHBoxLayout()
        header.addWidget(_bold(QLabel("Subjects")))
        header.addStretch(1)
        load_button = QPushButton("Load Folder...")
        load_button.clicked.connect(self._browse_folder)
        header.addWidget(load_button)
        layout.addLayout(header)

        # Subject list
        self.subject_tree = QTreeWidget()
        self.subject_tree.setColumnCount(1)
        self.subject_tree.setHeaderLabels(["Subject ID"])
        self.subject_tree.setRootIsDecorated(False)
        self.subject_tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.subject_tree.setColumnWidth(0, 180)
        self.subject_tree.itemSelectionChanged.connect(self._on_subject_select)
        layout.addWidget(self.subject_tree)

    def _create_image_pane(self, layout: QVBoxLayout):
        """Create the QGraphicsView image display + legend."""
        self.scene = QGraphicsScene(self)
        self.view = _ImageView(self._on_wheel, self._on_window_level_drag, self._on_zoom_drag)
        self.view.setScene(self.scene)
        self.view.setBackgroundBrush(QColor("black"))
        self.view.setAlignment(Qt.AlignCenter)

        self.pixmap_item = QGraphicsPixmapItem()
        # FastTransformation == nearest-neighbour: keep voxel/ROI edges crisp.
        self.pixmap_item.setTransformationMode(Qt.FastTransformation)
        self.scene.addItem(self.pixmap_item)

        layout.addWidget(self.view, stretch=1)
        self._create_legend(layout)

    def _create_legend(self, layout: QVBoxLayout):
        """Create ROI indicator legend (white swatch == ROI) + show-ROIs toggle."""
        legend = QHBoxLayout()
        swatch = QLabel()
        swatch.setFixedSize(16, 16)
        swatch.setStyleSheet("background-color: white; border: 1px solid gray;")
        legend.addWidget(swatch)
        legend.addWidget(QLabel("ROI regions"))
        legend.addStretch(1)

        # Show-ROIs toggle lives in the panel body (no menu bar); it drives the
        # same show-ROIs state the render path consumes.
        self.show_rois_check = QCheckBox("Show ROIs")
        self.show_rois_check.setChecked(True)
        self.show_rois_check.toggled.connect(self._update_display)
        legend.addWidget(self.show_rois_check)

        # Brain-mask toggle: blackens out-of-brain voxels for a focused view.
        # Default on; disabled for a subject that has no brain mask on disk.
        self.brain_mask_check = QCheckBox("Brain mask")
        self.brain_mask_check.setChecked(True)
        self.brain_mask_check.toggled.connect(self._update_display)
        legend.addWidget(self.brain_mask_check)

        layout.addLayout(legend)

    def _create_controls(self, parent_layout: QHBoxLayout):
        """Create slice navigation controls."""
        group = QGroupBox("Navigation")
        layout = QVBoxLayout(group)

        # ROI Type selection
        roi_row = QHBoxLayout()
        roi_row.addWidget(QLabel("ROI Type:"))
        self.roi_type_combo = QComboBox()
        self.roi_type_combo.setMinimumWidth(160)
        self.roi_type_combo.currentIndexChanged.connect(self._on_roi_type_change)
        roi_row.addWidget(self.roi_type_combo)
        roi_row.addStretch(1)
        layout.addLayout(roi_row)

        # View selection
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View:"))
        self.view_group = QButtonGroup(self)
        self.view_buttons: dict[str, QRadioButton] = {}
        for view in ("axial", "coronal", "sagittal"):
            button = QRadioButton(view.capitalize())
            if view == "axial":
                button.setChecked(True)
            button.toggled.connect(self._on_view_toggled)
            self.view_group.addButton(button)
            self.view_buttons[view] = button
            view_row.addWidget(button)
        view_row.addStretch(1)
        layout.addLayout(view_row)

        # Slice slider
        slice_row = QHBoxLayout()
        slice_row.addWidget(QLabel("Slice:"))
        self.slice_slider = QSlider(Qt.Horizontal)
        self.slice_slider.setRange(0, 100)
        self.slice_slider.setMinimumWidth(150)
        self.slice_slider.valueChanged.connect(self._on_slice_change)
        slice_row.addWidget(self.slice_slider)
        self.slice_label = QLabel("0 / 0")
        slice_row.addWidget(self.slice_label)
        layout.addLayout(slice_row)

        # Zoom controls: a geometric 10%-800% slider (the visible zoom
        # affordance, Decision 5) + a zoom-% label, and a "Reset view" button
        # that re-fits the zoom and restores the default window (Decision 4).
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom:"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, _ZOOM_SLIDER_MAX)
        self.zoom_slider.setMinimumWidth(150)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        zoom_row.addWidget(self.zoom_slider)
        self.zoom_label = QLabel("100%")
        zoom_row.addWidget(self.zoom_label)
        reset_view = QPushButton("Reset view")
        reset_view.clicked.connect(self._reset_view)
        zoom_row.addWidget(reset_view)
        zoom_row.addStretch(1)
        layout.addLayout(zoom_row)

        layout.addStretch(1)
        parent_layout.addWidget(group)

    def _create_metrics_panel(self, parent_layout: QHBoxLayout):
        """Create ALPS metrics display panel."""
        group = QGroupBox("ALPS Metrics")
        layout = QVBoxLayout(group)

        # Subject / method info row
        info = QHBoxLayout()
        info.addWidget(_bold(QLabel("Subject:")))
        self.subject_info_label = QLabel("None selected")
        info.addWidget(self.subject_info_label)
        info.addSpacing(20)
        info.addWidget(_bold(QLabel("Method:")))
        self.method_label = QLabel("--")
        info.addWidget(self.method_label)
        info.addStretch(1)
        layout.addLayout(info)

        # ALPS values container (rebuilt based on method).
        self.values_widget = QWidget()
        self.values_layout = QGridLayout(self.values_widget)
        self.values_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.values_widget)

        # Initialize with default layout
        self._build_metrics_labels("ALPS-LAB")

        # Status row
        status = QHBoxLayout()
        status.addWidget(QLabel("Status:"))
        self.status_value_label = QLabel("--")
        status.addWidget(self.status_value_label)
        status.addStretch(1)
        layout.addLayout(status)

        layout.addStretch(1)
        parent_layout.addWidget(group, stretch=1)

    def _build_metrics_labels(self, alps_method: str):
        """Build metrics labels based on ALPS method."""
        _clear_layout(self.values_layout)

        # Track which method labels are built for.
        self._labels_built_for_method = alps_method

        grid = self.values_layout
        if alps_method == "Both":
            # ALPS-LAB row
            grid.addWidget(_bold(QLabel("ALPS-LAB:")), 0, 0)
            grid.addWidget(QLabel("L:"), 0, 1)
            self.alps_lab_left_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_lab_left_label, 0, 2)
            grid.addWidget(QLabel("R:"), 0, 3)
            self.alps_lab_right_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_lab_right_label, 0, 4)
            grid.addWidget(QLabel("Bi:"), 0, 5)
            self.alps_lab_combined_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_lab_combined_label, 0, 6)

            # ALPS-PAS row
            grid.addWidget(_bold(QLabel("ALPS-PAS:")), 1, 0)
            grid.addWidget(QLabel("L:"), 1, 1)
            self.alps_pas_left_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_pas_left_label, 1, 2)
            grid.addWidget(QLabel("R:"), 1, 3)
            self.alps_pas_right_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_pas_right_label, 1, 4)
            grid.addWidget(QLabel("Bi:"), 1, 5)
            self.alps_pas_combined_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_pas_combined_label, 1, 6)
        else:
            # Single method (ALPS-LAB or ALPS-PAS)
            method_suffix = "LAB" if alps_method == "ALPS-LAB" else "PAS"
            grid.addWidget(QLabel(f"Left ALPS-{method_suffix}:"), 0, 0)
            self.alps_left_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_left_label, 0, 1)
            grid.addWidget(QLabel(f"Right ALPS-{method_suffix}:"), 0, 2)
            self.alps_right_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_right_label, 0, 3)
            grid.addWidget(QLabel("Combined:"), 0, 4)
            self.alps_combined_label = _bold(QLabel("--"))
            grid.addWidget(self.alps_combined_label, 0, 5)

    def _browse_folder(self):
        """Open folder browser and load results."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_VIEWER_FOLDER)
        folder = QFileDialog.getExistingDirectory(
            self, "Select DTI-ALPS Output Folder", initial_dir
        )
        if folder:
            user_config.set_from_path(UserConfig.KEY_VIEWER_FOLDER, folder)
            self._load_output_folder(folder)

    def _load_output_folder(self, folder_path: str):
        """Load all results from an output folder via the model."""
        result = self.model.load_session(folder_path)
        if isinstance(result, LoadError):
            self._show_load_error(result)
            return
        session = result

        # Update ROI type combobox from the (token, label) pairs. Block signals
        # so the programmatic repopulation does not fire _on_roi_type_change.
        self._roi_options = session.roi_options
        display_names = [label for _, label in session.roi_options]
        self.roi_type_combo.blockSignals(True)
        self.roi_type_combo.clear()
        self.roi_type_combo.addItems(display_names)
        self.roi_type_combo.setCurrentIndex(0)
        self.roi_type_combo.blockSignals(False)

        # Rebuild metrics labels if the detected method differs from the layout.
        self.alps_method = session.method
        if session.method != self._labels_built_for_method:
            self._build_metrics_labels(session.method)

        # Rebuild the subject tree.
        self._known_ids = {record.subject_id for record in session.subjects}
        self.subject_tree.blockSignals(True)
        self.subject_tree.clear()
        for record in session.subjects:
            QTreeWidgetItem(self.subject_tree, [record.subject_id])
        self.subject_tree.blockSignals(False)

        if not session.subjects:
            QMessageBox.information(
                self, "Info", "No valid subject folders found in the output directory."
            )
        else:
            # Select first subject (fires _on_subject_select).
            self.subject_tree.setCurrentItem(self.subject_tree.topLevelItem(0))

    def _show_load_error(self, error: LoadError):
        """Map a typed load error to its message box (adapter owns the phrasing)."""
        if error.kind == "folder_missing":
            QMessageBox.critical(self, "Error", f"Folder does not exist:\n{error.payload}")
        elif error.kind == "no_results":
            QMessageBox.critical(
                self,
                "Error",
                f"No ROI directories or alps_results.csv found in:\n{error.payload}\n\n"
                "Is this a valid output folder?",
            )
        elif error.kind == "csv_missing":
            QMessageBox.critical(
                self,
                "Error",
                f"No results CSV found at:\n{error.payload}\n\nIs this a valid output folder?",
            )

    def _on_subject_select(self):
        """Handle subject selection in tree."""
        items = self.subject_tree.selectedItems()
        if items:
            self._select_subject(items[0].text(0))

    def _select_subject(self, subject_id: str):
        """Load and display a subject's data."""
        if subject_id not in self._known_ids:
            return

        # The model makes the subject current and decodes its arrays; metrics are
        # shown regardless, so a load failure still updates the panel.
        loaded = self.model.select_subject(subject_id)
        self._update_metrics_display()

        if not loaded:
            QMessageBox.warning(self, "Warning", f"Could not load images for subject: {subject_id}")
            return

        # Honest UI: only offer the brain-mask toggle when this subject has one.
        self.brain_mask_check.setEnabled(self.model.has_brain_mask)

        # Seed the window/level from the new subject's FA volume (stable, per
        # subject; window settings are not carried across subjects, Decision 3).
        self.wl_center, self.wl_width = self.model.default_window()

        # Start on the slice holding the Left Projection ROI centroid so the ROI
        # is visible immediately (falls back to the middle slice when absent).
        if self.model.current_shape:
            self._update_slice_range()
            self.current_slice = self.model.initial_slice(self.current_view())
            self._set_slider_value(self.current_slice)

        # Auto-fit image to viewport, then draw.
        self._zoom_fit()
        self._update_display()

    def _update_metrics_display(self):
        """Update ALPS metrics labels for the current subject."""
        metrics = self.model.current_metrics()
        if metrics is None:
            self.subject_info_label.setText("None selected")
            self.method_label.setText("--")
            self.status_value_label.setText("--")
            # Reset all labels based on current layout
            self._build_metrics_labels(self.alps_method)
            return

        self.subject_info_label.setText(metrics.subject_id)
        self.method_label.setText(metrics.subject_method if metrics.subject_method else "--")

        # Rebuild metrics labels if the current ROI type's method changed.
        method = self.model.current_alps_method
        if method != self._labels_built_for_method:
            self._build_metrics_labels(method)
        self.alps_method = method

        # Update values based on method
        if self.alps_method == "Both":
            self.alps_lab_left_label.setText(self._fmt(metrics.lab_left))
            self.alps_lab_right_label.setText(self._fmt(metrics.lab_right))
            self.alps_lab_combined_label.setText(self._fmt(metrics.lab_combined))
            self.alps_pas_left_label.setText(self._fmt(metrics.pas_left))
            self.alps_pas_right_label.setText(self._fmt(metrics.pas_right))
            self.alps_pas_combined_label.setText(self._fmt(metrics.pas_combined))
        elif self.alps_method == "ALPS-PAS":
            self.alps_left_label.setText(self._fmt(metrics.pas_left))
            self.alps_right_label.setText(self._fmt(metrics.pas_right))
            self.alps_combined_label.setText(self._fmt(metrics.pas_combined))
        else:  # ALPS-LAB
            self.alps_left_label.setText(self._fmt(metrics.lab_left))
            self.alps_right_label.setText(self._fmt(metrics.lab_right))
            self.alps_combined_label.setText(self._fmt(metrics.lab_combined))

        self.status_value_label.setText(metrics.status if metrics.status else "--")

    @staticmethod
    def _fmt(value: float | None) -> str:
        """Format an ALPS value for display, or ``--`` when absent."""
        return f"{value:.4f}" if value is not None else "--"

    def _get_num_slices(self) -> int:
        """Get number of slices for current view."""
        return self.model.num_slices(self.current_view())

    def _update_slice_range(self):
        """Update slice slider range for current view."""
        num_slices = self._get_num_slices()
        if num_slices > 0:
            self.slice_slider.blockSignals(True)
            self.slice_slider.setMaximum(num_slices - 1)
            self.slice_slider.blockSignals(False)
            self.slice_label.setText(f"{self.current_slice} / {num_slices - 1}")

    def _set_slider_value(self, value: int):
        """Move the slider thumb without firing _on_slice_change."""
        self.slice_slider.blockSignals(True)
        self.slice_slider.setValue(value)
        self.slice_slider.blockSignals(False)

    def _update_display(self):
        """Update the image display."""
        if self.model.current_shape is None:
            self.pixmap_item.setPixmap(QPixmap())
            return

        # The model returns a finished, oriented RGB picture for this view/slice.
        image = self.model.render_slice(
            self.current_view(),
            self.current_slice,
            self.show_rois_check.isChecked(),
            self.brain_mask_check.isChecked(),
            self.wl_center,
            self.wl_width,
        )
        if image is None:
            return

        self._display_image(image)

        # Update slice label
        num_slices = self._get_num_slices()
        self.slice_label.setText(f"{self.current_slice} / {num_slices - 1}")

    def _display_image(self, image):
        """Place a finished RGB picture on the scene; the view transform zooms it."""
        arr = np.ascontiguousarray(image)
        h, w = arr.shape[:2]
        qimage = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB888)
        # QPixmap.fromImage copies the buffer, so it is safe once arr is freed.
        self.pixmap_item.setPixmap(QPixmap.fromImage(qimage))
        self.scene.setSceneRect(self.pixmap_item.boundingRect())

    def _on_roi_type_change(self, _index=None):
        """Handle ROI type selection change."""
        if not self._roi_options:
            return

        # Resolve the selected display name back to its token via the pairs.
        selected_display = self.roi_type_combo.currentText()
        token = next((t for t, label in self._roi_options if label == selected_display), None)
        if token is None or token == self.model.current_roi_type:
            return

        # The model switches type, loads that CSV (if present), and reloads the
        # current subject's ROI masks for the new type.
        self.model.set_roi_type(token)

        method = self.model.current_alps_method
        self.alps_method = method
        if method != self._labels_built_for_method:
            self._build_metrics_labels(method)

        # Refresh metrics + image for the current subject.
        if self.model.current_subject_id is not None:
            self._update_metrics_display()
            self._update_display()

    def _on_view_toggled(self, checked: bool):
        """A radio button toggled; react only to the newly-checked one."""
        if checked:
            self._on_view_change()

    def _on_view_change(self):
        """Handle view type change."""
        self._update_slice_range()
        # Anchor on the Left Projection ROI centroid for the new view (falls back
        # to the middle slice when that ROI is absent), keeping the ROI on screen.
        self.current_slice = self.model.initial_slice(self.current_view())
        self._set_slider_value(self.current_slice)
        # Re-fit: axial/coronal/sagittal have very different in-plane dimensions,
        # so a zoom that fit the old view can spill the new one out of frame
        # (Decision 7). Window is unchanged (same subject/volume).
        self._zoom_fit()
        self._update_display()

    def _on_slice_change(self, value):
        """Handle slice slider change."""
        self.current_slice = int(value)
        self._update_display()

    def _on_wheel(self, delta: int):
        """Handle mouse wheel for slice scrolling."""
        num_slices = self._get_num_slices()
        if num_slices > 0:
            new_slice = max(0, min(num_slices - 1, self.current_slice + delta))
            if new_slice != self.current_slice:
                self.current_slice = new_slice
                self._set_slider_value(self.current_slice)
                self._update_display()

    def _on_window_level_drag(self, dx: float, dy: float):
        """Left-drag: horizontal delta widens/narrows the window (contrast),
        vertical delta shifts the level/center (brightness).

        Moving up (``dy`` negative) lowers the center, mapping more of the FA
        range bright -- i.e. up brightens. Width is clamped to a small positive
        minimum so a drag never produces a zero-width window (Decision 8).
        """
        if self.model.current_shape is None:
            return
        self.wl_center += dy * _WL_LEVEL_PER_PIXEL
        self.wl_width = max(_WL_MIN_WIDTH, self.wl_width + dx * _WL_WIDTH_PER_PIXEL)
        self._update_display()

    def _on_zoom_drag(self, dy: float):
        """Right-drag: vertical delta zooms geometrically (up = in, down = out),
        center-anchored, syncing the slider thumb via :meth:`_set_zoom`."""
        if self.model.current_shape is None:
            return
        self._set_zoom(self.zoom_level * math.exp(-dy * _ZOOM_DRAG_PER_PIXEL))

    def current_view(self) -> str:
        """The currently selected orthogonal view."""
        for name, button in self.view_buttons.items():
            if button.isChecked():
                return name
        return "axial"

    def _apply_zoom(self):
        """Apply the current zoom as the view transform + update the % label.

        Does not move the slider thumb; callers that changed the zoom other than
        via the slider call :meth:`_sync_zoom_slider` to keep the two in sync.
        """
        self.view.setTransform(QTransform().scale(self.zoom_level, self.zoom_level))
        self.zoom_label.setText(f"{int(round(self.zoom_level * 100))}%")

    def _sync_zoom_slider(self):
        """Move the zoom slider thumb to match ``zoom_level`` without recursing."""
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(_zoom_to_slider_pos(self.zoom_level))
        self.zoom_slider.blockSignals(False)

    def _set_zoom(self, zoom: float):
        """Set a clamped zoom, apply the transform, and sync the slider thumb.

        The one entry point for zoom changes that originate outside the slider
        (fit and right-drag), keeping the slider and the zoom scalar in lockstep.
        """
        self.zoom_level = _clamp_zoom(zoom)
        self._apply_zoom()
        self._sync_zoom_slider()

    def _on_zoom_slider(self, value: int):
        """Handle the zoom slider moving: drive the shared zoom scalar."""
        self.zoom_level = _slider_pos_to_zoom(value)
        self._apply_zoom()

    def _reset_view(self):
        """Re-fit the zoom to the viewport and restore the default window."""
        self._zoom_fit()
        self.wl_center, self.wl_width = self.model.default_window()
        self._update_display()

    def _zoom_fit(self):
        """Fit image to viewport (best-effort), clamped into the zoom band."""
        shape = self.model.current_shape
        if shape is None:
            return

        view = self.current_view()
        if view == "axial":
            img_w, img_h = shape[0], shape[1]
        elif view == "coronal":
            img_w, img_h = shape[0], shape[2]
        else:
            img_w, img_h = shape[1], shape[2]

        canvas_w = self.view.viewport().width()
        canvas_h = self.view.viewport().height()

        if img_w > 0 and img_h > 0:
            zoom_w = canvas_w / img_w
            zoom_h = canvas_h / img_h
            self._set_zoom(min(zoom_w, zoom_h) * 0.9)


class ResultsViewer(QMainWindow):
    """
    Standalone window host for :class:`ResultsViewerPanel`.

    A thin wrapper: it instantiates a panel as the central widget, sets the
    window title/size, and forwards an optional initial folder. It has no menu
    bar — every control lives in the panel body, and the OS window chrome closes
    the window.
    """

    def __init__(self, output_folder: str | None = None, parent=None):
        """
        Initialize the standalone results viewer window.

        Args:
            output_folder: Path to output folder to load immediately (optional)
            parent: Parent Qt widget (optional)
        """
        super().__init__(parent)

        self.setWindowTitle("DTI-ALPS Results Viewer")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)

        self.panel = ResultsViewerPanel(self)
        self.setCentralWidget(self.panel)

        # Load output folder if provided. Defer so the window is realized first:
        # the panel's initial fit reads the viewport size, meaningful only once
        # the window is shown (fit-timing guard).
        if output_folder:
            QTimer.singleShot(100, lambda: self.panel.load_folder(output_folder))


def launch_viewer(output_folder: str | None = None):
    """Launch the results viewer as a standalone application."""
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv)

    viewer = ResultsViewer(output_folder=output_folder)
    viewer.show()

    if owns_app:
        app.exec()
    return viewer


if __name__ == "__main__":
    launch_viewer()
