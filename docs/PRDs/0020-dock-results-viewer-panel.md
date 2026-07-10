# PRD 0020 — Dock the Results Viewer into the main app (ResultsViewerPanel)

## Problem Statement

From the user's perspective, the DTI-ALPS tool has two windows that do not feel
like one program. The main application configures and runs the pipeline; the
**Results Viewer** — the direction-encoded-colour image browser with ROI
overlays and ALPS metrics — opens as a *separate* window, either from the "Open
Results Viewer..." button on the 9. Results screen or from `dti-alps --viewer`.

To inspect the results of a run the user has just launched, a second window
appears (spawned as its own OS process), competing for screen space, alt-tab
slots, and taskbar entries. Every feature the user cares about is not in one
place. When several results folders are being compared, the window juggling gets
worse. The user wants the viewer to live **inside** the main app, so that
configuring, running, and reviewing results are all reachable from a single
window without spawning anything.

## Solution

Dock the viewer into the main window as a first-class navigation destination.

A new **"Results Viewing"** entry appears in the left sidebar under the existing
**Output Settings** section, beside "Output Setup". Selecting it shows the full
viewer surface — subject list, DEC image pane, navigation/zoom controls, ROI
overlay toggle, and ALPS metrics — as an in-app page, not a separate window.

The viewer's content is extracted once into a reusable Qt widget,
**ResultsViewerPanel**, and hosted in two places from the same class:

- the standalone `dti-alps --viewer [folder]` window (kept, unchanged in
  behaviour) wraps a panel as its central content; and
- the main app embeds a panel as a resident page in its content stack.

The in-app "Open Results Viewer" buttons stop launching a subprocess. Instead
they switch to the docked "Results Viewing" page and load the relevant output
folder in place. They are relabelled **"View Results"** to match this in-app
navigation. Loading is on demand only: a finished batch run does not eagerly
populate the panel; the user loads a folder by clicking a "View Results" button
or the panel's own "Load Folder..." button.

## User Stories

1. As a researcher running the pipeline, I want to review a run's images and
   metrics inside the same window I launched it from, so that I don't have to
   manage a second window.
2. As a researcher, I want a "Results Viewing" entry in the sidebar under Output
   Settings, so that I can reach the viewer at any time without running anything.
3. As a researcher who just finished a batch run, I want to click "View Results"
   on the results screen and land on the docked viewer already loaded with that
   run's output folder, so that reviewing is one click away.
4. As a researcher, I want the "View Results" button in the batch results footer
   to open the docked viewer on that batch's output directory, so that the
   summary table and the image browser are two clicks apart in one window.
5. As a researcher, I want to load an arbitrary older results folder from inside
   the docked viewer via a "Load Folder..." button, so that I can review past
   work without leaving the app.
6. As a researcher, I want the docked viewer to keep showing the folder I last
   loaded when I navigate away to another page and back, so that my place is not
   lost.
7. As a researcher, I want the sidebar "Results Viewing" entry to simply show
   whatever is currently loaded (and an empty state on first use), so that
   navigating to it never triggers a surprising reload or long wait.
8. As a researcher, I do not want a run's completion to force the viewer to load,
   so that finishing a batch stays fast even when I don't intend to open the
   viewer.
9. As a researcher, I want to pick a subject from the list in the docked viewer
   and see its DEC image and ALPS metrics, so that I can inspect individual
   results — exactly as the standalone viewer does today.
10. As a researcher, I want to switch ROI type in the docked viewer and have the
    metrics and image refresh, so that I can compare ROI configurations.
11. As a researcher, I want axial / coronal / sagittal view radio buttons in the
    docked viewer, so that I can change orientation, with the slice resetting to
    the middle on a view change.
12. As a researcher, I want to scrub slices with the slider and the mouse wheel
    in the docked viewer, so that I can move through the volume.
