"""
Main application window for DTI-ALPS Processing Tool (PySide6 adapter).

The main window is a ``QMainWindow`` adapter over the **unchanged** tk-free
models (the Tk port, PRD 0013): it reads Qt widgets into a
:class:`~dti_alps.gui.form_model.FormState` and delegates to ``form_model``
(``build_batch_state`` / ``compute_readiness``) on the input side, and drains the
worker's typed :class:`~dti_alps.processing.messages.WorkerMessage` stream through
:class:`~dti_alps.gui.result_model.ResultModel` on the output side. No
input/output/science logic lives here.

Threading: a ``QTimer`` drains the same ``queue.Queue`` the workers write to, so
``processing/`` stays Qt-free (the headless core, CLI reanalysis, and batch paths
never import PySide6).

Two behavior differences from the former Tk window are deliberate (PRD 0013,
Scope): the sidebar stage buttons do not recolor during a run (status coloring
dropped, Decision 6), and there is a working Cancel button (Decision 7).
"""

import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..processing import results_layout
from ..processing.discovery import (
    SubjectFiles,
    discover_with_subdir_fallback,
    new_unique_runs,
)
from ..processing.pipeline import (
    BatchRunner,
    BatchState,
    BatchWorker,
)
from ..processing.validators import validate_runnable, validate_synb0_output_dir
from . import config
from .form_model import (
    FormState,
    OptionState,
    build_batch_state,
    collect_output_config,
    compute_readiness,
)
from .result_model import (
    AppendLog,
    BatchResultsView,
    ResetStageButtons,
    ResultModel,
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

    # ------------------------------------------------------------------ #
    # Shared CLI-option-row builder (Decision 9)
    # ------------------------------------------------------------------ #
    def _cli_header(self, grid: QGridLayout):
        """Add the aligned 5-column header + separator to a CLI-options grid."""
        grid.addWidget(_bold(QLabel("Option")), 0, 1, Qt.AlignLeft)
        grid.addWidget(_bold(QLabel("Value")), 0, 2, Qt.AlignLeft)
        grid.addWidget(_bold(QLabel("Description")), 0, 4, Qt.AlignLeft)
        grid.addWidget(_hline(), 1, 0, 1, 5)

    def add_cli_option_row(
        self,
        grid: QGridLayout,
        row: int,
        name: str,
        opt_type: str,
        description: str,
        stage_prefix: str,
        filetypes: list | None = None,
        choices: list | None = None,
    ) -> dict:
        """
        Populate one CLI-option row in a shared grid and return its handle.

        Five aligned columns (checkbox | name | value | Browse | description) in
        the caller's single grid, so columns line up across the header and every
        row (Decision 9). The handle exposes ``is_enabled()``/``value()`` (value
        always a string; int coercion stays in the model) and is registered under
        ``cli_option_rows[stage_prefix][name]`` — the Qt twin of
        ``cli_option_vars``.
        """
        self.cli_option_rows.setdefault(stage_prefix, {})

        checkbox = QCheckBox()
        grid.addWidget(checkbox, row, 0, Qt.AlignLeft)
        grid.addWidget(QLabel(name), row, 1, Qt.AlignLeft)

        value_widget = None
        browse_btn = None

        if opt_type == "flag":
            pass  # flags have no value widget
        elif opt_type == "choice" and choices:
            value_widget = QComboBox()
            value_widget.addItems(choices)
            value_widget.setEnabled(False)
            grid.addWidget(value_widget, row, 2, Qt.AlignLeft)
        elif opt_type in ("file", "dir", "output", "prefix"):
            value_widget = QLineEdit()
            value_widget.setEnabled(False)
            grid.addWidget(value_widget, row, 2)
            browse_btn = QPushButton("Browse...")
            browse_btn.setEnabled(False)
            if opt_type in ("file", "prefix"):
                browse_btn.clicked.connect(
                    lambda _c=False, v=value_widget, ft=filetypes: self._browse_cli_file(v, ft)
                )
            elif opt_type == "dir":
                browse_btn.clicked.connect(lambda _c=False, v=value_widget: self._browse_cli_dir(v))
            elif opt_type == "output":
                browse_btn.clicked.connect(
                    lambda _c=False, v=value_widget, ft=filetypes: self._browse_cli_save(
                        v, ft or config.NIFTI_FILETYPES
                    )
                )
            grid.addWidget(browse_btn, row, 3)
        else:
            value_widget = QLineEdit()
            value_widget.setEnabled(False)
            grid.addWidget(value_widget, row, 2, Qt.AlignLeft)

        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: gray;")
        grid.addWidget(desc_label, row, 4, Qt.AlignLeft)

        def on_toggle(checked, vw=value_widget, bb=browse_btn):
            if vw is not None:
                vw.setEnabled(checked)
            if bb is not None:
                bb.setEnabled(checked)

        checkbox.toggled.connect(on_toggle)

        def value_getter(vw=value_widget):
            if vw is None:
                return ""
            if isinstance(vw, QComboBox):
                return vw.currentText()
            return vw.text()

        handle = {
            "checkbox": checkbox,
            "value_widget": value_widget,
            "browse_btn": browse_btn,
            "type": opt_type,
            "is_enabled": checkbox.isChecked,
            "value": value_getter,
        }
        self.cli_option_rows[stage_prefix][name] = handle
        return handle

    def _build_options_group(self, title, options, stage_prefix, resolver=None):
        """Build a titled group box holding a header + one CLI row per option."""
        group = QGroupBox(title)
        grid = QGridLayout(group)
        self._cli_header(grid)
        for i, (name, opt_type, desc, _default) in enumerate(options):
            filetypes, choices = resolver(name, opt_type) if resolver else (None, None)
            self.add_cli_option_row(
                grid, i + 2, name, opt_type, desc, stage_prefix, filetypes, choices
            )
        grid.setColumnStretch(2, 1)
        return group

    def _browse_cli_file(self, value_widget, filetypes):
        """Browse for a file and set the value widget."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_CLI_FILE)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File", initial_dir, _qt_name_filter(filetypes)
        )
        if path:
            value_widget.setText(path)
            user_config.set_from_path(UserConfig.KEY_CLI_FILE, path)

    def _browse_cli_dir(self, value_widget):
        """Browse for a directory and set the value widget."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_CLI_DIR)
        path = QFileDialog.getExistingDirectory(self, "Select Directory", initial_dir)
        if path:
            value_widget.setText(path)
            user_config.set_from_path(UserConfig.KEY_CLI_DIR, path)

    def _browse_cli_save(self, value_widget, filetypes):
        """Browse for a save-file location and set the value widget."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_CLI_SAVE)
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Output File", initial_dir, _qt_name_filter(filetypes)
        )
        if path:
            value_widget.setText(path)
            user_config.set_from_path(UserConfig.KEY_CLI_SAVE, path)

    def _scroll_page(self, info_text: str):
        """A page with an info label above a vertical-scrolling content area."""
        page = QWidget()
        outer = QVBoxLayout(page)
        if info_text:
            info = QLabel(info_text)
            info.setWordWrap(True)
            outer.addWidget(info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
        return page, content_layout

    # ------------------------------------------------------------------ #
    # CLI-row stage pages (c-i)
    # ------------------------------------------------------------------ #
    def _create_dwidenoise_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Thermal noise removal using Marchenko-Pastur PCA denoising."))

        self.run_denoising_check = QCheckBox("Enable denoising (recommended)")
        self.run_denoising_check.setChecked(True)
        layout.addWidget(self.run_denoising_check)

        def resolver(name, t):
            if t == "file" and "mask" in name:
                return config.NIFTI_FILETYPES, None
            if t == "output":
                return config.NIFTI_FILETYPES, None
            if t == "choice" and name == "-datatype":
                return None, config.DWIDENOISE_DATATYPE_CHOICES
            if t == "choice" and name == "-estimator":
                return None, config.DWIDENOISE_ESTIMATOR_CHOICES
            return None, None

        layout.addWidget(
            self._build_options_group(
                "dwidenoise Options", config.DWIDENOISE_OPTIONS, "dwidenoise", resolver
            ),
            stretch=1,
        )
        self._register_page("dwidenoise", page)

    def _create_mrdegibbs_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Gibbs ringing artifact removal using local subvoxel-shifts."))

        self.run_degibbs_check = QCheckBox("Enable Gibbs ringing removal (recommended)")
        self.run_degibbs_check.setChecked(True)
        layout.addWidget(self.run_degibbs_check)

        layout.addWidget(
            self._build_options_group("mrdegibbs Options", config.MRDEGIBBS_OPTIONS, "mrdegibbs"),
            stretch=1,
        )
        self._register_page("mrdegibbs", page)

    def _create_dwifslpreproc_page(self):
        page, content = self._scroll_page(
            "Configure optional CLI arguments for dwifslpreproc.\n"
            "Core parameters (PE direction, readout time, RPE scheme) are set in Data Input."
        )

        def resolver(name, t):
            if t == "file":
                if "mask" in name:
                    return config.NIFTI_FILETYPES, None
                if "json" in name:
                    return config.JSON_FILETYPES, None
                if "slspec" in name:
                    return [("Text files", "*.txt"), ("All files", "*.*")], None
            return None, None

        content.addWidget(
            self._build_options_group(
                "dwifslpreproc Options",
                config.DWIFSLPREPROC_OPTIONS,
                "dwifslpreproc",
                resolver,
            )
        )
        content.addStretch()
        self._register_page("dwifslpreproc", page)

    def _create_eddy_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Configure FSL eddy for motion and distortion correction.\n"
                "Eddy will use the topup outputs from your synB0-DISCO run."
            )
        )

        def resolver(name, t):
            if name == "slm":
                return None, config.SYNB0_EDDY_SLM_CHOICES
            return None, None

        layout.addWidget(
            self._build_options_group(
                "Eddy Options", config.SYNB0_EDDY_OPTIONS, "synb0_eddy", resolver
            ),
            stretch=1,
        )

        # Pre-enable repol (recommended), mirroring the Tk default.
        repol = self.cli_option_rows.get("synb0_eddy", {}).get("repol")
        if repol:
            repol["checkbox"].setChecked(True)

        self._register_page("eddy", page)

    def _create_dwi2tensor_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Configure optional CLI arguments for dwi2tensor (DTI fitting).\n"
                "The diffusion tensor will be computed from the preprocessed DWI data."
            )
        )

        def resolver(name, t):
            if t == "file" and "mask" in name:
                return config.NIFTI_FILETYPES, None
            if t == "output":
                return config.NIFTI_FILETYPES, None
            return None, None

        layout.addWidget(
            self._build_options_group(
                "dwi2tensor Options", config.DWI2TENSOR_OPTIONS, "dwi2tensor", resolver
            ),
            stretch=1,
        )
        self._register_page("dwi2tensor", page)

    def _create_tensor2metric_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Configure optional CLI arguments for tensor2metric (metric extraction).\n"
                "FA and V1 are always computed (required for ALPS analysis).\n"
                "Additional metrics can be enabled below."
            )
        )

        def resolver(name, t):
            if t == "file" and "mask" in name:
                return config.NIFTI_FILETYPES, None
            if t == "output":
                return config.NIFTI_FILETYPES, None
            if t == "choice" and name == "-modulate":
                return None, config.TENSOR2METRIC_MODULATE_CHOICES
            return None, None

        layout.addWidget(
            self._build_options_group(
                "tensor2metric Options",
                config.TENSOR2METRIC_OPTIONS,
                "tensor2metric",
                resolver,
            ),
            stretch=1,
        )
        self._register_page("tensor2metric", page)

    def _create_registration_page(self):
        page, content = self._scroll_page(
            "Configure parameters for FA-to-template registration.\n"
            "This step registers the subject FA map to the JHU-ICBM template\n"
            "using dwi2mask for brain extraction and FSL tools (FLIRT/FNIRT) for registration."
        )

        # Brain extraction (dwi2mask) info panel — static text, no options.
        dwi2mask_group = QGroupBox("Brain Extraction (dwi2mask)")
        dwi2mask_layout = QVBoxLayout(dwi2mask_group)
        dwi2mask_info = QLabel(
            "Brain extraction is performed automatically using MRtrix3's dwi2mask.\n"
            "This method extracts a brain mask directly from the preprocessed DWI data,\n"
            "which is more reliable for diffusion images than traditional T1-based methods.\n\n"
            "The brain mask is then applied to the FA map before registration."
        )
        dwi2mask_layout.addWidget(dwi2mask_info)
        details = QGridLayout()
        details.addWidget(_bold(QLabel("Input:")), 0, 0, Qt.AlignLeft)
        details.addWidget(QLabel("Preprocessed DWI with bvecs/bvals"), 0, 1, Qt.AlignLeft)
        details.addWidget(_bold(QLabel("Output:")), 1, 0, Qt.AlignLeft)
        details.addWidget(QLabel("Binary brain mask applied to FA"), 1, 1, Qt.AlignLeft)
        details.addWidget(_bold(QLabel("Validation:")), 2, 0, Qt.AlignLeft)
        details.addWidget(
            QLabel("Pipeline fails if no b0 volumes found in DWI data"), 2, 1, Qt.AlignLeft
        )
        details.setColumnStretch(1, 1)
        dwi2mask_layout.addLayout(details)
        content.addWidget(dwi2mask_group)

        def flirt_resolver(name, t):
            if name == "-dof":
                return None, config.FLIRT_DOF_CHOICES
            if name == "-cost":
                return None, config.FLIRT_COST_CHOICES
            if name == "-interp":
                return None, config.FLIRT_INTERP_CHOICES
            return None, None

        content.addWidget(
            self._build_options_group(
                "FLIRT (Linear Registration)", config.FLIRT_OPTIONS, "flirt", flirt_resolver
            )
        )

        def fnirt_resolver(name, t):
            if name == "--intmod":
                return None, config.FNIRT_INTMOD_CHOICES
            return None, None

        content.addWidget(
            self._build_options_group(
                "FNIRT (Non-linear Registration)",
                config.FNIRT_OPTIONS,
                "fnirt",
                fnirt_resolver,
            )
        )
        content.addStretch()
        self._register_page("registration", page)

    # ------------------------------------------------------------------ #
    # Bespoke stage pages (c-ii)
    # ------------------------------------------------------------------ #
    def _create_synb0_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "synB0-DISCO must be run externally before using this pipeline.\n"
                "Please provide the path to your synB0-DISCO OUTPUTS directory.\n\n"
                "Required files in the OUTPUTS directory:\n"
                "  - topup_fieldcoef.nii.gz\n"
                "  - topup_movpar.txt\n"
                "  - acqparams.txt (in OUTPUTS or ../INPUTS/)"
            )
        )

        output_group = QGroupBox("synB0-DISCO Output Directory")
        out = QGridLayout(output_group)
        out.addWidget(QLabel("OUTPUTS Directory:"), 0, 0, Qt.AlignLeft)
        self.synb0_output_dir_edit = QLineEdit()
        self.synb0_output_dir_edit.textChanged.connect(self._update_run_button_state)
        out.addWidget(self.synb0_output_dir_edit, 0, 1)
        synb0_browse = QPushButton("Browse...")
        synb0_browse.clicked.connect(self._browse_synb0_output_dir)
        out.addWidget(synb0_browse, 0, 2)
        out.setColumnStretch(1, 1)
        self.synb0_validation_label = QLabel("")
        self.synb0_validation_label.setStyleSheet("color: gray;")
        out.addWidget(self.synb0_validation_label, 1, 0, 1, 3, Qt.AlignLeft)
        layout.addWidget(output_group)

        how_group = QGroupBox("How to Run synB0-DISCO")
        how_layout = QVBoxLayout(how_group)
        how_text = QLabel(
            "Run synB0-DISCO using Docker or Singularity:\n\n"
            "docker run --rm -v /path/to/INPUTS:/INPUTS -v /path/to/OUTPUTS:/OUTPUTS \\\n"
            "    -v /path/to/license.txt:/extra/freesurfer/license.txt \\\n"
            "    leonyichencai/synb0-disco:v3.1\n\n"
            "Required INPUTS:\n"
            "  - b0.nii.gz: mean b0 image (3D)\n"
            "  - T1.nii.gz: T1-weighted image\n"
            "  - acqparams.txt: acquisition parameters file"
        )
        how_text.setStyleSheet("font-family: monospace;")
        how_layout.addWidget(how_text)
        layout.addWidget(how_group)
        layout.addStretch()

        self._register_page("synb0", page)

    def _browse_synb0_output_dir(self):
        """Browse for the synB0-DISCO OUTPUTS directory and validate it."""
        user_config = get_user_config()
        initial_dir = user_config.get_initial_dir(UserConfig.KEY_SYNB0_OUTPUT_DIR)
        path = QFileDialog.getExistingDirectory(
            self, "Select synB0-DISCO OUTPUTS Directory", initial_dir
        )
        if path:
            self.synb0_output_dir_edit.setText(path)
            user_config.set_from_path(UserConfig.KEY_SYNB0_OUTPUT_DIR, path)
            self._validate_synb0_output_dir(path)

    def _validate_synb0_output_dir(self, path):
        """Validate the synB0 OUTPUTS directory contents and show the result."""
        ok, missing = validate_synb0_output_dir(path)
        if not ok:
            self.synb0_validation_label.setText(f"Missing: {', '.join(missing)}")
            self.synb0_validation_label.setStyleSheet("color: red;")
        else:
            self.synb0_validation_label.setText("All required files found")
            self.synb0_validation_label.setStyleSheet("color: green;")

    def _create_roi_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Configure parameters for ROI placement.\n"
                "ROI templates are transformed to native space using the inverse warp\n"
                "from registration, then spherical ROIs are created at the centroids."
            )
        )

        param_group = QGroupBox("ROI Placement Parameters")
        params = QGridLayout(param_group)

        # ROI shapes
        params.addWidget(QLabel("ROI Shapes:"), 0, 0, Qt.AlignLeft | Qt.AlignTop)
        shapes_row = QHBoxLayout()
        shape_labels = [
            ("sphere2", "Sphere 2.0mm", False),
            ("sphere2p5", "Sphere 2.5mm", False),
            ("sphere3", "Sphere 3.0mm", True),
            ("squarev4", "Square 2x2", False),
            ("squarev9", "Square 3x3", False),
        ]
        for token, label, default in shape_labels:
            chk = QCheckBox(label)
            chk.setChecked(default)
            self.roi_shape_checks[token] = chk
            shapes_row.addWidget(chk)
        shapes_row.addStretch()
        shapes_container = QWidget()
        shapes_container.setLayout(shapes_row)
        params.addWidget(shapes_container, 0, 1, 1, 2)

        # FA threshold — bounded spin box (Decision 10).
        params.addWidget(QLabel("FA Threshold:"), 1, 0, Qt.AlignLeft)
        self.fa_threshold_spin = QDoubleSpinBox()
        self.fa_threshold_spin.setRange(0.0, 1.0)
        self.fa_threshold_spin.setSingleStep(0.05)
        self.fa_threshold_spin.setValue(config.FA_THRESHOLD)
        params.addWidget(self.fa_threshold_spin, 1, 1, Qt.AlignLeft)
        fa_desc = QLabel("Minimum FA value for ROI voxels (filters out CSF)")
        fa_desc.setStyleSheet("color: gray;")
        params.addWidget(fa_desc, 1, 2, Qt.AlignLeft)

        # ALPS method
        params.addWidget(QLabel("ALPS Method:"), 2, 0, Qt.AlignLeft)
        self.alps_method_combo = QComboBox()
        self.alps_method_combo.addItems(config.ALPS_METHODS)
        self.alps_method_combo.setCurrentText(config.DEFAULT_ALPS_METHOD)
        params.addWidget(self.alps_method_combo, 2, 1, Qt.AlignLeft)
        alps_desc = QLabel("ALPS-LAB: tensor diagonal, ALPS-PAS: eigenvector-sorted eigenvalues")
        alps_desc.setStyleSheet("color: gray;")
        params.addWidget(alps_desc, 2, 2, Qt.AlignLeft)

        # ROI refinement
        params.addWidget(QLabel("ROI Refinement:"), 3, 0, Qt.AlignLeft)
        self.refine_roi_combo = QComboBox()
        self.refine_roi_combo.addItems(config.ROI_REFINEMENT_OPTIONS)
        self.refine_roi_combo.setCurrentText(config.DEFAULT_ROI_REFINEMENT)
        params.addWidget(self.refine_roi_combo, 3, 1, Qt.AlignLeft)
        refine_desc = QLabel("Refined: ±3 X, ±2 Y, ±2 Z voxels; ±1 Y/Z drift between proj/assoc")
        refine_desc.setStyleSheet("color: gray;")
        params.addWidget(refine_desc, 3, 2, Qt.AlignLeft)

        layout.addWidget(param_group)

        info_group = QGroupBox("ROI Placement Process")
        info_layout = QVBoxLayout(info_group)
        info_layout.addWidget(
            QLabel(
                "The ROI placement process (after registration) involves:\n\n"
                "1. Transform ROI templates to native space using inverse warp\n"
                "2. Find centroid of each transformed mask\n"
                "3. Optionally refine placement using fiber orientation (V1)\n"
                "4. Create spherical ROIs at final centroid positions\n\n"
                "ROI masks created:\n"
                "  - Left/Right Projection (superior corona radiata)\n"
                "  - Left/Right Association (superior longitudinal fasciculus)"
            )
        )
        layout.addWidget(info_group)
        layout.addStretch()

        self._register_page("roi", page)

    def _create_output_setup_page(self):
        page, content = self._scroll_page(
            "Configure which output files to keep after processing.\n"
            "By default, all intermediate and final outputs are saved.\n"
            "Uncheck files you don't need to save disk space."
        )

        sections = [
            (
                "Preprocessing Outputs",
                [
                    (
                        "denoised_dwi",
                        "Denoised DWI",
                        "DWI after thermal noise removal (dwidenoise)",
                    ),
                    ("degibbs_dwi", "Degibbs DWI", "DWI after Gibbs ringing removal (mrdegibbs)"),
                    (
                        "preprocessed_dwi",
                        "Preprocessed DWI",
                        "Final preprocessed DWI (dwifslpreproc)",
                    ),
                    (
                        "preprocessed_bvecs",
                        "Preprocessed bvecs/bvals",
                        "Corrected gradient directions and b-values",
                    ),
                ],
            ),
            (
                "DTI Outputs",
                [
                    ("tensor", "Diffusion Tensor", "Fitted diffusion tensor image"),
                    ("fa_map", "FA Map", "Fractional anisotropy map"),
                    (
                        "eigenvector_maps",
                        "Eigenvector/eigenvalue maps",
                        "V1, V2, V3, L1, L2, L3 maps",
                    ),
                ],
            ),
            (
                "Registration Outputs",
                [
                    ("b0_image", "Averaged B0 Image", "Mean b0 image extracted from DWI"),
                    ("brain_mask", "Brain Mask", "Brain mask from dwi2mask"),
                    ("fa_brain", "Skull-stripped FA", "FA image after brain mask application"),
                    ("affine_matrix", "Affine Matrix", "FLIRT linear transformation matrix"),
                    (
                        "warp_coefficients",
                        "Warp Coefficients",
                        "FNIRT non-linear warp coefficients",
                    ),
                    ("inverse_warp", "Inverse Warp", "Inverse warp for ROI transformation"),
                ],
            ),
            (
                "ROI & Results Outputs",
                [
                    ("roi_masks", "ROI Masks", "Spherical ROI masks in native space"),
                    ("log_file", "Processing Log", "Detailed log of pipeline execution"),
                ],
            ),
        ]
        for title, options in sections:
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            for key, display_name, description in options:
                row = QHBoxLayout()
                chk = QCheckBox(display_name)
                chk.setChecked(True)
                self.output_option_checks[key] = chk
                row.addWidget(chk)
                desc = QLabel(description)
                desc.setStyleSheet("color: gray;")
                row.addWidget(desc)
                row.addStretch()
                group_layout.addLayout(row)
            content.addWidget(group)

        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(lambda: self._set_all_outputs(True))
        deselect_all = QPushButton("Deselect All")
        deselect_all.clicked.connect(lambda: self._set_all_outputs(False))
        btn_row.addWidget(select_all)
        btn_row.addWidget(deselect_all)
        btn_row.addStretch()
        content.addLayout(btn_row)

        note = QLabel("Note: The ALPS results CSV is always saved.")
        note.setStyleSheet("color: gray;")
        content.addWidget(note)
        content.addStretch()

        self._register_page("output_setup", page)

    def _set_all_outputs(self, checked: bool):
        """Check or uncheck every output-retention checkbox."""
        for chk in self.output_option_checks.values():
            chk.setChecked(checked)

    def _create_results_page(self):
        page = QWidget()
        self.results_page_layout = QVBoxLayout(page)

        self.results_label = QLabel("Results will be displayed here after processing completes.")
        self.results_page_layout.addWidget(self.results_label)

        viewer_row = QHBoxLayout()
        open_viewer = QPushButton("Open Results Viewer...")
        open_viewer.clicked.connect(lambda: self._open_results_viewer())
        viewer_row.addWidget(open_viewer)
        viewer_note = QLabel("(View any previously processed results)")
        viewer_note.setStyleSheet("color: gray;")
        viewer_row.addWidget(viewer_note)
        viewer_row.addStretch()
        self.results_page_layout.addLayout(viewer_row)
        self.results_page_layout.addStretch()

        self._register_page("results", page)

    def _open_results_viewer(self, output_folder: str | None = None):
        """Open the results viewer in its own process.

        The viewer is a Qt QMainWindow (PRD 0010) and cannot be an in-process
        child of this window, so it is spawned as a separate process — the same
        as the Tk app did.
        """
        import subprocess
        import sys

        if output_folder is None and self.batch_state:
            output_folder = self.batch_state.config.output_dir

        cmd = [sys.executable, "-m", "dti_alps", "--viewer"]
        if output_folder:
            cmd.append(output_folder)
        subprocess.Popen(cmd)

    # ------------------------------------------------------------------ #
    # Run / cancel (live in region d)
    # ------------------------------------------------------------------ #
    def _run_pipeline(self):
        """Start batch pipeline execution."""
        # Pre-flight validation (first-failure-wins); adapter owns dialog phrasing.
        output_dir = self.output_dir_edit.text()
        ok, kind, invalid_ids = validate_runnable(self.subject_files_list, output_dir)
        if not ok:
            if kind == "no_subjects":
                QMessageBox.critical(self, "Validation Error", "No subject folders added.")
            elif kind == "invalid_subjects":
                names = ", ".join(invalid_ids[:5])
                if len(invalid_ids) > 5:
                    names += f" (and {len(invalid_ids) - 5} more)"
                QMessageBox.critical(
                    self,
                    "Validation Error",
                    f"Some subjects have missing files:\n{names}\n\n"
                    "Please remove invalid subjects or add missing files.",
                )
            elif kind == "no_output_dir":
                QMessageBox.critical(
                    self, "Validation Error", "Please specify an output directory."
                )
            return

        self.batch_state = build_batch_state(self._form_state(), self.subject_files_list)

        # Disable Run, arm Cancel.
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")

        # Clear log and console tree, then seed the tree with the subjects.
        self.log_text.clear()
        self.console_tree.clear()
        for subject_files in self.subject_files_list:
            QTreeWidgetItem(self.console_tree, [subject_files.subject_id, "Pending"])

        # No stage-button reset (status coloring dropped, Decision 6).

        self._show_console()

        self._init_log_file(output_dir)
        self._log("Starting batch processing...")

        self.result_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.result_model = ResultModel([s.subject_id for s in self.subject_files_list])

        batch_runner = BatchRunner(self.batch_state)
        self.worker = BatchWorker(batch_runner, self.result_queue, self.cancel_event)
        self.worker.start()

        QTimer.singleShot(100, self._check_results)

    def _cancel_pipeline(self):
        """Signal cancellation at the next subject boundary (Decision 7).

        Sets ``cancel_event``, disables Cancel, and relabels it to "Cancelling…"
        until the worker actually stops (the drain loop sees the thread die and
        the already-emitted ``BatchCancelled`` logs the line). The in-flight
        subject runs to completion; the batch stops before the next subject.
        """
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling…")

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

    def _clear_layout(self, layout):
        """Recursively remove and delete everything in ``layout``."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                child = item.layout()
                if child is not None:
                    self._clear_layout(child)

    def _show_batch_results(self, view: BatchResultsView):
        """Render the finished batch-results view (built by build_batch_results_table).

        Cells are already formatted strings; the adapter owns only chrome —
        column widths/alignments (``_BATCH_COLUMN_LAYOUT``), the footer buttons,
        and the "Results saved to:" label.
        """
        # Switch to the results stage (last stage, index varies by mode).
        self._show_stage(len(self.stage_buttons) - 1)

        self._clear_layout(self.results_page_layout)

        # Title + summary
        title_row = QHBoxLayout()
        title = _bold(QLabel(view.title))
        font = title.font()
        font.setPointSize(font.pointSize() + 2)
        title.setFont(font)
        title_row.addWidget(title)
        title_row.addWidget(QLabel(f"  ({view.summary})"))
        title_row.addStretch()
        self.results_page_layout.addLayout(title_row)

        # Results table — render the columns the builder chose, generically.
        results_group = QGroupBox("Subject Results")
        results_group_layout = QVBoxLayout(results_group)
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setColumnCount(len(view.columns))
        tree.setHeaderLabels([col.label for col in view.columns])
        for i, col in enumerate(view.columns):
            width, _anchor = self._BATCH_COLUMN_LAYOUT.get(col.key, self._BATCH_COLUMN_DEFAULT)
            tree.setColumnWidth(i, width)
        for row in view.rows:
            item = QTreeWidgetItem(tree, [row[col.key] for col in view.columns])
            for i, col in enumerate(view.columns):
                _width, anchor = self._BATCH_COLUMN_LAYOUT.get(col.key, self._BATCH_COLUMN_DEFAULT)
                item.setTextAlignment(i, anchor)
        results_group_layout.addWidget(tree)
        self.batch_results_tree = tree  # Store for export
        self.results_page_layout.addWidget(results_group, stretch=1)

        # Footer: results-on-disk label + folder/viewer buttons.
        footer = QHBoxLayout()
        csv_path = Path(view.output_dir) / results_layout.alps_csv_name(
            results_layout.DEFAULT_ROI_TOKEN
        )
        footer.addWidget(QLabel(f"Results saved to: {csv_path}"))
        footer.addStretch()
        open_viewer = QPushButton("Open Results Viewer")
        open_viewer.clicked.connect(
            lambda _c=False, d=view.output_dir: self._open_results_viewer(d)
        )
        open_folder = QPushButton("Open Output Folder")
        open_folder.clicked.connect(self._open_batch_output_folder)
        footer.addWidget(open_viewer)
        footer.addWidget(open_folder)
        self.results_page_layout.addLayout(footer)

    def _open_batch_output_folder(self):
        """Open the batch output folder in the OS file manager."""
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


def launch_app(output_folder: str | None = None):
    """Launch the DTI-ALPS main application as a standalone app.

    Mirrors :func:`dti_alps.gui.viewer.launch_viewer`: reuses an existing
    ``QApplication`` if one is running, otherwise owns one and runs its event
    loop. ``output_folder`` is accepted for signature symmetry with the viewer
    and is currently unused by the main window.
    """
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
    launch_app()
