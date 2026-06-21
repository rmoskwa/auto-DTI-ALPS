# PRD 0010 — Port the Results Viewer to PySide6

Status: Draft · Date: 2026-06-20 · Grilled: 2026-06-20 · Source: First slice of the Tkinter→PySide6 GUI migration. The viewer is the smallest, most self-contained window; porting it first lands the toolkit, the patterns, and the alongside-then-swap discipline before the heavier `app.py` port (PRD 0012).

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** Drafted at the user's request after a grilling session (2026-06-20) that fixed the migration's shape (behavior-preserving, incremental, window-at-a-time, no event-loop mixing), the image-pane technology, the verification approach, and the commit slicing. Drafting then surfaced one wrinkle the grill missed: the Tk `app.py` opens the viewer as an **in-process `tk.Toplevel` child** (`_open_results_viewer`, app.py:2383), which a Qt window cannot be while the app is still Tk — resolved by Decision 6. The decisions below reflect that.

---

## Problem Statement

The DTI-ALPS GUI is Tkinter. The standing architectural goal is to move it to **PySide6** for a more professional, native look (native theming, HiDPI, real scrollbars, platform-standard widgets), and eventually to ship a frozen, downloadable bundle for non-developer users. That migration is large; doing it as one rewrite would mean a long-lived broken branch, against this project's behavior-preserving, smallest-leaf-first discipline (PRDs 0001–0009).

The **Results Viewer** (`gui/viewer.py`, 696 lines, ~133 tk references) is the natural first slice:

- **It is a separate entry point.** `--viewer` dispatches to `launch_viewer` in its own process (`__main__.py:197`); it does not share a running Tk root with the main app. So it can be ported and shipped without touching `app.py`, and the transition needs no Tk/Qt mixing in one event loop.
- **Its logic is already tk-free.** PRD 0005 deepened `ViewerModel` (`gui/viewer_model.py`) into a presentation model that owns the session, the per-ROI-type CSV cache, the current selection, and the DEC rendering — `render_slice()` hands back a finished RGB NumPy array. `viewer.py` is a thin adapter: it reads widgets, calls the model, and blits the result. The port rewrites only that adapter.
- **It has no background threading.** Every model call (`load_session`, `select_subject`, `render_slice`) runs synchronously on the main thread; the only deferred call is a 100 ms initial-load timer (viewer.py:71). The viewer carries none of the worker/queue/`after`-poll machinery that makes `app.py` hard — that fight is deferred to PRD 0012.
- **It already has a regression net.** `tests/test_viewer_model.py` exercises the model, and imports neither tkinter nor Qt — so it keeps passing across the toolkit swap and *is* the behavior-preservation guarantee for everything but the pixels.

The viewer also has a latent limitation worth fixing on the way through: the image canvas has **no working scrollbars** — when a slice is zoomed past the canvas it is silently clipped and centered (`_display_image`, viewer.py:564–585), so the hidden edges are unreachable.

## Solution

Port **only the view layer** of the viewer to PySide6, reusing `ViewerModel` and `user_config` unchanged. Build the Qt window *alongside* the Tk one, flip the entry points, then delete the Tk module — so the suite stays green and the viewer stays runnable at every commit, and both windows can be A/B-compared mid-port.

The image pane — the one genuinely toolkit-specific concern — becomes a **`QGraphicsView` + `QGraphicsScene` + `QGraphicsPixmapItem`**: each slice is `numpy RGB → QImage(Format_RGB888) → QPixmap` on the pixmap item, zoom is `view.scale()`, the NEAREST look is `setTransformationMode(FastTransformation)`, and the mouse wheel keeps changing the **slice** (an overridden `wheelEvent`), not the zoom. Qt supplies real scrollbars when zoomed in — which **fixes today's silent clipping** as a free, strict improvement consistent with the "native look" goal. This is the one intended observable change; everything else is behavior-preserving. The viewer stops importing **PIL** entirely.

The work lands as **four behavior-preserving commits**, each leaving the suite green:

