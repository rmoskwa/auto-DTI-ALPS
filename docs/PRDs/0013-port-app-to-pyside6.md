# PRD 0013 — Port the Main App (`app.py`) to PySide6

Status: Architecture locked · Widget design locked · Date: 2026-07-06 · Source: Roadmap step 4 of the Tkinter→PySide6 migration — the main app window, the largest and last Tk surface after the viewer (PRD 0010). This PRD captures both the *architectural* decisions locked in the 2026-07-06 planning grill and the *widget-level* design locked in the 2026-07-06 dedicated widget grill (below).

This PRD also serves as the ADR of record for the decisions below.

> **Process note.** The port plan (see the `pyside6-port-plan` memory) always reserved a *dedicated grill* for this port because of its size (~2,283 lines, a single `DTIALPSApplication(tk.Tk)`). The first 2026-07-06 session grilled and locked the port's **architecture** (build strategy, threading, verification, scope) and confirmed **pre-port readiness**. The second 2026-07-06 session (the dedicated widget grill) locked the **widget-level design** — the Tk→Qt mapping, the region-commit boundaries, and two user-authorized scope changes (a dropped cosmetic and one added control). Both are recorded below; implementation may now begin from this PRD.

---

## Problem Statement

`gui/app.py` is the last Tkinter surface: a single 2,283-line `DTIALPSApplication(tk.Tk)`
god-window driving the 9-stage (10 in synB0 mode) pipeline. The viewer is already Qt
(PRD 0010, a separate process). The goal of the migration is a more professional look,
achieved by Qt's native widgets — not a redesign.

The port is a large widget rebuild, but the risk that usually makes such a rebuild
dangerous — non-widget logic tangled into the widget layer — has already been removed by
the preparation PRDs:

- **Output side** — worker messages → view-intents via the tk-free `ResultModel`
  (PRDs 0006/0007), now over a **typed `WorkerMessage` stream** (PRD 0012).
- **Input side** — form/widget state → `BatchState`/`Readiness` via the tk-free
  `form_model.py` (`FormState`, `build_batch_state`, `compute_readiness`) (PRD 0011).
- **Viewer** — fully ported (PRD 0010); `app.py` launches it as a subprocess.
- **Pure science / results contract** — `roi_placement.py`, `results_layout.py`,
  `alps_calculation.py`, all tk-free and tested.

**Pre-port readiness finding (2026-07-06):** after PRD 0012 there is **no large pre-port
extraction left**. What remains in `app.py` is genuine widget construction (the
`_create_*_frame` builders, ~60% of the file) plus thin adapter glue over already-extracted
pure calls (`discover_with_subdir_fallback`, `new_unique_runs`, the models). Two *micro*-
candidates were noted and deliberately **not** extracted pre-port — the
`_stage_to_button_indices` map and the "warn when crossing 1→multiple subjects in synB0
mode" predicate — because the port re-expresses them anyway and a separate pass is not
worth it. The adapter is thin enough on both sides; the port is a widget rebuild against
models that already exist.

## Solution (architecture — locked)

Port `app.py` to a PySide6 `QMainWindow` adapter over the **unchanged** tk-free models,
mirroring the viewer port's alongside-then-swap, but built incrementally because a single
window cannot run half-Tk/half-Qt at runtime.

### Build strategy — alongside, incremental regions, temporary entry point

The escape hatch from the half-and-half impossibility: the *codebase* carries both windows
at once behind separate entry points (as the viewer port carried `viewer.py` +
`viewer_qt.py`), even though a running window cannot mix toolkits.

1. **Build `gui/app_qt.py` beside `app.py`**, with a temporary hidden `--gui-qt` flag in
   `__main__.py` so the half-built Qt window is launchable and manually smokeable
   throughout the build. `--gui` stays on Tk until the final commit.
