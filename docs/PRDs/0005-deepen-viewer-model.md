# PRD 0005 — Deepen the Results Viewer behind a tk-free ViewerModel

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 1 ("give the Results Viewer the seam app.py got"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

`gui/viewer.py` is a 1237-line `tk.Toplevel` subclass (`ResultsViewer`) that tangles non-UI logic with Tkinter. PRD 0004 deepened `gui/app.py` behind a `ResultModel` seam but **explicitly left the viewer untouched** (PRD 0004, Out of Scope). The viewer therefore still carries every pre-refactor antipattern, addressable only by opening the window:

- **Output-folder discovery + subject file-matching** — `_load_output_folder` (580–707): scans subject folders, matches `*_FA.nii.gz` / `*_V1.nii.gz` / ROI masks, builds per-subject records, and populates the tree — interleaved with `combobox`/`treeview`/`messagebox` calls.
- **ALPS CSV parsing** — `_parse_alps_csv` (709–798): detects the ALPS method from column names and extracts values. It uses no `self` — a free function trapped as a method — and has **already drifted**: it carries a legacy `"Left Hemisphere ALPS"` (no-suffix) fallback (752–766) that the writers in `batch.py` / `reanalysis.py` never emit.
- **NIfTI loading** — `SubjectData.load_images` / `load_rois_for_type` (52–98): `nib.load` of FA/V1/ROI, lazy per-subject.
- **DEC rendering math** — `_create_dec_image` (961–1012) and `_add_roi_overlay` (1014–1056): direction-encoded-colour from |V1|, FA modulation, ROI compositing — pure NumPy, but reachable only through widget callbacks.
- **The on-disk contract itself** — the `rois_{token}/` directory convention (`f"rois_{...}"` and the magic `name[5:]` strip) and the `alps_results_{token}.csv` filename, plus the ALPS column schema, exist as repeated literals across `viewer.py`, `batch.py`, `reanalysis.py`, `report.py`, `pipeline.py`, and `registration/fsl.py`. No module owns this contract; the viewer's copy has diverged.

Consequence: roughly 191 lines of domain logic — "what ROI types does this folder have", "what does this CSV say", "what colour is this slice" — cannot be exercised or regression-tested without a display. None of it has a unit test today. This is a testability and locality problem on its own merit; the eventual Tkinter→PySide6 port is a downstream beneficiary, not the motivation.

## Solution

Give the viewer the same treatment `app.py` got: extract each trapped *decision* into a tk-free unit, leaving `viewer.py` as the adapter that reads widgets and renders returned data. Placement follows the engine/GUI split the recent PRDs enforce:

- **The on-disk contract → `processing/results_layout.py`.** A dependency-free engine leaf owning the ROI-directory and CSV naming and the ALPS column schema, exposing a typed `read_alps_csv(path) -> AlpsTable`. tk-free *and* GUI-text-free; it speaks ROI **tokens** only. This is the home the convention has never had.
- **The viewer's session logic → `gui/viewer_model.py`.** A tk-free, gui-side **presentation model** — the genuine twin of `gui/result_model.py` — that owns the loaded session and recomputes a rendered slice on demand. Unlike `ResultModel` (a `msg → list[Intent]` *translator* driven by the worker queue), the viewer is a stateful *session*: it has no message stream, so the model is a session object with command/query methods returning plain data and finished NumPy pictures.

All extracted units return **structured data or NumPy arrays, never user-facing strings or widgets**; `viewer.py` owns every bit of phrasing, colour, dialog type, zoom, and canvas placement. This keeps the engine free of GUI text and the presentation model free of Tkinter, consistent with PRDs 0002–0004.

The work lands as **three behavior-preserving, leaf-first commits**:

1. `processing/results_layout.py` (naming + schema + `read_alps_csv`) + tests — a pure addition, no consumer yet.
2. `gui/viewer_model.py` (`ViewerModel`, `SubjectRecord`, `SessionView`, `MetricsView`, the pure `render_dec_slice`) + tests — consumes commit 1; `viewer.py` not yet wired.
3. `gui/viewer.py` rewritten into a thin Tkinter adapter over the model.

This is **extract-then-port**: the viewer stays on Tkinter at the end of this work. The PySide6 rewrite is a separate, far-future adapter swap and is out of scope. No scientific output, no CSV format, and no observable viewer behavior changes.

## User Stories

1. As a maintainer, I want the viewer's "what ROI types are here / what does this CSV say / what does this slice look like" decisions out of the `tk.Toplevel` subclass, so that they can be unit-tested without a display.
2. As a developer, I want a `ViewerModel` that loads a results folder and answers queries with plain data and NumPy arrays, so that the viewer's session logic has one tested interface.
3. As a developer, I want the direction-encoded-colour rendering as a pure function of explicit arrays, so that the DEC math is verified array-in/array-out with no widgets and no files.
4. As a maintainer, I want the `rois_{token}/` and `alps_results_{token}.csv` naming in one module, so that the convention is found and changed in one obvious place instead of nine literals.
5. As a maintainer, I want a single typed `read_alps_csv` reader against one ALPS column schema, so that the viewer's reader can no longer drift from what the writers emit.
6. As a developer, I want `load_session` to return either a populated session or a typed load error, so that the four folder/CSV error dialogs become a tested map of error kinds rather than imperative `messagebox` calls.
7. As a maintainer, I want every extracted unit to return structured data or arrays and the adapter to own all phrasing, colour, dialog type, zoom, and canvas placement, so that the engine carries no GUI text and the model holds no Tkinter.
8. As a future contributor, I want `gui/viewer_model.py` to depend on no Tkinter, so that the presentation model stays unit-testable and the eventual PySide6 viewer can reuse it unchanged.
9. As a developer running the viewer, I want folder loading, ROI-type switching, subject selection, the slice/zoom/view controls, the metrics panel, and the ROI overlays to behave exactly as before, so that this refactor is invisible to users.
10. As a reviewer, I want the three extractions as their own behavior-preserving commits, smallest leaf first, so that the two pure additions and the one adapter rewrite can be read and reverted independently.
11. As a future contributor, I want `processing/results_layout.py` to be a dependency-free leaf that speaks ROI tokens only, so that importing it can never create a cycle and it carries no GUI display names.
12. As a maintainer, I want the per-subject record to be a frozen value with no decoded arrays and no baked metrics, so that subject identity is plain data and the model owns the one-subject-at-a-time array cache.
13. As a developer switching ROI type, I want the per-type metrics to be a lookup keyed by `(roi_type, subject)`, so that switching types no longer broadcasts a mutation into every subject record.
14. As a maintainer, I want the writers/parsers elsewhere in the engine (`batch`, `reanalysis`, `report`, `pipeline`, `fsl`) and the canonical ROI-name set recorded as a follow-up onto `results_layout`, so that "why didn't the duplication get fully killed" is answered.
15. As a maintainer, I want the PySide6 port kept out of this PRD, so that the seam is proven under the existing toolkit before any toolkit swap.

## Implementation Decisions

### 1. The seam is a stateful `ViewerModel` session object

The viewer's tk-free unit is a session object that owns loaded state and exposes command/query methods (`load_session`, `select_subject`, `set_roi_type`, `render_slice`, `current_metrics`) returning plain data and arrays. It is the twin of `ResultModel` in role (tk-free, gui-side, holds presentation logic) but not in shape.

- **Rejected — an intent-translator mirroring `ResultModel` literally** (`handle(...) -> list[Intent]` with `SetImage`, `SetMetrics`, …): the viewer has no external message producer like the worker queue. Intents would wrap plain return values (`SetImage(array)`) in a stream abstraction nothing is streaming — a forced fit.
- **Rejected — a bag of stateless free functions the widget orchestrates:** that leaves the session coherence (which subject, which ROI type, the CSV cache) and the "how are these called" bugs stranded in the widget. No locality; the test surface stays "the window".

### 2. Lean model: session state in the model, the view cursor in the adapter

The model owns the loaded session — subject records, the per-ROI-type CSV cache, the current selection, and the **current** subject's decoded arrays. The transient **view cursor** — current view (axial/coronal/sagittal), current slice, zoom, show-ROIs — lives in the adapter, and `render_slice(view, slice, show_rois)` takes them as explicit parameters. The model exposes `num_slices(view)` and `default_slice(view)` for the slider.

- **Rejected — a fat model that owns the cursor too** (`set_slice`/`set_view`/`toggle_rois` + a no-arg `render()`): it hides the render's inputs as internal state, so every render test needs setup-then-assert and the seam blurs. `zoom_level` is pure display scaling (a `PhotoImage`/`QPixmap` resize) and never belongs in a tk-free model. Parameterising render keeps it a pure function and keeps the "reset to middle on subject/view change" decision testable via `default_slice`.

### 3. `results_layout` owns naming **and** the typed CSV reader

`processing/results_layout.py` exposes `roi_dir_name(token, refined)`, `parse_roi_dir(name) -> token`, `alps_csv_name(token, refined)`, the ALPS column schema, and `read_alps_csv(path) -> AlpsTable` (detected method + per-subject rows, preserving the legacy no-suffix fallback). The `ViewerModel` consumes all of it.

- **Rejected — naming-only, leaving CSV parsing in the viewer:** the parsing is the worst piece of duplication and the one that has already drifted. Method detection (`"…ALPS-PAS" in columns → Both/PAS/LAB`) is a property of the schema, not the viewer, so it belongs beside the column names. A naming-only module would leave the gnarliest, most-divergent logic exactly where it is.

### 4. Narrow scope: only the viewer's consumers go on `results_layout` now

This PRD creates `results_layout` and puts the viewer's three consumers (`discover_roi_options`, `get_csv_path_for_roi_type`, `_parse_alps_csv`) on it. The processing-side writers/parsers — `batch.py`, `reanalysis.py`, `report.py`, `pipeline.py`, `registration/fsl.py` — keep their current literals; repointing them is a recorded follow-up.

- **Rejected — repoint everything in this PRD:** the CSV writers emit scientific output and deserve their own behavior-preserving review (does the emitted file stay byte-identical?); bundling them into a GUI refactor muddies the revert story. The interim duplication is byte-identical and strictly better than today. This mirrors PRD 0003, which created `processing/constants.py`, repointed only the consumers it needed, and deferred the rest.

### 5. `load_session` returns a result; errors are data, the adapter owns phrasing

`load_session(folder) -> SessionView | LoadError`. `LoadError(kind, payload)` with `kind ∈ {folder_missing, no_results, csv_missing}`; the adapter maps each kind to today's exact `messagebox` text and type. `SessionView` carries the ordered ROI options, the detected ALPS method, and the ordered subject records (paths only — no decoded arrays). The **empty-subjects** case is a `SessionView` with no subjects; the adapter shows the existing `showinfo`. Image decode stays lazy in `select_subject`, which returns a success flag the adapter turns into the per-subject `showwarning`.

- **Rejected — bake message strings into the model / make empty-subjects an error kind:** that puts GUI phrasing in a unit and treats a valid-but-empty folder as a failure. PRD 0004 settled this grammar (`validate_runnable` returns `(ok, kind, payload)`; the adapter owns the dialogs); the viewer follows it.

### 6. `ViewerModel` is gui-side; display names live in the model, not the engine

`processing/results_layout.py` speaks ROI **tokens** only. The GUI display names (`"Sphere 3.0mm"`, `"Square 3x3"`, the `" (r)"` suffix) are presentation text and live in `gui/viewer_model.py`; `SessionView` emits `roi_options` as `(token, label)` pairs.

- **Rejected — display names in `results_layout`:** the engine carries no GUI text (PRDs 0003/0004). Emitting `(token, label)` pairs also removes today's fragile reverse string-match (`get_roi_display_name(roi_type) == selected_display`) — the adapter holds the pairs and looks up by token.

### 7. `render_slice` returns a finished, oriented picture; orientation in the model

`render_slice(view, slice, show_rois) -> np.ndarray | None` returns a display-ready, **oriented** `uint8` H×W×3 RGB array — DEC, ROI overlay, and the per-view `rot90`/`fliplr` orientation all applied — at native voxel resolution, or `None` for an out-of-range slice. The adapter does only toolkit work: wrap (`PIL.Image`/`PhotoImage` today, `QImage`/`QPixmap` later), scale by zoom, place on the canvas.

- **Rejected — orientation in the adapter:** the `rot90`/`flip`-per-view rules are pure NumPy tightly coupled to slice extraction. Splitting them across the seam fragments "produce the correct 2D picture for this view" and forces any future adapter to re-encode the orientation table. Keeping orientation model-side means the picture arrives correct and the adapter only displays it scaled.
- **Note:** `ROI_COLOR = (255,255,255,200)` (viewer.py:207) is defined but unused — the overlay paints solid white. Behavior-preserving means the overlay keeps painting solid white; the dead constant is removed in commit 3.

### 8. The rendering math is a pure free function the model wraps

`render_dec_slice(fa, v1, roi_masks, view, slice, show_rois) -> np.ndarray | None` is a standalone pure function in `gui/viewer_model.py`; `ViewerModel.render_slice` is a one-line wrapper feeding it the current loaded arrays.

- **Rejected — render as a model method requiring loaded state:** a pure function is testable with hand-built arrays and no model/file setup, matching the codebase's existing pure-science split (`alps_calculation.py` ↔ `test_alps_calculation.py`). The render is the highest-value thing to test, so it is made maximally pure.

### 9. Split `SubjectData` into a frozen record + model-owned arrays/metrics

`SubjectData`'s fat shape is split: a frozen `SubjectRecord` (subject id, folder, FA/V1 paths, `all_roi_paths` keyed by token — **no decoded arrays, no baked metrics**), held in `SessionView.subjects`; and the `ViewerModel`, which owns the per-ROI-type CSV cache, the current selection, and the current subject's decoded arrays. `current_metrics()` is a **lookup** by `(current_roi_type, current_subject_id)`.

- **Rejected — carry `SubjectData`'s shape across:** it conflates immutable identity, the one-at-a-time array cache, and metrics-that-vary-by-ROI-type, and it forces the broadcast-mutation into every record on ROI-type change. The split deletes the `roi_paths`/`active_roi_type` backward-compat fallback (everything is `all_roi_paths` by token; `rois` is just a token).
- **Behavior pinned:** the subject-tree **status** column reflects the initially loaded CSV and does not change on ROI-type switch — preserved exactly.

### 10. `results_layout` owns naming + schema only; FA/V1/ROI globs stay in the model

The inside-folder file patterns — `*_FA.nii.gz`, `*_V1.nii.gz`, and the ROI-mask names `left_proj`/`right_proj`/`left_assoc`/`right_assoc` — stay in `ViewerModel.load_session`. `results_layout` owns only the dir/CSV naming and the CSV schema/reader.

- **Rejected — hoist the globs into `results_layout`:** the dir/CSV naming and column schema have multiple real consumers (which is what earns them a shared home); the FA/V1/ROI-mask globs have exactly one consumer today — the viewer. Hoisting a seam nothing else crosses is speculative. The canonical ROI-name set *is* a real cross-codebase duplication, but it travels with the Decision-4 follow-up, not this PRD.

### 11. Extract-then-port; the viewer stays Tkinter at the end of this work

The extraction is behavior-preserving under the existing Tkinter adapter; the suite is green at every commit. The Tkinter→PySide6 rewrite is a separate, far-future adapter swap.

- **Rejected — extract and port to PySide6 in one move:** a regression would be ambiguous (extraction bug or toolkit bug?), and it loses the green-at-every-commit revert story. Proving the seam under the current toolkit, with tests, is what makes the later port a contained swap.

### 12. Three behavior-preserving commits, leaf-first

(1) `results_layout` + tests; (2) `viewer_model` + tests; (3) `viewer.py` → thin adapter. Commits 1–2 are pure additions with full coverage and no behavior change; all observable-behavior risk is quarantined in commit 3.

- **Rejected — one sweeping rewrite of `viewer.py`:** loses the per-commit revert story and the behavior-preservation guarantee the user requires of refactors. The leaf-first order also establishes the test rhythm before the adapter rewire, as PRD 0004 did.

## Testing Decisions

**What makes a good test here:** it asserts the *external behavior of the extracted unit* — given these arrays / this CSV / this folder, this array / this `AlpsTable` / this `SessionView` — never a widget internal. Tests name no Tkinter object and never instantiate the viewer window.

**The seams:** the pure-function call boundaries in `results_layout` (`read_alps_csv`, the naming helpers) and the value/array-returning methods of `ViewerModel`. These are value-in/value-out; they need no fakes or injection (unlike the `*_seam.py` suites, which fake an *execution* seam this change does not have).

**New test files:**
- `tests/test_results_layout.py` — `read_alps_csv` over tiny CSVs written to `tmp_path` (LAB / PAS / Both / legacy no-suffix), asserting the detected method, the parsed values, and that the legacy `"Left Hemisphere ALPS"` fallback still resolves; plus round-trip tests of `roi_dir_name` / `parse_roi_dir` / `alps_csv_name`.
- `tests/test_viewer_model.py` — `render_dec_slice` over hand-built `(4,4,4)` FA/V1/mask arrays with the expected RGB computed by hand as an independent oracle; `load_session` over a synthetic output folder generated in `tmp_path` (subjects × `*_FA.nii.gz` / `*_V1.nii.gz` / `rois*/` masks + an `alps_results*.csv` via `nib.Nifti1Image`), asserting `SessionView` contents, each `LoadError` kind, and the empty-subjects case; and `set_roi_type` / `current_metrics` over the loaded session.

**No display instantiation.** The model is driven directly; the `viewer.py` adapter is verified by manual GUI smoke, the accepted coverage boundary (as in PRD 0004).

**Prior art:** the pure-array oracle tests follow `tests/test_alps_calculation.py`; the synthetic-NIfTI-in-`tmp_path` fixtures follow `tests/test_reanalysis_seam.py` and `tests/test_registration.py`. This is explicitly *not* modelled on `tests/fakes.py` / the `*_seam.py` injection suites.

## Out of Scope

- **Repointing the processing-side writers/parsers** (`batch.py`, `reanalysis.py`, `report.py`, `pipeline.py`, `registration/fsl.py`) onto `results_layout`, and de-duplicating the canonical ROI-name set: a separate behavior-preserving concern that touches scientific output. Recorded as a follow-up.
- **The Tkinter→PySide6 rewrite of `viewer.py`:** a far-future adapter swap once the codebase is wrangled; this PRD only proves the seam under Tkinter.
- **Zoom, canvas placement, and toolkit image conversion:** these stay in the adapter by design (Decision 2/7); they are not extracted.
- **`app.py` and `ResultModel`:** already deepened by PRD 0004; untouched here.
- **Headless-Tk / xvfb integration tests** driving `ResultsViewer` end-to-end.
- **Any change to viewer behavior, dialog text, default, the CSV format, or scientific output.** This refactor is invisible to users and to prior results.

## Further Notes

- **Sequencing:** commits 1 → 3 as listed (results_layout, viewer_model, viewer adapter). Each leaves the suite green; the two pure-addition commits establish the extract→test rhythm before the adapter rewire.
- **Relationship to prior PRDs:** this is the viewer-side counterpart of PRD 0004 (which deepened `app.py` behind `ResultModel` and named the viewer as a downstream beneficiary). It also begins to settle the on-disk-contract duplication that PRD 0003 flagged as a follow-up, scoped down to the viewer's consumers.
- **Domain model:** `CONTEXT.md` (created alongside this PRD) records the new ubiquitous-language terms — the results-on-disk contract, ROI token vs display name, and `ViewerModel` / `SessionView` / `SubjectRecord` / `MetricsView` / `render_dec_slice`.
- **Counts:** `viewer.py` is 1237 lines today; roughly 191 are non-UI logic trapped in widget callbacks. The drifted legacy CSV fallback lives at `viewer.py:752–766`.
- The grilling-session decisions behind this PRD are recorded in agent memory (`viewer-model-deepen-design`).
