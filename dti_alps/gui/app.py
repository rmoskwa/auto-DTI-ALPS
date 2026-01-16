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
        """Create main layout with sidebar and content area."""
        # Main container
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left sidebar with Main Console and stage navigation
        self.sidebar = ttk.Frame(main_frame, width=200)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        self.sidebar.pack_propagate(False)

        # Main Console button at top
        console_label = ttk.Label(
            self.sidebar, text="Main Console", font=("TkDefaultFont", 10, "bold")
        )
        console_label.pack(pady=(10, 5))

        self.console_btn = ttk.Button(
            self.sidebar,
            text="Console",
            command=self._show_console,
            width=20,
        )
        self.console_btn.pack(pady=2, padx=5, fill=tk.X)

        # Separator
        ttk.Separator(self.sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10, padx=5)

        # Pipeline Stages section
        sidebar_label = ttk.Label(
            self.sidebar, text="Pipeline Stages", font=("TkDefaultFont", 10, "bold")
        )
        sidebar_label.pack(pady=(0, 5))

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

        # Content frame (changes with stage/console)
        self.content_frame = ttk.LabelFrame(right_frame, text="Settings", padding=10)
        self.content_frame.pack(fill=tk.BOTH, expand=True)

        # Create console frame
        self._create_console_frame()

        # Create all stage frames (hidden initially)
        self.stage_frames = {}
        self._create_data_frame()
        self._create_dwifslpreproc_frame()
        self._create_dwi2tensor_frame()
        self._create_tensor2metric_frame()
        self._create_roi_frame()
        self._create_results_frame()

        # Storage for CLI option variables (checkbox vars and entry vars)
        # Initialized by _create_cli_option_row calls in frame creation

    def _create_console_frame(self):
        """Create the Main Console frame with status treeview and log output."""
        frame = ttk.Frame(self.content_frame)
        self.console_frame = frame

        # Status section with subject treeview
        status_frame = ttk.LabelFrame(frame, text="Processing Status", padding=10)
        status_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # Treeview for subject status
        tree_frame = ttk.Frame(status_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("subject_id", "status")
        self.console_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=8,
        )

        self.console_tree.heading("subject_id", text="Subject ID")
        self.console_tree.heading("status", text="Status")

        self.console_tree.column("subject_id", width=200)
        self.console_tree.column("status", width=150)

        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.console_tree.yview)
        self.console_tree.configure(yscrollcommand=tree_scroll.set)

        self.console_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Console output section
        console_output_frame = ttk.LabelFrame(frame, text="Console Output", padding=10)
        console_output_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        # Log text area
        log_frame = ttk.Frame(console_output_frame)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, height=12, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _show_console(self):
        """Show the Main Console view."""
        # Update button states - deselect all stage buttons
        for btn in self.stage_buttons:
            btn.state(["!pressed"])
        self.console_btn.state(["pressed"])

        # Hide all stage frames
        for stage_frame in self.stage_frames.values():
            stage_frame.pack_forget()

        # Show console frame
        self.console_frame.pack(fill=tk.BOTH, expand=True)

        # Update frame title
        self.content_frame.config(text="Main Console")

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

        columns = ("subject_id", "folder", "files")
        self.subjects_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=8,
        )

        self.subjects_tree.heading("subject_id", text="Subject ID")
        self.subjects_tree.heading("folder", text="Folder Path")
        self.subjects_tree.heading("files", text="Files Found")

        self.subjects_tree.column("subject_id", width=150)
        self.subjects_tree.column("folder", width=400)
        self.subjects_tree.column("files", width=170)

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

    def _create_cli_option_row(
        self,
        parent: ttk.Frame,
        option_name: str,
        option_type: str,
        description: str,
        row: int,
        stage_prefix: str,
        filetypes: list | None = None,
        choices: list | None = None,
    ) -> None:
        """
        Create a CLI option row with checkbox + entry/combo + browse button.

        Parameters
        ----------
        parent : ttk.Frame
            Parent frame to add widgets to
        option_name : str
            CLI option name (e.g., "-eddy_mask")
        option_type : str
            Type: "file", "dir", "string", "int", "flag", "output", "prefix", "choice"
        description : str
            Description text shown to the right
        row : int
            Grid row number
        stage_prefix : str
            Prefix for variable storage (e.g., "dwifslpreproc")
        filetypes : list, optional
            File type filters for file browser
        choices : list, optional
            Choice values for "choice" type options
        """
        # Initialize option vars storage if needed
        if not hasattr(self, "cli_option_vars"):
            self.cli_option_vars = {}
        if stage_prefix not in self.cli_option_vars:
            self.cli_option_vars[stage_prefix] = {}

        # Create checkbox to enable/disable option
        enabled_var = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(parent, variable=enabled_var)
        chk.grid(row=row, column=0, sticky=tk.W, padx=2, pady=2)

        # Option name label
        name_label = ttk.Label(parent, text=option_name, width=18, anchor=tk.W)
        name_label.grid(row=row, column=1, sticky=tk.W, padx=2, pady=2)

        # Value entry/combo (depends on type)
        value_var = tk.StringVar()
        entry_widget = None
        browse_btn = None

        if option_type == "flag":
            # Flag options have no value entry
            entry_widget = None
        elif option_type == "choice" and choices:
            # Dropdown for choice options
            entry_widget = ttk.Combobox(
                parent, textvariable=value_var, values=choices, width=12, state="disabled"
            )
            entry_widget.grid(row=row, column=2, sticky=tk.W, padx=2, pady=2)
        elif option_type in ("file", "dir", "output", "prefix"):
            # Entry + Browse button for file/dir types
            entry_widget = ttk.Entry(parent, textvariable=value_var, width=35, state=tk.DISABLED)
            entry_widget.grid(row=row, column=2, sticky=tk.EW, padx=2, pady=2)

            if option_type in ("file", "prefix"):
                browse_btn = ttk.Button(
                    parent,
                    text="Browse...",
                    command=lambda v=value_var, ft=filetypes: self._browse_cli_file(v, ft),
                    state=tk.DISABLED,
                )
            elif option_type == "dir":
                browse_btn = ttk.Button(
                    parent,
                    text="Browse...",
                    command=lambda v=value_var: self._browse_cli_dir(v),
                    state=tk.DISABLED,
                )
            elif option_type == "output":
                browse_btn = ttk.Button(
                    parent,
                    text="Browse...",
                    command=lambda v=value_var, ft=filetypes: self._browse_cli_save(
                        v, ft or config.NIFTI_FILETYPES
                    ),
                    state=tk.DISABLED,
                )

            if browse_btn:
                browse_btn.grid(row=row, column=3, padx=2, pady=2)
        else:
            # String/int: just an entry
            entry_widget = ttk.Entry(parent, textvariable=value_var, width=15, state=tk.DISABLED)
            entry_widget.grid(row=row, column=2, sticky=tk.W, padx=2, pady=2)

        # Description label
        desc_label = ttk.Label(parent, text=description, foreground="gray")
        desc_label.grid(row=row, column=4, sticky=tk.W, padx=5, pady=2)

        # Store variables for later collection
        self.cli_option_vars[stage_prefix][option_name] = {
            "enabled_var": enabled_var,
            "value_var": value_var,
            "type": option_type,
            "entry_widget": entry_widget,
            "browse_btn": browse_btn,
        }

        # Bind checkbox to enable/disable entry and browse button
        def toggle_enabled(*args, ew=entry_widget, bb=browse_btn, ot=option_type):
            if enabled_var.get():
                if ew:
                    if ot == "choice":
                        ew.config(state="readonly")
                    else:
                        ew.config(state=tk.NORMAL)
                if bb:
                    bb.config(state=tk.NORMAL)
            else:
                if ew:
                    ew.config(state=tk.DISABLED)
                if bb:
                    bb.config(state=tk.DISABLED)

        enabled_var.trace_add("write", toggle_enabled)

    def _browse_cli_file(self, var: tk.StringVar, filetypes: list | None):
        """Browse for a file and set the variable."""
        ft = filetypes or [("All files", "*.*")]
        path = filedialog.askopenfilename(filetypes=ft)
        if path:
            var.set(path)

    def _browse_cli_dir(self, var: tk.StringVar):
        """Browse for a directory and set the variable."""
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _browse_cli_save(self, var: tk.StringVar, filetypes: list):
        """Browse for a save file location and set the variable."""
        path = filedialog.asksaveasfilename(filetypes=filetypes)
        if path:
            var.set(path)

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

                files_found = subject_files.get_files_summary()

                # Add to Data Input tree (without status)
                self.subjects_tree.insert(
                    "",
                    tk.END,
                    values=(
                        subject_files.subject_id,
                        folder_path,
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

    def _create_dwifslpreproc_frame(self):
        """Create dwifslpreproc options frame (Stage 2)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["dwifslpreproc"] = frame

        # Info text
        info_label = ttk.Label(
            frame,
            text="Configure optional CLI arguments for dwifslpreproc.\n"
            "Core parameters (PE direction, readout time, RPE scheme) are set in Data Input.",
            justify=tk.LEFT,
        )
        info_label.pack(anchor=tk.W, pady=(0, 10))

        # Scrollable frame for options
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
        options_frame = ttk.Frame(canvas)

        options_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=options_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Column headers
        ttk.Label(options_frame, text="", width=3).grid(row=0, column=0)
        ttk.Label(options_frame, text="Option", width=18, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(options_frame, text="Value", width=35, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=2, sticky=tk.W
        )
        ttk.Label(options_frame, text="", width=8).grid(row=0, column=3)
        ttk.Label(options_frame, text="Description", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=4, sticky=tk.W
        )

        ttk.Separator(options_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=5, sticky=tk.EW, pady=5
        )

        # Create option rows from config
        for i, (opt_name, opt_type, opt_desc, _default) in enumerate(config.DWIFSLPREPROC_OPTIONS):
            filetypes = None
            if opt_type == "file":
                if "mask" in opt_name:
                    filetypes = config.NIFTI_FILETYPES
                elif "json" in opt_name:
                    filetypes = config.JSON_FILETYPES
                elif "slspec" in opt_name:
                    filetypes = [("Text files", "*.txt"), ("All files", "*.*")]

            self._create_cli_option_row(
                options_frame,
                opt_name,
                opt_type,
                opt_desc,
                row=i + 2,
                stage_prefix="dwifslpreproc",
                filetypes=filetypes,
            )

        options_frame.columnconfigure(2, weight=1)

    def _create_dwi2tensor_frame(self):
        """Create dwi2tensor options frame (Stage 3)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["dwi2tensor"] = frame

        # Info text
        info_label = ttk.Label(
            frame,
            text="Configure optional CLI arguments for dwi2tensor (DTI fitting).\n"
            "The diffusion tensor will be computed from the preprocessed DWI data.",
            justify=tk.LEFT,
        )
        info_label.pack(anchor=tk.W, pady=(0, 10))

        # Options frame
        options_frame = ttk.LabelFrame(frame, text="dwi2tensor Options", padding=10)
        options_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Column headers
        ttk.Label(options_frame, text="", width=3).grid(row=0, column=0)
        ttk.Label(options_frame, text="Option", width=18, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(options_frame, text="Value", width=35, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=2, sticky=tk.W
        )
        ttk.Label(options_frame, text="", width=8).grid(row=0, column=3)
        ttk.Label(options_frame, text="Description", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=4, sticky=tk.W
        )

        ttk.Separator(options_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=5, sticky=tk.EW, pady=5
        )

        # Create option rows from config
        for i, (opt_name, opt_type, opt_desc, _default) in enumerate(config.DWI2TENSOR_OPTIONS):
            filetypes = None
            if opt_type == "file" and "mask" in opt_name:
                filetypes = config.NIFTI_FILETYPES
            elif opt_type == "output":
                filetypes = config.NIFTI_FILETYPES

            self._create_cli_option_row(
                options_frame,
                opt_name,
                opt_type,
                opt_desc,
                row=i + 2,
                stage_prefix="dwi2tensor",
                filetypes=filetypes,
            )

        options_frame.columnconfigure(2, weight=1)

    def _create_tensor2metric_frame(self):
        """Create tensor2metric options frame (Stage 4)."""
        frame = ttk.Frame(self.content_frame)
        self.stage_frames["tensor2metric"] = frame

        # Info text
        info_label = ttk.Label(
            frame,
            text="Configure optional CLI arguments for tensor2metric (metric extraction).\n"
            "FA and V1 are always computed (required for ALPS analysis).\n"
            "Additional metrics can be enabled below.",
            justify=tk.LEFT,
        )
        info_label.pack(anchor=tk.W, pady=(0, 10))

        # Options frame
        options_frame = ttk.LabelFrame(frame, text="tensor2metric Options", padding=10)
        options_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Column headers
        ttk.Label(options_frame, text="", width=3).grid(row=0, column=0)
        ttk.Label(options_frame, text="Option", width=18, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=1, sticky=tk.W
        )
        ttk.Label(options_frame, text="Value", width=35, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=2, sticky=tk.W
        )
        ttk.Label(options_frame, text="", width=8).grid(row=0, column=3)
        ttk.Label(options_frame, text="Description", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=4, sticky=tk.W
        )

        ttk.Separator(options_frame, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=5, sticky=tk.EW, pady=5
        )

        # Create option rows from config
        for i, (opt_name, opt_type, opt_desc, _default) in enumerate(config.TENSOR2METRIC_OPTIONS):
            filetypes = None
            choices = None
            if opt_type == "file" and "mask" in opt_name:
                filetypes = config.NIFTI_FILETYPES
            elif opt_type == "output":
                filetypes = config.NIFTI_FILETYPES
            elif opt_type == "choice" and opt_name == "-modulate":
                choices = config.TENSOR2METRIC_MODULATE_CHOICES

            self._create_cli_option_row(
                options_frame,
                opt_name,
                opt_type,
                opt_desc,
                row=i + 2,
                stage_prefix="tensor2metric",
                filetypes=filetypes,
                choices=choices,
            )

        options_frame.columnconfigure(2, weight=1)

    def _create_roi_frame(self):
        """Create ROI detection parameters frame (Stage 5)."""
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
        """Create results display frame (Stage 6)."""
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

        # Add Results Viewer button (always available)
        viewer_frame = ttk.Frame(frame)
        viewer_frame.pack(fill=tk.X, pady=10)

        ttk.Button(
            viewer_frame,
            text="Open Results Viewer...",
            command=self._open_results_viewer,
        ).pack(side=tk.LEFT, padx=5)

        ttk.Label(
            viewer_frame,
            text="(View any previously processed results)",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=5)

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

        # Update button states - deselect console button
        self.console_btn.state(["!pressed"])

        # Update stage button states
        for i, btn in enumerate(self.stage_buttons):
            if i == stage_idx:
                btn.state(["pressed"])
            else:
                btn.state(["!pressed"])

        # Hide console frame and all stage frames
        self.console_frame.pack_forget()
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

    def _collect_cli_options(self, stage_prefix: str) -> dict[str, any]:
        """
        Collect enabled CLI options from a stage into a dictionary.

        Parameters
        ----------
        stage_prefix : str
            Stage prefix (e.g., "dwifslpreproc", "dwi2tensor", "tensor2metric")

        Returns
        -------
        dict
            Dictionary of option_name -> value for enabled options
        """
        options = {}
        if not hasattr(self, "cli_option_vars"):
            return options

        stage_vars = self.cli_option_vars.get(stage_prefix, {})
        for option_name, var_info in stage_vars.items():
            if not var_info["enabled_var"].get():
                continue  # Option not enabled

            opt_type = var_info["type"]
            if opt_type == "flag":
                # Flag option: just True when enabled
                options[option_name] = True
            else:
                # Value option: get the value
                value = var_info["value_var"].get()
                if value:  # Only add non-empty values
                    if opt_type == "int":
                        try:
                            options[option_name] = int(value)
                        except ValueError:
                            pass  # Skip invalid int
                    else:
                        options[option_name] = value

        return options

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

        # Collect CLI options from each stage
        dwifslpreproc_options = self._collect_cli_options("dwifslpreproc")
        dwi2tensor_options = self._collect_cli_options("dwi2tensor")
        tensor2metric_options = self._collect_cli_options("tensor2metric")

        # Create batch config
        batch_config = BatchConfig(
            pe_direction=self.pe_dir_var.get(),
            auto_pe_direction=self.pe_auto_var.get(),
            readout_time=readout_time,
            rpe_scheme=self.rpe_var.get(),
            # CLI options dicts from new GUI
            dwifslpreproc_options=dwifslpreproc_options,
            dwi2tensor_options=dwi2tensor_options,
            tensor2metric_options=tensor2metric_options,
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

        # Clear log and console tree
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Clear and populate console tree with subjects
        for item in self.console_tree.get_children():
            self.console_tree.delete(item)
        for subject_files in self.subject_files_list:
            self.console_tree.insert("", tk.END, values=(subject_files.subject_id, "Pending"))

        # Switch to console view
        self._show_console()
        self._log("Starting batch processing...")

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
            self._log(f"Processing 0/{total} subjects")
        elif msg_type == "subject_start":
            index, subject_id = data
            total = len(self.subject_files_list)
            self._log(f"Processing {index + 1}/{total}: {subject_id}")
            # Update console tree status
            items = self.console_tree.get_children()
            if index < len(items):
                self.console_tree.set(items[index], "status", "Processing")
        elif msg_type == "subject_complete":
            index, result = data
            total = len(self.subject_files_list)
            completed = index + 1

            self._log(f"Completed {completed}/{total} subjects")

            # Update console tree status
            items = self.console_tree.get_children()
            if index < len(items):
                status = "Completed" if result.status == "completed" else "Failed"
                self.console_tree.set(items[index], "status", status)
        elif msg_type == "batch_complete":
            batch_state = data
            self._log(
                f"Batch complete: {batch_state.success_count}/{batch_state.total_subjects} succeeded"
            )
            self._show_batch_results(batch_state)
        elif msg_type == "batch_success":
            batch_state = data
            self._log("All subjects processed successfully!")
            self._show_batch_results(batch_state)
        elif msg_type == "batch_partial":
            batch_state = data
            self._log(
                f"Batch completed with errors: {batch_state.success_count}/"
                f"{batch_state.total_subjects} succeeded"
            )
            self._show_batch_results(batch_state)
        elif msg_type == "batch_cancelled":
            self._log("Batch processing cancelled.")
        elif msg_type == "complete":
            # Single subject complete (legacy)
            self._log("Pipeline completed successfully!")
            self._show_results(data)
        elif msg_type == "failed":
            self._log("Pipeline failed.")
        elif msg_type == "cancelled":
            self._log("Pipeline cancelled.")
        elif msg_type == "error":
            self._log(f"Error: {data}")

    def _log(self, message):
        """Append message to log."""
        from datetime import datetime

        timestamp = datetime.now().strftime("[%H:%M:%S]")

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{timestamp} {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _update_stage_status(self, stage, status):
        """Update stage indicator (logs status changes)."""
        stage_names = {
            "preproc": "Preprocessing",
            "dti": "DTI Fitting",
            "roi": "ROI Detection",
            "results": "Calculating ALPS",
        }

        stage_name = stage_names.get(stage, stage)
        if status == "running":
            self._log(f"Running: {stage_name}")
        elif status == "complete":
            self._log(f"Completed: {stage_name}")

    def _show_results(self, alps_results):
        """Display ALPS results."""
        if not alps_results:
            return

        # Switch to results stage (stage 6, index 5)
        self._show_stage(5)

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
        # Switch to results stage (stage 6, index 5)
        self._show_stage(5)

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

        ttk.Button(
            btn_frame,
            text="Open Results Viewer",
            command=lambda: self._open_results_viewer(batch_state.config.output_dir),
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

    def _open_results_viewer(self, output_folder: str | None = None):
        """Open the results viewer window."""
        from .viewer import ResultsViewer

        # Use current output dir if not specified and available
        if output_folder is None and self.batch_state:
            output_folder = self.batch_state.config.output_dir

        viewer = ResultsViewer(self, output_folder)
        viewer.focus_set()

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
