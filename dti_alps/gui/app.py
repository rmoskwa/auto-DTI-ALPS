"""
Main application window for DTI-ALPS Processing Tool.
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..processing.discovery import SubjectDiscovery, SubjectFiles
from ..processing.pipeline import (
    BatchConfig,
    BatchRunner,
    BatchState,
    BatchWorker,
    PipelineState,
)
from . import config


class DTIALPSApplication(tk.Tk):
    """
    Main application window for DTI-ALPS processing.

    Features:
    - Pipeline stage navigation
    - Progress tracking and logging
    - Background processing
    """

    def __init__(self):
        super().__init__()

        self.title(config.APP_NAME)
        self.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}")
        self.minsize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)

        # State
        self.current_stage = 0
        self.pipeline_state = PipelineState()
        self.worker = None
        self.result_queue = None
        self.cancel_event = None

        # Batch processing state
        self.subject_files_list: list[SubjectFiles] = []
        self.batch_state: BatchState | None = None

        # Build UI
        self._create_menu()
        self._create_toolbar()
        self._create_main_layout()

        # Initialize first stage
        self._show_stage(0)

    def _create_menu(self):
        """Create menu bar."""
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Load Settings...", command=self._load_settings)
        file_menu.add_command(label="Save Settings...", command=self._save_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about)

    def _create_toolbar(self):
        """Create toolbar with action buttons."""
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        # Spacer
        ttk.Frame(toolbar).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Action buttons
        self.run_btn = ttk.Button(toolbar, text="Run Pipeline", command=self._run_pipeline)
        self.run_btn.pack(side=tk.RIGHT, padx=5)

        self.stop_btn = ttk.Button(
            toolbar, text="Stop", command=self._stop_pipeline, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.RIGHT, padx=5)

    def _create_main_layout(self):
        """Create main layout with sidebar, content, and progress panel."""
        # Main container
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left sidebar with stage navigation
        self.sidebar = ttk.Frame(main_frame, width=200)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        self.sidebar.pack_propagate(False)

        sidebar_label = ttk.Label(
            self.sidebar, text="Pipeline Stages", font=("TkDefaultFont", 10, "bold")
        )
        sidebar_label.pack(pady=10)

        self.stage_buttons = []
        for i, (_stage_id, stage_name) in enumerate(config.PIPELINE_STAGES):
            btn = ttk.Button(
                self.sidebar,
                text=f"{i + 1}. {stage_name}",
                command=lambda idx=i: self._show_stage(idx),
                width=20,
            )
            btn.pack(pady=2, padx=5, fill=tk.X)
            self.stage_buttons.append(btn)

        # Right content area
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Content frame (changes with stage)
        self.content_frame = ttk.LabelFrame(right_frame, text="Settings", padding=10)
        self.content_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # Progress panel at bottom
        self.progress_frame = ttk.LabelFrame(right_frame, text="Progress", padding=5)
        self.progress_frame.pack(fill=tk.X, pady=(5, 0))

        self._create_progress_panel()

        # Create all stage frames (hidden initially)
        self.stage_frames = {}
        self._create_data_frame()
        self._create_preproc_frame()
        self._create_dti_frame()
        self._create_roi_frame()
        self._create_results_frame()

    def _create_progress_panel(self):
        """Create progress tracking panel."""
        # Progress bar
        progress_top = ttk.Frame(self.progress_frame)
        progress_top.pack(fill=tk.X)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_top, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.status_label = ttk.Label(progress_top, text="Ready")
        self.status_label.pack(side=tk.LEFT)

        # Log text area
        log_frame = ttk.Frame(self.progress_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self.log_text = tk.Text(log_frame, height=6, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _create_data_frame(self):
        """Create batch data input frame (Stage 1)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["data"] = frame

        # Subject folders section
        folders_frame = ttk.LabelFrame(frame, text="Subject Folders", padding=10)
        folders_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Instructions
        ttk.Label(
            folders_frame,
            text="Add folders containing DWI data. Each folder should have a .nii.gz image "
            "with matching .bvec and .bval files.",
            wraplength=700,
        ).pack(anchor=tk.W, pady=(0, 10))

        # Treeview for subject list
        tree_frame = ttk.Frame(folders_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("subject_id", "folder", "status", "files")
        self.subjects_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=8,
        )

        self.subjects_tree.heading("subject_id", text="Subject ID")
        self.subjects_tree.heading("folder", text="Folder Path")
        self.subjects_tree.heading("status", text="Status")
        self.subjects_tree.heading("files", text="Files Found")

        self.subjects_tree.column("subject_id", width=120)
        self.subjects_tree.column("folder", width=350)
        self.subjects_tree.column("status", width=100)
        self.subjects_tree.column("files", width=150)

        # Scrollbars
        y_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.subjects_tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.subjects_tree.xview)
        self.subjects_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        # Grid layout for tree + scrollbars
        self.subjects_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # Buttons frame
        btn_frame = ttk.Frame(folders_frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Add Folder...", command=self._add_subject_folder).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="Remove Selected", command=self._remove_selected_subjects).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="Clear All", command=self._clear_all_subjects).pack(
            side=tk.LEFT, padx=5
        )

        # Common parameters section
        params_frame = ttk.LabelFrame(frame, text="Common Parameters", padding=10)
        params_frame.pack(fill=tk.X, pady=5)

        # Row 1: PE direction with auto-extract option
        ttk.Label(params_frame, text="PE Direction:").grid(row=0, column=0, sticky=tk.W, pady=2)

        self.pe_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            params_frame,
            text="Auto from JSON",
            variable=self.pe_auto_var,
            command=self._on_pe_auto_change,
        ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        self.pe_dir_var = tk.StringVar(value=config.DEFAULT_PE_DIRECTION)
        self.pe_combo = ttk.Combobox(
            params_frame,
            textvariable=self.pe_dir_var,
            values=config.PE_DIRECTIONS,
            width=8,
            state="disabled",  # Start disabled (auto mode)
        )
        self.pe_combo.grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)

        # Row 2: Readout time with auto-extract option
        ttk.Label(params_frame, text="Readout Time:").grid(row=1, column=0, sticky=tk.W, pady=2)

        self.readout_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            params_frame,
            text="Auto from JSON/NIfTI",
            variable=self.readout_auto_var,
            command=self._on_readout_auto_change,
        ).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        self.readout_var = tk.StringVar(value=str(config.DEFAULT_READOUT_TIME))
        self.readout_entry = ttk.Entry(params_frame, textvariable=self.readout_var, width=10)
        self.readout_entry.grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        self.readout_entry.config(state=tk.DISABLED)  # Start disabled (auto mode)

        ttk.Label(params_frame, text="(seconds)").grid(row=1, column=3, sticky=tk.W, pady=2)

        # Row 3: RPE scheme
        ttk.Label(params_frame, text="RPE Scheme:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.rpe_var = tk.StringVar(value=config.DEFAULT_RPE_SCHEME)
        rpe_combo = ttk.Combobox(
            params_frame,
            textvariable=self.rpe_var,
            values=list(config.RPE_SCHEMES.keys()),
            width=10,
            state="readonly",
        )
        rpe_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        # RPE description label
        self.rpe_desc_label = ttk.Label(
            params_frame, text=config.RPE_SCHEMES.get(config.DEFAULT_RPE_SCHEME, "")
        )
        self.rpe_desc_label.grid(row=2, column=2, columnspan=4, sticky=tk.W, padx=5, pady=2)
        rpe_combo.bind("<<ComboboxSelected>>", self._on_rpe_combo_change)

        # Output settings
        out_frame = ttk.LabelFrame(frame, text="Output", padding=10)
        out_frame.pack(fill=tk.X, pady=5)

        ttk.Label(out_frame, text="Output Directory:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.output_dir_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.output_dir_var, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=5, pady=2
        )
        ttk.Button(out_frame, text="Browse...", command=self._browse_output_dir).grid(
            row=0, column=2, pady=2
        )

        out_frame.columnconfigure(1, weight=1)

    def _on_pe_auto_change(self):
        """Handle PE direction auto-extract checkbox change."""
        if self.pe_auto_var.get():
            self.pe_combo.config(state="disabled")
        else:
            self.pe_combo.config(state="readonly")

    def _on_readout_auto_change(self):
        """Handle readout time auto-extract checkbox change."""
        if self.readout_auto_var.get():
            self.readout_entry.config(state=tk.DISABLED)
        else:
            self.readout_entry.config(state=tk.NORMAL)

    def _on_rpe_combo_change(self, event=None):
        """Handle RPE scheme combobox change."""
        scheme = self.rpe_var.get()
        desc = config.RPE_SCHEMES.get(scheme, "")
        self.rpe_desc_label.config(text=desc)

    def _add_subject_folder(self):
        """Add a folder and discover all DWI runs within it."""
        folder = filedialog.askdirectory(title="Select Folder with DWI Data")
        if folder:
            self._discover_and_add_folder(folder)

    def _discover_and_add_folder(self, folder_path: str) -> int:
        """
        Discover all DWI runs in folder and add each as a separate subject entry.

        Returns the number of runs successfully added.
        """
        try:
            discovery = SubjectDiscovery(folder_path)
            discovered_runs = discovery.discover_files()

            if not discovered_runs:
                messagebox.showinfo(
                    "No Data Found",
                    f"No DWI files with matching bvec/bval files found in:\n{folder_path}",
                )
                return 0

            added = 0
            for subject_files in discovered_runs:
                # Check for duplicates by DWI path (more specific than folder)
                is_duplicate = False
                for existing in self.subject_files_list:
                    if existing.dwi_path == subject_files.dwi_path:
                        is_duplicate = True
                        break

                if is_duplicate:
                    continue

                # Determine status
                if subject_files.is_valid:
                    status = "Ready"
                else:
                    status = "Missing Files"

                files_found = subject_files.get_files_summary()

                # Add to tree
                self.subjects_tree.insert(
                    "",
                    tk.END,
                    values=(
                        subject_files.subject_id,
                        folder_path,
                        status,
                        files_found,
                    ),
                )

                # Store SubjectFiles object
                self.subject_files_list.append(subject_files)
                added += 1

            if added > 0:
                self._log(f"Added {added} DWI run(s) from {folder_path}")

            return added

        except Exception as e:
            messagebox.showwarning(
                "Discovery Error", f"Could not process folder:\n{folder_path}\n\nError: {e}"
            )
            return 0

    def _remove_selected_subjects(self):
        """Remove selected subjects from the list."""
        selected = self.subjects_tree.selection()
        if not selected:
            return

        # Get indices to remove (reverse order to maintain indices)
        indices_to_remove = []
        for item in selected:
            idx = self.subjects_tree.index(item)
            indices_to_remove.append(idx)

        # Remove from list (reverse order)
        for idx in sorted(indices_to_remove, reverse=True):
            del self.subject_files_list[idx]

        # Remove from tree
        for item in selected:
            self.subjects_tree.delete(item)

    def _clear_all_subjects(self):
        """Clear all subjects from the list."""
        if self.subject_files_list:
            if messagebox.askyesno("Confirm", "Clear all subjects from the list?"):
                self.subject_files_list.clear()
                for item in self.subjects_tree.get_children():
                    self.subjects_tree.delete(item)

    def _create_preproc_frame(self):
        """Create preprocessing options frame (Stage 2, Advanced only)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["preproc"] = frame

        # Eddy options
        eddy_frame = ttk.LabelFrame(frame, text="Eddy Options", padding=10)
        eddy_frame.pack(fill=tk.X, pady=5)

        self._create_file_row(
            eddy_frame, "Processing Mask:", "eddy_mask", config.NIFTI_FILETYPES, 0
        )
        self._create_file_row(
            eddy_frame,
            "Slice Spec File:",
            "eddy_slspec",
            [("Text files", "*.txt"), ("All files", "*.*")],
            1,
        )

        ttk.Label(eddy_frame, text="Extra Options:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.eddy_options_var = tk.StringVar()
        ttk.Entry(eddy_frame, textvariable=self.eddy_options_var, width=50).grid(
            row=2, column=1, sticky=tk.EW, padx=5, pady=2
        )
        ttk.Label(eddy_frame, text="e.g., --repol --data_is_shelled").grid(
            row=2, column=2, sticky=tk.W, padx=5
        )

        eddy_frame.columnconfigure(1, weight=1)

        # Topup options
        topup_frame = ttk.LabelFrame(frame, text="Topup Options", padding=10)
        topup_frame.pack(fill=tk.X, pady=5)

        ttk.Label(topup_frame, text="Extra Options:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.topup_options_var = tk.StringVar()
        ttk.Entry(topup_frame, textvariable=self.topup_options_var, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=5, pady=2
        )

        topup_frame.columnconfigure(1, weight=1)

        # QC options
        qc_frame = ttk.LabelFrame(frame, text="Quality Control", padding=10)
        qc_frame.pack(fill=tk.X, pady=5)

        self.generate_qc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            qc_frame, text="Generate eddy QC reports", variable=self.generate_qc_var
        ).pack(anchor=tk.W)

        self.keep_intermediate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            qc_frame, text="Keep intermediate files", variable=self.keep_intermediate_var
        ).pack(anchor=tk.W)

    def _create_dti_frame(self):
        """Create DTI fitting frame (Stage 3)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["dti"] = frame

        info_label = ttk.Label(
            frame,
            text="DTI tensor fitting will be performed automatically using MRtrix3.\n\n"
            "The following outputs will be generated:\n"
            "  - Diffusion tensor image (for ALPS calculation)\n"
            "  - FA map (for ROI localization)\n"
            "  - Principal eigenvector V1 (for fiber classification)",
            justify=tk.LEFT,
        )
        info_label.pack(anchor=tk.W, pady=20)

        # Optional mask
        mask_frame = ttk.LabelFrame(frame, text="Optional Settings", padding=10)
        mask_frame.pack(fill=tk.X, pady=5)

        self._create_file_row(mask_frame, "DTI Mask:", "dti_mask", config.NIFTI_FILETYPES, 0)

    def _create_roi_frame(self):
        """Create ROI detection parameters frame (Stage 4)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["roi"] = frame

        info_label = ttk.Label(
            frame,
            text="Configure parameters for automatic ROI detection.\n"
            "The algorithm will find optimal locations for projection and association fiber ROIs.",
            justify=tk.LEFT,
        )
        info_label.pack(anchor=tk.W, pady=10)

        # Parameters
        param_frame = ttk.LabelFrame(frame, text="Detection Parameters", padding=10)
        param_frame.pack(fill=tk.X, pady=5)

        # FA threshold
        row = 0
        ttk.Label(param_frame, text="FA Threshold:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.fa_thresh_var = tk.DoubleVar(value=config.DEFAULT_FA_THRESH)
        fa_scale = ttk.Scale(
            param_frame,
            from_=0.1,
            to=0.5,
            variable=self.fa_thresh_var,
            orient=tk.HORIZONTAL,
            length=200,
        )
        fa_scale.grid(row=row, column=1, sticky=tk.W, padx=5)
        self.fa_thresh_label = ttk.Label(param_frame, text=f"{config.DEFAULT_FA_THRESH:.2f}")
        self.fa_thresh_label.grid(row=row, column=2, sticky=tk.W)
        fa_scale.config(command=lambda v: self.fa_thresh_label.config(text=f"{float(v):.2f}"))

        # Orientation threshold
        row += 1
        ttk.Label(param_frame, text="Orientation Threshold:").grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        self.orient_thresh_var = tk.DoubleVar(value=config.DEFAULT_ORIENT_THRESH)
        orient_scale = ttk.Scale(
            param_frame,
            from_=0.5,
            to=0.9,
            variable=self.orient_thresh_var,
            orient=tk.HORIZONTAL,
            length=200,
        )
        orient_scale.grid(row=row, column=1, sticky=tk.W, padx=5)
        self.orient_thresh_label = ttk.Label(
            param_frame, text=f"{config.DEFAULT_ORIENT_THRESH:.2f}"
        )
        self.orient_thresh_label.grid(row=row, column=2, sticky=tk.W)
        orient_scale.config(
            command=lambda v: self.orient_thresh_label.config(text=f"{float(v):.2f}")
        )

        # Min zone width
        row += 1
        ttk.Label(param_frame, text="Min Zone Width (voxels):").grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        self.min_width_var = tk.IntVar(value=config.DEFAULT_MIN_ZONE_WIDTH)
        ttk.Spinbox(param_frame, from_=3, to=15, textvariable=self.min_width_var, width=5).grid(
            row=row, column=1, sticky=tk.W, padx=5
        )

        # ROI radius
        row += 1
        ttk.Label(param_frame, text="ROI Radius (mm):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.roi_radius_var = tk.DoubleVar(value=config.DEFAULT_ROI_RADIUS_MM)
        ttk.Spinbox(
            param_frame, from_=2.0, to=8.0, increment=0.5, textvariable=self.roi_radius_var, width=5
        ).grid(row=row, column=1, sticky=tk.W, padx=5)

        # Z tolerance
        row += 1
        ttk.Label(param_frame, text="Z-Tolerance (voxels):").grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        self.z_tolerance_var = tk.IntVar(value=config.DEFAULT_Z_TOLERANCE)
        ttk.Spinbox(param_frame, from_=0, to=5, textvariable=self.z_tolerance_var, width=5).grid(
            row=row, column=1, sticky=tk.W, padx=5
        )

    def _create_results_frame(self):
        """Create results display frame (Stage 5)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["results"] = frame

        # Results will be populated after processing
        self.results_label = ttk.Label(
            frame,
            text="Results will be displayed here after processing completes.",
            justify=tk.LEFT,
        )
        self.results_label.pack(anchor=tk.W, pady=20)

        # Placeholder for results table
        self.results_tree = None

    def _create_file_row(self, parent, label_text, var_name, filetypes, row):
        """Create a file browser row."""
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky=tk.W, pady=2)

        var = tk.StringVar()
        setattr(self, f"{var_name}_var", var)

        entry = ttk.Entry(parent, textvariable=var, width=50)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=2)

        btn = ttk.Button(
            parent, text="Browse...", command=lambda: self._browse_file(var_name, filetypes)
        )
        btn.grid(row=row, column=2, pady=2)

        parent.columnconfigure(1, weight=1)

    def _browse_file(self, var_name, filetypes):
        """Open file browser and update variable."""
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var = getattr(self, f"{var_name}_var")
            var.set(path)

    def _browse_output_dir(self):
        """Open directory browser for output."""
        path = filedialog.askdirectory()
        if path:
            self.output_dir_var.set(path)

    def _show_stage(self, stage_idx):
        """Show the specified pipeline stage."""
        self.current_stage = stage_idx

        # Update button states
        for i, btn in enumerate(self.stage_buttons):
            if i == stage_idx:
                btn.state(["pressed"])
            else:
                btn.state(["!pressed"])

        # Hide all frames
        for frame in self.stage_frames.values():
            frame.pack_forget()

        # Show selected frame
        stage_id = config.PIPELINE_STAGES[stage_idx][0]
        frame = self.stage_frames.get(stage_id)
        if frame:
            frame.pack(fill=tk.BOTH, expand=True)

        # Update frame title
        stage_name = config.PIPELINE_STAGES[stage_idx][1]
        self.content_frame.config(text=f"Stage {stage_idx + 1}: {stage_name}")

    def _collect_batch_state(self) -> BatchState:
        """Collect all UI values into batch state."""
        # Determine readout time
        if self.readout_auto_var.get():
            readout_time = None  # Auto-extract from JSON
        else:
            try:
                readout_time = float(self.readout_var.get())
            except ValueError:
                readout_time = config.DEFAULT_READOUT_TIME

        # Create batch config
        batch_config = BatchConfig(
            pe_direction=self.pe_dir_var.get(),
            auto_pe_direction=self.pe_auto_var.get(),
            readout_time=readout_time,
            rpe_scheme=self.rpe_var.get(),
            # Preprocessing options
            eddy_options=getattr(self, "eddy_options_var", tk.StringVar()).get(),
            topup_options=getattr(self, "topup_options_var", tk.StringVar()).get(),
            generate_qc=getattr(self, "generate_qc_var", tk.BooleanVar()).get(),
            keep_intermediates=getattr(self, "keep_intermediate_var", tk.BooleanVar()).get(),
            # ROI detection parameters
            fa_thresh=self.fa_thresh_var.get(),
            orient_thresh=self.orient_thresh_var.get(),
            min_zone_width=self.min_width_var.get(),
            roi_radius_mm=self.roi_radius_var.get(),
            z_tolerance=self.z_tolerance_var.get(),
            # Output
            output_dir=self.output_dir_var.get(),
        )

        # Create batch state
        batch_state = BatchState(
            config=batch_config,
            subjects=list(self.subject_files_list),  # Copy the list
        )

        return batch_state

    def _run_pipeline(self):
        """Start batch pipeline execution."""
        # Validate we have subjects
        if not self.subject_files_list:
            messagebox.showerror("Validation Error", "No subject folders added.")
            return

        # Check for invalid subjects
        invalid_subjects = [s for s in self.subject_files_list if not s.is_valid]
        if invalid_subjects:
            names = ", ".join(s.subject_id for s in invalid_subjects[:5])
            if len(invalid_subjects) > 5:
                names += f" (and {len(invalid_subjects) - 5} more)"
            messagebox.showerror(
                "Validation Error",
                f"Some subjects have missing files:\n{names}\n\n"
                "Please remove invalid subjects or add missing files.",
            )
            return

        # Validate output directory
        output_dir = self.output_dir_var.get()
        if not output_dir:
            messagebox.showerror("Validation Error", "Please specify an output directory.")
            return

        # Collect batch state
        self.batch_state = self._collect_batch_state()

        # Disable UI
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Reset progress
        self.progress_var.set(0)
        self.status_label.config(text="Starting batch processing...")

        # Create batch worker
        self.result_queue = queue.Queue()
        self.cancel_event = threading.Event()

        batch_runner = BatchRunner(self.batch_state)
        self.worker = BatchWorker(batch_runner, self.result_queue, self.cancel_event)
        self.worker.start()

        # Start polling for results
        self.after(100, self._check_results)

    def _stop_pipeline(self):
        """Request pipeline cancellation."""
        if self.cancel_event:
            self.cancel_event.set()
            self._log("Cancellation requested...")

    def _check_results(self):
        """Poll result queue for updates."""
        try:
            while True:
                msg = self.result_queue.get_nowait()
                self._handle_result(msg)
        except queue.Empty:
            pass

        # Continue polling if worker is alive
        if self.worker and self.worker.is_alive():
            self.after(100, self._check_results)
        else:
            self.run_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

    def _handle_result(self, msg):
        """Handle message from worker."""
        msg_type = msg[0]
        data = msg[1] if len(msg) > 1 else None

        if msg_type == "log":
            self._log(data)
        elif msg_type == "stage":
            stage, status = data
            self._update_stage_status(stage, status)
        elif msg_type == "batch_start":
            total = data
            self.status_label.config(text=f"Processing 0/{total} subjects")
        elif msg_type == "subject_start":
            index, subject_id = data
            total = len(self.subject_files_list)
            self.status_label.config(text=f"Processing {index + 1}/{total}: {subject_id}")
            # Update tree status
            items = self.subjects_tree.get_children()
            if index < len(items):
                self.subjects_tree.set(items[index], "status", "Processing")
        elif msg_type == "subject_complete":
            index, result = data
            total = len(self.subject_files_list)
            completed = index + 1

            # Update progress bar
            self.progress_var.set((completed / total) * 100)
            self.status_label.config(text=f"Completed {completed}/{total} subjects")

            # Update tree status
            items = self.subjects_tree.get_children()
            if index < len(items):
                status = "Completed" if result.status == "completed" else "Failed"
                self.subjects_tree.set(items[index], "status", status)
        elif msg_type == "batch_complete":
            batch_state = data
            self._log(
                f"Batch complete: {batch_state.success_count}/{batch_state.total_subjects} succeeded"
            )
            self.progress_var.set(100)
            self._show_batch_results(batch_state)
        elif msg_type == "batch_success":
            batch_state = data
            self._log("All subjects processed successfully!")
            self.status_label.config(text="Complete")
            self.progress_var.set(100)
            self._show_batch_results(batch_state)
        elif msg_type == "batch_partial":
            batch_state = data
            self._log(
                f"Batch completed with errors: {batch_state.success_count}/"
                f"{batch_state.total_subjects} succeeded"
            )
            self.status_label.config(text="Completed with errors")
            self.progress_var.set(100)
            self._show_batch_results(batch_state)
        elif msg_type == "batch_cancelled":
            self._log("Batch processing cancelled.")
            self.status_label.config(text="Cancelled")
        elif msg_type == "complete":
            # Single subject complete (legacy)
            self._log("Pipeline completed successfully!")
            self.status_label.config(text="Complete")
            self.progress_var.set(100)
            self._show_results(data)
        elif msg_type == "failed":
            self._log("Pipeline failed.")
            self.status_label.config(text="Failed")
        elif msg_type == "cancelled":
            self._log("Pipeline cancelled.")
            self.status_label.config(text="Cancelled")
        elif msg_type == "error":
            self._log(f"Error: {data}")
            self.status_label.config(text="Error")

    def _log(self, message):
        """Append message to log."""
        from datetime import datetime

        timestamp = datetime.now().strftime("[%H:%M:%S]")

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{timestamp} {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _update_stage_status(self, stage, status):
        """Update stage indicator."""
        stage_names = {
            "preproc": "Preprocessing",
            "dti": "DTI Fitting",
            "roi": "ROI Detection",
            "results": "Calculating ALPS",
        }

        stage_progress = {"preproc": 25, "dti": 50, "roi": 75, "results": 90}

        if status == "running":
            self.status_label.config(text=f"Running: {stage_names.get(stage, stage)}")
            self.progress_var.set(stage_progress.get(stage, 0))
        elif status == "complete":
            self.progress_var.set(stage_progress.get(stage, 0) + 10)

    def _show_results(self, alps_results):
        """Display ALPS results."""
        if not alps_results:
            return

        # Switch to results stage
        self._show_stage(4)

        # Update results frame
        frame = self.stage_frames["results"]

        # Clear previous content
        for child in frame.winfo_children():
            child.destroy()

        # Title
        ttk.Label(frame, text="DTI-ALPS Results", font=("TkDefaultFont", 12, "bold")).pack(
            anchor=tk.W, pady=10
        )

        # Results table
        results_frame = ttk.LabelFrame(frame, text="ALPS Index", padding=10)
        results_frame.pack(fill=tk.X, pady=5)

        # Create treeview for results
        columns = ("Metric", "Left", "Right", "Bilateral")
        tree = ttk.Treeview(results_frame, columns=columns, show="headings", height=6)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=120, anchor=tk.CENTER)

        # Add data rows
        alps_left = alps_results.get("ALPS_left", 0)
        alps_right = alps_results.get("ALPS_right", 0)
        alps_bilateral = alps_results.get("ALPS_bilateral", 0)

        tree.insert(
            "",
            tk.END,
            values=("ALPS Index", f"{alps_left:.4f}", f"{alps_right:.4f}", f"{alps_bilateral:.4f}"),
        )

        # Add component values
        tree.insert(
            "",
            tk.END,
            values=(
                "Dxx (proj)",
                f"{alps_results.get('Dxx_proj_left', 0):.6f}",
                f"{alps_results.get('Dxx_proj_right', 0):.6f}",
                "",
            ),
        )

        tree.insert(
            "",
            tk.END,
            values=(
                "Dxx (assoc)",
                f"{alps_results.get('Dxx_assoc_left', 0):.6f}",
                f"{alps_results.get('Dxx_assoc_right', 0):.6f}",
                "",
            ),
        )

        tree.insert(
            "",
            tk.END,
            values=(
                "Dyy (proj)",
                f"{alps_results.get('Dyy_proj_left', 0):.6f}",
                f"{alps_results.get('Dyy_proj_right', 0):.6f}",
                "",
            ),
        )

        tree.insert(
            "",
            tk.END,
            values=(
                "Dzz (assoc)",
                f"{alps_results.get('Dzz_assoc_left', 0):.6f}",
                f"{alps_results.get('Dzz_assoc_right', 0):.6f}",
                "",
            ),
        )

        tree.pack(fill=tk.X)

        # Export buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Save CSV Report", command=self._export_csv).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="View ROI Masks", command=self._view_rois).pack(
            side=tk.LEFT, padx=5
        )

    def _show_batch_results(self, batch_state: BatchState):
        """Display batch processing results."""
        # Switch to results stage
        self._show_stage(4)

        # Update results frame
        frame = self.stage_frames["results"]

        # Clear previous content
        for child in frame.winfo_children():
            child.destroy()

        # Title with summary
        title_frame = ttk.Frame(frame)
        title_frame.pack(fill=tk.X, pady=10)

        ttk.Label(
            title_frame, text="Batch Processing Results", font=("TkDefaultFont", 12, "bold")
        ).pack(side=tk.LEFT)

        summary_text = (
            f"{batch_state.success_count}/{batch_state.total_subjects} succeeded, "
            f"{batch_state.failed_count} failed"
        )
        ttk.Label(title_frame, text=f"  ({summary_text})").pack(side=tk.LEFT)

        # Results table
        results_frame = ttk.LabelFrame(frame, text="Subject Results", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Create treeview for batch results
        columns = ("subject", "alps_left", "alps_right", "alps_combined", "status")
        tree = ttk.Treeview(results_frame, columns=columns, show="headings", height=12)

        tree.heading("subject", text="Subject ID")
        tree.heading("alps_left", text="Left ALPS")
        tree.heading("alps_right", text="Right ALPS")
        tree.heading("alps_combined", text="Combined ALPS")
        tree.heading("status", text="Status")

        tree.column("subject", width=150)
        tree.column("alps_left", width=100, anchor=tk.CENTER)
        tree.column("alps_right", width=100, anchor=tk.CENTER)
        tree.column("alps_combined", width=100, anchor=tk.CENTER)
        tree.column("status", width=100, anchor=tk.CENTER)

        # Add scrollbar
        scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Add data rows
        for result in batch_state.results:
            alps_left = f"{result.alps_left:.4f}" if result.alps_left is not None else ""
            alps_right = f"{result.alps_right:.4f}" if result.alps_right is not None else ""
            alps_bi = f"{result.alps_bilateral:.4f}" if result.alps_bilateral is not None else ""

            tree.insert(
                "",
                tk.END,
                values=(result.subject_id, alps_left, alps_right, alps_bi, result.status),
            )

        self.batch_results_tree = tree  # Store for export

        # Export buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        # CSV path info
        csv_path = Path(batch_state.config.output_dir) / "alps_results.csv"
        ttk.Label(btn_frame, text=f"Results saved to: {csv_path}").pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame, text="Open Output Folder", command=self._open_batch_output_folder
        ).pack(side=tk.RIGHT, padx=5)

    def _open_batch_output_folder(self):
        """Open the batch output folder."""
        import subprocess
        import sys

        if self.batch_state and self.batch_state.config.output_dir:
            output_dir = self.batch_state.config.output_dir
            if Path(output_dir).exists():
                if sys.platform == "darwin":
                    subprocess.run(["open", output_dir])
                elif sys.platform == "linux":
                    subprocess.run(["xdg-open", output_dir])
                else:
                    subprocess.run(["explorer", output_dir])

    def _export_csv(self):
        """Export results to CSV."""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path and self.pipeline_state.alps_results:
            import csv

            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Metric", "Left", "Right", "Bilateral"])

                results = self.pipeline_state.alps_results
                writer.writerow(
                    [
                        "ALPS_Index",
                        results.get("ALPS_left", ""),
                        results.get("ALPS_right", ""),
                        results.get("ALPS_bilateral", ""),
                    ]
                )

            messagebox.showinfo("Export", f"Results saved to {path}")

    def _view_rois(self):
        """Open ROI directory."""
        import subprocess
        import sys

        roi_dir = self.pipeline_state.output_dir
        if roi_dir and Path(roi_dir).exists():
            if sys.platform == "darwin":
                subprocess.run(["open", roi_dir])
            elif sys.platform == "linux":
                subprocess.run(["xdg-open", roi_dir])
            else:
                subprocess.run(["explorer", roi_dir])

    def _load_settings(self):
        """Load settings from file."""
        messagebox.showinfo("Info", "Settings load not yet implemented")

    def _save_settings(self):
        """Save settings to file."""
        messagebox.showinfo("Info", "Settings save not yet implemented")

    def _show_about(self):
        """Show about dialog."""
        messagebox.showinfo(
            "About",
            f"{config.APP_NAME}\n"
            f"Version {config.APP_VERSION}\n\n"
            "Automatic DTI-ALPS ROI Placement and Analysis\n\n"
            "Uses MRtrix3 for preprocessing and DTI fitting.",
        )
