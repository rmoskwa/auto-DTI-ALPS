"""
Results Viewer for DTI-ALPS processing output.

Displays FA-modulated RGB direction-encoded color (DEC) images with ROI overlays.
"""

import csv
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import nibabel as nib
import numpy as np
from PIL import Image, ImageTk


@dataclass
class SubjectData:
    """Container for a single subject's loaded data."""

    subject_id: str
    folder_path: Path
    fa_path: Path | None = None
    v1_path: Path | None = None
    roi_paths: dict[str, Path] = field(default_factory=dict)

    # All available ROI sets keyed by ROI type (e.g., "rois", "squarev9", "sphere2p5")
    all_roi_paths: dict[str, dict[str, Path]] = field(default_factory=dict)
    # Currently active ROI type
    active_roi_type: str = "rois"

    # ALPS metrics from CSV - method-specific
    alps_method: str = ""  # "ALPS-LAB", "ALPS-PAS", or "Both"
    alps_lab_left: float | None = None
    alps_lab_right: float | None = None
    alps_lab_combined: float | None = None
    alps_pas_left: float | None = None
    alps_pas_right: float | None = None
    alps_pas_combined: float | None = None
    status: str = ""
    error: str = ""

    # Loaded image data (lazy loaded)
    _fa_data: np.ndarray | None = field(default=None, repr=False)
    _v1_data: np.ndarray | None = field(default=None, repr=False)
    _roi_data: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    _affine: np.ndarray | None = field(default=None, repr=False)

    def load_images(self) -> bool:
        """Load FA and V1 images into memory. Returns True if successful."""
        try:
            if self.fa_path and self.fa_path.exists():
                fa_img = nib.load(self.fa_path)
                self._fa_data = fa_img.get_fdata()
                self._affine = fa_img.affine

            if self.v1_path and self.v1_path.exists():
                v1_img = nib.load(self.v1_path)
                self._v1_data = v1_img.get_fdata()

            # Load ROIs for active type
            self.load_rois_for_type(self.active_roi_type)

            return self._fa_data is not None and self._v1_data is not None
        except Exception as e:
            print(f"Error loading images for {self.subject_id}: {e}")
            return False

    def load_rois_for_type(self, roi_type: str):
        """Load ROI masks for a specific ROI type."""
        self._roi_data.clear()

        # Get paths for the requested type
        if roi_type in self.all_roi_paths:
            paths = self.all_roi_paths[roi_type]
        elif roi_type == "rois" and self.roi_paths:
            # Fallback to default roi_paths for backward compatibility
            paths = self.roi_paths
        else:
            return

        for roi_name, roi_path in paths.items():
            if roi_path.exists():
                try:
                    roi_img = nib.load(roi_path)
                    self._roi_data[roi_name] = roi_img.get_fdata()
                except Exception as e:
                    print(f"Error loading ROI {roi_name}: {e}")

    def unload_images(self):
        """Release image data from memory."""
        self._fa_data = None
        self._v1_data = None
        self._roi_data = {}
        self._affine = None

    @property
    def fa_data(self) -> np.ndarray | None:
        return self._fa_data

    @property
    def v1_data(self) -> np.ndarray | None:
        return self._v1_data

    @property
    def roi_data(self) -> dict[str, np.ndarray]:
        return self._roi_data

    @property
    def shape(self) -> tuple[int, ...] | None:
        if self._fa_data is not None:
            return self._fa_data.shape
        return None


def discover_roi_options(output_folder: Path) -> list[str]:
    """
    Discover available ROI options in an output folder.

    Looks for directories named 'rois' (default) and 'rois_*' (reanalysis).

    Parameters
    ----------
    output_folder : Path
        Path to the DTI-ALPS output folder

    Returns
    -------
    list[str]
        List of available ROI type names, e.g., ['rois', 'squarev9', 'sphere2p5']
    """
    roi_options = []

    # Look in any subject folder for ROI directories
    for subject_folder in output_folder.iterdir():
        if not subject_folder.is_dir():
            continue

        # Check for default rois/ directory
        if (subject_folder / "rois").exists():
            if "rois" not in roi_options:
                roi_options.append("rois")

        # Check for reanalysis directories (rois_*)
        for roi_dir in subject_folder.glob("rois_*"):
            if roi_dir.is_dir():
                # Extract type name (e.g., "squarev9" from "rois_squarev9")
                roi_type = roi_dir.name[5:]  # Remove "rois_" prefix
                if roi_type not in roi_options:
                    roi_options.append(roi_type)

        # Only need to check one subject folder
        if roi_options:
            break

    # Sort with "rois" first, then alphabetically
    if "rois" in roi_options:
        roi_options.remove("rois")
        roi_options = ["rois"] + sorted(roi_options)
    else:
        roi_options = sorted(roi_options)

    return roi_options


