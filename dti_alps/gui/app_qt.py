"""
Main application window for DTI-ALPS Processing Tool (PySide6 adapter).

This is the Qt port of ``app.py`` (PRD 0013). It is a ``QMainWindow`` adapter
over the **unchanged** tk-free models: it reads Qt widgets into a
:class:`~dti_alps.gui.form_model.FormState` and delegates to ``form_model``
(``build_batch_state`` / ``compute_readiness``) on the input side, and drains the
worker's typed :class:`~dti_alps.processing.messages.WorkerMessage` stream through
:class:`~dti_alps.gui.result_model.ResultModel` on the output side. No
input/output/science logic is reimplemented here.

Threading is unchanged from the Tk app: a ``QTimer`` drains the same
``queue.Queue`` the workers write to, so ``processing/`` stays Qt-free.

Two behavior changes from the Tk window are deliberate and authorized (PRD 0013,
Scope): the sidebar stage buttons no longer recolor during a run (status coloring
dropped, Decision 6), and a working Cancel button is added (Decision 7).

Built alongside ``app.py`` behind the temporary ``--gui-qt`` flag until the final
flip.
"""

import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..processing.discovery import (
    SubjectFiles,
    discover_with_subdir_fallback,
    new_unique_runs,
)
from ..processing.pipeline import (
    BatchState,
)
from . import config
from .form_model import (
    FormState,
    OptionState,
    collect_output_config,
    compute_readiness,
)
from .result_model import (
    AppendLog,
    BatchResultsView,
    ResetStageButtons,
    SetRowStatus,
    ShowBatchResults,
    UpdateStageStatus,
)
from .user_config import UserConfig, get_user_config

# One-line QSS for the prominent green Run button — the whole app's only styling
# (PRD 0013, Decision 6). Disabled state greys out via the ``:disabled`` rule.
_RUN_BUTTON_QSS = """
QPushButton {
    background-color: #5cb85c;
    color: white;
    font-weight: bold;
    padding: 8px 20px;
}
QPushButton:hover:enabled { background-color: #4a9f4a; }
QPushButton:disabled { background-color: #cccccc; color: #666666; }
"""

# Console-tree status tag -> foreground colour, mirroring the Tk viewer's
# ``tag_configure`` map (Decision 8).
_ROW_TAG_COLORS = {
    "processing": config.COLORS["processing"],
    "completed": config.COLORS["success"],
    "failed": config.COLORS["error"],
}


def _qt_name_filter(filetypes: list | None) -> str:
    """Translate Tk ``filetypes`` tuples to a Qt name-filter string.

    ``[("NIfTI files", "*.nii *.nii.gz"), ("All files", "*.*")]`` becomes
    ``"NIfTI files (*.nii *.nii.gz);;All files (*.*)"``.
    """
    if not filetypes:
        return "All files (*.*)"
    return ";;".join(f"{label} ({patterns})" for label, patterns in filetypes)