2. **Grow `app_qt.py` across region-commits**, each a coherent slice, each leaving the
   suite green (model tests untouched; `app_qt.py` imported by no test) and the Qt window
   smoke-launchable via `--gui-qt`. The bulk region (c) is pre-split into c-i/c-ii, so
   the sequence is ~5 region-commits + the flip:
   - **(a) Shell** — `QMainWindow`, menu, toolbar (green Run **+ Cancel**, both placed
     here but Cancel inert until the run path lands in (d)), main layout, and the `QTimer`
     drain wired to the unchanged `ResultModel` + typed `WorkerMessage` stream.
   - **(b) Data input** — the subject tree, folder add/discovery (over the unchanged
     `discover_with_subdir_fallback`/`new_unique_runs`), remove/clear, plus the
     Common-Parameters + Output block (PE, readout, RPE, output dir, staging).
   - **(c-i) CLI-row stage frames** — the repetitive per-stage parameter frames built on
     the shared `add_cli_option_row` builder (dwidenoise, mrdegibbs, dwifslpreproc, eddy,
     dwi2tensor, tensor2metric, flirt/fnirt), reading Qt widgets into the **unchanged**
     `FormState` and delegating to `build_batch_state`/`compute_readiness`.
   - **(c-ii) Bespoke frames** — the non-CLI-row frames: synB0 output-dir + validation,
     ROI params, output-setup retention checkboxes, results placeholder.
   - **(d) Console / log / results + live run** — the run console, streamed log, and the
     batch results view rendering the unchanged `BatchResultsView`; Cancel goes live here.
     (Per the widget grill, there is **no** sidebar stage-status coloring — see Decision 6.)
3. **Final commit — flip and delete.** `--gui` points at the Qt window; `app.py`, the temp
   `--gui-qt` flag, and any now-dead Tk deps are removed — mirroring the viewer's
   "delete Tk, rename" closer.

### Threading — unchanged from the plan (firm)

A `QTimer` drains the **same** `queue.Queue` the workers already write to; `processing/`
and `workers.py` **stay Qt-free**. QThread+Signals was rejected specifically to keep the
headless core, CLI reanalysis, and batch paths free of any PySide6 import. Cancellation
keeps the existing `threading.Event` mechanism.

### Scope — no visual redesign (hard boundary, two authorized exceptions)

The professional look comes **only** from Qt's native widgets. The port preserves the
current layout, control set, and observable behavior. Any restyling or layout rework is an
explicit *later* effort, out of scope here — so that "behavior-preserving" stays meaningful
and the smoke checklist stays anchored.

**Two deliberate, user-authorized exceptions** (widget grill, 2026-07-06) — recorded here
so "behavior-preserving" stays truthful rather than silently violated:

1. **Dropped: sidebar stage-status coloring.** The stage buttons no longer recolor
   purple/green during a run (see Decision 6). A pure removal of a cosmetic convenience,
   redundant with the console during a run; it deletes the port's messiest interaction.
2. **Added: a working Cancel button** (see Decision 7). The engine already supports
   cancellation end-to-end; today's Tk window never wired a control to it. This is a
   control-set *addition*, so the Tk and Qt windows differ in behavior during the alongside
   phase — accepted.

## User Stories

*(Architecture-level; widget-level stories are added by the dedicated grill.)*

1. As a maintainer, I want `app.py` ported to a PySide6 `QMainWindow` adapter over the
   unchanged tk-free models, so that no input/output/science logic is rewritten during the
   port.
2. As a maintainer, I want the Qt window built alongside the Tk window behind a temporary
   `--gui-qt` flag, so that each region-commit is launch-and-smoke without disturbing the
   live `--gui`.
3. As a maintainer, I want each region-commit to leave the suite green and the Qt window
   smokeable, so that the port never becomes one large untestable diff.
4. As a maintainer, I want the `QTimer`-drains-the-same-queue threading model with
   `processing/` staying Qt-free, so that the headless core, CLI, and batch paths never
   import PySide6.
5. As a maintainer, I want the port to preserve layout and behavior (no redesign), so that
   the model tests plus the smoke checklist are a valid net across the swap.
6. As a maintainer, I want the final commit to flip `--gui`, delete `app.py`, and remove the
   temp flag, so that the end state carries exactly one toolkit.

## Implementation Decisions (architecture)

### 1. Alongside + incremental regions + temporary `--gui-qt`, not one big build

A single window cannot be half-Tk/half-Qt at runtime, but the codebase can carry both
behind separate entry points (as PRD 0010 did with `viewer.py`/`viewer_qt.py`).
**Rejected:** a single "build `app_qt.py`" commit + a "flip" commit — simpler bookkeeping
but a ~2,000-line unreviewable, only-manually-testable diff that violates the project's
single-concern / minimal-scope working style. Building region by region behind an unflipped
`--gui-qt` keeps "green at each step" meaningful: suite green **and** the window smokeable at
every commit.

### 2. `QTimer` drains the same `queue.Queue`; `processing/` stays Qt-free (firm)