13. As a researcher, I want the zoom −/+/Fit controls to behave in the docked
    viewer as they do standalone, including scrollbars on a zoomed-in slice, so
    that I don't lose that capability by docking.
14. As a researcher, I want a "Show ROIs" toggle available directly in the panel
    body, so that I can turn ROI overlays on and off even though the docked view
    has no menu bar.
15. As a researcher who prefers the standalone viewer, I want `dti-alps --viewer`
    and `dti-alps --viewer /path/to/output` to keep working exactly as before, so
    that my existing scripts and habits are unaffected.
16. As a researcher using the standalone viewer, I want every control (load, view,
    slice, zoom, show-ROIs) reachable without a menu bar, so that the removal of
    the menu costs me no functionality.
17. As a researcher using the standalone viewer, I want to close its window from
    the OS window chrome, so that dropping the File > Close menu item does not
    trap me.
18. As a researcher, I want the docked viewer's content area titled "Results
    Viewing" while it is shown, so that I always know which screen I'm on, matching
    how the other pages title the content group.
19. As a maintainer, I want the viewer's content to exist as a single reusable
    widget shared by both hosts, so that a fix or feature lands once and appears in
    both the docked and standalone views.
20. As a maintainer, I want the toolkit-free ViewerModel (session loading, CSV
    parsing, DEC rendering) untouched by this change, so that its existing tests
    remain the behaviour guarantee and nothing scientific is at risk.
21. As a maintainer, I want the engine/GUI split preserved (gui → processing,
    never the reverse), so that this GUI-only change does not compromise the
    headless core.
22. As a researcher, I want load-error cases (missing folder, no results, missing
    CSV) to still report their messages in the docked and standalone views, so
    that a bad folder is explained rather than silently ignored.
23. As a researcher, I want no second process, taskbar entry, or window to appear
    when I open results from within the app, so that the tool behaves as one
    application.

## Implementation Decisions

- **New reusable widget: ResultsViewerPanel.** Extract all of the current
  `ResultsViewer` content — subject list, DEC image pane and legend, navigation
  controls (ROI type, view radios, slice slider, zoom), and the ALPS metrics
  panel — into a `ResultsViewerPanel(QWidget)` in `gui/viewer.py`. It is the
  adapter for the unchanged `ViewerModel` and is host-agnostic. This is the term
  recorded in `CONTEXT.md`.
- **Panel is self-sufficient (no menu bar).** All controls live in the panel
  body. The current menu-only **"Show ROI Overlays"** action becomes a **"Show
  ROIs" checkbox** in the panel (near the legend/controls), driving the same
  show-ROIs state that the render path already consumes. "Load folder" is already
  a panel button; the three views are already radio buttons; "Close" is dropped
  (OS window chrome closes the standalone window).
- **Standalone window becomes a thin wrapper.** `ResultsViewer(QMainWindow)`
  stays but shrinks to a wrapper that instantiates a `ResultsViewerPanel`, sets it
  as the central widget, sets the window title/size, and forwards an optional
  initial folder. Its menu bar is removed. `launch_viewer(output_folder)` keeps
  its signature and behaviour, wiring through the wrapper.
- **External load entry point.** The panel exposes a public load method (e.g.
  `load_folder(path)`) that performs the existing `ViewerModel.load_session` flow
  and refreshes the widgets. Both hosts call it: the standalone wrapper on
  construction when given a folder, and the main app when a "View Results" button
  is clicked.
- **Main-app docking.** In `gui/app.py`, a `ResultsViewerPanel` instance is built
  and registered as a resident page in the content `QStackedWidget` (via the
  existing `_register_page` mechanism). A new checkable **"Results Viewing"** nav
  button is added under the **Output Settings** sidebar heading, beside "Output
  Setup", using the existing `_nav_button` + `_show_page` pattern. Showing it sets
  the content-group title to "Results Viewing".
