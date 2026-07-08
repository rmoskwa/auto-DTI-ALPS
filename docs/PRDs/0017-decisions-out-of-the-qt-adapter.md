# PRD 0017 — Move two presentation decisions out of the Qt adapter

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges the architecture review's Candidate 4 ("Two decisions that slipped back into the Qt adapter") and the follow-up PRD 0016 left Out-of-Scope (the batch-results footer for non-default / multi-shape runs). The Tkinter→PySide6 port (PRD 0013) is otherwise a thin adapter, but two domain-presentation decisions — the results-CSV filename and the stage-transition log phrasing — stayed in `app.py`. One is a live correctness bug.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why.

> **Process note.** Taken through a grilling session (2026-07-08) using the domain-modeling skill. The session revised the review's framing in one material way: the review's fix — "let `build_batch_results_table` carry the *correct CSV name*" — assumes a single name, but a batch legitimately writes **one CSV per shape token × refinement mode** (`batch.py::_write_csv_results`). So the footer cannot name "the" CSV; it names the output directory and a **count** instead. The session also confirmed the stage-phrasing move collapses two now-jobless view-intents (`UpdateStageStatus`, `ResetStageButtons`), since PRD 0013's Decision 6 already dropped sidebar recoloring.

---

## Problem Statement

The ported `app.py` is a thin adapter: it reads widgets, calls a presentation model or the engine, and applies plain data back to widgets, owning only phrasing/colour/width. Two spots break that rule, and both were verified against the live code.

**1. The results-CSV filename — a live correctness bug.** `app.py::_show_batch_results` builds its footer label as:

```python
csv_path = Path(view.output_dir) / results_layout.alps_csv_name(results_layout.DEFAULT_ROI_TOKEN)
```

so it always says `alps_results.csv`. But `batch.py::_write_csv_results` writes **one CSV per shape** (`alps_csv_name(shape_token)` for each token in the run) and `alps_results.csv` is produced *only* in the default-sphere + standard-refinement case. For a `squarev9`, `sphere2p5`, or **any** refined run, the footer points at a file **that was never written**. PRD 0016 fixed this for the default shape only and named the general case as this PRD's work. This is also the sole reason `app.py` imports `results_layout` at all — a domain contract leaking into the widget layer.

**2. The stage-transition phrasing.** `app.py::_update_stage_status` holds the stage-id → display-name map (`"denoise" → "Denoising"`, …) and the `"Running: …"` / `"Completed: …"` wording. `ResultModel` merely passes `Stage(stage, status)` through as a raw `UpdateStageStatus` intent the adapter re-phrases — the one message type whose log wording is *not* already baked by the model. Worse, the adapter branches only on `"running"` and `"complete"`, but the engine emits `"failed"` for **every** stage on error (`pipeline.py`, ~30 sites). So a failed `denoise`/`degibbs`/`preproc`/`dti` stage — which log no other detail line — is **silent** in the console today.

## Solution

Move both decisions into the tested, tk-free presentation models, and let the adapter keep only chrome.

- **The CSV set gets one home.** Add `results_layout.alps_csv_names(tokens) -> list[str]` — the single enumerator of "which CSVs a run produces" (empty tokens → the single default name). `batch.py::_write_csv_results` loops it to write; `build_batch_results_table` counts it. The count on screen cannot drift from the files on disk because both come from the same function — the same pairing spirit as `shape_token`/`roi_dir_name`.
- **`BatchResultsView` carries `csv_count: int`.** The domain fact (how many CSVs the run wrote) crosses into the model; the adapter composes the label chrome `f"Results saved to: {output_dir}  ({csv_count} CSV files)"`. The token/filename *decision* leaves `app.py`; the `results_layout` import leaves with it.
- **`ResultModel` bakes the stage phrasing.** The `Stage` case emits a fully-phrased `AppendLog` — `Running:` / `Completed:` / `Failed:` against a stage-name map that now lives in `result_model`. The `"failed"` status stops vanishing. `UpdateStageStatus` and `ResetStageButtons` — jobless once phrasing moves and colour is already gone (PRD 0013 Decision 6) — are deleted, shrinking the intent union to `AppendLog | SetRowStatus | ShowBatchResults`.

The work lands **PRD-first, then the two single-concern code commits**:

