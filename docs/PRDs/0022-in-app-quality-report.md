# PRD 0022 — In-app Quality Report section (GUI companion to `--report`)

## Problem Statement

The engine already knows how to score ROI placement quality. `python -m dti_alps
--report /path/to/output` walks an output directory, discovers every ROI shape,
and writes one `quality_report_{shape}.csv` per shape — each row a subject, each
column one of four metrics (**Directional Alignment (V1)**, **Angular Dispersion
(V1)**, **Fractional Anisotropy**, **Radial Asymmetry λ2/λ3**) over the four ROIs
(`left_proj` / `left_assoc` / `right_proj` / `right_assoc`). This is a real
quality-control tool: it tells the researcher whether the ROIs actually landed on
well-aligned, coherent white-matter fibres before they trust the ALPS numbers.

But it lives entirely on the command line. A user driving the GUI has to leave the
app, run a CLI incantation, find the written CSVs on disk, and open them in a
spreadsheet to read a grouped two-row-header table. The `--viewer` flag already
grew an in-app home ("Results Viewing" under Output Settings, PRD 0020); `--report`
has no such companion. The user wants to run the quality report and read its values
**inside the application**, the same way they now review images and ALPS metrics.

Two frictions the CLI cannot address motivate a companion rather than a shell-out:

- The CLI is **whole-folder only** — there is no way to report on just a few
  subjects. When a folder holds many sessions/runs (the travelling-phantom output
  has 30+ `*_dwi` subject folders per results dir), the user often wants to look at
  a handful, not all of them.
- The output is a **file on disk**, not something the app can show. Reading it means
  a spreadsheet and a grouped header that is easy to misread.

## Solution

Add a **"Quality Report"** page to the main window as a third entry in the left
sidebar under the existing **Output Settings** heading, beside "Output Setup" and
"Results Viewing". It is the GUI companion to `--report`: same metrics, same
compute core, presented and driven in-app.

The label in the UI is **"Quality Report"** — the engine's own term
(`quality_report_{shape}.csv`, `processing/report.py`). No rename of files, module,
or functions; the sidebar simply speaks the engine's language.

The page's flow:

1. **Load folder** (mirroring the viewer) selects an output directory and scans it.
2. A **shape dropdown** is populated from the shapes discovered in that folder,
   labelled for humans by the same `roi_display_name` mapping the viewer uses
   (`sphere3` → "Sphere 3mm", `squarev9_refined` → the square label, refined).
3. Selecting a shape refreshes a **subject checkbox list** to exactly the subjects
   that have *that* shape (`discover_subjects_for_shape`), all checked by default.
   The shape is the primary axis; the subject list follows it.
4. The user unchecks any subjects they don't want and clicks **Generate**. This runs
   over the chosen **subset** — a capability the whole-folder CLI does not have.