1. **Add PySide6 to the optional `[gui]` extra.** Nothing imports it yet; headless/CLI installs are unaffected.
2. **Build the Qt viewer as a second module** (`gui/viewer_qt.py`): a `QMainWindow` `ResultsViewer` + `launch_viewer`, reusing `ViewerModel` and `user_config` unchanged. Not yet wired into any entry point — importable, unreferenced, green.
3. **Flip the three consumers to the Qt viewer** (Decision 6) and update the dependency check (Decision 7). Verified by manual smoke.
4. **Delete Tk `gui/viewer.py`; rename `viewer_qt.py` → `viewer.py`;** restore the canonical import paths; prune Pillow (and matplotlib if confirmed unused) from `[gui]` (Decision 5).

This is a single-concern, view-layer port. It changes no session logic, no CSV parsing, no DEC rendering, and no ALPS value. The only observable changes are cosmetic (native widgets) and the corrected scroll/clip behavior. It is independent of PRDs 0011 (typed message stream) and 0012 (`app.py` port), and is the first of four migration efforts (0010 viewer → 0011 typed stream → 0012 `app.py` → 0013 frozen bundle).

## User Stories

1. As a researcher reviewing results, I want the viewer to look and behave like a native desktop application, so that the tool feels professional and trustworthy.
2. As a researcher, I want every existing viewer capability — load a results folder, pick a subject, switch ROI type, switch axial/coronal/sagittal view, scrub slices, zoom, toggle ROI overlays, read ALPS metrics — to work exactly as before, so that the port costs me no familiarity.
3. As a researcher, I want the mouse wheel to keep scrolling through slices (not zoom), so that my established interaction is preserved.
4. As a researcher, I want to scroll to the edges of a zoomed-in slice, so that I can inspect the whole image instead of losing the clipped borders the current viewer hides.
5. As a researcher, I want NEAREST-style (unsmoothed) rendering preserved, so that voxel and ROI-boundary edges stay crisp rather than being blurred by interpolation.
6. As a researcher, I want the ROI-type dropdown, the subject list with status, and the ALPS metrics panel (including the "Both" ALPS-LAB/ALPS-PAS two-row layout) to show the same information with the same formatting, so that nothing I rely on moves or disappears.
7. As a researcher, I want the same load-error messages (folder missing, no results, CSV missing) and the same "no valid subjects" notice, so that failure modes read identically.
8. As a maintainer, I want all viewer logic to stay in the tk-free `ViewerModel`, so that the Qt view is a thin adapter and the existing `test_viewer_model.py` suite remains the regression net unchanged.
9. As a maintainer, I want the Qt view to introduce no new test seam and no GUI test framework, so that the project's "test the model, manually smoke the adapter" pattern (PRDs 0005/0006) is preserved and CI needs no display server.
10. As a maintainer, I want the viewer to stop depending on PIL/Pillow, so that the port removes a dependency Qt makes redundant rather than carrying it forward.
11. As a maintainer, I want PySide6 added only to the optional `[gui]` extra, so that headless CLI, batch, and reanalysis installs never pull in Qt.
12. As a developer, I want the Tk app's "View Results" action to keep opening the viewer on the same folder during the transition, so that the in-app path keeps working even though the viewer is now a Qt window in a separate process.
13. As a reviewer, I want the port delivered as four small commits (add dep, build alongside, flip entry points, delete-and-rename), so that each step is independently runnable, reviewable, and revertible, and the viewer is never broken between commits.
14. As a maintainer, I want both viewers to coexist briefly (commit 2–3), so that I can run the Qt window and the Tk window side-by-side and confirm parity before deleting the old one.
15. As a future contributor, I want the Qt viewer to remain a thin adapter over `ViewerModel`, so that new viewer logic is added to the model (and tested) rather than buried in widget callbacks.
16. As a maintainer, I want the dependency check to validate PySide6 (not PIL) on the viewer path, so that a missing-Qt install fails with a clear message instead of an obscure import error.
17. As the eventual packager, I want the viewer port to leave the codebase bundle-ready (Qt-only viewer, no PIL), so that the later frozen-bundle effort (PRD 0013) has one fewer toolkit and dependency to freeze.

