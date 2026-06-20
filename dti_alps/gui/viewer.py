"""
Results Viewer for DTI-ALPS processing output.

Displays FA-modulated RGB direction-encoded color (DEC) images with ROI overlays.

This module is the **Tkinter adapter**: it reads widgets, calls
:class:`~dti_alps.gui.viewer_model.ViewerModel` (a tk-free presentation model)
and ``results_layout`` (the on-disk contract), and applies the returned plain
data and finished NumPy pictures to widgets. It owns all phrasing, dialog type,
zoom, and canvas placement; the session logic, the ALPS/CSV parsing, and the
DEC rendering math live in the model and the engine leaf (PRD 0005).
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .user_config import UserConfig, get_user_config
from .viewer_model import LoadError, ViewerModel


class ResultsViewer(tk.Toplevel):
    """
    Viewer for DTI-ALPS processing results.

    Displays FA-modulated RGB DEC images with ROI overlays and ALPS metrics.
    """

    def __init__(self, parent=None, output_folder: str | None = None):
        """
        Initialize the results viewer.

        Args:
            parent: Parent Tk window (optional)
            output_folder: Path to output folder to load immediately (optional)
        """
        super().__init__(parent)

        self.title("DTI-ALPS Results Viewer")
        self.geometry("1400x900")
        self.minsize(1000, 700)

        # The tk-free session model owns the loaded session, the per-ROI-type
        # CSV cache, the current selection, and the current subject's arrays.
        self.model = ViewerModel()

        # View cursor (adapter-owned, transient): the current slice and zoom.
        # The current view / show-ROIs live in their Tk vars below.
        self.current_slice = 0
        self.zoom_level = 1.0

        # ALPS method the metrics labels are currently laid out for.
        self.alps_method = "ALPS-LAB"
        self._labels_built_for_method = "ALPS-LAB"

        # Ordered (token, display label) ROI options for the combobox.
        self._roi_options: list[tuple[str, str]] = []
        # Subject ids present in the tree (defensive guard for selection).
        self._known_ids: set[str] = set()

        # Image display
        self._photo_image: ImageTk.PhotoImage | None = None

        # Build UI
        self._create_menu()
        self._create_layout()

        # Load output folder if provided
        if output_folder:
            self.after(100, lambda: self._load_output_folder(output_folder))

    def _create_menu(self):
        """Create menu bar."""
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Load Results Folder...", command=self._browse_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self.destroy)

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Axial View", command=lambda: self._set_view("axial"))
        view_menu.add_command(label="Coronal View", command=lambda: self._set_view("coronal"))
        view_menu.add_command(label="Sagittal View", command=lambda: self._set_view("sagittal"))
        view_menu.add_separator()

        self.show_rois_var = tk.BooleanVar(value=True)
        view_menu.add_checkbutton(
            label="Show ROI Overlays", variable=self.show_rois_var, command=self._update_display
        )

    def _create_layout(self):
        """Create main layout."""
        # Main container
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left panel - Subject list
        left_panel = ttk.Frame(main_frame, width=280)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_panel.pack_propagate(False)

        self._create_subject_panel(left_panel)

        # Right panel - Image and controls
        right_panel = ttk.Frame(main_frame)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top: Image canvas
        canvas_frame = ttk.LabelFrame(right_panel, text="Image View", padding=5)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self._create_canvas(canvas_frame)

        # Bottom: Controls and metrics
        bottom_frame = ttk.Frame(right_panel)
        bottom_frame.pack(fill=tk.X)

        self._create_controls(bottom_frame)
        self._create_metrics_panel(bottom_frame)

    def _create_subject_panel(self, parent):
        """Create subject list panel."""
        # Header with load button
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(header_frame, text="Subjects", font=("TkDefaultFont", 10, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Button(header_frame, text="Load Folder...", command=self._browse_folder).pack(
            side=tk.RIGHT
        )

        # Subject list
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("subject", "status")
        self.subject_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )

        self.subject_tree.heading("subject", text="Subject ID")
        self.subject_tree.heading("status", text="Status")
        self.subject_tree.column("subject", width=180)
        self.subject_tree.column("status", width=70)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.subject_tree.yview)
        self.subject_tree.configure(yscrollcommand=scrollbar.set)

        self.subject_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind selection event
        self.subject_tree.bind("<<TreeviewSelect>>", self._on_subject_select)

    def _create_canvas(self, parent):
        """Create image display canvas."""
        # Canvas with scrollbars for large images
        canvas_container = ttk.Frame(parent)
        canvas_container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_container, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Bind mouse wheel for slice scrolling
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down

        # Legend frame
        self.legend_frame = ttk.Frame(parent)
        self.legend_frame.pack(fill=tk.X, pady=(5, 0))

        self._create_legend()

    def _create_legend(self):
        """Create ROI indicator legend."""
        # Simple legend showing white = ROI
        frame = ttk.Frame(self.legend_frame)
        frame.pack(side=tk.LEFT, padx=5)

        # White swatch
        swatch = tk.Canvas(frame, width=16, height=16, highlightthickness=1)
        swatch.pack(side=tk.LEFT, padx=(0, 5))
        swatch.create_rectangle(0, 0, 16, 16, fill="white", outline="gray")

        ttk.Label(frame, text="ROI regions", font=("TkDefaultFont", 9)).pack(side=tk.LEFT)

    def _create_controls(self, parent):
        """Create slice navigation controls."""
        controls_frame = ttk.LabelFrame(parent, text="Navigation", padding=5)
        controls_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # ROI Type selection
        roi_frame = ttk.Frame(controls_frame)
        roi_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(roi_frame, text="ROI Type:").pack(side=tk.LEFT)
        self.roi_type_var = tk.StringVar(value="rois")
        self.roi_type_combo = ttk.Combobox(
            roi_frame,
            textvariable=self.roi_type_var,
            state="readonly",
            width=20,
        )
        self.roi_type_combo.pack(side=tk.LEFT, padx=5)
        self.roi_type_combo.bind("<<ComboboxSelected>>", self._on_roi_type_change)

        # View selection
        view_frame = ttk.Frame(controls_frame)
        view_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(view_frame, text="View:").pack(side=tk.LEFT)
        self.view_var = tk.StringVar(value="axial")
        for view in ["axial", "coronal", "sagittal"]:
            ttk.Radiobutton(
                view_frame,
                text=view.capitalize(),
                value=view,
                variable=self.view_var,
                command=self._on_view_change,
            ).pack(side=tk.LEFT, padx=3)

        # Slice slider
        slice_frame = ttk.Frame(controls_frame)
        slice_frame.pack(fill=tk.X, pady=5)

        ttk.Label(slice_frame, text="Slice:").pack(side=tk.LEFT)

        self.slice_var = tk.IntVar(value=0)
        self.slice_slider = ttk.Scale(
            slice_frame,
            from_=0,
            to=100,
            variable=self.slice_var,
            orient=tk.HORIZONTAL,
            command=self._on_slice_change,
            length=150,
        )
        self.slice_slider.pack(side=tk.LEFT, padx=5)

        self.slice_label = ttk.Label(slice_frame, text="0 / 0")
        self.slice_label.pack(side=tk.LEFT)

        # Zoom controls
        zoom_frame = ttk.Frame(controls_frame)
        zoom_frame.pack(fill=tk.X, pady=5)

        ttk.Label(zoom_frame, text="Zoom:").pack(side=tk.LEFT)
        ttk.Button(zoom_frame, text="-", width=3, command=self._zoom_out).pack(side=tk.LEFT, padx=2)

        self.zoom_label = ttk.Label(zoom_frame, text="100%", width=6)
        self.zoom_label.pack(side=tk.LEFT)

        ttk.Button(zoom_frame, text="+", width=3, command=self._zoom_in).pack(side=tk.LEFT, padx=2)
        ttk.Button(zoom_frame, text="Fit", width=4, command=self._zoom_fit).pack(
            side=tk.LEFT, padx=5
        )

    def _create_metrics_panel(self, parent):
        """Create ALPS metrics display panel."""
        self.metrics_frame = ttk.LabelFrame(parent, text="ALPS Metrics", padding=10)
        self.metrics_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Subject info
        info_frame = ttk.Frame(self.metrics_frame)
        info_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(info_frame, text="Subject:", font=("TkDefaultFont", 9, "bold")).pack(side=tk.LEFT)
        self.subject_info_label = ttk.Label(info_frame, text="None selected")
        self.subject_info_label.pack(side=tk.LEFT, padx=5)

        # Method info
        ttk.Label(info_frame, text="  Method:", font=("TkDefaultFont", 9, "bold")).pack(
            side=tk.LEFT, padx=(20, 0)
        )
        self.method_label = ttk.Label(info_frame, text="--")
        self.method_label.pack(side=tk.LEFT, padx=5)

        # ALPS values container (will be rebuilt based on method)
        self.values_frame = ttk.Frame(self.metrics_frame)
        self.values_frame.pack(fill=tk.X)

        # Initialize with default layout
        self._build_metrics_labels("ALPS-LAB")

        # Status
        status_frame = ttk.Frame(self.metrics_frame)
        status_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(status_frame, text="Status:").pack(side=tk.LEFT)
        self.status_value_label = ttk.Label(status_frame, text="--")
        self.status_value_label.pack(side=tk.LEFT, padx=5)

    def _build_metrics_labels(self, alps_method: str):
        """Build metrics labels based on ALPS method."""
        # Clear existing widgets
        for widget in self.values_frame.winfo_children():
            widget.destroy()

        # Track which method labels are built for
        self._labels_built_for_method = alps_method

        if alps_method == "Both":
            # ALPS-LAB row
            ttk.Label(self.values_frame, text="ALPS-LAB:", font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=0, sticky=tk.W, padx=(0, 5)
            )
            ttk.Label(self.values_frame, text="L:", font=("TkDefaultFont", 9)).grid(
                row=0, column=1, sticky=tk.W
            )
            self.alps_lab_left_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_lab_left_label.grid(row=0, column=2, sticky=tk.W, padx=(0, 10))
            ttk.Label(self.values_frame, text="R:", font=("TkDefaultFont", 9)).grid(
                row=0, column=3, sticky=tk.W
            )
            self.alps_lab_right_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_lab_right_label.grid(row=0, column=4, sticky=tk.W, padx=(0, 10))
            ttk.Label(self.values_frame, text="Bi:", font=("TkDefaultFont", 9)).grid(
                row=0, column=5, sticky=tk.W
            )
            self.alps_lab_combined_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_lab_combined_label.grid(row=0, column=6, sticky=tk.W)

            # ALPS-PAS row
            ttk.Label(self.values_frame, text="ALPS-PAS:", font=("TkDefaultFont", 9, "bold")).grid(
                row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0)
            )
            ttk.Label(self.values_frame, text="L:", font=("TkDefaultFont", 9)).grid(
                row=1, column=1, sticky=tk.W, pady=(5, 0)
            )
            self.alps_pas_left_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_pas_left_label.grid(row=1, column=2, sticky=tk.W, padx=(0, 10), pady=(5, 0))
            ttk.Label(self.values_frame, text="R:", font=("TkDefaultFont", 9)).grid(
                row=1, column=3, sticky=tk.W, pady=(5, 0)
            )
            self.alps_pas_right_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_pas_right_label.grid(row=1, column=4, sticky=tk.W, padx=(0, 10), pady=(5, 0))
            ttk.Label(self.values_frame, text="Bi:", font=("TkDefaultFont", 9)).grid(
                row=1, column=5, sticky=tk.W, pady=(5, 0)
            )
            self.alps_pas_combined_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 10, "bold")
            )
            self.alps_pas_combined_label.grid(row=1, column=6, sticky=tk.W, pady=(5, 0))
        else:
            # Single method (ALPS-LAB or ALPS-PAS)
            method_suffix = "LAB" if alps_method == "ALPS-LAB" else "PAS"
            ttk.Label(
                self.values_frame, text=f"Left ALPS-{method_suffix}:", font=("TkDefaultFont", 9)
            ).grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
            ttk.Label(
                self.values_frame, text=f"Right ALPS-{method_suffix}:", font=("TkDefaultFont", 9)
            ).grid(row=0, column=2, sticky=tk.W, padx=(20, 10))
            ttk.Label(self.values_frame, text="Combined:", font=("TkDefaultFont", 9)).grid(
                row=0, column=4, sticky=tk.W, padx=(20, 10)
            )

            self.alps_left_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 11, "bold")
            )
            self.alps_left_label.grid(row=0, column=1, sticky=tk.W)

            self.alps_right_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 11, "bold")
            )
            self.alps_right_label.grid(row=0, column=3, sticky=tk.W)

            self.alps_combined_label = ttk.Label(
                self.values_frame, text="--", font=("TkDefaultFont", 11, "bold")
            )
            self.alps_combined_label.grid(row=0, column=5, sticky=tk.W)

    def _browse_folder(self):
        """Open folder browser and load results."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_VIEWER_FOLDER)
        folder = filedialog.askdirectory(
            title="Select DTI-ALPS Output Folder", initialdir=initial_dir
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

        # Update ROI type combobox from the (token, label) pairs.
        self._roi_options = session.roi_options
        display_names = [label for _, label in session.roi_options]
        self.roi_type_combo["values"] = display_names
        self.roi_type_var.set(display_names[0])

        # Rebuild metrics labels if the detected method differs from the layout.
        self.alps_method = session.method
        if session.method != self._labels_built_for_method:
            self._build_metrics_labels(session.method)

        # Rebuild the subject tree.
        self._known_ids = {record.subject_id for record in session.subjects}
        for item in self.subject_tree.get_children():
            self.subject_tree.delete(item)
        for record in session.subjects:
            status = record.status if record.status else "unknown"
            self.subject_tree.insert(
                "", tk.END, iid=record.subject_id, values=(record.subject_id, status)
            )

        if not session.subjects:
            messagebox.showinfo("Info", "No valid subject folders found in the output directory.")
        else:
            # Select first subject
            first_id = session.subjects[0].subject_id
            self.subject_tree.selection_set(first_id)
            self._select_subject(first_id)

    def _show_load_error(self, error: LoadError):
        """Map a typed load error to its messagebox (adapter owns the phrasing)."""
        if error.kind == "folder_missing":
            messagebox.showerror("Error", f"Folder does not exist:\n{error.payload}")
        elif error.kind == "no_results":
            messagebox.showerror(
                "Error",
                f"No ROI directories or alps_results.csv found in:\n{error.payload}\n\n"
                "Is this a valid output folder?",
            )
        elif error.kind == "csv_missing":
            messagebox.showerror(
                "Error",
                f"No results CSV found at:\n{error.payload}\n\nIs this a valid output folder?",
            )

    def _on_subject_select(self, event):
        """Handle subject selection in tree."""
        selection = self.subject_tree.selection()
        if selection:
            subject_id = selection[0]
            self._select_subject(subject_id)

    def _select_subject(self, subject_id: str):
        """Load and display a subject's data."""
        if subject_id not in self._known_ids:
            return

        # The model makes the subject current and decodes its arrays; metrics are
        # shown regardless, so a load failure still updates the panel.
        loaded = self.model.select_subject(subject_id)
        self._update_metrics_display()

        if not loaded:
            messagebox.showwarning("Warning", f"Could not load images for subject: {subject_id}")
            return

        # Reset slice to middle
        if self.model.current_shape:
            self._update_slice_range()
            self.current_slice = self.model.default_slice(self.view_var.get())
            self.slice_var.set(self.current_slice)

        # Auto-fit image to canvas (after canvas has been sized)
        self.update_idletasks()
        self._zoom_fit()

        self._update_display()

    def _update_metrics_display(self):
        """Update ALPS metrics labels for the current subject."""
        metrics = self.model.current_metrics()
        if metrics is None:
            self.subject_info_label.config(text="None selected")
            self.method_label.config(text="--")
            self.status_value_label.config(text="--")
            # Reset all labels based on current layout
            self._build_metrics_labels(self.alps_method)
            return

        self.subject_info_label.config(text=metrics.subject_id)
        self.method_label.config(text=metrics.subject_method if metrics.subject_method else "--")

        # Rebuild metrics labels if the current ROI type's method changed.
        method = self.model.current_alps_method
        if method != self._labels_built_for_method:
            self._build_metrics_labels(method)
        self.alps_method = method

        # Update values based on method
        if self.alps_method == "Both":
            self.alps_lab_left_label.config(text=self._fmt(metrics.lab_left))
            self.alps_lab_right_label.config(text=self._fmt(metrics.lab_right))
            self.alps_lab_combined_label.config(text=self._fmt(metrics.lab_combined))
            self.alps_pas_left_label.config(text=self._fmt(metrics.pas_left))
            self.alps_pas_right_label.config(text=self._fmt(metrics.pas_right))
            self.alps_pas_combined_label.config(text=self._fmt(metrics.pas_combined))
        elif self.alps_method == "ALPS-PAS":
            self.alps_left_label.config(text=self._fmt(metrics.pas_left))
            self.alps_right_label.config(text=self._fmt(metrics.pas_right))
            self.alps_combined_label.config(text=self._fmt(metrics.pas_combined))
        else:  # ALPS-LAB
            self.alps_left_label.config(text=self._fmt(metrics.lab_left))
            self.alps_right_label.config(text=self._fmt(metrics.lab_right))
            self.alps_combined_label.config(text=self._fmt(metrics.lab_combined))

        self.status_value_label.config(text=metrics.status if metrics.status else "--")

    @staticmethod
    def _fmt(value: float | None) -> str:
        """Format an ALPS value for display, or ``--`` when absent."""
        return f"{value:.4f}" if value is not None else "--"

    def _get_num_slices(self) -> int:
        """Get number of slices for current view."""
        return self.model.num_slices(self.view_var.get())

    def _update_slice_range(self):
        """Update slice slider range for current view."""
        num_slices = self._get_num_slices()
        if num_slices > 0:
            self.slice_slider.config(to=num_slices - 1)
            self.slice_label.config(text=f"{self.current_slice} / {num_slices - 1}")

    def _update_display(self):
        """Update the image display."""
        if self.model.current_shape is None:
            self.canvas.delete("all")
            return

        # The model returns a finished, oriented RGB picture for this view/slice.
        image = self.model.render_slice(
            self.view_var.get(), self.current_slice, self.show_rois_var.get()
        )
        if image is None:
            return

        # Apply zoom and display
        self._display_image(image)

        # Update slice label
        num_slices = self._get_num_slices()
        self.slice_label.config(text=f"{self.current_slice} / {num_slices - 1}")

    def _display_image(self, image):
        """Scale a finished RGB picture by the zoom level and place it on the canvas."""
        h, w = image.shape[:2]
        new_w = max(1, int(w * self.zoom_level))
        new_h = max(1, int(h * self.zoom_level))

        pil_image = Image.fromarray(image, mode="RGB")
        pil_image = pil_image.resize((new_w, new_h), Image.Resampling.NEAREST)

        # Convert to PhotoImage
        self._photo_image = ImageTk.PhotoImage(pil_image)

        # Update canvas
        self.canvas.delete("all")
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        # Center image
        x = max(0, (canvas_w - new_w) // 2)
        y = max(0, (canvas_h - new_h) // 2)

        self.canvas.create_image(x, y, anchor=tk.NW, image=self._photo_image)

    def _on_roi_type_change(self, event=None):
        """Handle ROI type selection change."""
        if not self._roi_options:
            return

        # Resolve the selected display name back to its token via the pairs.
        selected_display = self.roi_type_var.get()
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

    def _on_view_change(self):
        """Handle view type change."""
        self._update_slice_range()
        # Reset to middle slice
        self.current_slice = self.model.default_slice(self.view_var.get())
        self.slice_var.set(self.current_slice)
        self._update_display()

    def _on_slice_change(self, value):
        """Handle slice slider change."""
        self.current_slice = int(float(value))
        self._update_display()

    def _on_mousewheel(self, event):
        """Handle mouse wheel for slice scrolling."""
        # Determine scroll direction
        if event.num == 4 or event.delta > 0:
            delta = 1
        else:
            delta = -1

        num_slices = self._get_num_slices()
        if num_slices > 0:
            new_slice = max(0, min(num_slices - 1, self.current_slice + delta))
            if new_slice != self.current_slice:
                self.current_slice = new_slice
                self.slice_var.set(self.current_slice)
                self._update_display()

    def _set_view(self, view: str):
        """Set the current view type."""
        self.view_var.set(view)
        self._on_view_change()

    def _zoom_in(self):
        """Increase zoom level."""
        self.zoom_level = min(5.0, self.zoom_level * 1.25)
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
        self._update_display()

    def _zoom_out(self):
        """Decrease zoom level."""
        self.zoom_level = max(0.25, self.zoom_level / 1.25)
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
        self._update_display()

    def _zoom_fit(self):
        """Fit image to canvas."""
        shape = self.model.current_shape
        if shape is None:
            return

        view = self.view_var.get()

        if view == "axial":
            img_w, img_h = shape[0], shape[1]
        elif view == "coronal":
            img_w, img_h = shape[0], shape[2]
        else:
            img_w, img_h = shape[1], shape[2]

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if img_w > 0 and img_h > 0:
            zoom_w = canvas_w / img_w
            zoom_h = canvas_h / img_h
            self.zoom_level = min(zoom_w, zoom_h) * 0.9
            self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
            self._update_display()


def launch_viewer(output_folder: str | None = None):
    """Launch the results viewer as a standalone application."""
    root = tk.Tk()
    root.withdraw()  # Hide the root window

    viewer = ResultsViewer(root, output_folder)
    viewer.protocol("WM_DELETE_WINDOW", lambda: (viewer.destroy(), root.destroy()))

    viewer.mainloop()


if __name__ == "__main__":
    launch_viewer()