5. Generation runs on a **background worker thread** (a `threading.Thread` in the
   engine, following the pipeline's queue-drain discipline, *not* on the GUI thread),
   reporting per-subject progress and staying **cancellable**. The UI never freezes.
6. Results fill a **two-tier grouped table** — a top band per metric group spanning
   its four ROI columns, subjects as rows — the on-screen twin of the CLI CSV's two
   header rows. Subjects are compared side by side, which is the report's whole point.
7. **Generate writes nothing to disk.** A separate **"Save report as CSV…"** button
   opens a Save-As dialog pre-filled with `quality_report_{shape}.csv` in the loaded
   output dir; the user accepts or renames. Writing is always an explicit, deliberate
   act, so a subset report can never silently clobber a full-folder CLI report.

## User Stories

1. As a researcher, I want a "Quality Report" entry in the sidebar under Output
   Settings, so that I can reach the ROI quality metrics at any time without leaving
   the app or touching the command line.
2. As a researcher, I want to load any output folder into the Quality Report page,
   so that I can inspect past runs, not only the one I just launched.
3. As a researcher, I want a shape dropdown listing the ROI shapes found in the
   folder, labelled the same way the viewer labels them, so that "Sphere 3mm" means
   the same thing on both pages.
4. As a researcher, I want the subject list to show the subjects that actually have
   the selected shape (all checked by default), so that I never generate a report on
   a subject that has no data for that shape.
5. As a researcher, I want to uncheck subjects and report on just the ones I care
   about, so that a folder with dozens of sessions doesn't force an all-or-nothing view.
6. As a researcher, I want Generate to run in the background with per-subject progress
   and a Cancel button, so that a large folder doesn't make the app appear hung and I
   can stop a run I started by mistake.
7. As a researcher, I want the metrics shown in a grouped table (metric group over its
   four ROIs, one row per subject), so that the on-screen layout matches the CSV I
   already know and I can compare subjects at a glance.
8. As a researcher running a LAB-only analysis, I want the Radial-Asymmetry columns to
   simply be blank (no L2/L3), so that the table is honest about what was computed,
   exactly as the CLI CSV is.
9. As a researcher, I want Generate to change nothing on disk, so that browsing quality
   metrics is a safe, read-only act.
10. As a researcher, I want an explicit "Save report as CSV…" button with a Save-As
    dialog pre-filled with the standard name, so that I can persist a report when I
    choose to, and rename it to avoid overwriting a full-folder report I made earlier.
11. As a researcher, I want the loaded folder / shape / table to persist when I navigate
    away and back, so that I don't lose my place.
12. As a researcher, I want a bad or empty folder (not an output dir, no shapes found)
    to be explained rather than silently ignored, so that I know why nothing appeared.
13. As a maintainer, I want the scientific compute (`processing/report.py`) reused
    byte-for-byte, so that the in-app numbers are identical to the CLI's and its tests
    remain the guarantee.
14. As a maintainer, I want the report to have its own worker and message channel,
    separate from the pipeline's closed `WorkerMessage` union, so that a GUI-only
    feature does not pollute the batch-lifecycle protocol.
15. As a maintainer, I want the engine/GUI split preserved (`gui → processing`, never
    the reverse; `processing/` imports no Qt), so that this change does not compromise
    the headless core.
16. As a researcher who prefers the CLI, I want `--report` to keep working exactly as
    before (whole folder, writes every shape's CSV), so that my scripts are unaffected.

## Implementation Decisions

- **New sidebar entry + resident page.** In `gui/app.py`, add a checkable **"Quality
  Report"** nav button under the Output Settings heading (via the existing `_nav_button`
  + `_show_page` pattern), and register a resident page in the content `QStackedWidget`
  (via `_register_page`), beside how "Results Viewing" is wired. Showing it titles the
  content group "Quality Report".
- **New tk-free presentation model: QualityReportModel** (`gui/report_model.py`). The
  read-side sibling of `ViewerModel`. `load_folder(folder)` scans an output dir and
  returns the discovered shape tokens (errors-as-data, `LoadError`-style);
  `subjects_for_shape(token)` returns the subjects that have that shape. A
  `generate(token, subject_subset, progress_cb, cancel)` composes the existing
  `processing/report.py` leaf functions over the chosen subset and returns a
  **QualityReportView** (plain data). A `save_csv(view, path)` writes via the existing
  `write_report_csv`. No Qt, no widgets — this is the term recorded in `CONTEXT.md`.
- **Reuse the compute leaf unchanged.** `processing/report.py` already exposes exactly
  the pieces needed — `discover_roi_shapes`, `discover_subjects_for_shape`,
  `calculate_subject_metrics` (per-subject `SubjectReportData`, returns `None` on
  missing files), and `write_report_csv`. The model composes them over a subject subset
  and skips the whole-folder `generate_reports`/`run_report` driver (which discovers all
  shapes and writes CSVs). **No change to `report.py`.**
- **Dedicated report worker: `processing/report_worker.py`** (new). A
  `threading.Thread` (like `BatchWorker`, *not* a `QThread`) that runs the subset
  compute and pushes its **own** small typed messages — report progress (subject i of
  n), complete (the `QualityReportView`/rows), error, cancelled — onto a `queue.Queue`.
  These messages are **not** added to the closed `WorkerMessage` union in
  `processing/messages.py`; they are a separate, report-only set, so pipeline dispatch
  (`ResultModel.handle`) is untouched and its raises-on-unknown guarantee is unaffected.
- **Adapter drain, mirroring the pipeline.** `gui/app.py` drains the report queue with
  a `QTimer.singleShot` poll loop (the same idiom as `_check_results`), translating each
  message into widget updates: progress into a status line/bar, complete into the
  grouped table, error into a message, cancelled into a reset. The adapter owns all
  phrasing, colour, and the two-tier header layout.
- **Shape labels reuse `roi_display_name`.** The dropdown maps each discovered token to
  its human label through the viewer's existing `roi_display_name` (`gui/viewer_model.py`),
  so the two pages cannot drift on what a shape is called.
- **Subject list is shape-driven.** Selecting a shape (re)builds the checkbox list from
  `discover_subjects_for_shape`, all checked. Changing shape refreshes the list and
  clears/invalidates the shown table (a fresh Generate is required, since a new shape is
  a new compute).
- **Two-tier grouped table.** The table replicates the CSV's grouped header: a top band
  cell per metric group spanning its four ROI sub-columns (`l_proj`/`l_assoc`/`r_proj`/
  `r_assoc`), subjects as rows, cells formatted as the model provides them. Wide but
  horizontally scrollable; faithful to the artifact.
- **Generate is read-only; Save is explicit.** Generate computes and displays only.
  "Save report as CSV…" opens a `QFileDialog` save dialog pre-filled with
  `output_dir/quality_report_{shape}.csv`; on accept, the model writes via
  `write_report_csv`. No implicit writes anywhere in the Generate path.
- **Errors-as-data.** Folder-not-an-output-dir and no-shapes-found are returned by the
  model as `LoadError`-style values; the adapter owns the messagebox/label phrasing,
  mirroring `ViewerModel`.
- **Engine/GUI split preserved.** `report_worker.py` uses only `threading`/`queue`
  (Qt-free); `report_model.py` imports no Qt; the adapter lives in `gui/app.py`. The
  dependency arrow stays `gui → processing`.

## Testing Decisions

- **What makes a good test here:** it asserts external behaviour at the
  `QualityReportModel` seam — folder / shape / subject-subset in; discovered shapes,
  per-shape subject lists, and a `QualityReportView` (grouped rows, formatted cells,
  empty Radial-Asymmetry group under LAB-only) out — never a widget detail. This mirrors
  the `ViewerModel` test discipline (PRDs 0005/0010/0020).
- **New model suite: `tests/test_report_model.py`.** Drives `QualityReportModel` against
  a synthetic/fixture output dir: shape discovery, shape-driven subject lists, subset
  generation (a report over 2 of 4 subjects has exactly those 2 rows), the LAB-only
  blank-Radial-Asymmetry case, `save_csv` round-tripping through `write_report_csv`, and
  the error-as-data cases (missing dir, no shapes). Imports neither Qt nor tkinter.
- **Existing `report.py` tests unchanged.** The compute leaf is reused byte-for-byte;
  its current tests remain the scientific guarantee and must stay green.
- **The engine-independence guard stays green.** `tests/test_engine_independence.py`
  must still pass: `processing/report_worker.py` imports no GUI toolkit. This is the
  automated check that the new worker did not smuggle Qt into the engine.
- **No pytest-qt / no `QApplication` infra.** Consistent with PRDs 0013/0020, the Qt
  adapter (nav button, checkboxes, dropdown, table, drain loop, Save dialog) is
  intentionally-trivial glue verified by a manual smoke pass, not a Qt test harness.
- **Manual smoke checklist (the Qt verification contract):**
  1. Launch the app; click "Quality Report" under Output Settings on a fresh start —
     empty page, no error.
  2. Load a real multi-shape output folder — the shape dropdown populates with
     human labels; selecting a shape fills the subject checkboxes (all checked).
  3. Switch shape — the subject list refreshes to that shape's subjects and the table
     clears.
  4. Uncheck a subject or two, click Generate — a background run shows per-subject
     progress, the UI stays responsive, and the table fills with exactly the checked
     subjects in a grouped two-tier header.
  5. Start Generate on a large folder and click Cancel — generation stops promptly and
     the page resets.
  6. Load a LAB-only run — the Radial-Asymmetry columns are blank.
  7. Click "Save report as CSV…" — the dialog is pre-filled with
     `quality_report_{shape}.csv`; save it and confirm the file matches a CLI `--report`
     CSV for the same shape/subjects.
  8. Navigate away and back — the loaded folder, shape, and table persist.
  9. Load a non-output folder and a folder with no shapes — each shows an explanatory
     message, not a silent blank.
  10. Confirm `python -m dti_alps --report /path/to/output` still writes every shape's
      CSV exactly as before.
- **Prior art:** the "test the tk-free model, manually smoke the adapter" pattern
  (PRDs 0005/0006/0010/0013/0020); `tests/test_viewer_model.py` is the sibling suite.

## Out of Scope

- **Changing `--report` / `report.py` behaviour.** The CLI stays whole-folder and keeps
  writing every shape's `quality_report_{shape}.csv`. The compute leaf is reused, not
  modified.
- **The `*_ALPS_Report_*.html` / `.xlsx` artifact.** Those files (seen in example output
  dirs) are a *different* report, not produced by `--report`, and are untouched here.
- **Renaming the concept.** No `roi_report_*` rename; files, module, and functions keep
  their `quality_report` / `report.py` names. "ROI Report" as a UI label is rejected in
  favour of the engine's "Quality Report".
- **Showing all shapes at once** (tabs/stacked tables). One shape at a time via the
  dropdown; a folder with ten shapes does not render ten tables.
- **Writing on Generate**, or an implicit fixed-path write. Writing is only the explicit
  Save-As action.
- **Adding report progress to the pipeline's `WorkerMessage` union / `ResultModel`.** The
  report has its own worker and message channel.
- **A Qt GUI test framework** (pytest-qt, offscreen CI) — the adapter is smoke-tested.
- **Charts / plots / thresholding / pass-fail colouring** of the quality metrics. This
  page shows the numbers; interpreting them is the researcher's job (a future PRD may
  add visual QC cues).