## Implementation Decisions

### 1. Behavior-preserving, view-layer-only port; alongside-then-swap in four commits

The port rewrites `viewer.py`'s widget layer in PySide6 and nothing else. `ViewerModel` and `user_config` are reused byte-for-byte. The new Qt module is built beside the Tk one (`viewer_qt.py`), the three consumers are flipped to it (Decision 6), and the Tk module is deleted and the new one renamed into its place — four commits, each leaving the suite green and the viewer runnable.

- **Rejected — rewrite `viewer.py` in place (one big commit):** the viewer would be non-functional until a single large diff lands, with no opportunity to A/B the two windows. The alongside path keeps every commit green and runnable, matching the smallest-leaf-first discipline of PRDs 0001–0009.
- **Rejected — port without a PRD, as a direct PR:** every architectural change in this tree is recorded as an ADR-style PRD; skipping it loses the rationale trail and the doc/code-drift catches the grilling habit produces.
- **Rejected — redesign the viewer's UX while porting:** the goal is a *toolkit swap*, with the professional look arriving from native widgets, not from rethinking the layout. A redesign has no "current behavior" to verify against and would balloon scope.

### 2. Reuse `ViewerModel` and `user_config` unchanged; the seam is the existing model interface

The Qt view calls the same model surface the Tk view does: `load_session` (returning a `SessionView` or a typed `LoadError`), `select_subject`, `set_roi_type`, `render_slice`, `num_slices`, `default_slice`, `current_metrics`, `current_shape`, `current_alps_method`, `current_roi_type`, `current_subject_id`, plus the `SessionView` / `SubjectRecord` / `MetricsView` shapes. `user_config` (`get_user_config`, `UserConfig.KEY_VIEWER_FOLDER`, `get_initial_dir`, `set_from_path`) is reused as-is for last-folder persistence. No model method is added, changed, or removed.

- **Rejected — refactor the model "while we're in here":** any model change is a separate concern riding inside a view port. If the Qt adapter is tempted to hold logic, that logic belongs in the model under its own PRD, not in widget callbacks (US-15).
- **Rejected — introduce a new view/presenter seam for Qt:** the existing `ViewerModel` boundary is already the highest possible seam (arrays/typed-views in and out) and is already tested. Adding another seam would raise the seam count for no gain; the ideal count is one, and it already exists.

### 3. Image pane: `QGraphicsView` + `QGraphicsScene` + `QGraphicsPixmapItem`

The rendered RGB slice from `ViewerModel.render_slice()` is shown as `numpy → QImage(…, Format_RGB888) → QPixmap` set on a `QGraphicsPixmapItem`. Zoom is `view.scale()` over the existing 0.25×–5.0× range with `-`/`+`/`Fit` buttons mapped to it; `Fit` reproduces today's fit-to-viewport math. NEAREST rendering is preserved via `QGraphicsPixmapItem.setTransformationMode(Qt.FastTransformation)`. The mouse wheel keeps changing the **slice** via an overridden `wheelEvent`. When a slice is zoomed past the viewport, `QGraphicsView` shows real scrollbars.

- **Rejected — `QLabel` + `QPixmap` in a `QScrollArea`:** the most behavior-faithful and simplest option (scale the pixmap per zoom, center via alignment), but less flexible for future pan/zoom and not meaningfully simpler than the graphics-view path once scrollbars and wheel-override are wired. `QGraphicsView` is the canonical Qt image-viewer pattern and is the better foundation if the app.py-side rendering ever wants it.
- **Rejected — keep the PIL resize and only swap the final blit:** lowest diff, but keeps Pillow as a dependency Qt makes redundant (Decision 4) and carries forward the no-scrollbar clipping behavior instead of fixing it.
- **Note — the scrollbars are an intended behavior change.** Today's viewer clips zoomed slices silently; the Qt pane lets the user scroll to the edges. This is the lone observable behavior change and is a strict improvement, called out explicitly here the way PRD 0009 called out its corrected log string.

### 4. Drop PIL from the viewer

