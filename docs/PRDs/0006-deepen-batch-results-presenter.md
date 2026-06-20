# PRD 0006 — Deepen the batch results panel behind a tk-free presenter

Status: Accepted · Date: 2026-06-20 · Source: Architecture review (pre-PySide6) Candidate 1 ("Deepen the live-results panel behind a tk-free presenter"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

PRD 0004 dissolved `gui/app.py`'s result dispatch behind a `ResultModel` that maps a worker message to view-intents, but it deliberately kept the *results screen* intents coarse: `handle` returns `ShowBatchResults(batch_state)` and `ShowResults(data)` carrying **raw** engine objects (`result_model.py:46–56, 127–159`). The adapter then makes every presentation decision. Two consequences remain, both addressable only by opening the window:

- **The batch results screen is built inside the `tk.Tk` adapter.** `gui/app.py::_show_batch_results` (2409–2587, ~179 lines) branches on `alps_method` to choose the column set ("Both" → 8 columns; `ALPS-LAB`/`ALPS-PAS` → 5), spells out every header and width, and formats every cell (`f"{x:.4f}"` with a `None → ""` rule). Which columns an ALPS method implies, how an ALPS index reads, and the per-subject row assembly are domain-presentation decisions trapped in widget code. None of it has a unit test today.
- **A dead single-subject results path is carried alongside it.** `ShowResults` is produced only by `ResultModel`'s `"complete"` branch, which is emitted only by `PipelineWorker`. The GUI **only ever** instantiates `BatchWorker` (`app.py:2170`) — a single subject runs as a one-row batch — so `ShowResults`, `_show_results` (2307–2407), `_export_csv` (2615–2642), `_view_rois` (2644), and the `complete`/`failed`/`cancelled` branches in `ResultModel` are unreachable from the running GUI. `_export_csv`/`_view_rois` are reachable only from the dead `_show_results` buttons; `self.pipeline_state` is then written at init and read only by those dead methods.

Consequence: roughly 180 lines of presentation logic — "which columns does this method imply, what does this row read as" — cannot be exercised or regression-tested without a display, and ~135 lines of dead single-subject view code sit next to it. The eventual Tkinter→PySide6 port is the downstream beneficiary: a render-only adapter ports near-mechanically, where today's method-conditional table assembly would be re-implemented by hand in Qt table code.

## Solution

Give the batch results screen the same treatment the dispatch (PRD 0004) and the viewer (PRD 0005) got: extract the trapped *decision* into a tk-free unit, leaving `app.py` as the adapter that renders returned data. Placement follows the engine/GUI split the recent PRDs enforce — this is GUI-side presentation, so it lives beside `ResultModel`:

- **A pure builder → `gui/result_model.py`.** `build_batch_results_table(batch_state) -> BatchResultsView` turns the raw batch into a finished, render-ready table; `ShowBatchResults` carries the `BatchResultsView` instead of the raw `BatchState`. The model holds no Tkinter; the table shape becomes part of the tested intent stream. This is the live-panel twin of PRD 0005's pure `render_dec_slice`.
- **Delete the dead single-subject path first.** Remove `ShowResults`, `_show_results`, `_export_csv`, `_view_rois`, the orphaned `self.pipeline_state`, and the `complete`/`failed`/`cancelled` branches from `ResultModel`, trimming the golden-replay test to batch-only. `PipelineWorker` stays — it is a public engine symbol (re-exported from `processing/__init__.py`), the library's single-subject path, independent of the GUI.

All extracted units return **structured data or finished display strings, never widgets**; `app.py` owns canvas/treeview construction, column widths/anchors, the footer buttons, and the `"Results saved to:"` chrome. This keeps the model free of Tkinter, consistent with PRDs 0004–0005.

The work lands as **two behavior-preserving commits**, dead-code removal first:

1. Delete the dead single-subject view + strip the `ResultModel` legacy trio + trim the test.
2. Add `BatchResultsView` + `build_batch_results_table`; `ShowBatchResults` carries the view; rewrite `_show_batch_results` into a render-only loop; builder test + golden-replay rewire.

This is **extract-then-port**: the panel stays on Tkinter at the end of this work. No scientific output, no CSV format, and no observable behavior changes (one cosmetic exception, Decision 7).

## User Stories

1. As a maintainer, I want the "which columns does this ALPS method imply / what does this row read as" decision out of the `tk.Tk` subclass, so that it can be unit-tested without a display.
2. As a developer, I want `build_batch_results_table` as a pure function returning a finished `BatchResultsView`, so that the table shape is verified data-in/data-out with no widgets.
3. As a maintainer, I want `ShowBatchResults` to carry the finished view rather than a raw `BatchState`, so that the table shape is visible in the tested intent stream and the adapter only renders.
4. As a future contributor, I want the batch adapter to be a generic `for col in view.columns` render loop, so that the PySide6 port re-implements no method-conditional table logic.
5. As a maintainer, I want the unreachable single-subject view (`ShowResults`, `_show_results`, `_export_csv`, `_view_rois`) and the `ResultModel` `complete`/`failed`/`cancelled` branches removed, so that the port does not carry dead code.
6. As a maintainer, I want `PipelineWorker` left intact, so that the engine's public single-subject library path is unchanged by a GUI refactor.
7. As a developer running the GUI, I want the batch results table — its columns per method, values, statuses, title, and summary — to look exactly as before (modulo the documented cosmetic width), so that this refactor is invisible to users.
8. As a reviewer, I want the dead-code removal and the presenter deepening as two behavior-preserving commits, so that they can be read and reverted independently.
9. As a maintainer, I want the results-on-disk filename (the `alps_results.csv` literal in the footer label) left in the adapter, so that Candidate 2's `results_layout` consolidation owns it and this PRD does not touch the on-disk contract.

## Implementation Decisions

### 1. Delete the single-subject results view as dead code

The GUI instantiates only `BatchWorker` (`app.py:2170`); it never imports `PipelineWorker`. So `ShowResults` (produced only by the `"complete"` branch) and its adapter methods are unreachable. Commit 1 removes `_show_results`, `_export_csv`, `_view_rois`, the `ShowResults` application branch (`app.py:2212–2213`), the `ShowResults` import, the `ShowResults` dataclass + its place in the `Intent` union, and the now write-only `self.pipeline_state` attribute.

- **Rejected — keep & deepen both views:** only worth it if a single-subject quick-run path is planned for the PySide6 GUI; none is. Deepening an unreachable view doubles the presenter's shape for no live caller.
- **Rejected — keep but don't deepen:** carries ~135 lines of dead code straight into the port, the exact thing this review exists to prevent.

### 2. Strip the whole legacy trio from `ResultModel`; keep `PipelineWorker`

Remove the `complete`, `failed`, and `cancelled` branches from `ResultModel.handle` (the dead single-subject trio — the GUI produces only the `batch_*` family). Trim `tests/test_result_model.py`'s single-subject golden case. Leave `PipelineWorker` and its `complete`/`failed`/`cancelled` message production in `processing/workers.py`.

- **Rejected — minimal (remove only `ShowResults` + the `complete` branch):** leaving the `failed`/`cancelled` `AppendLog` branches is half a job; they are GUI-unreachable for the same reason.
- **Rejected — go all the way and delete `PipelineWorker`:** that changes the engine's public API (`PipelineWorker` is re-exported from `processing/__init__.py` and `pipeline.py.__all__`) inside a GUI presentation refactor. The library's single-subject path is out of scope; severing it muddies the revert story.

### 3. The seam is a pure builder; the intent carries the finished view

`build_batch_results_table(batch_state) -> BatchResultsView` is a standalone pure function in `gui/result_model.py`; `ResultModel.handle`'s three batch branches return `ShowBatchResults(build_batch_results_table(batch_state))`. The adapter renders `intent.view`.

- **Rejected — a new module `gui/results_presenter.py`:** fragments the live-results dispatch concern across two GUI files. The builder *is* part of translating a worker message into what the adapter renders, which is exactly `result_model.py`'s job.
- **Rejected — a method on `ResultModel` with `ShowBatchResults(batch_state)` unchanged:** the shaping would then run adapter-side and stay out of the tested intent stream — the leak we are closing. A pure function is testable with a hand-built `BatchState` and matches PRD 0005's `render_dec_slice` precedent.

### 4. Cells are pre-formatted strings; the builder owns `.4f` and `None → ""`

The builder bakes each cell to its display string (`f"{x:.4f}"`, or `""` when the value is `None`). The adapter inserts strings verbatim.

- **Rejected — raw `float | None` cells, adapter formats:** keeps formatting as "pure display" but re-scatters the precision and empty-on-None rules into every adapter (tk now, Qt later) and leaves them untested. The precision a metric reads at is domain-presentation; PRD 0005's render returns finished pixels by the same logic.

### 5. Rows are dicts keyed by column key

`BatchResultsView.rows` is a `tuple[dict[str, str], ...]`; each row maps a column key to its formatted cell. The adapter projects `[row[col.key] for col in view.columns]`.

- **Rejected — positional tuples aligned to `columns`:** the universal table shape (tk Treeview / Qt model consume it directly), but test assertions become positional puzzles — exactly what PRD 0004 Decision 8 rejected for intents. The columns vary by method (8 vs 5), so a fixed `ResultRow` dataclass does not fit; dict-keyed rows are self-documenting in test failures and trivially projected by the adapter.

### 6. The view carries the table + title + summary + `output_dir`; the footer stays adapter-side

`BatchResultsView` carries: `title` (`"Batch Processing Results ({method})"`), `summary` (`"{success}/{total} succeeded, {failed} failed"`), `columns: tuple[ResultColumn, ...]` (each `ResultColumn(key, label)`), `rows: tuple[dict[str, str], ...]`, and `output_dir: str`. The two footer buttons (Open Output Folder, Open Results Viewer) and the `"Results saved to: …"` label remain adapter-side, fed `output_dir`.

- **Rejected — minimal view (columns + rows only):** re-scatters the method/count phrasing into the adapter, untested. The title/summary are phrasing of domain data — model-side per PRD 0004 (which put `AppendLog` text in the model).
- **Rejected — full screen including the footer label string:** would pull the results-on-disk filename (`alps_results.csv`) into this builder, re-baking the very literal Candidate 2 (`results_layout`) exists to consolidate. Keeping the footer label adapter-side leaves that literal where Candidate 2 will sweep it (Decision 9 / Out of Scope).

### 7. Column width/anchor live in a static adapter-side map

The adapter holds a small map keyed by stable column keys (`subject`, `status`, `lab_left`, `pas_combined`, …) → `(width, anchor)`, with a centered default; it iterates `view.columns` generically. Widget layout stays out of the tk-free view (PRD 0005 Decision 7: zoom/widths are display the adapter owns).

- **Cosmetic change, accepted:** today the `subject` column is 120 px in "Both" mode and 150 px in single-method mode. A key-based map gives it one width (120). This is a default initial width on a resizable column — no scientific or logical behavior changes.
- **Rejected — preserve exact per-method widths:** the view would carry `alps_method` (or the adapter would branch on column count) purely to vary a pixel width, re-introducing the method conditional this PRD removes from the adapter.

### 8. The builder test owns view content; the golden replay owns dispatch wiring

A new test asserts `build_batch_results_table` over `Both` / `ALPS-LAB` / `ALPS-PAS`, including the `None → ""` and `.4f` edge cases, against a hand-built `BatchState`. `tests/test_result_model.py`'s golden replay drops the legacy trio and asserts `ShowBatchResults` wraps the builder output (the dispatch-wraps-builder wiring), without re-spelling the full view literal.

- **Rejected — golden replay asserts the full finished view inline:** duplicates the `BatchResultsView` literal across two test files.
- **Rejected — builder test only:** leaves the dispatch-wraps-builder wiring untested.

### 9. Two behavior-preserving commits, dead-code removal first

(1) delete the dead single-subject view + strip the `ResultModel` trio + trim the test; (2) add `BatchResultsView`/`ResultColumn`/`build_batch_results_table`, repoint `ShowBatchResults`, rewrite `_show_batch_results` render-only, add the builder test + rewire the golden replay. Suite green at each.

- **Rejected — three commits** (split the GUI-method deletion from the `ResultModel`/test trim): more ceremony for one dead-code concern that Decision 1/2 treat as a unit.
- **Rejected — one commit:** loses the clean revert boundary between removing dead code and reshaping live code.

## Testing Decisions

**What makes a good test here:** it asserts the *external behavior of the extracted unit* — given this `BatchState`, this `BatchResultsView` — never a widget internal. Tests name no Tkinter object and never instantiate the window.

**The seams:** the pure `build_batch_results_table` call boundary and the `ResultModel.handle` boundary. Both are value-in/value-out; they need no fakes or injection.

**Test files:**
- `tests/test_result_model.py` (new test + edits) — a `build_batch_results_table` suite over `Both` / `ALPS-LAB` / `ALPS-PAS`, asserting `columns` (keys + labels), `title`, `summary`, and `rows` including a `None`-metric subject (→ `""`) and `.4f` formatting; plus the golden replay trimmed of the single-subject case and rewired to assert `ShowBatchResults(build_batch_results_table(bs))`.

**No Tk instantiation.** `_show_batch_results` is verified by manual GUI smoke, the accepted coverage boundary (as in PRDs 0004/0005).

**Prior art:** the pure-builder test follows `render_dec_slice` in `tests/test_viewer_model.py` and `tests/test_alps_calculation.py`; the golden replay follows `tests/test_result_model.py`'s existing shape.

## Out of Scope

- **The results-on-disk contract (Candidate 2 / `results_layout`).** The `alps_results.csv` literal in the footer label stays in the adapter; repointing the engine writers/readers onto `results_layout` is its own behavior-preserving PRD.
- **`PipelineWorker` and its message production** in `processing/workers.py`: a public engine path, left intact (Decision 2).
- **The `~40-field tk.Var → BatchConfig` copy** in `_collect_batch_state`: settled by PRD 0004 Decision 5 (rejected `FormSnapshot`); not reopened.
- **The Tkinter→PySide6 rewrite** of `app.py`: a far-future adapter swap once the panel is render-only; this PRD only proves the seam under Tkinter.
- **Any change to scientific output, the CSV format, dialog text, or behavior** beyond the documented cosmetic `subject`-column width (Decision 7).
- **Headless-Tk / xvfb integration tests** driving `DTIALPSApplication`.

## Further Notes

- **Sequencing:** commit 1 (delete dead view) → commit 2 (deepen presenter). Each leaves the suite green.
- **Relationship to prior PRDs:** this is the live-panel counterpart of PRD 0004 (which deepened the dispatch and kept `ShowResults`/`ShowBatchResults` coarse) and PRD 0005 (which deepened the viewer behind a pure `render_dec_slice`). It removes the last island of presentation logic in `app.py`'s pipeline window.
- **Domain model:** `CONTEXT.md` gains `BatchResultsView`, `ResultColumn`, and `build_batch_results_table` under Presentation models.
- **Counts (verified):** `_show_batch_results` is 179 lines (`app.py:2409–2587`); the dead single-subject view is ~135 lines (`_show_results` 2307–2407, `_export_csv` 2615–2642, `_view_rois` 2644). The GUI's only worker is `BatchWorker` (`app.py:2170`).