Carried unchanged from the port plan. **Rejected:** QThread + Signals — it would pull
PySide6 into the worker/processing layer and break the `gui → processing` one-way arrow that
the import-guard test pins. The existing `threading.Event` cancellation is reused.

### 3. No pytest-qt; a written smoke checklist is the Qt verification contract

The project's deliberate pattern (reaffirmed at 0010) is no pytest-qt / no `QApplication`
test infra: the tk-free models carry the logic and their tests guard it across the swap; the
widget layer is manually smoked. Held here despite the larger window, because the Qt-specific
risk is only widget construction + signal wiring — which an offscreen pytest-qt smoke would
catch only shallowly, at the cost of the offscreen-CI infra the project avoids. **Rejected:**
a one-time pytest-qt "constructs offscreen" exception — it breaks a deliberate invariant and
tends to grow. Instead, the port PRD carries a **written smoke checklist** (below) that each
region-commit runs the relevant slice of.

### 4. Hard "no visual redesign" scope boundary

The look upgrade is native-widget only; layout, controls, and behavior are preserved.
**Rejected:** folding a restyle/layout rework into the port — it would make
"behavior-preserving" meaningless and leave the smoke checklist unanchored. Restyling is a
recorded later effort.

### 5. One PRD, multi-commit; widget design deferred to a dedicated grill

The region-commits are not independently shippable (the window is unusable until fully built
and flipped), so they are commits under one PRD 0013, as 0009/0010 carried multi-commit work.
**Rejected:** splitting into multiple PRDs. The widget-by-widget Tk→Qt mapping (now
*Widget Design Decisions* below) is filled in by this PRD's own grill before implementation.

## Widget Design Decisions (locked in the dedicated grill, 2026-07-06)

### 6. Sidebar = navigation-only `QStackedWidget` + checkable button column; status coloring dropped

The Tk sidebar is a vertical stack of `ttk.Button`s that `pack_forget`/`pack` a hand-rolled
frame stack (`_show_stage`/`_show_console`), and those same buttons **double as run-status
indicators** (recolored purple=running / green=complete via `_stage_to_button_indices` +
`_update_stage_status`). That dual role is the port's single messiest interaction: in Qt a
`setStyleSheet` background clobbers the native selected look, so selection and status don't
compose without a `status`-property + re-polish scheme.

**Locked:** the nav is a column of **checkable `QPushButton`s in an exclusive
`QButtonGroup`** (native `:checked` marks the current stage) driving a **`QStackedWidget`**
(all pages resident; the synB0 toggle clears/repopulates the button column, 9↔10). The
**status coloring is dropped** (an authorized Scope exception) — it is a cosmetic
convenience, redundant with the console during a run (on Run the app switches to the console
view, where the per-subject tree + streamed log are the real feedback). Consequences: the
`_stage_to_button_indices`/`_synb0_stage_to_button_indices` maps are **not ported** (one of
the two flagged micro-candidates is now moot); the adapter keeps only the **log** half of
`UpdateStageStatus` and treats `ResetStageButtons` as a **no-op**; `ResultModel` is
consumed **unchanged**. The one bit of QSS in the whole app is a one-line green Run button.
**Rejected:** carrying the full coloring scheme via a dynamic `status` property + exclusive
`QButtonGroup` `:checked` composition — correct but the port's messiest code, for a
redundant cosmetic; and `QListWidget` nav — cleaner Qt idiom but a look/behavior change the
no-redesign boundary forbids.

### 7. A working Cancel button (control-set addition; engine already supports it)

`BatchWorker` already checks `cancel_event.is_set()` **between subjects** and emits
`BatchCancelled`, and `ResultModel` already maps that to `"Batch processing cancelled."` —
but today's Tk window **never sets the event** (no Cancel control, no window-close wiring),
so the whole cancel path is unreachable from the running app, and smoke-checklist item 5
described behavior that did not exist.

**Locked:** wire a real Cancel button (an authorized Scope exception). **Two-button
toolbar** — the green Run stays as-is; a **Cancel** sits beside it, disabled except during a
run. On press it calls `cancel_event.set()`, disables itself, and relabels to "Cancelling…"
until the worker actually stops (`_check_results` sees the thread die → the already-emitted
`BatchCancelled` logs the line and Run re-enables per `compute_readiness`). Semantics are
**subject-boundary**: the in-flight subject runs to completion; Cancel stops the batch
before the next subject — matching what the engine already does (mid-subject interruption
would need engine changes, out of scope). The "Cancelling…" interim is **adapter-only** — no
model change. **Rejected:** faithful omission + a follow-up feature PRD (cleaner "valid net
across the swap" story, but the checklist already assumed cancellation and the engine was
waiting for it); and a single Run↔Cancel toggle button (juggles label + state + green
styling on one control, blurs two distinct actions).