## Further Notes

- **Why subject subsetting when the CLI has none.** This is the one place the companion
  deliberately exceeds the CLI. Whole-folder reports on a 30-session directory bury the
  few subjects a user is actually checking; an in-app tool with checkboxes is where
  subsetting belongs, and it costs nothing in the engine (the per-subject
  `calculate_subject_metrics` already operates one subject at a time). The CLI stays
  simple; the GUI adds the interaction the GUI is good at.
- **Why a dedicated worker instead of reusing the pipeline's.** The pipeline's
  `WorkerMessage` union is closed and batch-lifecycle-specific (`BatchStart`,
  `SubjectComplete`, …), and `ResultModel.handle` *raises* on any unmembered message —
  a property the pipeline relies on. Threading report progress through it would either
  widen that union with foreign members or force awkward reuse of batch messages for a
  non-batch activity. A separate `threading.Thread` + report-only message set keeps both
  channels honest: the pipeline union stays pristine, and the report path is
  self-contained and independently testable. The cost is a second small drain loop,
  which is cheap and mirrors an already-understood idiom.
- **Why display-only Generate + explicit Save.** A subset report written to
  `quality_report_{shape}.csv` would silently overwrite a full-folder report the user
  made earlier via the CLI — a real data-loss footgun. Separating compute (safe,
  read-only) from persistence (explicit, named, renameable) removes it while still
  letting the user save when they mean to.
