# CONTEXT — ubiquitous language for autoDTI-ALPS

The shared vocabulary for this codebase. Names here are load-bearing: use them in
code, commits, PRDs, and reviews. Architecture terms (module, interface, seam,
adapter, depth, leverage, locality) come from the `/codebase-design` skill and are
not redefined here.

## The engine / GUI split

- **Engine** — the `dti_alps/processing/` package: the distributable analysis core.
  It is tk-free and, since PRD 0003, does not import `dti_alps.gui`. The dependency
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
  no-suffix `…ALPS`). `read_alps_csv(path) -> AlpsTable` is the one typed reader; it
  detects the **ALPS method** (`ALPS-LAB`, `ALPS-PAS`, or `Both`) from the present
  columns. Writers (`batch`, `reanalysis`) are repointed to this schema as a
  follow-up.

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

## Science terms (brief)

- **DTI-ALPS index** — Taoka's diffusivity-along-perivascular-space measure:
  `mean(Dx_proj, Dx_assoc) / mean(Dperp_proj, Dperp_assoc)`. See
  `processing/alps_calculation.py` for the authoritative formula and the
  ALPS-LAB / ALPS-PAS distinction.
- **Projection / association ROI** — the Superior-Inferior and Anterior-Posterior
  fibre regions sampled left and right; the four canonical masks are
  `left_proj`, `right_proj`, `left_assoc`, `right_assoc`.