The Qt path goes `numpy RGB → QImage(Format_RGB888) → QPixmap` directly; the `Image.fromarray(...).resize(NEAREST)` + `ImageTk.PhotoImage` chain is deleted. `app.py` uses no PIL (verified — its only `Canvas` references are `tk.Canvas` scroll containers), and the sole other PIL reference is the dependency check in `gui/__init__.py` (Decision 7). So after the port **Pillow is unused tree-wide** and is pruned from the `[gui]` extra in commit 4.

- **Matplotlib is a separate, unverified prune.** `[gui]` also lists `matplotlib`, but the viewer renders via NumPy/PIL, not matplotlib. Before removing matplotlib, a tree-wide usage check is required (it may be used by `app.py` or be already-stale). Pillow's removal is certain; matplotlib's is conditional on that check (Out of Scope until confirmed).

### 5. PySide6 in the optional `[gui]` extra; not a core dependency; bundle deferred

PySide6 (chosen over PyQt6 for its LGPL license, which a redistributable frozen bundle needs) is added to the existing `[project.optional-dependencies].gui` extra alongside the current GUI deps, in commit 1. The core `dependencies` (numpy/nibabel/scipy) stay GUI-free, so headless science, CLI reanalysis, and batch installs never pull in Qt.

- **Rejected — make PySide6 a core dependency:** it would force the full Qt stack onto headless/CLI/batch users who never open a window, contradicting the design where the GUI is strictly optional.
- **Rejected — split `gui-qt` / `gui-tk` extras for the transition:** transient complexity for a single-developer tool; the Tk side is deleted at the end of this PRD anyway, so the split would have a one-PRD shelf life. One `gui` extra, with both toolkits coexisting only between commit 1 and commit 4 (Tk is stdlib, so it costs nothing in `pyproject`).
- **Deferred — the frozen bundle (PyInstaller/Briefcase + Qt platform plugins + LGPL dynamic-link compliance):** the downloadable artifact for non-dev users is the migration's end-state (PRD 0013), produced only after `app.py` is also Qt, so the bundle never contains both toolkits. This PRD only makes the viewer bundle-ready; it stands up no freeze tooling.

### 6. The Tk app's "View Results" launches the Qt viewer as a subprocess during the transition

`app.py._open_results_viewer` (app.py:2375) today constructs `ResultsViewer(self, output_folder)` — an in-process `tk.Toplevel` child of the Tk app. A Qt `QMainWindow` cannot be a child of a Tk window, and embedding it would mean two event loops in one process — exactly the mixing the migration rules out. In commit 3, this call site is changed to **spawn the viewer as a separate process** (`python -m dti_alps --viewer <folder>`), the same `subprocess` idiom `app.py` already uses to open output folders (app.py:2369). From the user's perspective, clicking "View Results" still opens the viewer on the current output folder; it is now its own top-level window/process rather than a child tied to the app's lifecycle.

This is the edit that frees `app.py` from importing the in-process viewer class, which is what lets commit 4 delete the Tk `viewer.py`.

- **Rejected — keep the Tk viewer alive solely for the in-app embed:** two viewers would coexist permanently, re-creating the "two homes" smell and blocking the delete-and-rename. Not viable.
- **Rejected — defer flipping the in-app button until `app.py` is ported (0012):** then `app.py` keeps importing `ResultsViewer` and commit 4 cannot delete `viewer.py`; the whole viewer slice would stall behind the much larger app port.
- **Note — temporary bridge.** Once `app.py` is Qt (PRD 0012), it may re-embed the Qt viewer in-process or keep the subprocess launch; that choice is 0012's, not this PRD's.

### 7. Update `_check_dependencies`: drop PIL, validate PySide6 on the viewer path only

`gui/__init__.py._check_dependencies()` currently hard-requires PIL ("required by viewer") and is called by both `main()` (the Tk app) and `viewer()`. In commit 3 the PIL requirement is removed (nothing needs it after Decision 4), and a **PySide6** check is added on the viewer launch path only — so the still-Tk app's `main()` is never made to require Qt during the transition. A missing-Qt install then fails with a clear, actionable message, mirroring how the old check guarded PIL.