- **Buttons navigate instead of spawning.** `_open_results_viewer` is rewritten:
  instead of `subprocess.Popen([... "--viewer" ...])`, it selects the docked
  "Results Viewing" page and calls the panel's `load_folder` with the target
  output directory. The existing folder-resolution stays (explicit argument, else
  the current `batch_state.config.output_dir`); when neither is available it
  simply shows the empty docked page. The subprocess/`sys` import for this path is
  removed.
- **Button relabelling.** The two in-app buttons — on the placeholder results
  stage page (`_create_results_page`) and in the batch results footer
  (`_show_batch_results`) — are relabelled **"View Results"** (no ellipsis), since
  they now navigate in-app rather than opening a window.
- **On-demand loading only.** No auto-load on batch completion. The docked panel
  is populated solely by a "View Results" button or its own "Load Folder..."
  button. Because the panel is a resident widget, its loaded state persists across
  navigation with no extra bookkeeping.
- **CLI path unchanged.** `__main__.py`'s `--viewer` branch keeps its behaviour,
  including the up-front `_check_viewer_dependencies()` PySide6 validation; it now
  reaches the panel through the wrapper.
- **Fit-timing guard (carried over, not new behaviour).** The panel's
  fit-to-viewport step reads the viewport size, which is only meaningful once the
  page is visible. Because in-app loading is triggered by the navigating button
  (the page is current by then) and the standalone constructor already defers its
  initial load, the panel guards its initial fit with a deferred call, mirroring
  the existing `QTimer.singleShot` approach in the current viewer constructor.
- **No change to ViewerModel or the results-on-disk contract.** Session loading,
  the per-ROI-type CSV cache, metrics shaping, and `render_dec_slice` are reused
  byte-for-byte. This is an adapter-layer reshaping only.
- **Engine/GUI split preserved.** Entirely within `dti_alps/gui/`; `processing/`
  is not imported into anything new and remains Qt-free.

## Testing Decisions

- **What makes a good test here:** it asserts external behaviour at the
  `ViewerModel` seam — folder / selection / view / slice / ROI-type in, session
  views / metrics views / rendered RGB arrays out — never a widget detail. The Qt
  panel is a thin adapter; its correctness is the model's correctness plus a manual
  smoke pass, exactly as PRDs 0010 and 0013 established.
- **The seam is the existing one, untouched.** `ViewerModel`'s function/property
  interface already owns all viewer behaviour and is not modified. `tests/
  test_viewer_model.py` imports neither tkinter nor PySide6 and stays green
  unchanged; it is the behaviour-preservation guarantee for session logic, CSV
  parsing, and DEC rendering across this refactor. **No new test seam is
  introduced** because no new toolkit-free logic is introduced.
- **No pytest-qt / no `QApplication` infra.** Consistent with Decision 3 of PRD
  0013, the docked/standalone hosting is intentionally-trivial glue and is not
  worth a Qt test-harness dependency or an offscreen-platform CI lane.
- **The engine-independence guard stays green.** `tests/test_engine_independence.py`
  continues to assert `processing/` imports no GUI toolkit; this change must not
  perturb it.
- **Manual smoke checklist (the Qt verification contract):**
  1. Launch the main app; click "Results Viewing" under Output Settings on a fresh
     start — the page shows an empty viewer, no error.
  2. Use the panel's "Load Folder..." to load a real output folder — subject list
     populates with statuses, the first subject auto-selects, image and ALPS
     metrics render.
  3. Switch ROI type — metrics and image refresh; switch axial/coronal/sagittal —
     slice resets to the middle; scrub the slice slider and mouse wheel; zoom
     −/+/Fit and confirm a zoomed slice scrolls rather than clips; toggle the new
     "Show ROIs" checkbox and confirm overlays appear/disappear.
  4. Run a batch to completion; confirm the docked viewer stays empty (no
     auto-load); click "View Results" in the batch footer — the app navigates to
     "Results Viewing" already loaded on that output directory.
  5. Click "View Results" on the placeholder results stage page (with a prior run
     present) — it loads that run's output directory in the docked panel.
  6. Navigate away to another page and back to "Results Viewing" — the loaded
     session and current subject are preserved.
  7. Confirm no subprocess/second window appears for any in-app "View Results"
     action.
  8. Trigger each load-error path (missing folder, no results, missing CSV) — the
     message text still appears.
  9. Standalone: `dti-alps --viewer` and `dti-alps --viewer /path/to/output` both
     launch, render, and behave as before; the window has no menu bar; every
     control is reachable in the panel; the window closes from OS chrome.