1. `docs: PRD 0017 + CONTEXT.md` — this document and the `alps_csv_names` / `csv_count` / stage-phrasing glossary notes.
2. `fix: name the real CSV set in the batch-results footer` — **commit A**, the correctness fix: `alps_csv_names` enumerator + `batch.py` repointed onto it + `csv_count` in `BatchResultsView`/`build_batch_results_table` + the footer chrome + drop `app.py`'s `results_layout` import.
3. `refactor: move stage-transition phrasing into ResultModel` — **commit B**, the tidy: the stage-name map + `Running/Completed/Failed` wording into `result_model`, delete `UpdateStageStatus`/`ResetStageButtons` and their adapter handlers, update the exhaustiveness test.

No ADR beyond this PRD: both moves *finish* the established engine/GUI-split and results-on-disk contracts rather than introducing a new, hard-to-reverse shape.

## User Stories

1. As a user reading the batch-results screen after a `squarev9` or refined run, I want the "Results saved to:" line to point at something that exists, so I am not sent to a missing `alps_results.csv`.
2. As a user running several ROI shapes at once, I want the footer to tell me how many result files were written and where, rather than naming one arbitrary file, so the screen is honest about a multi-CSV run.
3. As a maintainer, I want "which CSVs a run writes" computed in exactly one place (`alps_csv_names`), so the footer count and the batch writer cannot disagree.
4. As a user whose run fails at an early stage, I want a `Failed: {stage}` line in the console, so a denoise/preproc failure is not silent.
5. As a maintainer, I want every log line's phrasing in the tested `ResultModel`, so the adapter holds no stage-name map and the intent union carries only what actually renders.
6. As a developer, I want `app.py` to stop importing `results_layout`, so the on-disk contract no longer reaches into the widget layer.

## Implementation Decisions

### 1. The footer names the output directory and a CSV count, not a single filename

`_show_batch_results` shows `Results saved to: {output_dir}  ({n} CSV files)`.

- **Grill resolved — a batch is inherently multi-CSV, so no single filename is correct.** `_write_csv_results` writes one CSV per shape token, and refine=`"Both"` doubles it; naming any one file is either wrong (a shape that wasn't run) or a half-truth (hides the rest). The directory + count never points at a missing file and is honest for one shape or many.
- **Rejected — name the first shape's CSV** (`alps_csv_name(first_shape_token)`, matching the on-screen table, which already shows only the first shape's numbers): coherent with the table but still hides the other CSVs, and for a multi-shape run silently privileges one file the user didn't single out.
- **Rejected — list every CSV name:** fully accurate but the footer grows unbounded with shape count, and the table still shows only one shape — a label/table mismatch either way.

### 2. `alps_csv_names` is the single home for the written-CSV set, in `results_layout`

`results_layout.alps_csv_names(tokens)` returns the ordered CSV filenames a run produces (empty `tokens` → `[alps_csv_name(DEFAULT_ROI_TOKEN)]`, the backward-compat single-CSV case). `batch.py::_write_csv_results` loops it instead of hand-mapping `alps_csv_name` over `sorted(all_shapes)`; `build_batch_results_table` takes `len()` of it over the same token set.

- **Grill resolved — centralize the drift-prone mapping in the contract owner.** The count the GUI shows and the files the writer lands must come from one function, or they can diverge — exactly the class of bug this candidate exists to kill. `results_layout` already owns `alps_csv_name`/`roi_dir_name`; the set enumerator belongs beside them, and it stays token-only (no `BatchState` import), keeping the leaf dependency-free.
- **Rejected — `build_batch_results_table` recomputes the set inline:** re-derives the `all_shapes` union + empty-fallback rule the writer owns — the same duplication the candidate is consolidating.
- **Rejected — `batch.py` records the actually-written paths onto `BatchState`; the view reads `len()`:** reflects real writes even on a partial `OSError` abort, but adds mutable post-run state and couples the view to a field only the writer populates. The predicted set matches the written set in the happy path, and per-CSV write failures already surface as their own log lines; the extra coupling isn't worth the edge case.

### 3. `BatchResultsView` carries `csv_count: int`; the adapter keeps the label chrome

The view gains `csv_count`; the adapter composes `f"Results saved to: {view.output_dir}  ({view.csv_count} CSV files)"`.

- **Grill resolved — move the decision, not the chrome.** The leak is the *token/filename decision*, not the label wording; `BatchResultsView`'s own docstring already assigns the "Results saved to:" label to the adapter and passes `output_dir` as data. Carrying the count (a domain fact) and leaving the phrasing in the adapter fixes the leak without over-moving UI chrome into the model.
- **Rejected — bake a fully-phrased `saved_to: str` into the view:** puts all phrasing in the model (like the pre-phrased `summary`), but overrides the established split for this label and pulls widget-facing chrome into the presentation model for no correctness gain.