class DTIALPSApplication(QMainWindow):
    """
    Main application window for DTI-ALPS processing (Qt adapter).

    Features:
    - Pipeline stage navigation (checkable button column + QStackedWidget)
    - Progress tracking and logging
    - Background processing
    """

    # Initial (width, alignment) for each batch-results column, keyed by the
    # stable column key from build_batch_results_table. Mirrors the Tk adapter's
    # _BATCH_COLUMN_LAYOUT (Decision 8): a key-based map collapsing the former
    # per-method subject/status widths to a single value.
    _BATCH_COLUMN_LAYOUT = {
        "subject": (120, Qt.AlignLeft),
        "lab_left": (80, Qt.AlignCenter),
        "lab_right": (80, Qt.AlignCenter),
        "lab_combined": (90, Qt.AlignCenter),
        "pas_left": (80, Qt.AlignCenter),
        "pas_right": (80, Qt.AlignCenter),
        "pas_combined": (90, Qt.AlignCenter),
        "alps_left": (100, Qt.AlignCenter),
        "alps_right": (100, Qt.AlignCenter),
        "alps_combined": (100, Qt.AlignCenter),
        "status": (80, Qt.AlignCenter),
    }
    _BATCH_COLUMN_DEFAULT = (100, Qt.AlignCenter)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(config.APP_NAME)
        self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        self.setMinimumSize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)

        # State
        self.current_stage = 0
        self.worker = None
        self.result_queue = None
        self.result_model = None  # Presentation model translating worker messages to intents
        self.cancel_event: threading.Event | None = None
        self.log_file = None  # File handle for log output
        self.log_file_path: str | None = None

        # Batch processing state
        self.subject_files_list: list[SubjectFiles] = []
        self.batch_state: BatchState | None = None

        # Per-page widgets (all pages resident in the stack); the synB0 toggle
        # only rebuilds the stage *button* column, never the pages (Decision 6).
        self.pages: dict[str, QWidget] = {}
        self.stage_buttons: list[QPushButton] = []

        # CLI option-row handles, keyed stage -> option name (Decision 9); the Qt
        # twin of the Tk adapter's ``cli_option_vars``. Populated in region (c-i).
        self.cli_option_rows: dict[str, dict] = {}
        # ROI-shape / output-retention checkboxes, populated in region (c-ii).
        self.roi_shape_checks: dict[str, QWidget] = {}
        self.output_option_checks: dict[str, QWidget] = {}

        # Build UI
        self._create_menu()
        self._create_toolbar()
        self._create_main_layout()

        # Initialize first stage
        self._show_stage(0)
        self._update_run_button_state()

    # ------------------------------------------------------------------ #
    # Form snapshot
    # ------------------------------------------------------------------ #
    def _txt(self, name: str, default: str = "") -> str:
        w = getattr(self, name, None)
        return w.text() if w is not None else default

    def _checked(self, name: str, default: bool) -> bool:
        w = getattr(self, name, None)
        return w.isChecked() if w is not None else default

    def _combo(self, name: str, default: str) -> str:
        w = getattr(self, name, None)
        return w.currentText() if w is not None else default

    def _form_state(self) -> FormState:
        """
        Snapshot the Qt form widgets into a tk-free ``FormState``.

        Every widget is read through a "not built yet" fallback so this stays
        safe when called during construction (before later-region widgets
        exist) — the Qt analogue of the Tk adapter's lifecycle-safe reads. The
        model always receives a fully-populated snapshot.

        Unlike the Tk snapshot there is no ``TclError`` guard for FA: FA is a
        bounded ``QDoubleSpinBox`` (Decision 10), so ``.value()`` is always a
        valid float. Readout stays free-text and its raw string flows through
        unchanged, so ``compute_readiness`` still blocks Run on an invalid
        readout.
        """
        fa_spin = getattr(self, "fa_threshold_spin", None)
        fa_threshold = fa_spin.value() if fa_spin is not None else config.FA_THRESHOLD

        roi_shape_flags = {key: chk.isChecked() for key, chk in self.roi_shape_checks.items()}
        output_flags = {key: chk.isChecked() for key, chk in self.output_option_checks.items()}
        cli_options = {
            stage: {
                name: OptionState(
                    enabled=handle["is_enabled"](),
                    value=handle["value"](),
                    type=handle["type"],
                )
                for name, handle in stage_rows.items()
            }
            for stage, stage_rows in self.cli_option_rows.items()
        }

        return FormState(
            run_denoising=self._checked("run_denoising_check", True),
            run_degibbs=self._checked("run_degibbs_check", True),
            pe_direction=self._combo("pe_combo", config.DEFAULT_PE_DIRECTION),
            auto_pe_direction=self._checked("pe_auto_check", True),
            readout_auto=self._checked("readout_auto_check", True),
            readout_raw=self._txt("readout_edit", str(config.DEFAULT_READOUT_TIME)),
            rpe_scheme=self._combo("rpe_combo", config.DEFAULT_RPE_SCHEME),
            use_synb0=self._checked("synb0_check", False),
            synb0_output_dir_raw=self._txt("synb0_output_dir_edit", ""),
            fa_threshold=fa_threshold,
            alps_method=self._combo("alps_method_combo", config.DEFAULT_ALPS_METHOD),
            refine_roi_placement=self._combo("refine_roi_combo", config.DEFAULT_ROI_REFINEMENT),
            output_dir=self._txt("output_dir_edit", ""),
            staging_enabled=self._checked("staging_enabled_check", False),
            staging_dir_raw=self._txt("staging_dir_edit", ""),
            roi_shape_flags=roi_shape_flags,
            output_flags=output_flags,
            cli_options=cli_options,
        )

    def _update_run_button_state(self):
        """Enable/disable the Run button from the current form snapshot."""
        readiness = compute_readiness(self._form_state(), self.subject_files_list)
        # While a run is in flight, Run stays disabled regardless of readiness.
        running = self.worker is not None and self.worker.is_alive()
        self.run_btn.setEnabled(readiness.can_run and not running)

    # ------------------------------------------------------------------ #
    # Menu / toolbar
    # ------------------------------------------------------------------ #
    def _create_menu(self):
        """Create the menu bar (File / Help)."""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _create_toolbar(self):
        """Create the centered two-button toolbar (Run + Cancel)."""
        self.toolbar_widget = QWidget()
        layout = QHBoxLayout(self.toolbar_widget)
        layout.addStretch()

        self.run_btn = QPushButton("Run Pipeline")
        self.run_btn.setStyleSheet(_RUN_BUTTON_QSS)
        self.run_btn.clicked.connect(self._run_pipeline)
        self.run_btn.setEnabled(False)
        layout.addWidget(self.run_btn)

        # Cancel is created here but stays inert until the run path lands (region
        # d). Disabled except during a run (Decision 7).
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_pipeline)
        self.cancel_btn.setEnabled(False)
        layout.addWidget(self.cancel_btn)

        layout.addStretch()

    def _create_main_layout(self):
        """Create the sidebar (nav) + content stack."""
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.addWidget(self.toolbar_widget)

        body = QHBoxLayout()
        outer.addLayout(body)

        # Left sidebar: nav column
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.sidebar)

        # Exclusive group so exactly one nav button is :checked at a time
        # (native selection marks the current stage — Decision 6).
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        # Main Console
        sidebar_layout.addWidget(_bold(QLabel("Main Console")))
        self.console_btn = self._nav_button("Console", self._show_console)
        sidebar_layout.addWidget(self.console_btn)

        sidebar_layout.addWidget(_hline())

        # Pipeline Stages
        sidebar_layout.addWidget(_bold(QLabel("Pipeline Stages")))
        self.stage_buttons_container = QWidget()
        self.stage_buttons_layout = QVBoxLayout(self.stage_buttons_container)
        self.stage_buttons_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(self.stage_buttons_container)
        self._build_stage_buttons()

        sidebar_layout.addWidget(_hline())

        # Output Settings
        sidebar_layout.addWidget(_bold(QLabel("Output Settings")))
        self.output_setup_btn = self._nav_button("Output Setup", self._show_output_setup)
        sidebar_layout.addWidget(self.output_setup_btn)

        sidebar_layout.addStretch()

        # Right content area: a titled group wrapping the resident page stack.
        self.content_group = QGroupBox("Settings")
        content_layout = QVBoxLayout(self.content_group)
        self.content_stack = QStackedWidget()
        content_layout.addWidget(self.content_stack)
        body.addWidget(self.content_group, stretch=1)

        # Build all pages (resident). Console first (drain target); the rest are
        # filled in by later regions.
        self._create_console_page()
        self._create_data_page()
        self._create_dwidenoise_page()
        self._create_mrdegibbs_page()
        self._create_dwifslpreproc_page()
        self._create_synb0_page()
        self._create_eddy_page()
        self._create_dwi2tensor_page()
        self._create_tensor2metric_page()
        self._create_registration_page()
        self._create_roi_page()
        self._create_output_setup_page()
        self._create_results_page()

    def _nav_button(self, text: str, on_click) -> QPushButton:
        """Create a checkable nav button wired into the exclusive nav group."""
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.clicked.connect(on_click)
        self.nav_group.addButton(btn)
        return btn

    def _register_page(self, page_id: str, widget: QWidget) -> QWidget:
        """Add ``widget`` to the resident stack under ``page_id`` and return it."""
        self.pages[page_id] = widget
        self.content_stack.addWidget(widget)
        return widget

    # ------------------------------------------------------------------ #
    # Stage buttons (rebuilt on synB0 toggle)
    # ------------------------------------------------------------------ #
    def _current_stages(self):
        """The active stage list (standard or synB0), from the checkbox."""
        use_synb0 = self._checked("synb0_check", False)
        return config.SYNB0_PIPELINE_STAGES if use_synb0 else config.PIPELINE_STAGES

    def _build_stage_buttons(self):
        """Create the stage nav buttons for the current mode."""
        for i, (_stage_id, stage_name) in enumerate(self._current_stages()):
            btn = QPushButton(f"{i + 1}. {stage_name}")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, idx=i: self._show_stage(idx))
            self.nav_group.addButton(btn)
            self.stage_buttons_layout.addWidget(btn)
            self.stage_buttons.append(btn)

    def _rebuild_stage_buttons(self):
        """Clear and recreate the stage button column for the current mode."""
        for btn in self.stage_buttons:
            self.nav_group.removeButton(btn)
            self.stage_buttons_layout.removeWidget(btn)
            btn.deleteLater()
        self.stage_buttons.clear()
        self._build_stage_buttons()
        self._show_stage(0)

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def _show_page(self, page_id: str, title: str, button: QPushButton | None):
        self.content_stack.setCurrentWidget(self.pages[page_id])
        self.content_group.setTitle(title)
        if button is not None:
            button.setChecked(True)

    def _show_console(self):
        self._show_page("console", "Main Console", self.console_btn)

    def _show_output_setup(self):
        self._show_page("output_setup", "Output Setup", self.output_setup_btn)

    def _show_stage(self, stage_idx: int):
        """Show the pipeline stage at ``stage_idx`` for the current mode."""
        self.current_stage = stage_idx
        stages = self._current_stages()
        if stage_idx >= len(stages):
            return
        stage_id, stage_name = stages[stage_idx]
        button = self.stage_buttons[stage_idx] if stage_idx < len(self.stage_buttons) else None
        self._show_page(stage_id, f"Stage {stage_idx + 1}: {stage_name}", button)

    # ------------------------------------------------------------------ #
    # Console page (drain target)
    # ------------------------------------------------------------------ #
    def _create_console_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        status_group = QGroupBox("Processing Status")
        status_layout = QVBoxLayout(status_group)
        self.console_tree = QTreeWidget()
        self.console_tree.setColumnCount(2)
        self.console_tree.setHeaderLabels(["Subject ID", "Status"])
        self.console_tree.setRootIsDecorated(False)
        self.console_tree.setColumnWidth(0, 200)
        status_layout.addWidget(self.console_tree)
        layout.addWidget(status_group, stretch=1)

        output_group = QGroupBox("Console Output")
        output_layout = QVBoxLayout(output_group)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        output_layout.addWidget(self.log_text)
        layout.addWidget(output_group, stretch=1)

        self._register_page("console", page)

    # ------------------------------------------------------------------ #
    # Placeholder pages (filled in by later regions)
    # ------------------------------------------------------------------ #
    def _placeholder(self, page_id: str, text: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(text))
        layout.addStretch()
        self._register_page(page_id, page)

    def _create_data_page(self):
        """Create the batch data-input page (subjects + common params + output)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        # --- Subject folders ---------------------------------------------- #
        folders_group = QGroupBox("Subject Folders")
        folders_layout = QVBoxLayout(folders_group)

        instructions = QLabel(
            "Add folders containing DWI data. Each folder should have a .nii.gz "
            "image with matching .bvec and .bval files."
        )
        instructions.setWordWrap(True)
        folders_layout.addWidget(instructions)

        self.subjects_tree = QTreeWidget()
        self.subjects_tree.setColumnCount(3)
        self.subjects_tree.setHeaderLabels(["Subject ID", "Folder Path", "Files Found"])
        self.subjects_tree.setRootIsDecorated(False)
        self.subjects_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.subjects_tree.setColumnWidth(0, 150)
        self.subjects_tree.setColumnWidth(1, 400)
        folders_layout.addWidget(self.subjects_tree, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder...")
        add_btn.clicked.connect(self._add_subject_folder)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected_subjects)
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._clear_all_subjects)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        folders_layout.addLayout(btn_row)

        layout.addWidget(folders_group, stretch=1)

        # --- Common parameters -------------------------------------------- #
        params_group = QGroupBox("Common Parameters")
        params = QGridLayout(params_group)

        # PE direction
        params.addWidget(QLabel("PE Direction:"), 0, 0, Qt.AlignLeft)
        self.pe_auto_check = QCheckBox("Auto from JSON")
        self.pe_auto_check.setChecked(True)
        self.pe_auto_check.toggled.connect(self._on_pe_auto_change)
        params.addWidget(self.pe_auto_check, 0, 1, Qt.AlignLeft)
        self.pe_combo = QComboBox()
        self.pe_combo.addItems(config.PE_DIRECTIONS)
        self.pe_combo.setCurrentText(config.DEFAULT_PE_DIRECTION)
        self.pe_combo.setEnabled(False)  # auto mode
        params.addWidget(self.pe_combo, 0, 2, Qt.AlignLeft)

        # Readout time
        params.addWidget(QLabel("Readout Time:"), 1, 0, Qt.AlignLeft)
        self.readout_auto_check = QCheckBox("Auto from JSON/NIfTI")
        self.readout_auto_check.setChecked(True)
        self.readout_auto_check.toggled.connect(self._on_readout_auto_change)
        params.addWidget(self.readout_auto_check, 1, 1, Qt.AlignLeft)
        self.readout_edit = QLineEdit(str(config.DEFAULT_READOUT_TIME))
        self.readout_edit.setEnabled(False)  # auto mode
        self.readout_edit.textChanged.connect(self._update_run_button_state)
        params.addWidget(self.readout_edit, 1, 2, Qt.AlignLeft)
        params.addWidget(QLabel("(seconds)"), 1, 3, Qt.AlignLeft)

        # RPE scheme
        params.addWidget(QLabel("RPE Scheme:"), 2, 0, Qt.AlignLeft)
        self.rpe_combo = QComboBox()
        self.rpe_combo.addItems(list(config.RPE_SCHEMES.keys()))
        self.rpe_combo.setCurrentText(config.DEFAULT_RPE_SCHEME)
        self.rpe_combo.currentTextChanged.connect(self._on_rpe_combo_change)
        params.addWidget(self.rpe_combo, 2, 1, Qt.AlignLeft)
        self.rpe_desc_label = QLabel(config.RPE_SCHEMES.get(config.DEFAULT_RPE_SCHEME, ""))
        params.addWidget(self.rpe_desc_label, 2, 2, 1, 3, Qt.AlignLeft)

        # synB0-DISCO
        self.synb0_check = QCheckBox("Use synB0-DISCO")
        self.synb0_check.toggled.connect(self._on_synb0_toggle)
        params.addWidget(self.synb0_check, 3, 0, 1, 2, Qt.AlignLeft)
        synb0_desc = QLabel("Use pre-computed synB0-DISCO outputs instead of dwifslpreproc")
        synb0_desc.setStyleSheet("color: gray;")
        params.addWidget(synb0_desc, 3, 2, 1, 3, Qt.AlignLeft)

        layout.addWidget(params_group)

        # --- Output ------------------------------------------------------- #
        out_group = QGroupBox("Output")
        out = QGridLayout(out_group)

        out.addWidget(QLabel("Output Directory:"), 0, 0, Qt.AlignLeft)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.textChanged.connect(self._update_run_button_state)
        out.addWidget(self.output_dir_edit, 0, 1)
        out_browse = QPushButton("Browse...")
        out_browse.clicked.connect(self._browse_output_dir)
        out.addWidget(out_browse, 0, 2)

        self.staging_enabled_check = QCheckBox("Stage files to local storage")
        self.staging_enabled_check.toggled.connect(self._on_staging_toggle)
        out.addWidget(self.staging_enabled_check, 1, 0, 1, 2, Qt.AlignLeft)
        staging_desc = QLabel(
            "Copy inputs to fast local disk before processing (recommended for WSL2/VM)"
        )
        staging_desc.setStyleSheet("color: gray;")
        out.addWidget(staging_desc, 2, 0, 1, 3, Qt.AlignLeft)

        out.addWidget(QLabel("Staging Directory:"), 3, 0, Qt.AlignLeft)
        self.staging_dir_edit = QLineEdit()
        self.staging_dir_edit.setEnabled(False)
        out.addWidget(self.staging_dir_edit, 3, 1)
        self.staging_dir_browse_btn = QPushButton("Browse...")
        self.staging_dir_browse_btn.setEnabled(False)
        self.staging_dir_browse_btn.clicked.connect(self._browse_staging_dir)
        out.addWidget(self.staging_dir_browse_btn, 3, 2)

        staging_note = QLabel("Leave empty to use system temp directory")
        staging_note.setStyleSheet("color: gray;")
        out.addWidget(staging_note, 4, 1, Qt.AlignLeft)
        out.setColumnStretch(1, 1)

        layout.addWidget(out_group)

        self._register_page("data", page)

    # --- Data-input handlers --------------------------------------------- #
    def _on_pe_auto_change(self):
        """Enable the PE combo only in manual mode."""
        self.pe_combo.setEnabled(not self.pe_auto_check.isChecked())

    def _on_readout_auto_change(self):
        """Enable the readout entry only in manual mode."""
        self.readout_edit.setEnabled(not self.readout_auto_check.isChecked())
        self._update_run_button_state()

    def _on_rpe_combo_change(self):
        """Update the RPE description label."""
        scheme = self.rpe_combo.currentText()
        self.rpe_desc_label.setText(config.RPE_SCHEMES.get(scheme, ""))

    def _on_synb0_toggle(self):
        """Handle the synB0-DISCO checkbox: rebuild stage buttons, maybe warn."""
        if self.synb0_check.isChecked() and len(self.subject_files_list) > 1:
            QMessageBox.information(
                self,
                "synB0-DISCO Mode",
                "Note: The same synB0-DISCO output directory will be used\n"
                "for all subjects in the batch.\n\n"
                "Ensure the synB0 outputs are appropriate for all subjects.",
            )
        self._rebuild_stage_buttons()
        self._update_run_button_state()

    def _add_subject_folder(self):
        """Add a folder and discover all DWI runs within it."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_SUBJECT_FOLDER)
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with DWI Data", initial_dir)
        if folder:
            user_config.set_from_path(UserConfig.KEY_SUBJECT_FOLDER, folder)
            self._discover_and_add_folder(folder)

    def _discover_and_add_folder(self, folder_path: str) -> int:
        """
        Discover all DWI runs in a folder and add each as a subject entry.

        If no DWI files are found directly in the selected folder, checks
        immediate subdirectories (subdir fallback). Returns the number of runs
        added. Mirrors the Tk adapter, including the synB0 batch warning on
        first crossing 1 -> multiple subjects.
        """
        try:
            discovered_runs = discover_with_subdir_fallback(folder_path)

            if not discovered_runs:
                QMessageBox.information(
                    self,
                    "No Data Found",
                    f"No DWI files with matching bvec/bval files found in:\n{folder_path}\n\n"
                    "Also checked immediate subdirectories.",
                )
                return 0

            count_before = len(self.subject_files_list)
            new_runs = new_unique_runs(self.subject_files_list, discovered_runs)

            for subject_files in new_runs:
                files_found = subject_files.get_files_summary()
                QTreeWidgetItem(
                    self.subjects_tree,
                    [subject_files.subject_id, subject_files.folder_path, files_found],
                )
                self.subject_files_list.append(subject_files)

            added = len(new_runs)
            if added > 0:
                now_multiple = len(self.subject_files_list) > 1
                self._log(f"Added {added} DWI run(s) from {folder_path}")
                self._update_run_button_state()

                if self.synb0_check.isChecked() and count_before <= 1 and now_multiple:
                    QMessageBox.information(
                        self,
                        "synB0-DISCO Mode",
                        "Note: The same synB0-DISCO output directory will be used\n"
                        "for all subjects in the batch.\n\n"
                        "Ensure the synB0 outputs are appropriate for all subjects.",
                    )

            return added

        except Exception as e:
            QMessageBox.warning(
                self,
                "Discovery Error",
                f"Could not process folder:\n{folder_path}\n\nError: {e}",
            )
            return 0

    def _remove_selected_subjects(self):
        """Remove selected subjects from the list and the tree."""
        selected = self.subjects_tree.selectedItems()
        if not selected:
            return

        indices = sorted(
            (self.subjects_tree.indexOfTopLevelItem(item) for item in selected),
            reverse=True,
        )
        for idx in indices:
            if 0 <= idx < len(self.subject_files_list):
                del self.subject_files_list[idx]
            self.subjects_tree.takeTopLevelItem(idx)

        self._update_run_button_state()

    def _clear_all_subjects(self):
        """Clear all subjects from the list (with confirmation)."""
        if not self.subject_files_list:
            return
        reply = QMessageBox.question(
            self,
            "Confirm",
            "Clear all subjects from the list?",
        )
        if reply == QMessageBox.Yes:
            self.subject_files_list.clear()
            self.subjects_tree.clear()
            self._update_run_button_state()

    def _browse_output_dir(self):
        """Open a directory browser for the output directory."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_OUTPUT_DIR)
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", initial_dir)
        if path:
            self.output_dir_edit.setText(path)
            user_config.set_from_path(UserConfig.KEY_OUTPUT_DIR, path)

    def _on_staging_toggle(self):
        """Enable the staging directory controls only when staging is on."""
        enabled = self.staging_enabled_check.isChecked()
        self.staging_dir_edit.setEnabled(enabled)
        self.staging_dir_browse_btn.setEnabled(enabled)

    def _browse_staging_dir(self):
        """Open a directory browser for the staging directory."""
        path = QFileDialog.getExistingDirectory(self, "Select Staging Directory")
        if path:
            self.staging_dir_edit.setText(path)

    def _create_dwidenoise_page(self):
        self._placeholder("dwidenoise", "dwidenoise — coming in region (c-i).")

    def _create_mrdegibbs_page(self):
        self._placeholder("mrdegibbs", "mrdegibbs — coming in region (c-i).")

    def _create_dwifslpreproc_page(self):
        self._placeholder("dwifslpreproc", "dwifslpreproc — coming in region (c-i).")

    def _create_synb0_page(self):
        self._placeholder("synb0", "synB0-DISCO — coming in region (c-ii).")

    def _create_eddy_page(self):
        self._placeholder("eddy", "Eddy — coming in region (c-i).")

    def _create_dwi2tensor_page(self):
        self._placeholder("dwi2tensor", "dwi2tensor — coming in region (c-i).")

    def _create_tensor2metric_page(self):
        self._placeholder("tensor2metric", "tensor2metric — coming in region (c-i).")

    def _create_registration_page(self):
        self._placeholder("registration", "Registration — coming in region (c-i).")

    def _create_roi_page(self):
        self._placeholder("roi", "ROI Placement — coming in region (c-ii).")

    def _create_output_setup_page(self):
        self._placeholder("output_setup", "Output Setup — coming in region (c-ii).")

    def _create_results_page(self):
        self._placeholder("results", "Results — coming in region (c-ii)/(d).")

    # ------------------------------------------------------------------ #
    # Run / cancel (live in region d)
    # ------------------------------------------------------------------ #
    def _run_pipeline(self):
        """Start batch pipeline execution (wired live in region d)."""
        # Inert until region (d).

    def _cancel_pipeline(self):
        """Signal cancellation (wired live in region d)."""
        # Inert until region (d).

    # ------------------------------------------------------------------ #
    # Worker-message drain
    # ------------------------------------------------------------------ #
    def _check_results(self):
        """Poll the result queue for updates (mirrors the Tk ``after`` loop)."""
        try:
            while True:
                msg = self.result_queue.get_nowait()
                self._handle_result(msg)
        except queue.Empty:
            pass

        if self.worker and self.worker.is_alive():
            QTimer.singleShot(100, self._check_results)
        else:
            self._on_run_finished()

    def _handle_result(self, msg):
        """Handle a worker message by applying the model's view-intents."""
        for intent in self.result_model.handle(msg):
            self._apply_intent(intent)

    def _apply_intent(self, intent):
        """Apply a single view-intent from ResultModel to the widgets.

        Per Decision 6 the adapter keeps only the **log** half of
        ``UpdateStageStatus`` and treats ``ResetStageButtons`` as a **no-op**
        (the sidebar no longer recolors during a run).
        """
        if isinstance(intent, AppendLog):
            self._log(intent.text)
        elif isinstance(intent, UpdateStageStatus):
            self._update_stage_status(intent.stage, intent.status)
        elif isinstance(intent, SetRowStatus):
            self._set_row_status(intent.index, intent.text, intent.tag)
        elif isinstance(intent, ResetStageButtons):
            pass  # No-op: status coloring dropped (Decision 6).
        elif isinstance(intent, ShowBatchResults):
            self._show_batch_results(intent.view)

    def _set_row_status(self, index: int, text: str, tag: str):
        """Set the status cell + colour of the console-tree row at ``index``."""
        if 0 <= index < self.console_tree.topLevelItemCount():
            item = self.console_tree.topLevelItem(index)
            item.setText(1, text)
            color = _ROW_TAG_COLORS.get(tag)
            if color is not None:
                from PySide6.QtGui import QBrush, QColor

                brush = QBrush(QColor(color))
                item.setForeground(0, brush)
                item.setForeground(1, brush)

    def _update_stage_status(self, stage: str, status: str):
        """Log stage transitions (the log half of the intent; no button colour)."""
        stage_names = {
            "denoise": "Denoising",
            "degibbs": "Gibbs Ringing Removal",
            "preproc": "Preprocessing",
            "synb0": "synB0-DISCO",
            "eddy": "Eddy",
            "dti": "DTI Fitting",
            "registration": "Registration",
            "roi": "ROI Placement",
            "results": "Calculating ALPS",
        }
        stage_name = stage_names.get(stage, stage)
        if status == "running":
            self._log(f"Running: {stage_name}")
        elif status == "complete":
            self._log(f"Completed: {stage_name}")

    def _on_run_finished(self):
        """Tear down after the worker thread dies (region d wires the rest)."""
        self._close_log_file()
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel")
        self._update_run_button_state()

    def _show_batch_results(self, view: BatchResultsView):
        """Render the finished batch-results view (region d)."""
        # Filled in by region (d).

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    def _init_log_file(self, output_dir: str):
        """Initialize log file in the output directory."""
        import os
        from datetime import datetime

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = os.path.join(output_dir, f"dti_alps_{timestamp}.log")
        try:
            self.log_file = open(self.log_file_path, "w", encoding="utf-8")
            self._log(f"Log file created: {self.log_file_path}")
        except OSError as e:
            self._log(f"Warning: Could not create log file: {e}")
            self.log_file = None
            self.log_file_path = None

    def _close_log_file(self):
        """Close the log file if open, and delete if not wanted."""
        import os

        if self.log_file:
            try:
                self.log_file.close()
            except OSError:
                pass
            self.log_file = None

        if self.log_file_path:
            output_config = collect_output_config(self._form_state().output_flags)
            if not output_config.log_file and os.path.exists(self.log_file_path):
                try:
                    os.remove(self.log_file_path)
                except OSError:
                    pass
            self.log_file_path = None

    def _log(self, message: str):
        """Append a timestamped line to the log console (and the log file)."""
        from datetime import datetime

        timestamp = datetime.now().strftime("[%H:%M:%S]")
        log_line = f"{timestamp} {message}"

        self.log_text.appendPlainText(log_line)
        self.log_text.ensureCursorVisible()

        if self.log_file:
            try:
                self.log_file.write(log_line + "\n")
                self.log_file.flush()
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    def _show_about(self):
        QMessageBox.information(
            self,
            "About",
            f"{config.APP_NAME}\n"
            f"Version {config.APP_VERSION}\n\n"
            "Automatic DTI-ALPS ROI Placement and Analysis\n\n"
            "Uses MRtrix3 for preprocessing and DTI fitting.",
        )


def _bold(label: QLabel) -> QLabel:
    """Make a label's font bold in place and return it (for inline use)."""
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def _hline() -> QWidget:
    """A horizontal separator line."""
    from PySide6.QtWidgets import QFrame

    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def launch_app_qt():
    """Launch the Qt main application as a standalone app."""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv)

    window = DTIALPSApplication()
    window.show()

    if owns_app:
        app.exec()
    return window


if __name__ == "__main__":
    launch_app_qt()
