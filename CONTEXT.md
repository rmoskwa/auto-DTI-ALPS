# CONTEXT — ubiquitous language for autoDTI-ALPS

The shared vocabulary for this codebase. Names here are load-bearing: use them in
code, commits, and reviews. Architecture terms (module, interface, seam,
adapter, depth, leverage, locality) come from the `/codebase-design` skill and are
not redefined here.

## The engine / GUI split

- **Engine** — the `dti_alps/processing/` package: the distributable analysis core.
  It imports **no GUI toolkit** (`PySide6`, `tkinter`) — not directly, and not via
  `dti_alps.gui`, which it also does not import. The dependency arrow points one way
  only: **`gui → processing`, never the reverse**. Pinned by a subprocess import-guard
  test that asserts the *property* (no toolkit resident after importing an engine
  module — the engine runs headless), not merely the proxy (no `dti_alps.gui`
  resident).
- **GUI** — the `dti_alps/gui/` package. May hold Qt-free **presentation models**
  (see *ResultModel*, *ViewerModel*) that carry GUI text and shaping but import no
  PySide6, plus the Qt **adapter** layer that renders them.
- **Adapter** — the PySide6 layer (`gui/app.py`, `gui/viewer.py`). It reads widgets,
  calls a presentation model or engine function, and applies the returned plain data to
  widgets. It owns all phrasing, colour, truncation, and dialog type. The engine and
  the presentation models carry no widget code. Qt is the only toolkit: the Tk adapter
  was removed in PRD 0013, and the models it was written against survived the port
  unchanged — which is the point of the seam.

## Front ends

The engine has two, and neither is a client of the other (PRD 0024).

- **Front end** — a package that drives the engine for a user: the **GUI**
  (`dti_alps/gui/`) or the **CLI** (`dti_alps/cli/`). Each owns its own phrasing and
  its own presentation model over the same worker message stream; neither imports the
  other.
- **Verb** — a CLI subcommand naming one thing the tool does: `run`, `reanalyze`,
  `report`, `view`, `gui`. `run` is the only one that executes the full pipeline; the
  rest operate on an output directory a run already produced.
  _Avoid_: mode, command, action.

## Protocol vs run placement

The two unlike halves of a batch configuration. The distinction is what makes an
analysis shareable, and it is load-bearing wherever configuration is written to disk.

- **Protocol** — the portable description of *what the analysis is*: which stages run,
  the acquisition parameters, every per-stage tool option, the ROI shapes and ALPS
  method, the adaptive search envelope, which outputs are kept. Contains nothing
  machine-specific, so it can be published beside a methods section or handed to a
  collaborator unchanged. This is the only thing a **protocol file** carries.
  _Avoid_: config, settings, preset.
- **Run placement** — where one invocation lands: the output directory and the staging
  choice. Machine- and invocation-specific, and never serialized into a protocol file.
  _Avoid_: output config (that name already means the retention flags).
- **Protocol hash** — a stable digest of a protocol, stored beside a subject's results
  so a later run can tell whether it was produced under the *same* analysis.
- **Completion marker** — the per-subject results artifact carrying that subject's
  status, ALPS values, and protocol hash. Its presence with a matching hash is what
  makes a subject skippable on resume; a mismatch means the subject is reprocessed.

## Subject identity

- **Subject id** — the name a discovered run is filed under: it is both the
  per-subject output directory name and the key of a row in the results CSV. Derived
  from the data's path, so two runs that resolve to the same id would overwrite each
  other — a **collision**, which the engine refuses to start on rather than resolving
  silently.
  _Avoid_: subject name, participant id.

## The results-on-disk contract

The artifact the engine writes and the viewer/reports read. Owned by
`processing/results_layout.py` (the home for this contract; created with the
ViewerModel work).

- **ROI type / token** — the machine name of an ROI configuration as it appears on
  disk: `rois` (the default 3.0 mm sphere), `squarev9`, `squarev4`, `sphere2p5`,
  optionally suffixed `_refined`. The engine speaks **tokens**; the GUI maps a token
  to a **display name** (`"Square 3x3"`, `"Sphere 2.5mm (r)"`) for the user.