- **Alternatives considered and rejected during design (grilling session):**
  - *Whole-folder scope (strict CLI parity)* — rejected; the user specifically wants to
    report on selected subjects, which the CLI cannot do.
  - *Write the subset CSV on Generate* — rejected as a clobber footgun (above).
  - *Label the section "ROI Report"* — rejected to avoid a third "report" name colliding
    with the engine's "quality report" and the unrelated `*_ALPS_Report_*` artifact.
  - *Show all shapes stacked/tabbed* — rejected as heavier UI and slower generate for no
    gain over a shape dropdown that matches the viewer's mental model.
  - *Synchronous generation with a busy cursor* — rejected because loading several NIfTI
    volumes per subject freezes the app on a large folder; a cancellable background
    worker is worth the plumbing.
  - *Reuse the pipeline worker/union/ResultModel* — rejected to keep the closed union
    batch-only (above).
  - *Flat header ("Metric — ROI" columns) or per-subject pivot* — rejected; the grouped
    two-tier header is faithful to the CSV and preserves side-by-side subject comparison.
- **Vocabulary.** `CONTEXT.md` carries the new **QualityReportModel** / **QualityReportView**
  / **Report worker** glossary entries under the presentation-models cluster.
- **Lineage.** This mirrors PRD 0020 (dock the viewer): another CLI capability
  (`--report`, as `--viewer` was there) gains an in-app home, built on a tk-free model +
  Qt adapter over the results-on-disk contract, reusing the engine compute unchanged.
