# CONTEXT — ubiquitous language for autoDTI-ALPS

The shared vocabulary for this codebase. Names here are load-bearing: use them in
code, commits, and reviews. Architecture terms (module, interface, seam,
adapter, depth, leverage, locality) come from the `/codebase-design` skill and are
not redefined here.

## The engine / GUI split

- **Engine** — the `dti_alps/processing/` package: the distributable analysis core.
  It is tk-free and does not import `dti_alps.gui`. The dependency
  arrow points one way only: **`gui → processing`, never the reverse** (pinned by a
  subprocess import-guard test).
- **GUI** — the `dti_alps/gui/` package. May hold tk-free **presentation models**
  (see *ResultModel*, *ViewerModel*) that carry GUI text and shaping but import no
  Tkinter, plus the Tkinter **adapter** layer that renders them.
- **Adapter** — the Tkinter (one day PySide6) layer. It reads widgets, calls a
  presentation model or engine function, and applies the returned plain data to
  widgets. It owns all phrasing, colour, truncation, and dialog type. The engine and
  the presentation models carry no widget code.

## The results-on-disk contract

The artifact the engine writes and the viewer/reports read. Owned by
`processing/results_layout.py` (the home for this contract; created with the
ViewerModel work).

- **ROI type / token** — the machine name of an ROI configuration as it appears on
  disk: `rois` (the default 3.0 mm sphere), `squarev9`, `squarev4`, `sphere2p5`,
  optionally suffixed `_refined`. The engine speaks **tokens**; the GUI maps a token
  to a **display name** (`"Square 3x3"`, `"Sphere 2.5mm (r)"`) for the user.
- **ROI directory** — `rois/` for the default, `rois_{token}/` otherwise
  (`{token}_refined` when refined). Built by `roi_dir_name`, parsed by
  `parse_roi_dir` — replacing the scattered `f"rois_{...}"` literals and the magic
  `name[5:]` strip.
- **ALPS results CSV** — `alps_results.csv` for the default, `alps_results_{token}.csv`
  otherwise (`alps_csv_name`).
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

## Presentation models (tk-free, GUI-side)

- **ResultModel** (`gui/result_model.py`) — translates a worker-queue message into an
  ordered list of **view-intents** (frozen dataclasses the adapter applies). Drives
  the live pipeline run. A *translator*: `handle(msg) -> list[Intent]`.
  - **BatchResultsView** — the finished batch results screen as plain data, carried by
    the `ShowBatchResults` intent: `title`, `summary`, `output_dir`, an ordered tuple of
    **ResultColumn**(`key`, `label`), and `rows` (a tuple of dicts keyed by column key,
    cells already formatted — the `.4f` precision and `None → ""` rule are baked in).
    Built by the pure `build_batch_results_table(batch_state) -> BatchResultsView`; the
    adapter renders it with a generic `for col in columns` loop and an adapter-side
    key→(width, anchor) map. The live-panel twin of [[render_dec_slice]]. (There is no
    single-subject results view — the GUI runs every job, even one subject, as a batch.)
- **ViewerModel** (`gui/viewer_model.py`) — the Results Viewer's stateful **session
  model**. Owns the loaded session and recomputes a rendered slice on demand. Not a
  translator (there is no message stream); a session object with command/query
  methods returning plain data.
  - **SessionView** — the plain-data result of `load_session(folder)`: the ordered
    `(token, label)` ROI options, the detected ALPS method, and the ordered
    `SubjectRecord`s. Its sibling is **LoadError(kind, payload)** for the
    folder-missing / no-results / csv-missing cases (errors-as-data; the adapter owns
    the messagebox phrasing).
  - **SubjectRecord** — a frozen value record: subject id, folder, FA/V1 paths, and
    `all_roi_paths` keyed by token. Holds no decoded arrays and no metrics (metrics
    vary by ROI type and are looked up from the model's CSV cache).
  - **MetricsView** — the ALPS numbers for the current `(roi_type, subject)`, shaped
    for display.
  - **render_dec_slice(fa, v1, roi_masks, view, slice, show_rois)** — the pure
    rendering function: DEC (direction-encoded colour) from |V1|, FA modulation, ROI
    overlay, and the per-view orientation, returning a finished oriented uint8 RGB
    picture. `ViewerModel.render_slice` is a thin wrapper feeding it the current
    loaded arrays; zoom and toolkit conversion stay in the adapter.
- **Form model** (input side, `gui/form_model.py`) — the tk-free **input model**:
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
  - **compute_readiness(form_state, subjects) -> Readiness** — the pure Run-button
    decision. `Readiness` carries `can_run` plus the per-condition flags
    (`has_subjects`, `all_subjects_valid`, `has_output_dir`, `readout_valid`,
    `synb0_dir_valid`). It computes each condition independently (so a future adapter
    can say *why* a run is blocked); it agrees with the first-failure-wins pre-flight
    [[validate_runnable]] by construction, not by calling it. Readout validity comes
    from its own `is_readout_valid(auto, raw)` predicate — deliberately **not**
    `resolve_readout_time` (which coerces bad manual input to a default and so would
    mis-report validity for both the auto and the unparseable-manual cases).

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
ALPS module and `constants.py`). The IO shells that load FA/V1/L2/L3 and save the
masks stay in `registration/fsl.py` and `reanalysis.py`; the science is pure
(arrays in → masks/tuples out).

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
- **Joint pair-refinement** — `refine_roi_pair_placement` searches a
  **±3 X / ±1 Y / ±2 Z** window around both template centroids and returns the
  (proj, assoc) pair maximizing the **geometric mean** of their scores, subject to
  the **Y/Z-drift pairing constraint** (`|Δy| ≤ 1`, `|Δz| ≤ 1`) that keeps both
  ROIs on the same X-direction pathway. A degenerate neighbourhood (all out of
  bounds, or every candidate scoring ≤ 0) keeps the original centroids and returns
  score `−1`.