- **Geometry → token** — `shape_token(shape_type, sphere_radius)` is the single
  home for turning an ROI *geometry* into its base on-disk **token**, including the
  **default collapse**: the default 3.0 mm sphere (`DEFAULT_SPHERE_RADIUS`) is the
  bare `rois` token, every other sphere is `sphere{radius}` (`2.5 → sphere2p5`),
  squares pass through by type. Both writers (`registration/fsl.py`,
  `reanalysis.py`) call it, so the default sphere cannot bypass the collapse and
  land in `rois_sphere3/` (PRD 0016; was previously duplicated + drifted).
- **ROI directory** — `rois/` for the default, `rois_{token}/` otherwise
  (`{token}_refined` when refined; the refined default is `rois_refined/`, not
  `rois_rois_refined/`). Built by `roi_dir_name`, parsed by `parse_roi_dir` —
  replacing the scattered `f"rois_{...}"` literals and the magic `name[5:]` strip.
- **ALPS results CSV** — `alps_results.csv` for the default, `alps_results_{token}.csv`
  otherwise (`alps_csv_name`). A single run writes **one CSV per shape token**; the whole
  set it produces is named by `alps_csv_names(tokens)` (empty tokens → the single default
  name), the one home shared by the batch writer and the results-screen footer so the
  count the GUI shows cannot drift from the files that land on disk (PRD 0017).
- **ALPS column schema** — the canonical column names of the results CSV
  (`Left/Right Hemisphere ALPS-LAB/-PAS`, `Combined ALPS-*`, plus the legacy
  no-suffix `…ALPS`), ordered by `alps_columns(method)`. `read_alps_csv(path) ->
  AlpsTable` is the one typed reader; it detects the **ALPS method** (`ALPS-LAB`,
  `ALPS-PAS`, or `Both`) from the present columns.
- **Writer twin** — `write_alps_csv(path, table)` is the inverse of
  `read_alps_csv` over the same `AlpsTable` value, so `read(write(table))`
  round-trips. It emits only the suffixed columns (never the legacy ones) with
  `.6f` cells. The `batch` and `reanalysis` writers convert their result objects
  into an `AlpsTable` at the call site and write through it; the writer is the
  intended sink for any future CSV writer too. A pure file-I/O leaf — no
  directory creation, logging, or error-swallowing (those stay with the caller).
- **ROI-mask identity** — the four canonical mask names live in one place as
  `ROI_NAMES`, and the on-disk filename `{subject}_{roi_name}.nii.gz` is a
  producer/consumer pair over one template: `roi_mask_name` (writers) and
  `roi_mask_glob` (viewer), so the written name and the glob that finds it
  cannot drift.
- **Brain mask** — the `dwi2mask` native-space mask the registration backend
  writes to `REGISTRATION_DIR` (`registration/`) as `{subject}_brain_mask.nii.gz`.
  A producer/consumer pair over one template — `brain_mask_name` (the `fsl.py`
  and `state.py` writers) and `brain_mask_glob` (the viewer) — so the three former
  copies of the literal collapse to one home. Kept on disk unconditionally (no
  cleanup clause), so the viewer can rely on it for the focused-view toggle.

## The worker message stream

The typed protocol a background worker sends to the GUI. Produced in `processing/`
(so it stays Qt-free) and consumed by [[ResultModel]]; the two share one import.
Owned by `processing/messages.py`.

- **Worker message** — one progress event on the worker→GUI queue, a frozen
  dataclass. The closed set is the **WorkerMessage** union; the live members are the
  batch lifecycle (`BatchStart`, `SubjectStart`, `SubjectComplete`, `BatchComplete`,
  `BatchSuccess`, `BatchPartial`, `BatchCancelled`) plus the shared `Log`, `Stage`,
  and `Error`. Replaces the former stringly-typed `(msg_type, data)` tuples.
- **Closed union** — `ResultModel.handle` dispatches over the whole union and
  **raises** on an unmembered message (no silent drop). A new member is a compile-time-
  visible, test-caught gap, not a log line that vanishes at runtime.
- **Single producer path** — only the batch route is live: `BatchWorker` runs a
  `BatchRunner` whose inner `PipelineRunner` emits `Log`/`Stage`, and the worker frames
  the batch-level messages. (The single-subject `PipelineWorker` and its
  `complete`/`cancelled`/`failed` messages were cut — the GUI runs every job as a batch.)