### 8. All three `ttk.Treeview`s → flat `QTreeWidget`

The subjects tree, console tree, and dynamic results tree all use `show="headings"` — flat,
header-only grids, no hierarchy. **Locked:** flat **`QTreeWidget`** for all three (matches
the viewer port's precedent; one Treeview-replacement idiom across the GUI). Index
addressing (`SetRowStatus.index`) → `topLevelItem(i)`; the tag→foreground rule becomes an
adapter-side `tag→QColor` map mirroring today's `tag_configure`; multi-select on the
subjects tree → `ExtendedSelection`; the results grid keeps the adapter's
`_BATCH_COLUMN_LAYOUT` key→(width, anchor) map. **Rejected:** `QTableWidget` — a truer
data-grid, but introduces a second Treeview idiom alongside the viewer's `QTreeWidget`.

### 9. CLI-option row → shared-grid builder returning a handle

The eight CLI-row stage frames share the `_create_cli_option_row` pattern: five aligned
grid columns (checkbox │ name │ value │ Browse │ description) in **one shared** grid, so
columns line up across the header and every row. A self-contained per-row `QWidget` would
lay out independently and break that alignment (a visible look change).

**Locked:** a **shared `QGridLayout` per options group + a builder** `add_cli_option_row(grid,
row, name, type, …)` that populates the row and returns a small handle `{checkbox,
value_widget, type}` exposing `is_enabled()`/`value()`. A registry
`cli_option_rows[stage][name]` mirrors `cli_option_vars`; the Qt `_form_state()` twin reads
`OptionState(enabled=h.is_enabled(), value=h.value(), type=h.type)` (value always a string;
int coercion stays in the model). The checkbox `toggled` signal enables/disables its own
value+Browse widgets; Browse → `QFileDialog` (`getOpenFileName`/`getExistingDirectory`/
`getSaveFileName`) with the Tk `filetypes` tuples translated to Qt name-filter strings and
the same `user_config` initial-dir keys. **Rejected:** a self-contained `CliOptionRow(QWidget)`
— cleaner encapsulation but drifts the aligned columns unless faked with brittle fixed widths.

### 10. Mechanical widget mappings, and the FA/readout split

**Locked** 1:1 substitutions (all behavior-preserving; viewer.py already uses these):
`ttk.LabelFrame` → `QGroupBox`; the `Canvas`+`Scrollbar` scroll hack → `QScrollArea`; the
read-only scrolled `tk.Text` log → `QPlainTextEdit` (read-only, `appendPlainText`,
`ensureCursorVisible`); `messagebox.*` → `QMessageBox.{information,warning,critical,question}`;
readonly `ttk.Combobox` → non-editable `QComboBox` (auto/manual toggle → `setEnabled`);
`pack`/`grid` → `QVBoxLayout`/`QHBoxLayout`/`QGridLayout`; menu/toolbar/browse →
`QMenuBar`+`QAction` / toolbar row / `QFileDialog`.

**The one behavior nuance (locked):** the two numeric fields are **not** symmetric.
**Readout stays a free-text `QLineEdit`** — it can be unparseable and `compute_readiness`
deliberately blocks Run on an invalid readout (`is_readout_valid`); that path must survive.
**FA threshold → a bounded `QDoubleSpinBox`** (0.0–1.0, step 0.05), which makes an
unparseable/out-of-range FA impossible, so `_form_state()`'s `TclError` guard for FA simply
disappears in the Qt snapshot. A small, arguably-better tightening — recorded because it is
an observable behavior change.

### 11. Region-commit boundaries and their smoke slices

**Locked:** the four regions of the build strategy, with the bulk region (c) pre-split into
**c-i** (the CLI-row stage frames on the shared builder) and **c-ii** (the bespoke frames) —
~5 region-commits + the final flip. The Cancel *widget* is created in (a) but stays inert
until the run path lands in (d), where it goes live. The seven smoke-checklist items map
onto the regions: (b) = item 1 + subjects/output-dir half of item 7; (c) = items 2, 3 +
synb0-dir/readout half of item 7; (d) = items 4, 5 (cancel), 6 + remainder of item 7. Each
region-commit runs its slice; the flip runs the whole checklist. **Rejected:** region (c) as
a single commit — it is eleven frames and violates the single-concern / minimal-scope style;
kept flexible (c-i may stay one commit if its diff is modest).

### 12. Temporary `--gui-qt` flag, `main_qt()` dispatch, and the flip closer

**Locked:** a **hidden** `--gui-qt` branch in `__main__.main()`, placed before the default
`--gui` dispatch and **absent from `__doc__`/`--help`**. It calls a new **`gui.main_qt()`**
(mirroring `gui.viewer()`) that runs `_check_viewer_dependencies()` (PySide6, same message as
the viewer) + a science-package check and launches `app_qt` via `QApplication`/`exec()` — and
**never requires tkinter**, achieved by factoring the numpy/nibabel/scipy loop out of
`_check_dependencies()` into a small `_check_science_deps()` that both callers share. `--gui`
stays Tk throughout the alongside phase. **Flip closer (final commit)** mirrors the viewer's
"delete Tk, rename": point `--gui` at Qt (fold `main_qt` into `main`), **delete `app.py`**,
remove the `--gui-qt` branch, swap `_check_dependencies` to require **PySide6 instead of
tkinter**, and **rename `app_qt.py` → `app.py`**. End state carries one toolkit and no temp flag.

## Testing Decisions

- **The existing tk-free model tests are the logic net** — `test_result_model.py`,
  `test_form_model.py`, `test_viewer_model.py`, and (after PRD 0012) the typed-stream tests
  stay green unchanged across the port.
- **A written manual smoke checklist is the Qt verification contract.** Each region-commit
  runs the relevant slice; the final flip runs the whole thing. Draft checklist:
  1. Add subjects — single folder, and a parent folder with per-subject subdirectories
     (subdir fallback); duplicate-run dedup.
  2. Toggle synB0 mode — the stage list rebuilds (9→10 stages), and the "same synB0 output
     for all subjects" warning fires on crossing 1→multiple subjects.
  3. Edit CLI options across stages (flag, value, int-coercion, skip-empty) and confirm the
     assembled run reflects them.
  4. Run a batch to completion — log streams, per-subject row statuses update, the results
     table renders (`BatchResultsView`). (No stage-button coloring — dropped, Decision 6.)
  5. Cancel mid-run — the Cancel button sets `cancel_event` and shows "Cancelling…"; the
     in-flight subject finishes, then the batch stops **at the next subject boundary**, the
     log shows "Batch processing cancelled.", the Run button re-enables, and the log file
     closes/prunes per the output config. (Cancel is a control added by this port,
     Decision 7 — subject-boundary, not mid-subject.)
  6. Open the results output folder; launch the results viewer subprocess.
  7. Run-button readiness — blocked/enabled correctly for missing subjects, invalid subjects,
     no output dir, unparseable readout, missing synB0 dir.
- **No pytest-qt / no `QApplication` infra** (Decision 3).

## Out of Scope

- **Any visual redesign / layout rework / restyling** — a recorded later effort (Decision 4),
  save the two authorized exceptions recorded under *Scope* (dropped status coloring, added
  Cancel button).
- **Mid-subject cancellation** — Cancel is subject-boundary only (Decision 7); interrupting
  the in-flight subject's MRtrix/FSL subprocesses would need engine changes.
- **The Qt-only frozen bundle** (roadmap step 5) — only after both windows are Qt.
- **Changes to the tk-free models, the typed message stream, or the science/results
  contracts** — all consumed unchanged.
- **The remaining micro-extraction** (the synB0 1→multiple warning predicate) — re-expressed
  in the port, not pre-extracted. (The other candidate, the `_stage_to_button_indices` map,
  is now moot: Decision 6 drops the status coloring, so it is not ported at all.)

## Widget design — resolved

The widget-by-widget Tk→Qt mapping, layout mechanics, styling, region-commit boundaries, and
the `--gui-qt` flag form — all *deferred* by the architecture draft — are now locked in
*Widget Design Decisions* (6–12) above. Nothing in this PRD remains open for a further grill;
implementation proceeds from Decisions 1–12.

## Further Notes

- Follows the single-concern / behavior-preserving / green-at-each-step working style
  (see PRDs 0001–0006, 0009–0012).
- Roadmap position: step 4. Precedes the Qt-only frozen bundle (step 5), which requires both
  windows Qt.
- Depends on PRD 0012 (typed `WorkerMessage` stream) landing first — the port's output side
  drains that contract.