def get_roi_display_name(roi_type: str) -> str:
    """Convert ROI type to human-readable display name."""
    if roi_type == "rois":
        return "Default (sphere 3mm)"
    elif roi_type.startswith("sphere"):
        # e.g., "sphere2p5" -> "Sphere 2.5mm"
        radius = roi_type[6:].replace("p", ".")
        return f"Sphere {radius}mm"
    elif roi_type == "squarev9":
        return "Square 3x3 (9 voxels)"
    else:
        return roi_type.replace("_", " ").title()


def get_csv_path_for_roi_type(output_folder: Path, roi_type: str) -> Path:
    """Get the CSV path for a specific ROI type."""
    if roi_type == "rois":
        return output_folder / "alps_results.csv"
    else:
        return output_folder / f"alps_results_{roi_type}.csv"


class ResultsViewer(tk.Toplevel):
    """
    Viewer for DTI-ALPS processing results.

    Displays FA-modulated RGB DEC images with ROI overlays and ALPS metrics.
    """

    # ROI overlay color (solid white with alpha)
    ROI_COLOR = (255, 255, 255, 200)

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

        # State
        self.subjects: dict[str, SubjectData] = {}
        self.current_subject: SubjectData | None = None
        self.current_slice = 0
        self.current_view = "axial"  # axial, coronal, sagittal
        self.show_rois = True
        self.zoom_level = 1.0
        self.alps_method = "ALPS-LAB"  # Detected from CSV
        self._labels_built_for_method = "ALPS-LAB"  # Track which method labels are built for

        # ROI type selection state
        self.output_folder: Path | None = None
        self.available_roi_types: list[str] = []
        self.current_roi_type: str = "rois"
        self.alps_data_by_roi_type: dict[str, dict] = {}  # Cache for CSV data per ROI type

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
        folder = filedialog.askdirectory(title="Select DTI-ALPS Output Folder")
        if folder:
            self._load_output_folder(folder)

    def _load_output_folder(self, folder_path: str):
        """Load all results from an output folder."""
        folder = Path(folder_path)

        if not folder.exists():
            messagebox.showerror("Error", f"Folder does not exist:\n{folder}")
            return

        # Discover available ROI options
        self.available_roi_types = discover_roi_options(folder)
        if not self.available_roi_types:
            # Fallback: check for alps_results.csv even without ROIs
            if not (folder / "alps_results.csv").exists():
                messagebox.showerror(
                    "Error",
                    f"No ROI directories or alps_results.csv found in:\n{folder}\n\n"
                    "Is this a valid output folder?",
                )
                return
            self.available_roi_types = ["rois"]

        # Update ROI type combobox
        display_names = [get_roi_display_name(t) for t in self.available_roi_types]
        self.roi_type_combo["values"] = display_names
        self.current_roi_type = self.available_roi_types[0]
        self.roi_type_var.set(display_names[0])

        # Store output folder
        self.output_folder = folder

        # Clear cached CSV data
        self.alps_data_by_roi_type.clear()

        # Load CSV for current ROI type
        csv_path = get_csv_path_for_roi_type(folder, self.current_roi_type)
        if not csv_path.exists():
            messagebox.showerror(
                "Error",
                f"No results CSV found at:\n{csv_path}\n\nIs this a valid output folder?",
            )
            return

        # Clear existing data
        self.subjects.clear()
        for item in self.subject_tree.get_children():
            self.subject_tree.delete(item)

        # Parse CSV
        alps_data, alps_method = self._parse_alps_csv(csv_path)
        self.alps_method = alps_method  # Store for display
        self.alps_data_by_roi_type[self.current_roi_type] = alps_data

        # Rebuild labels if method changed from initial layout
        if alps_method != self._labels_built_for_method:
            self._build_metrics_labels(alps_method)

        # Find subject folders and match with CSV data
        for subject_folder in sorted(folder.iterdir()):
            if not subject_folder.is_dir():
                continue

            subject_id = subject_folder.name

            # Find FA and V1 files
            fa_files = list(subject_folder.glob("*_FA.nii.gz"))
            v1_files = list(subject_folder.glob("*_V1.nii.gz"))

            if not fa_files or not v1_files:
                continue  # Skip folders without required files

            # Find ROI files for all available ROI types
            all_roi_paths: dict[str, dict[str, Path]] = {}
            for roi_type in self.available_roi_types:
                if roi_type == "rois":
                    roi_dir = subject_folder / "rois"
                else:
                    roi_dir = subject_folder / f"rois_{roi_type}"

                roi_paths = {}
                if roi_dir.exists():
                    for roi_name in ["left_proj", "right_proj", "left_assoc", "right_assoc"]:
                        roi_files = list(roi_dir.glob(f"*_{roi_name}.nii.gz"))
                        if roi_files:
                            roi_paths[roi_name] = roi_files[0]

                if roi_paths:
                    all_roi_paths[roi_type] = roi_paths

            # Default roi_paths for backward compatibility
            default_roi_paths = all_roi_paths.get(self.current_roi_type, {})

            # Create SubjectData
            subject_data = SubjectData(
                subject_id=subject_id,
                folder_path=subject_folder,
                fa_path=fa_files[0],
                v1_path=v1_files[0],
                roi_paths=default_roi_paths,
                all_roi_paths=all_roi_paths,
                active_roi_type=self.current_roi_type,
            )

            # Add ALPS metrics from CSV
            if subject_id in alps_data:
                csv_row = alps_data[subject_id]
                subject_data.alps_method = csv_row.get("alps_method", "")
                subject_data.alps_lab_left = csv_row.get("alps_lab_left")
                subject_data.alps_lab_right = csv_row.get("alps_lab_right")
                subject_data.alps_lab_combined = csv_row.get("alps_lab_combined")
                subject_data.alps_pas_left = csv_row.get("alps_pas_left")
                subject_data.alps_pas_right = csv_row.get("alps_pas_right")
                subject_data.alps_pas_combined = csv_row.get("alps_pas_combined")
                subject_data.status = csv_row.get("status", "")
                subject_data.error = csv_row.get("error", "")

            self.subjects[subject_id] = subject_data

            # Add to tree
            status = subject_data.status if subject_data.status else "unknown"
            self.subject_tree.insert("", tk.END, iid=subject_id, values=(subject_id, status))

        if not self.subjects:
            messagebox.showinfo("Info", "No valid subject folders found in the output directory.")
        else:
            # Select first subject
            first_id = list(self.subjects.keys())[0]
            self.subject_tree.selection_set(first_id)
            self._select_subject(first_id)

    def _parse_alps_csv(self, csv_path: Path) -> tuple[dict, str]:
        """
        Parse alps_results.csv and return dict keyed by subject ID.

        Returns
        -------
        tuple[dict, str]
            (data dict, alps_method detected from column names)
        """
        alps_data = {}
        alps_method = "ALPS-LAB"  # Default

        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []

            # Detect method from column names
            if (
                "Left Hemisphere ALPS-PAS" in fieldnames
                and "Left Hemisphere ALPS-LAB" in fieldnames
            ):
                alps_method = "Both"
            elif "Left Hemisphere ALPS-PAS" in fieldnames:
                alps_method = "ALPS-PAS"
            elif "Left Hemisphere ALPS-LAB" in fieldnames:
                alps_method = "ALPS-LAB"
            else:
                # Legacy format (no method suffix)
                alps_method = "ALPS-LAB"

            for row in reader:
                subject_id = row.get("Filename", "")
                if not subject_id:
                    continue

                data = {
                    "alps_method": alps_method,
                    "status": row.get("Status", ""),
                    "error": row.get("Error", ""),
                }

                # Parse ALPS-LAB values
                if alps_method in ("ALPS-LAB", "Both"):
                    lab_left_col = (
                        "Left Hemisphere ALPS-LAB"
                        if "Left Hemisphere ALPS-LAB" in fieldnames
                        else "Left Hemisphere ALPS"
                    )
                    lab_right_col = (
                        "Right Hemisphere ALPS-LAB"
                        if "Right Hemisphere ALPS-LAB" in fieldnames
                        else "Right Hemisphere ALPS"
                    )
                    lab_combined_col = (
                        "Combined ALPS-LAB"
                        if "Combined ALPS-LAB" in fieldnames
                        else "Combined ALPS"
                    )

                    try:
                        data["alps_lab_left"] = float(row.get(lab_left_col, ""))
                    except (ValueError, TypeError):
                        data["alps_lab_left"] = None
                    try:
                        data["alps_lab_right"] = float(row.get(lab_right_col, ""))
                    except (ValueError, TypeError):
                        data["alps_lab_right"] = None
                    try:
                        data["alps_lab_combined"] = float(row.get(lab_combined_col, ""))
                    except (ValueError, TypeError):
                        data["alps_lab_combined"] = None

                # Parse ALPS-PAS values
                if alps_method in ("ALPS-PAS", "Both"):
                    try:
                        data["alps_pas_left"] = float(row.get("Left Hemisphere ALPS-PAS", ""))
                    except (ValueError, TypeError):
                        data["alps_pas_left"] = None
                    try:
                        data["alps_pas_right"] = float(row.get("Right Hemisphere ALPS-PAS", ""))
                    except (ValueError, TypeError):
                        data["alps_pas_right"] = None
                    try:
                        data["alps_pas_combined"] = float(row.get("Combined ALPS-PAS", ""))
                    except (ValueError, TypeError):
                        data["alps_pas_combined"] = None

                alps_data[subject_id] = data

        return alps_data, alps_method

    def _on_subject_select(self, event):
        """Handle subject selection in tree."""
        selection = self.subject_tree.selection()
        if selection:
            subject_id = selection[0]
            self._select_subject(subject_id)

    def _select_subject(self, subject_id: str):
        """Load and display a subject's data."""
        if subject_id not in self.subjects:
            return

        # Unload previous subject's images
        if self.current_subject:
            self.current_subject.unload_images()

        self.current_subject = self.subjects[subject_id]

        # Update metrics display
        self._update_metrics_display()

        # Load images
        if not self.current_subject.load_images():
            messagebox.showwarning("Warning", f"Could not load images for subject: {subject_id}")
            return

        # Reset slice to middle
        shape = self.current_subject.shape
        if shape:
            self._update_slice_range()
            self.current_slice = self._get_num_slices() // 2
            self.slice_var.set(self.current_slice)

        # Auto-fit image to canvas (after canvas has been sized)
        self.update_idletasks()
        self._zoom_fit()

        self._update_display()

    def _update_metrics_display(self):
        """Update ALPS metrics labels for current subject."""
        if not self.current_subject:
            self.subject_info_label.config(text="None selected")
            self.method_label.config(text="--")
            self.status_value_label.config(text="--")
            # Reset all labels based on current layout
            self._build_metrics_labels(self.alps_method)
            return

        subject = self.current_subject

        self.subject_info_label.config(text=subject.subject_id)
        self.method_label.config(text=subject.alps_method if subject.alps_method else "--")

        # Rebuild metrics labels if method changed from what labels are built for
        if subject.alps_method and subject.alps_method != self._labels_built_for_method:
            self.alps_method = subject.alps_method
            self._build_metrics_labels(self.alps_method)

        # Update values based on method
        if self.alps_method == "Both":
            # Update ALPS-LAB labels
            if subject.alps_lab_left is not None:
                self.alps_lab_left_label.config(text=f"{subject.alps_lab_left:.4f}")
            else:
                self.alps_lab_left_label.config(text="--")
            if subject.alps_lab_right is not None:
                self.alps_lab_right_label.config(text=f"{subject.alps_lab_right:.4f}")
            else:
                self.alps_lab_right_label.config(text="--")
            if subject.alps_lab_combined is not None:
                self.alps_lab_combined_label.config(text=f"{subject.alps_lab_combined:.4f}")
            else:
                self.alps_lab_combined_label.config(text="--")

            # Update ALPS-PAS labels
            if subject.alps_pas_left is not None:
                self.alps_pas_left_label.config(text=f"{subject.alps_pas_left:.4f}")
            else:
                self.alps_pas_left_label.config(text="--")
            if subject.alps_pas_right is not None:
                self.alps_pas_right_label.config(text=f"{subject.alps_pas_right:.4f}")
            else:
                self.alps_pas_right_label.config(text="--")
            if subject.alps_pas_combined is not None:
                self.alps_pas_combined_label.config(text=f"{subject.alps_pas_combined:.4f}")
            else:
                self.alps_pas_combined_label.config(text="--")
        elif self.alps_method == "ALPS-PAS":
            if subject.alps_pas_left is not None:
                self.alps_left_label.config(text=f"{subject.alps_pas_left:.4f}")
            else:
                self.alps_left_label.config(text="--")
            if subject.alps_pas_right is not None:
                self.alps_right_label.config(text=f"{subject.alps_pas_right:.4f}")
            else:
                self.alps_right_label.config(text="--")
            if subject.alps_pas_combined is not None:
                self.alps_combined_label.config(text=f"{subject.alps_pas_combined:.4f}")
            else:
                self.alps_combined_label.config(text="--")
        else:  # ALPS-LAB
            if subject.alps_lab_left is not None:
                self.alps_left_label.config(text=f"{subject.alps_lab_left:.4f}")
            else:
                self.alps_left_label.config(text="--")
            if subject.alps_lab_right is not None:
                self.alps_right_label.config(text=f"{subject.alps_lab_right:.4f}")
            else:
                self.alps_right_label.config(text="--")
            if subject.alps_lab_combined is not None:
                self.alps_combined_label.config(text=f"{subject.alps_lab_combined:.4f}")
            else:
                self.alps_combined_label.config(text="--")

        self.status_value_label.config(text=subject.status if subject.status else "--")

    def _get_num_slices(self) -> int:
        """Get number of slices for current view."""
        if not self.current_subject or self.current_subject.shape is None:
            return 0

        shape = self.current_subject.shape
        view = self.view_var.get()

        if view == "axial":
            return shape[2] if len(shape) > 2 else 0
        elif view == "coronal":
            return shape[1] if len(shape) > 1 else 0
        else:  # sagittal
            return shape[0]

    def _update_slice_range(self):
        """Update slice slider range for current view."""
        num_slices = self._get_num_slices()
        if num_slices > 0:
            self.slice_slider.config(to=num_slices - 1)
            self.slice_label.config(text=f"{self.current_slice} / {num_slices - 1}")

    def _update_display(self):
        """Update the image display."""
        if not self.current_subject or self.current_subject.fa_data is None:
            self.canvas.delete("all")
            return

        # Create DEC image
        dec_image = self._create_dec_image()
        if dec_image is None:
            return

        # Add ROI overlays if enabled
        if self.show_rois_var.get():
            dec_image = self._add_roi_overlay(dec_image)

        # Apply zoom and display
        self._display_image(dec_image)

        # Update slice label
        num_slices = self._get_num_slices()
        self.slice_label.config(text=f"{self.current_slice} / {num_slices - 1}")

    def _create_dec_image(self) -> np.ndarray | None:
        """Create FA-modulated direction-encoded color image for current slice."""
        subject = self.current_subject
        if subject is None or subject.fa_data is None or subject.v1_data is None:
            return None

        fa = subject.fa_data
        v1 = subject.v1_data
        view = self.view_var.get()
        s = self.current_slice

        # Extract slice based on view
        if view == "axial":
            if s >= fa.shape[2]:
                return None
            fa_slice = fa[:, :, s]
            v1_slice = v1[:, :, s, :]
        elif view == "coronal":
            if s >= fa.shape[1]:
                return None
            fa_slice = fa[:, s, :]
            v1_slice = v1[:, s, :, :]
        else:  # sagittal
            if s >= fa.shape[0]:
                return None
            fa_slice = fa[s, :, :]
            v1_slice = v1[s, :, :, :]

        # Create RGB from V1 (absolute values since direction doesn't matter)
        # V1 is [x, y, z] -> RGB mapping: |x|=R, |y|=G, |z|=B
        rgb = np.abs(v1_slice)

        # Handle NaN values
        rgb = np.nan_to_num(rgb, nan=0.0)
        fa_slice = np.nan_to_num(fa_slice, nan=0.0)

        # Normalize RGB values
        rgb_max = np.max(rgb, axis=-1, keepdims=True)
        rgb_max = np.where(rgb_max > 0, rgb_max, 1)
        rgb = rgb / rgb_max

        # Modulate by FA
        fa_max = np.max(fa_slice)
        fa_norm = np.clip(fa_slice / fa_max if fa_max > 0 else fa_slice, 0, 1)
        fa_mod = fa_norm[:, :, np.newaxis]

        rgb_modulated = rgb * fa_mod

        # Convert to uint8
        rgb_uint8 = (np.clip(rgb_modulated, 0, 1) * 255).astype(np.uint8)

        return rgb_uint8

    def _add_roi_overlay(self, dec_image: np.ndarray) -> np.ndarray:
        """Add ROI overlays to the DEC image as solid white."""
        subject = self.current_subject
        if subject is None or not subject.roi_data:
            return dec_image

        # Work with a copy
        result = dec_image.copy()

        view = self.view_var.get()
        s = self.current_slice

        # Combine all ROI masks into one
        combined_mask = None

        for roi_vol in subject.roi_data.values():
            # Extract ROI slice
            if view == "axial":
                if s >= roi_vol.shape[2]:
                    continue
                roi_slice = roi_vol[:, :, s]
            elif view == "coronal":
                if s >= roi_vol.shape[1]:
                    continue
                roi_slice = roi_vol[:, s, :]
            else:  # sagittal
                if s >= roi_vol.shape[0]:
                    continue
                roi_slice = roi_vol[s, :, :]

            # Create mask
            mask = roi_slice > 0

            if combined_mask is None:
                combined_mask = mask
            else:
                combined_mask = combined_mask | mask

        if combined_mask is not None and np.any(combined_mask):
            # Apply solid white to all ROI voxels
            result[combined_mask] = [255, 255, 255]

        return result

    def _display_image(self, image: np.ndarray):
        """Display the image on the canvas."""
        # Rotate/flip for proper orientation
        view = self.view_var.get()

        if view == "axial":
            # Rotate 90 degrees counterclockwise and flip
            image = np.rot90(image, k=1)
            image = np.fliplr(image)
        elif view == "coronal":
            image = np.rot90(image, k=1)
            image = np.fliplr(image)
        else:  # sagittal
            image = np.rot90(image, k=1)

        # Apply zoom
        h, w = image.shape[:2]
        new_w = max(1, int(w * self.zoom_level))
        new_h = max(1, int(h * self.zoom_level))

        # Convert to PIL Image
        if image.shape[-1] == 4:
            pil_image = Image.fromarray(image, mode="RGBA")
        else:
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
        if not self.output_folder or not self.available_roi_types:
            return

        # Get selected display name and find matching roi_type
        selected_display = self.roi_type_var.get()
        new_roi_type = None
        for roi_type in self.available_roi_types:
            if get_roi_display_name(roi_type) == selected_display:
                new_roi_type = roi_type
                break

        if new_roi_type is None or new_roi_type == self.current_roi_type:
            return

        self.current_roi_type = new_roi_type

        # Load CSV data for this ROI type if not cached
        if new_roi_type not in self.alps_data_by_roi_type:
            csv_path = get_csv_path_for_roi_type(self.output_folder, new_roi_type)
            if csv_path.exists():
                alps_data, alps_method = self._parse_alps_csv(csv_path)
                self.alps_data_by_roi_type[new_roi_type] = alps_data
                self.alps_method = alps_method

                # Rebuild labels if method changed
                if alps_method != self._labels_built_for_method:
                    self._build_metrics_labels(alps_method)

        # Update metrics for all subjects from new CSV
        if new_roi_type in self.alps_data_by_roi_type:
            alps_data = self.alps_data_by_roi_type[new_roi_type]
            for subject_id, subject_data in self.subjects.items():
                subject_data.active_roi_type = new_roi_type
                if subject_id in alps_data:
                    csv_row = alps_data[subject_id]
                    subject_data.alps_method = csv_row.get("alps_method", "")
                    subject_data.alps_lab_left = csv_row.get("alps_lab_left")
                    subject_data.alps_lab_right = csv_row.get("alps_lab_right")
                    subject_data.alps_lab_combined = csv_row.get("alps_lab_combined")
                    subject_data.alps_pas_left = csv_row.get("alps_pas_left")
                    subject_data.alps_pas_right = csv_row.get("alps_pas_right")
                    subject_data.alps_pas_combined = csv_row.get("alps_pas_combined")
                    subject_data.status = csv_row.get("status", "")
                    subject_data.error = csv_row.get("error", "")

        # Reload ROIs for current subject and update display
        if self.current_subject:
            self.current_subject.load_rois_for_type(new_roi_type)
            self._update_metrics_display()
            self._update_display()

    def _on_view_change(self):
        """Handle view type change."""
        self._update_slice_range()
        # Reset to middle slice
        self.current_slice = self._get_num_slices() // 2
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
        if not self.current_subject or self.current_subject.shape is None:
            return

        shape = self.current_subject.shape
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