## Presentation models (Qt-free, GUI-side)

- **ResultModel** (`gui/result_model.py`) — translates a worker-queue message into an
  ordered list of **view-intents** (frozen dataclasses the adapter applies). Drives
  the live pipeline run. A *translator*: `handle(msg) -> list[Intent]`. Owns **all** log
  phrasing, including the stage-id → display-name map and the `Running:`/`Completed:`/
  `Failed:` stage lines — a `Stage` message becomes a fully-phrased `AppendLog` here, not
  a raw pass-through the adapter re-phrases (PRD 0017). The intent union is only what the
  adapter renders: `AppendLog`, `SetRowStatus`, `ShowBatchResults`.
  - **BatchResultsView** — the finished batch results screen as plain data, carried by
    the `ShowBatchResults` intent: `title`, `summary`, `output_dir`, `csv_count` (how many
    CSVs the run wrote, so the footer says "Results saved to: {dir} ({n} CSV files)" —
    the token/filename *decision* lives in the model, only the label chrome stays in the
    adapter), an ordered tuple of **ResultColumn**(`key`, `label`), and `rows` (a tuple of
    dicts keyed by column key, cells already formatted — the `.4f` precision and
    `None → ""` rule are baked in). Built by the pure
    `build_batch_results_table(batch_state) -> BatchResultsView`; the adapter renders it
    with a generic `for col in columns` loop and an adapter-side key→(width, anchor) map.
    The live-panel twin of [[render_dec_slice]]. (There is no single-subject results view —
    the GUI runs every job, even one subject, as a batch.)