- **Rejected — add the PySide6 check to the shared `_check_dependencies`:** that would force Qt onto the Tk app path during the transition. The check must stay viewer-specific until `app.py` is ported.

### 8. The widget translation is mechanical and behavior-preserving

The remaining widgets map directly and keep their current phrasing, dialog types, and control semantics: `tk.Toplevel` → `QMainWindow`; the `tk.Menu` bar → `QMenuBar`/`QAction`; the subject `ttk.Treeview` (Subject ID / Status) → `QTreeWidget` (or `QTableWidget`) with the same columns and single-row selection; the ROI-type `ttk.Combobox` → `QComboBox` over the same `(token, label)` options; the view `ttk.Radiobutton`s → `QRadioButton` + `QButtonGroup`; the slice `ttk.Scale` → `QSlider`; the `-`/`+`/`Fit` zoom buttons → `QPushButton`s; the ALPS metrics `ttk.Label` grid (including the "Both" two-row ALPS-LAB/ALPS-PAS layout and the single-method layout) → `QLabel`s in a `QGridLayout`; the legend swatch → a small colored `QLabel`; `filedialog.askdirectory` → `QFileDialog.getExistingDirectory`; `messagebox` → `QMessageBox`. The same four-decimal ALPS formatting and the `--` placeholders are preserved.

- **Rejected — adopt Qt conveniences that change behavior (drag-pan, smooth zoom, editable combo, multi-select tree):** each is a behavior change smuggled into a port. The only deliberate behavior change is the scrollbars (Decision 3); everything else mirrors today.

### 9. QApplication lifecycle replaces the Tk root; the deferred initial load becomes a Qt timer

`launch_viewer` constructs a `QApplication` (singleton), shows the `ResultsViewer` `QMainWindow`, and runs `app.exec()`, replacing the current `tk.Tk()` + `withdraw()` + `mainloop()` (viewer.py:684–692). The one timed call — the 100 ms deferred initial folder load (viewer.py:71) — becomes `QTimer.singleShot(100, …)` (or a direct call after `show()`), preserving the "load after the window is realized" intent.

- **Rejected — share a single `QApplication` with a future Qt `app.py`:** the viewer runs in its own process (`--viewer`, and the in-app launch is now a subprocess per Decision 6), so each process owns its `QApplication`. No shared-instance coordination is needed now or after 0012.

## Testing Decisions

**What makes a good test here:** it asserts external behavior at the `ViewerModel` seam — folder/selection/view/slice in, session views / metrics views / rendered RGB arrays out — never a widget detail. The Qt view is a thin adapter; its correctness is the model's correctness plus a manual smoke pass, exactly as the Tk view was always verified.

**The seam:** the existing `ViewerModel` function/property interface. **No new seam is introduced, and no GUI test framework is added.** `tests/test_viewer_model.py` imports neither tkinter nor PySide6, so it keeps passing unchanged across the swap and is the behavior-preservation guarantee for all session logic, CSV parsing, and DEC rendering (`render_dec_slice`).

**Manual smoke checklist (commit 3, against real output):** load a results folder; confirm the subject list populates with statuses and the first subject auto-selects; confirm the image and ALPS metrics render; switch ROI type and confirm metrics + image refresh; switch axial/coronal/sagittal and confirm the slice range resets to the middle; scrub the slice slider and the mouse wheel; zoom `-`/`+`/`Fit` and confirm a zoomed slice now scrolls instead of clipping; toggle ROI overlays; and trigger each load-error path (missing folder, no results, missing CSV) to confirm the message text.

**Prior art:** the project's deliberate "test the tk-free model, manually smoke the adapter" pattern (PRDs 0005/0006); `tests/test_viewer_model.py` is the model suite that already encodes it. No `tests/fakes.py`/`*_seam.py` machinery is involved — the viewer issues no subprocess and no `nib.load` at the view layer.

