"""
Main application window for DTI-ALPS Processing Tool.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue
import threading
from pathlib import Path

from . import config
from .processing.pipeline import PipelineState, PipelineRunner, PipelineWorker
from .processing import validators


class DTIALPSApplication(tk.Tk):
    """
    Main application window for DTI-ALPS processing.

    Features:
    - Simple/Advanced mode toggle
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
        self.mode = tk.StringVar(value="simple")
        self.current_stage = 0
        self.pipeline_state = PipelineState()
        self.worker = None
        self.result_queue = None
        self.cancel_event = None

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
        """Create toolbar with mode toggle and action buttons."""
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        # Mode toggle
        mode_frame = ttk.LabelFrame(toolbar, text="Mode", padding=5)
        mode_frame.pack(side=tk.LEFT, padx=5)

        ttk.Radiobutton(
            mode_frame, text="Simple", variable=self.mode,
            value="simple", command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=5)

        ttk.Radiobutton(
            mode_frame, text="Advanced", variable=self.mode,
            value="advanced", command=self._on_mode_change
        ).pack(side=tk.LEFT, padx=5)

        # Spacer
        ttk.Frame(toolbar).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Action buttons
        self.run_btn = ttk.Button(
            toolbar, text="Run Pipeline", command=self._run_pipeline
        )
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

        sidebar_label = ttk.Label(self.sidebar, text="Pipeline Stages",
                                  font=('TkDefaultFont', 10, 'bold'))
        sidebar_label.pack(pady=10)

        self.stage_buttons = []
        for i, (stage_id, stage_name) in enumerate(config.PIPELINE_STAGES):
            btn = ttk.Button(
                self.sidebar, text=f"{i+1}. {stage_name}",
                command=lambda idx=i: self._show_stage(idx),
                width=20
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
        self.progress_bar = ttk.Progressbar(
            progress_top, variable=self.progress_var, maximum=100
        )
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
        """Create data acquisition frame (Stage 1)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["data"] = frame

        # Required files section
        req_frame = ttk.LabelFrame(frame, text="Required Files", padding=10)
        req_frame.pack(fill=tk.X, pady=5)

        # DWI file
        self._create_file_row(req_frame, "DWI Image:", "dwi",
                             config.NIFTI_FILETYPES, 0)

        # bvecs file
        self._create_file_row(req_frame, "bvecs File:", "bvecs",
                             config.BVEC_FILETYPES, 1)

        # bvals file
        self._create_file_row(req_frame, "bvals File:", "bvals",
                             config.BVAL_FILETYPES, 2)

        # Phase encoding section
        pe_frame = ttk.LabelFrame(frame, text="Phase Encoding", padding=10)
        pe_frame.pack(fill=tk.X, pady=5)

        # PE direction
        ttk.Label(pe_frame, text="Direction:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.pe_dir_var = tk.StringVar(value=config.DEFAULT_PE_DIRECTION)
        pe_combo = ttk.Combobox(pe_frame, textvariable=self.pe_dir_var,
                                values=config.PE_DIRECTIONS, width=10, state="readonly")
        pe_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        # Readout time
        ttk.Label(pe_frame, text="Readout Time (s):").grid(row=0, column=2, sticky=tk.W, padx=(20, 0), pady=2)
        self.readout_var = tk.StringVar(value=str(config.DEFAULT_READOUT_TIME))
        readout_entry = ttk.Entry(pe_frame, textvariable=self.readout_var, width=10)
        readout_entry.grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        # RPE scheme section
        rpe_frame = ttk.LabelFrame(frame, text="Reverse Phase Encoding", padding=10)
        rpe_frame.pack(fill=tk.X, pady=5)

        self.rpe_var = tk.StringVar(value=config.DEFAULT_RPE_SCHEME)

        for i, (scheme, desc) in enumerate(config.RPE_SCHEMES.items()):
            ttk.Radiobutton(
                rpe_frame, text=f"{scheme}: {desc}",
                variable=self.rpe_var, value=scheme,
                command=self._on_rpe_change
            ).grid(row=i, column=0, columnspan=3, sticky=tk.W, pady=2)

        # Reverse PE file (conditional)
        self.reverse_pe_frame = ttk.Frame(rpe_frame)
        self.reverse_pe_frame.grid(row=len(config.RPE_SCHEMES), column=0, columnspan=3, sticky=tk.EW, pady=5)

        ttk.Label(self.reverse_pe_frame, text="Reverse PE b=0:").pack(side=tk.LEFT)
        self.reverse_pe_var = tk.StringVar()
        self.reverse_pe_entry = ttk.Entry(self.reverse_pe_frame, textvariable=self.reverse_pe_var, width=50)
        self.reverse_pe_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(self.reverse_pe_frame, text="Browse...",
                  command=lambda: self._browse_file("reverse_pe", config.NIFTI_FILETYPES)).pack(side=tk.LEFT)

        self._on_rpe_change()  # Set initial state

        # Optional files (advanced only)
        self.json_frame = ttk.LabelFrame(frame, text="Optional Files (Advanced)", padding=10)
        self.json_frame.pack(fill=tk.X, pady=5)

        self._create_file_row(self.json_frame, "JSON Sidecar:", "json",
                             config.JSON_FILETYPES, 0)

        # Output settings
        out_frame = ttk.LabelFrame(frame, text="Output", padding=10)
        out_frame.pack(fill=tk.X, pady=5)

        ttk.Label(out_frame, text="Output Directory:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.output_dir_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.output_dir_var, width=50).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(out_frame, text="Browse...", command=self._browse_output_dir).grid(row=0, column=2, pady=2)

        ttk.Label(out_frame, text="Output Prefix:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.output_prefix_var = tk.StringVar(value="subject")
        ttk.Entry(out_frame, textvariable=self.output_prefix_var, width=20).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        out_frame.columnconfigure(1, weight=1)

    def _create_preproc_frame(self):
        """Create preprocessing options frame (Stage 2, Advanced only)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["preproc"] = frame

        # Eddy options
        eddy_frame = ttk.LabelFrame(frame, text="Eddy Options", padding=10)
        eddy_frame.pack(fill=tk.X, pady=5)

        self._create_file_row(eddy_frame, "Processing Mask:", "eddy_mask",
                             config.NIFTI_FILETYPES, 0)
        self._create_file_row(eddy_frame, "Slice Spec File:", "eddy_slspec",
                             [("Text files", "*.txt"), ("All files", "*.*")], 1)

        ttk.Label(eddy_frame, text="Extra Options:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.eddy_options_var = tk.StringVar()
        ttk.Entry(eddy_frame, textvariable=self.eddy_options_var, width=50).grid(row=2, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Label(eddy_frame, text="e.g., --repol --data_is_shelled").grid(row=2, column=2, sticky=tk.W, padx=5)

        eddy_frame.columnconfigure(1, weight=1)

        # Topup options
        topup_frame = ttk.LabelFrame(frame, text="Topup Options", padding=10)
        topup_frame.pack(fill=tk.X, pady=5)

        ttk.Label(topup_frame, text="Extra Options:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.topup_options_var = tk.StringVar()
        ttk.Entry(topup_frame, textvariable=self.topup_options_var, width=50).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)

        topup_frame.columnconfigure(1, weight=1)

        # QC options
        qc_frame = ttk.LabelFrame(frame, text="Quality Control", padding=10)
        qc_frame.pack(fill=tk.X, pady=5)

        self.generate_qc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(qc_frame, text="Generate eddy QC reports",
                       variable=self.generate_qc_var).pack(anchor=tk.W)

        self.keep_intermediate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(qc_frame, text="Keep intermediate files",
                       variable=self.keep_intermediate_var).pack(anchor=tk.W)

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
            justify=tk.LEFT
        )
        info_label.pack(anchor=tk.W, pady=20)

        # Optional mask
        mask_frame = ttk.LabelFrame(frame, text="Optional Settings", padding=10)
        mask_frame.pack(fill=tk.X, pady=5)

        self._create_file_row(mask_frame, "DTI Mask:", "dti_mask",
                             config.NIFTI_FILETYPES, 0)

    def _create_roi_frame(self):
        """Create ROI detection parameters frame (Stage 4)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["roi"] = frame

        info_label = ttk.Label(
            frame,
            text="Configure parameters for automatic ROI detection.\n"
                 "The algorithm will find optimal locations for projection and association fiber ROIs.",
            justify=tk.LEFT
        )
        info_label.pack(anchor=tk.W, pady=10)

        # Parameters
        param_frame = ttk.LabelFrame(frame, text="Detection Parameters", padding=10)
        param_frame.pack(fill=tk.X, pady=5)

        # FA threshold
        row = 0
        ttk.Label(param_frame, text="FA Threshold:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.fa_thresh_var = tk.DoubleVar(value=config.DEFAULT_FA_THRESH)
        fa_scale = ttk.Scale(param_frame, from_=0.1, to=0.5, variable=self.fa_thresh_var,
                            orient=tk.HORIZONTAL, length=200)
        fa_scale.grid(row=row, column=1, sticky=tk.W, padx=5)
        self.fa_thresh_label = ttk.Label(param_frame, text=f"{config.DEFAULT_FA_THRESH:.2f}")
        self.fa_thresh_label.grid(row=row, column=2, sticky=tk.W)
        fa_scale.config(command=lambda v: self.fa_thresh_label.config(text=f"{float(v):.2f}"))

        # Orientation threshold
        row += 1
        ttk.Label(param_frame, text="Orientation Threshold:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.orient_thresh_var = tk.DoubleVar(value=config.DEFAULT_ORIENT_THRESH)
        orient_scale = ttk.Scale(param_frame, from_=0.5, to=0.9, variable=self.orient_thresh_var,
                                orient=tk.HORIZONTAL, length=200)
        orient_scale.grid(row=row, column=1, sticky=tk.W, padx=5)
        self.orient_thresh_label = ttk.Label(param_frame, text=f"{config.DEFAULT_ORIENT_THRESH:.2f}")
        self.orient_thresh_label.grid(row=row, column=2, sticky=tk.W)
        orient_scale.config(command=lambda v: self.orient_thresh_label.config(text=f"{float(v):.2f}"))

        # Min zone width
        row += 1
        ttk.Label(param_frame, text="Min Zone Width (voxels):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.min_width_var = tk.IntVar(value=config.DEFAULT_MIN_ZONE_WIDTH)
        ttk.Spinbox(param_frame, from_=3, to=15, textvariable=self.min_width_var, width=5).grid(row=row, column=1, sticky=tk.W, padx=5)

        # ROI radius
        row += 1
        ttk.Label(param_frame, text="ROI Radius (mm):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.roi_radius_var = tk.DoubleVar(value=config.DEFAULT_ROI_RADIUS_MM)
        ttk.Spinbox(param_frame, from_=2.0, to=8.0, increment=0.5, textvariable=self.roi_radius_var, width=5).grid(row=row, column=1, sticky=tk.W, padx=5)

        # Z tolerance
        row += 1
        ttk.Label(param_frame, text="Z-Tolerance (voxels):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self.z_tolerance_var = tk.IntVar(value=config.DEFAULT_Z_TOLERANCE)
        ttk.Spinbox(param_frame, from_=0, to=5, textvariable=self.z_tolerance_var, width=5).grid(row=row, column=1, sticky=tk.W, padx=5)

    def _create_results_frame(self):
        """Create results display frame (Stage 5)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["results"] = frame

        # Results will be populated after processing
        self.results_label = ttk.Label(
            frame,
            text="Results will be displayed here after processing completes.",
            justify=tk.LEFT
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

        btn = ttk.Button(parent, text="Browse...",
                        command=lambda: self._browse_file(var_name, filetypes))
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

    def _on_mode_change(self):
        """Handle mode toggle."""
        mode = self.mode.get()

        # Show/hide advanced-only elements
        if hasattr(self, 'json_frame'):
            if mode == "advanced":
                self.json_frame.pack(fill=tk.X, pady=5)
            else:
                self.json_frame.pack_forget()

        # Refresh current stage
        self._show_stage(self.current_stage)

    def _on_rpe_change(self):
        """Handle RPE scheme change."""
        scheme = self.rpe_var.get()

        # Enable/disable reverse PE file selection
        if scheme == "pair":
            for child in self.reverse_pe_frame.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Button)):
                    child.config(state=tk.NORMAL)
        else:
            for child in self.reverse_pe_frame.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Button)):
                    child.config(state=tk.DISABLED)

    def _show_stage(self, stage_idx):
        """Show the specified pipeline stage."""
        self.current_stage = stage_idx

        # Update button states
        for i, btn in enumerate(self.stage_buttons):
            if i == stage_idx:
                btn.state(['pressed'])
            else:
                btn.state(['!pressed'])

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

        # Handle mode-specific visibility
        if stage_id == "preproc" and self.mode.get() == "simple":
            # Show message that advanced mode is needed
            for child in frame.winfo_children():
                child.pack_forget()
            ttk.Label(
                frame,
                text="Preprocessing options are available in Advanced mode.\n\n"
                     "Default settings will be used in Simple mode.",
                justify=tk.LEFT
            ).pack(anchor=tk.W, pady=20)

    def _collect_state(self):
        """Collect all UI values into pipeline state."""
        state = self.pipeline_state

        # Input files
        state.dwi_path = self.dwi_var.get() or None
        state.bvecs_path = self.bvecs_var.get() or None
        state.bvals_path = self.bvals_var.get() or None
        state.reverse_pe_path = self.reverse_pe_var.get() or None

        # Phase encoding
        state.pe_direction = self.pe_dir_var.get()
        try:
            state.readout_time = float(self.readout_var.get())
        except ValueError:
            state.readout_time = config.DEFAULT_READOUT_TIME
        state.rpe_scheme = self.rpe_var.get()

        # Advanced options
        if self.mode.get() == "advanced":
            state.json_sidecar_path = getattr(self, 'json_var', tk.StringVar()).get() or None
            state.eddy_mask_path = getattr(self, 'eddy_mask_var', tk.StringVar()).get() or None
            state.eddy_slspec_path = getattr(self, 'eddy_slspec_var', tk.StringVar()).get() or None
            state.eddy_options = self.eddy_options_var.get()
            state.topup_options = self.topup_options_var.get()
            state.generate_qc = self.generate_qc_var.get()
            state.keep_intermediates = self.keep_intermediate_var.get()
            state.dti_mask_path = getattr(self, 'dti_mask_var', tk.StringVar()).get() or None

        # ROI detection parameters
        state.fa_thresh = self.fa_thresh_var.get()
        state.orient_thresh = self.orient_thresh_var.get()
        state.min_zone_width = self.min_width_var.get()
        state.roi_radius_mm = self.roi_radius_var.get()
        state.z_tolerance = self.z_tolerance_var.get()

        # Output
        state.output_dir = self.output_dir_var.get()
        state.output_prefix = self.output_prefix_var.get() or "subject"

        return state

    def _run_pipeline(self):
        """Start pipeline execution."""
        # Collect state from UI
        state = self._collect_state()

        # Validate
        errors = validators.validate_pipeline_state(state)
        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors))
            return

        # Disable UI
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Create worker
        self.result_queue = queue.Queue()
        self.cancel_event = threading.Event()

        runner = PipelineRunner(state)
        self.worker = PipelineWorker(runner, self.result_queue, self.cancel_event)
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
        elif msg_type == "complete":
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
            "results": "Calculating ALPS"
        }

        stage_progress = {
            "preproc": 25,
            "dti": 50,
            "roi": 75,
            "results": 90
        }

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
        ttk.Label(frame, text="DTI-ALPS Results",
                 font=('TkDefaultFont', 12, 'bold')).pack(anchor=tk.W, pady=10)

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
        alps_left = alps_results.get('ALPS_left', 0)
        alps_right = alps_results.get('ALPS_right', 0)
        alps_bilateral = alps_results.get('ALPS_bilateral', 0)

        tree.insert("", tk.END, values=(
            "ALPS Index",
            f"{alps_left:.4f}",
            f"{alps_right:.4f}",
            f"{alps_bilateral:.4f}"
        ))

        # Add component values
        tree.insert("", tk.END, values=(
            "Dxx (proj)",
            f"{alps_results.get('Dxx_proj_left', 0):.6f}",
            f"{alps_results.get('Dxx_proj_right', 0):.6f}",
            ""
        ))

        tree.insert("", tk.END, values=(
            "Dxx (assoc)",
            f"{alps_results.get('Dxx_assoc_left', 0):.6f}",
            f"{alps_results.get('Dxx_assoc_right', 0):.6f}",
            ""
        ))

        tree.insert("", tk.END, values=(
            "Dyy (proj)",
            f"{alps_results.get('Dyy_proj_left', 0):.6f}",
            f"{alps_results.get('Dyy_proj_right', 0):.6f}",
            ""
        ))

        tree.insert("", tk.END, values=(
            "Dzz (assoc)",
            f"{alps_results.get('Dzz_assoc_left', 0):.6f}",
            f"{alps_results.get('Dzz_assoc_right', 0):.6f}",
            ""
        ))

        tree.pack(fill=tk.X)

        # Export buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="Save CSV Report",
                  command=self._export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="View ROI Masks",
                  command=self._view_rois).pack(side=tk.LEFT, padx=5)

    def _export_csv(self):
        """Export results to CSV."""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path and self.pipeline_state.alps_results:
            import csv
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Metric", "Left", "Right", "Bilateral"])

                results = self.pipeline_state.alps_results
                writer.writerow([
                    "ALPS_Index",
                    results.get('ALPS_left', ''),
                    results.get('ALPS_right', ''),
                    results.get('ALPS_bilateral', '')
                ])

            messagebox.showinfo("Export", f"Results saved to {path}")

    def _view_rois(self):
        """Open ROI directory."""
        import subprocess
        import sys

        roi_dir = self.pipeline_state.output_dir
        if roi_dir and Path(roi_dir).exists():
            if sys.platform == 'darwin':
                subprocess.run(['open', roi_dir])
            elif sys.platform == 'linux':
                subprocess.run(['xdg-open', roi_dir])
            else:
                subprocess.run(['explorer', roi_dir])

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
            "Uses MRtrix3 for preprocessing and DTI fitting."
        )