### 4. The `Stage` case emits fully-phrased `AppendLog`; `UpdateStageStatus` and `ResetStageButtons` are deleted

`ResultModel.handle`'s `Stage` case builds the log line itself — `Running:`/`Completed:`/`Failed: {display_name}` — from a stage-id → display-name map that lives in `result_model`. The now-jobless `UpdateStageStatus` and the already-no-op `ResetStageButtons` intents are removed, along with `_update_stage_status`, the two `_apply_intent` branches, and `ResetStageButtons` in the `SubjectStart` emission; the exhaustiveness test is updated.

- **Grill resolved — finish the adapter pattern; delete what renders nothing.** Every other message's wording is already baked by the model; `Stage` was the exception. Once it emits `AppendLog`, `UpdateStageStatus` has no job, and `ResetStageButtons` has been a no-op since PRD 0013 Decision 6 dropped sidebar recoloring. The intent union should carry only what the adapter renders.
- **Rejected — keep both intents and emit `AppendLog` alongside `UpdateStageStatus`:** smallest diff, but leaves two dead intents and a half-used adapter method.
- **Rejected — delete `UpdateStageStatus` but keep `ResetStageButtons` as a reserved no-op seam:** speculative seam for a "highlight current stage" feature that Decision 6 explicitly ruled out; a dead intent held for a hypothetical.
- **The stage-name map is GUI-side, not engine-side.** The engine speaks stage *ids* (`Stage(stage, status)`); the display names (`"Denoising"`, `"ROI Placement"`) are presentation text, so they live in `result_model`, never in `processing/`.

### 5. A failed stage logs `Failed: {stage_name}` for every stage

The `Stage` case emits `Failed: {display_name}` whenever `status == "failed"`, for all stages.

- **Grill resolved — one consistent stage marker.** It fills the genuinely-silent gap for `denoise`/`degibbs`/`preproc`/`dti` (which log no other failure detail), and on `registration`/`roi` it sits beside the engine's existing `ERROR: … failed` detail line — a stage marker next to a detail, not a true duplicate.
- **Rejected — emit `Failed:` only for stages that don't self-report an ERROR line:** bakes "which stages self-log" into `result_model`, a fragile coupling to engine internals.
- **Rejected — emit nothing on `failed` (today's behavior):** leaves early-stage failures invisible in the console and forfeits the candidate's "dropped failed stops vanishing" win.

## Testing Decisions

- **`tests/test_results_layout.py`** — `alps_csv_names`: empty tokens → `["alps_results.csv"]`; a single non-default token → its one suffixed name; multiple tokens → the ordered set of suffixed names; ordering is stable (`sorted`).
- **`tests/` for `result_model`** — the `Stage` case: `("denoise","running") → AppendLog("Running: Denoising")`, `("roi","complete") → AppendLog("Completed: ROI Placement")`, and the newly-covered `("denoise","failed") → AppendLog("Failed: Denoising")`; `build_batch_results_table` sets `csv_count` from the run's token set (1 for a single default shape, N for N shapes). The exhaustiveness test is updated for the shrunk `Intent` union (`UpdateStageStatus`/`ResetStageButtons` removed).
- **`batch.py`** — existing CSV tests stay green: `_write_csv_results` writes the same filenames, now sourced from `alps_csv_names`.

## Out of Scope

- **The union-of-shape-tokens gather.** Both `batch._write_csv_results` and `build_batch_results_table` collect the token set from `result.alps_results_by_shape.keys()` — a trivial one-line union that stays duplicated because `results_layout` is dependency-free and cannot import the `SubjectResult` state type. Only the drift-prone token → filename mapping is centralized (in `alps_csv_names`); the gather is not.
- **The on-screen results table showing only the first shape's numbers.** `build_batch_results_table` renders `result.alps_lab_*` (the primary/first-shape fields, `pipeline.py:563`); a multi-shape run's table still reflects one shape. Making the table shape-aware is a separate presentation question; this PRD only makes the footer honest about the *file count*.
- **The other "Also noted" leaks** from the review (the `alps_columns(method)` rule re-derived in two models, the CLI resolver name-substring closures, the teardown reading the live form) — each its own candidate.