- **Prior art:** the project's deliberate "test the tk-free model, manually smoke
  the adapter" pattern (PRDs 0005/0006/0010/0013); `tests/test_viewer_model.py`
  is the model suite that already encodes it.

## Out of Scope

- Merging or cross-linking the docked viewer with the 9. Results **batch-results
  table**. The stage page and its post-run table are untouched and keep their own
  home; unifying the summary table and the image browser is not part of this work.
- Any change to the `ViewerModel`, `render_dec_slice`, the results-on-disk
  contract, or ALPS science.
- Removing the standalone viewer or the `--viewer` CLI (explicitly kept).
- Auto-loading the docked viewer on run completion (explicitly rejected in favour
  of on-demand loading).
- Adding a Qt GUI test framework (pytest-qt, offscreen CI).
- Converting the app to a Qt native docking system (`QDockWidget`) — the app's
  sidebar-nav + stacked-page paradigm is retained; "docked" here means an in-app
  page, not a floatable Qt dock.
- Any visual restyling of the viewer beyond relocating the show-ROIs control into
  the panel body.
- Multi-folder / multi-session comparison inside one panel (the panel shows one
  loaded session at a time, as today).

## Further Notes

- **Why a shared widget rather than nesting the QMainWindow.** A `QMainWindow`
  does not sit cleanly inside a `QStackedWidget` page — which is exactly why the
  current app shells out to a subprocess (see the comment in the present
  `_open_results_viewer`). Extracting a plain `QWidget` panel removes that
  constraint and follows the codebase's established model/adapter discipline, so
  both hosts share one implementation with zero logic duplication.
- **Alternatives considered and rejected during design (grilling session):**
  - *Repurpose the "9. Results" stage page* for the viewer — rejected because that
    page already hosts the post-run batch-results table and its stage number is not
    even stable (stage 10 under synB0), so overloading it forces the table and the
    viewer to fight for one page.
  - *Embed the existing `ResultsViewer(QMainWindow)` directly as a child* — rejected
    as a non-idiomatic hack with awkward menu-bar and sizing behaviour.
  - *Convert to `QDockWidget`* — rejected as clashing with the app's sidebar-nav +
    stacked-page metaphor.
  - *Remove the standalone viewer entirely* — rejected as a breaking CLI change with
    no benefit once the panel is shared; the standalone window serves the
    "browse old results without the full app" use case.
  - *Keep the standalone menu bar alongside a new panel checkbox* — rejected as a
    duplicated, must-be-synced control; the menu's other items (Load, View) already
    duplicate panel widgets, so the whole menu bar is dropped.
  - *Auto-load the panel on batch completion* — rejected because loading reads every
    subject's CSV and decodes arrays lazily; eager loading wastes work when the user
    never opens the viewer and couples run-completion to viewer state.
- **Vocabulary.** `CONTEXT.md` already carries the `ResultsViewerPanel` glossary
  entry (host-agnostic viewer adapter; self-sufficient, no menu bar; on-demand
  load) under the ViewerModel cluster.
- **Lineage.** This completes the arc of PRD 0010 (viewer → PySide6) and PRD 0013
  (app → PySide6): with both hosts on one toolkit, the viewer content can finally
  be a single embeddable widget rather than a separate process.