- **Rejected — add pytest-qt smoke tests for the Qt view:** introduces a dev dependency plus `QT_QPA_PLATFORM=offscreen` CI to test intentionally-trivial glue. If view-layer tests are ever wanted, the heavier `app.py` port (0012) is the place to weigh them — not this thin viewer.
- **Rejected — golden render-output fixtures:** the model (and thus `render_slice`) is unchanged in this port, so pixel-golden tests would mostly re-assert already-covered code.

## Out of Scope

- **The `app.py` port** (PRD 0012) and the **typed worker→GUI message stream** (PRD 0011) — separate, later efforts. This PRD touches `app.py` only at the single `_open_results_viewer` call site (Decision 6).
- **The frozen, downloadable bundle** (PRD 0013) — the migration end-state; this PRD only leaves the viewer bundle-ready (Qt-only, no PIL).
- **Any UX redesign** — layout, flow, and interactions are preserved; the professional look comes from native widgets, not rethinking the screen.
- **Any change to `ViewerModel`, `user_config`, the DEC rendering math, CSV parsing, or ALPS values** — reused unchanged.
- **A GUI test framework (pytest-qt) and any offscreen-display CI** — the model suite plus manual smoke is the chosen verification.
- **Removing `matplotlib` from `[gui]`** — conditional on a tree-wide usage check (Decision 4); Pillow's removal is in scope, matplotlib's is not until confirmed unused.
- **Porting the main app's in-process viewer embedding to native Qt** — during the transition the in-app launch is a subprocess; re-embedding (if wanted) is 0012's call.

## Further Notes

- **Sequencing (four commits, each leaving the suite green and the viewer runnable):**
  1. add `PySide6` to the `[gui]` extra (nothing imports it yet);
  2. add `gui/viewer_qt.py` — the Qt `ResultsViewer` + `launch_viewer`, reusing `ViewerModel`/`user_config`, wired to nothing;
  3. flip the three consumers — `__main__.py` `--viewer`, `gui/__init__.py.viewer()`, and `app.py._open_results_viewer` (subprocess, Decision 6) — and update `_check_dependencies` (Decision 7); manual smoke;
  4. delete `gui/viewer.py`, rename `viewer_qt.py` → `viewer.py`, restore the canonical `from .viewer import …` paths, and prune Pillow (and matplotlib if confirmed unused) from `[gui]`.
  Commits 2 and 1 are order-free (the dead module imports the new dep); commit 3 is the only one with an observable change and is the manual-smoke gate; commit 4 is mechanical (delete + rename + dep prune). Commits 3 and 4 may be squashed if a reviewer prefers the swap to land atomically.
- **The migration roadmap this opens:** 0010 (viewer) → 0011 (type the worker→GUI message stream, while still Tk) → 0012 (`app.py`, its own grill first) → 0013 (Qt-only frozen bundle). Recorded so the slice is read as step one of a plan, not a one-off.
- **Guardrail carried forward (relevant to 0012, stated here for the record):** `processing/` and `processing/workers.py` stay **Qt-free** — the headless core, CLI reanalysis, and batch paths must never import PySide6. The viewer port does not touch them; the app port must honor it (workers stay plain-thread + `queue.Queue`; the Qt view polls via `QTimer`).
- **PySide6, not PyQt6:** LGPL, so a redistributable frozen bundle (0013) can dynamic-link Qt without a commercial license.
- **Counts (verified):** `viewer.py` is 696 lines (~133 tk refs); `ViewerModel` lives in `gui/viewer_model.py` and is tk-free; the viewer's consumers are `__main__.py:198` (`--viewer`), `gui/__init__.py:51` (`viewer()`), and `app.py:2377/2383` (in-process Toplevel child); `app.py` uses no PIL; Pillow's only references are `viewer.py` and the `gui/__init__.py` dependency check.
- **Drafting status:** grilled 2026-06-20 (migration shape, image pane, verification, slicing all resolved); the app-embed wrinkle (Decision 6), the dependency-check update (Decision 7), and the Pillow/matplotlib prune split (Decision 4) were surfaced during drafting. Status remains `Draft` pending a grill of this PRD specifically.