- **ViewerModel** (`gui/viewer_model.py`) — the Results Viewer's stateful **session
  model**. Owns the loaded session and recomputes a rendered slice on demand. Not a
  translator (there is no message stream); a session object with command/query
  methods returning plain data.
  - **SessionView** — the plain-data result of `load_session(folder)`: the ordered
    `(token, label)` ROI options, the detected ALPS method, and the ordered
    `SubjectRecord`s. Its sibling is **LoadError(kind, payload)** for the
    folder-missing / no-results / csv-missing cases (errors-as-data; the adapter owns
    the messagebox phrasing).
  - **SubjectRecord** — a frozen value record: subject id, folder, FA/V1 paths,
    `all_roi_paths` keyed by token, and an optional `brain_mask_path` (the
    `registration/` mask, or `None` when the subject has none). Holds no decoded
    arrays and no metrics (metrics vary by ROI type and are looked up from the
    model's CSV cache).
  - **MetricsView** — the ALPS numbers for the current `(roi_type, subject)`, shaped
    for display.
  - **render_dec_slice(fa, v1, roi_masks, brain_mask, view, slice, show_rois,
    show_brain_mask, wl_center, wl_width)** — the pure rendering function: DEC
    (direction-encoded colour) from |V1|, **FA windowing** (below), an optional
    **brain-mask blackening**, an ROI overlay, and the per-view orientation, returning
    a finished oriented uint8 RGB picture. The brain-mask step blackens out-of-brain
    voxels on the *finished* image (never perturbing the windowed FA, so toggling
    leaves in-brain pixels identical) and runs *before* the ROI overlay, so ROI voxels
    are never hidden by the mask. `ViewerModel.render_slice` is a thin wrapper feeding
    it the current loaded arrays; zoom, pan, and toolkit conversion stay in the adapter.
  - **Window/level (FA windowing)** — FA is the *intensity* channel of the DEC image
    (hue comes from |V1|). Brightness/contrast is a **window** over FA: `render_dec_slice`
    remaps FA by `clip((FA − (center − width/2)) / width, 0, 1)` before it modulates the
    colour, replacing the old per-slice `FA / slice-max` auto-normalisation (which made
    brightness jump slice-to-slice). Hue is untouched. The **default window** is
    volume-derived — `ViewerModel.default_window() -> (center, width)` returns
    `(FA-volume-max / 2, FA-volume-max)`, so the default looks like the old brightness
    but is *stable across slices*; it is recomputed only on subject-select and never
    carried across subjects. `wl_center`/`wl_width` are adapter-owned transient view
    cursor state (the twin of zoom and slice), driven by **left-drag**; the adapter
    clamps `wl_width` to a small positive minimum so a zero-width window never divides
    (PRD 0021).
  - **ResultsViewerPanel** (`gui/viewer.py`) — the reusable Qt widget that is
    [[ViewerModel]]'s adapter: the whole viewer surface (subject list, DEC image
    pane, navigation/zoom controls, metrics) as one host-agnostic `QWidget`. Both
    hosts embed the **same** panel class — the standalone `dti-alps --viewer`
    window wraps one as its central widget, and the main app docks another as its
    **"Results Viewing"** page under Output Settings. Self-sufficient: every
    control (Load folder, view, slice, zoom, show-ROIs, brain-mask) is a panel
    widget, so it carries no menu bar. The **brain-mask** checkbox (default on)
    blackens out-of-brain voxels for a focused view; it is disabled for a subject
    with no mask on disk (driven by `ViewerModel.has_brain_mask`). Loading is
    on-demand only (a host calls `load_folder`); a finished batch run does not
    auto-populate it.
  - **Radiological image interaction** — the image pane follows the PACS mouse
    convention (PRD 0021, superseding PRD 0010's zoom buttons): **left-drag** =
    [[window/level|Window/level (FA windowing)]] (vertical = level, horizontal = width),
    **right-drag** = zoom (up = in, centre-anchored, geometric), **middle-drag** = pan,
    **wheel** = change slice. Zoom is a single centre-anchored scalar shared by the
    right-drag and a **geometric 10 %–800 % zoom slider** (the visible affordance that
    replaced the `-`/`+`/`Fit` buttons), with a zoom-% label. **Best-effort fit** runs
    on subject-load and on view-switch (not on resize or ROI-switch); a single **"Reset
    view"** button re-fits the zoom *and* restores the default window. The
    `QGraphicsView` scrollbars (PRD 0010) are kept `AsNeeded` as a pan fallback for
    users without a middle button. All of zoom/pan/window-level is adapter-owned
    transient cursor state; the pixel math stays in [[render_dec_slice]].
- **QualityReportModel** (`gui/report_model.py`) — the **Quality Report** page's
  Qt-free presentation model, the read-side sibling of [[ViewerModel]] over the same
  **results-on-disk contract**. It is the GUI companion to the `--report` CLI
  (`processing/report.py`). `load_folder(folder)` scans an output dir and returns the
  discovered **shape tokens** (labelled by the same `roi_display_name` the viewer uses)
  with **errors-as-data** (`LoadError`-style, as [[ViewerModel]] does, never a widget or
  messagebox); `subjects_for_shape(token)` lists the subjects that have that
  shape (the shape drives the checkbox list). It reuses the Qt-free compute leaf
  `processing/report.py` (`calculate_subject_metrics`, `write_report_csv`) **unchanged** —
  the model composes those over a chosen **subject subset** without writing to disk, and
  shapes the numbers into a **QualityReportView**. This subsetting is a capability the
  whole-folder CLI does not have (PRD 0022).
  - **QualityReportView** — the plain-data grouped table for one *(shape × subject
    subset)*: the ordered **metric groups** (`Directional Alignment (V1)`,
    `Angular Dispersion (V1)`, `Fractional Anisotropy`, `Radial Asymmetry (λ2/λ3)`), the
    four ROI sub-columns (`left_proj`/`left_assoc`/`right_proj`/`right_assoc`), and one
    row per subject with cells already formatted. The adapter renders it as a **two-tier
    grouped header** (a band per metric group spanning its four ROI columns) — the
    on-disk twin of the CLI CSV's two header rows. The Radial-Asymmetry group is empty
    for a LAB-only run (no L2/L3), exactly as the CLI CSV leaves it.
    - **Quality warnings** — each row also carries a parallel `warnings` tuple: a cell
      is flagged when its metric lands outside a **quality threshold**, so ROIs needing
      manual inspection stand out. The direction differs per metric — **directional
      alignment** (< 0.80) and **FA** (< 0.25) warn when *low*; **angular dispersion**
      (> 10°) and **radial asymmetry** (> 2.0, PAS/Both only, `None` never warns) warn
      when *high*. The thresholds are engine constants (`processing/constants.py`,
      `QUALITY_WARN_*`), so the app and any future CLI flag the same cells; the adapter
      owns only the highlight colour (offending cell soft-red, a flagged subject amber via
      `row.has_warning`). Computed from the raw metric floats in `build_quality_report_view`,
      never re-parsed from the formatted strings.
  - **Report worker** (`processing/report_worker.py`) — a `threading.Thread` in the
    engine (Qt-free) that runs the subset compute and pushes its **own** small typed
    message set (report *progress* / *complete* / *error* / *cancelled*) onto a
    `queue.Queue`, drained by the adapter's `QTimer` — the same threading discipline as
    the pipeline's **worker message stream** but a **separate channel**: these messages
    are deliberately **not** members of the closed
    **WorkerMessage** union, so a report event can never leak into pipeline dispatch and
    the union stays batch-lifecycle-only. Cancellable between subjects via a cancel event.
- **ROI shape catalog** (`gui/config.py`, `ROI_SHAPES`) — the single ordered table of
  the *selectable* ROI shapes: one frozen **RoiShape**(`token`, `label`, `geometry`,
  `default`) row per shape. It owns the **closed** input-selection vocabulary
  (`sphere2`, `sphere2p5`, `sphere3`, `squarev4`, `squarev9`) — distinct from the
  **open** on-disk token vocabulary (any reanalysis radius, `_refined`, the `rois`
  default alias) that [[ViewerModel]]'s `roi_display_name` parses. The checkbox
  adapter reads `token`/`label`/`default`/order; the form builder reads `token`/
  `geometry`. Exactly one row is `default=True` — it both pre-checks the box and is
  the form model's "nothing selected → this shape" fallback. The engine never imports
  the catalog; it sees only the `geometry` dict inside `BatchConfig` (PRD 0015).
- **Form model** (input side, `gui/form_model.py`) — the Qt-free **input model**:
  *not* a stateful `*Model` class but a module of pure builders over a **FormState**
  snapshot. The input-side mirror of [[ResultModel]]: ResultModel maps *worker message
  → view-intents*; the form model maps *form snapshot → domain objects*. The adapter
  reads its widgets into a `FormState` and calls the builders; toolkit lifecycle (a
  widget not built yet) is guarded in the adapter's snapshot step, never in the model.
  - **FormState** — a single flat frozen dataclass: the *raw* widget values at one
    instant (booleans, strings such as `readout_raw`, the `fa_threshold` float, the
    `refine_roi` string passed through verbatim), plus three keyed collections —
    `roi_shape_flags`/`output_flags` (`dict[str, bool]`) and `cli_options`
    (`dict[str, dict[str, OptionState]]`). Holds raw values, never resolved config;
    all interpretation lives in the builders.
  - **OptionState** — a frozen `(enabled: bool, value: str, type: str)` for one CLI
    option. `build_batch_state` reproduces the collection rules: skip when disabled;
    `flag` → `True`; `int` coerced (empty or unparseable skipped); every other `type`
    passed through as its string.
  - **build_batch_state(form_state, subjects) -> BatchState** — the pure builder that
    replaces the `_collect_*` methods. Applies the ROI "default to sphere 3 mm when
    nothing selected" fallback, the `OutputConfig` per-key default-true, and the
    empty-string → `None` rule for the synB0 and staging dirs. Reuses
    `resolve_readout_time`. The `OutputConfig` assembly is also exposed as the public
    `collect_output_config(output_flags)`, since the adapter needs it on its own when
    deciding whether to delete the log file, not only inside a full batch build.
  - **compute_readiness(form_state, subjects, missing_tools) -> Readiness** — the pure
    Run-button decision. `Readiness` carries `can_run` plus the per-condition flags
    (`has_subjects`, `all_subjects_valid`, `has_output_dir`, `readout_valid`,
    `synb0_dir_valid`, `tools_available`). It computes each condition independently (so a future adapter
    can say *why* a run is blocked); it agrees with the first-failure-wins pre-flight
    [[validate_runnable]] by construction, not by calling it. Readout validity comes
    from its own `is_readout_valid(auto, raw)` predicate — deliberately **not**
    `resolve_readout_time` (which coerces bad manual input to a default and so would
    mis-report validity for both the auto and the unparseable-manual cases).
  - **compute_blockers(form_state, subjects) -> list[Blocker]** — the wording side of
    the same decision: one **Blocker** (display `text` + a semantic nav `target`) per
    *outstanding* requirement, in nav-flow order, empty exactly when
    `compute_readiness(...).can_run`. This is the readiness strip's **only** phrasing
    home — the adapter renders and routes but supplies no text. The two subject
    conditions collapse to one adaptive row (no subjects → "No subjects added"; some
    invalid → "N subject(s) … invalid"); the synB0 row appears only in synB0 mode.
    - **Blocker target** — a semantic page id (`NAV_DATA_INPUT="data"`,
      `NAV_SYNB0="synb0"`, `NAV_OUTPUT_SETUP="output_setup"`), the on-disk page ids the
      adapter already registers, so the model names *where to send the user* without
      importing a widget or a nav method; the adapter maps each to its `_show_*` call.
      `NAV_NONE=""` is the exception: the row is rendered as plain text, not a link,
      because no page fixes it.

## Preflight

- **Preflight** — the pre-run answer to "will this die on a missing tool?":
  `processing/commands.preflight(use_synb0) -> list[str]`, the external commands the
  chosen route invokes but cannot find on PATH. It lives in the **engine** so both
  front ends ask the identical question and cannot disagree about what counts as
  required; only the reaction differs — `dti-alps run` prints a report and exits 3,
  the GUI raises a [[compute_blockers|Blocker]] row (`NAV_NONE`) that disables Run.
  The route matters: the synB0 path drops `dwifslpreproc` and demands `eddy`.
  The PATH probe is *not* done inside the form model, which stays pure — the adapter
  probes (caching per route, since PATH is fixed for the process's life) and passes
  `missing_tools` in. Without it a missing `fnirt` first surfaces as a stage-7
  failure, hours into a batch.

## The readiness strip (GUI adapter)

- **Readiness strip** — the always-visible band under the Run/Cancel toolbar (outside
  the content stack, so it survives navigation) that replaces the silently-disabled
  Run button with a guided setup checklist. Three states: **blocked** — the
  outstanding [[compute_blockers|Blocker]] rows as neutral amber `○` to-do items,
  each a link that jumps to the page that fixes it; **ready** — a green
  "✓ Ready to run N subjects" line (the Run button is now enabled); **running** — a
  muted "▶ Running — see Console" line. Rebuilt each `_update_run_button_state` from
  the Qt-free model; the adapter owns only colour, marker, and link markup. Tone is
  deliberately never red — a blank first launch is a checklist, not an error list.

## Science terms (brief)

- **DTI-ALPS index** — Taoka's diffusivity-along-perivascular-space measure:
  `mean(Dx_proj, Dx_assoc) / mean(Dperp_proj, Dperp_assoc)`. See
  `processing/alps_calculation.py` for the authoritative formula and the
  ALPS-LAB / ALPS-PAS distinction.
- **Projection / association ROI** — the Superior-Inferior and Anterior-Posterior
  fibre regions sampled left and right; the four canonical masks are
  `left_proj`, `right_proj`, `left_assoc`, `right_assoc`.

## ROI placement (pure science)

The geometry / quality / search cluster that decides *which voxels* the ALPS
formula reads. A dependency-free, numpy-only leaf, `processing/roi_placement.py`
(lifted out of the registration backend; the sibling of the pure
ALPS module and `constants.py`); the science is pure (arrays in → masks/tuples out).
The IO shell that loads FA/V1/L2/L3, transforms the templates, and saves the masks
is its own module, **native placement** (below) — no longer copied into each caller.

- **ROI mask creators** — `create_sphere_mask` (mm-distance, **inclusive**
  boundary `dist² ≤ r²`, so anisotropic voxels are honoured), `create_square_v9_mask`
  (a 3×3 in-plane block at one Z slice), and `create_square_v4_mask` (a 2×2 block).
- **V1-optimized corner selection** — squarev4 puts the centroid at one corner of
  the 2×2 and picks the best of four configurations by maximizing mean `|V1_z|`
  (projection) or `|V1_y|` (association). **Tie-break:** strict `>` in list order,
  so the **lower-index** configuration wins; falls back to configuration 0 when V1
  is absent or every configuration is partly out of bounds.
- **ROI quality score** — `calculate_roi_quality` returns
  `(purity, direction_strength, mean_fa, combined)` with `combined = purity ·
  direction · FA`, then a **crossing-fiber penalty** `sqrt(1.8/ratio)` applied
  *only* when mean λ2/λ3 (over λ3>0 voxels) exceeds **1.8** (Georgiopoulos et al.
  2024); no L2/L3, or no above-threshold ratio → no penalty.
- **Joint pair-refinement** — `adaptive_roi_pair_placement` searches a window
  around both template centroids and returns the (proj, assoc) pair maximizing the
  **geometric mean** of their scores, subject to the **Y/Z-drift pairing
  constraint** that keeps both ROIs on the same X-direction pathway. A degenerate
  neighbourhood (all out of bounds, or every candidate scoring ≤ 0) keeps the
  original centroids and returns score `−1`.
- **ROI placement method** — the closed tri-state vocabulary `adaptive_roi_placement`
  is drawn from (`ROI_METHOD_OPTIONS` in `constants.py`): **Standard**, **Adaptive**,
  or **Both**. Spelled identically wherever it is set — `run --roi-method`,
  `reanalyze --roi-method`, the GUI combo — because a second spelling is how the two
  drift apart.
  - **Placement mode** — one *pass*: the `adaptive` boolean the placement leaf takes.
    `placement_modes(method)` is the single expansion of the method into its passes,
    and lives beside the vocabulary it decodes so the pipeline and reanalysis cannot
    disagree about what "Both" means. Standard runs first, so a "Both" run's outputs
    land in the same order as their on-disk suffixes. An unknown value **raises** —
    falling back to Standard would silently run a different analysis than asked for.
- **Adaptive search envelope** (`AdaptiveSearchConfig`, in `constants.py`) — the
  five-integer tuning of the joint pair-refinement, all user-settable ±1–4: the
  **search window** `search_x / search_y / search_z` (how far each ROI may
  independently move from its template centroid) and the **drift constraint**
  `max_y_drift / max_z_drift` (how far the association ROI may diverge from the
  projection ROI). Default `3 / 1 / 2 / 1 / 1` (the historical hard-coded values).
  Meaningful only for the Adaptive method; the Standard method ignores it.

## Native ROI placement (the IO shell)

The single IO shell that composes the pure [[ROI placement]] kernels into masks on
disk. Owned by `processing/native_placement.py` — the *paths in → mask files out*
twin of the *arrays in → masks out* pure leaf. Both callers (`registration/fsl.py`'s
`place_rois()` and `reanalysis.py`'s `reanalyze_subject()`) call it instead of each
carrying its own copy of the loop (PRD 0014; previously duplicated).

- **place_rois_in_native** — does one shape × one refinement mode over the four ROI
  templates: cache-if-exists `applywarp` (via the injected **ToolRunner**) →
  `find_mask_centroid` → conditional V1/L2/L3 load (with the V1-missing fallbacks) →
  joint pair-refinement → mask creation → save under `roi_mask_name`. Takes resolved
  **paths** and a required `applywarp_cmd` (the caller resolves FSL's bin — the shell
  never falls back to `PATH`, so FSLDIR-only installs keep working). Returns
  `(roi_mask_paths, roi_centroids)`; raises **ROIPlacementError** on a failed
  transform or an empty centroid — each caller translates that into its own envelope
  (`ROIPlacementResult` / `ReanalysisResult`), which stay caller-side. The engine leaf
  never imports the `registration` result dataclasses (the dependency arrow points
  `registration → placement`, never back).
- **Transform cache** — the four `{prefix}_{roi}_transformed.nii.gz` in `reg_dir`
  depend only on the (immutable) inverse warp + template + FA grid, so they are reused
  across shapes/modes and across a later reanalysis of the same subject (`prefix ==
  subject_id`). Correct-by-construction, not merely an optimization.
- **Outer looping stays in the callers** — the pipeline's `shapes × refinement_modes`
  loop and `"Both"` mode live in `fsl.place_rois()`; reanalysis is one-shape-per-CLI-
  invocation. The shell is single-shape/single-mode.
