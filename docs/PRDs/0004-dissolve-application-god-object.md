# PRD 0004 — Dissolve the application god-object

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 4 ("Dissolve the application god-object"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

`gui/app.py` is a 2744-line `tk.Tk` subclass (`DTIALPSApplication`, ~90 methods) that does far more than wire widgets. Several non-UI responsibilities are trapped inside it, addressable only by launching the window:

- **Subject discovery + dedup** — `_discover_and_add_folder` (966–1047). Delegates the scan to `processing/discovery.py::SubjectDiscovery`, but the subdirectory-fallback loop, the dedup-by-`dwi_path` against the session list, and the synB0 batch warning are tangled with `messagebox`, treeview inserts, and `_log`.
- **synB0 output validation** — `_validate_synb0_output_dir` (1277–1304). Nearly pure filesystem checks (required topup files; `acqparams.txt` in `OUTPUTS` or `../INPUTS`) whose only UI act is writing a red/green label.
- **Readout-time resolution** — inside `_collect_batch_state` (2065–2071). auto → `None`; else parse the string, falling back to `DEFAULT_READOUT_TIME` on `ValueError`.
- **Pre-flight run validation** — inside `_run_pipeline` (2136–2158). no-subjects → invalid-subjects (with name truncation) → no-output-dir, first-failure-wins, each raising a `messagebox`.
- **Result dispatch** — `_handle_result` (2215–2284). A thirteen-branch `if/elif` (the review's "15-way") that interprets worker messages *and* mutates the treeview and log widget in the same breath.

Consequence: the logic that decides *what should happen* — which folders are new, whether a batch can run, what a `subject_complete` message means for row 3 — cannot be exercised, or regression-tested, without a display. None of it has a unit test today.

(Two responsibilities the review grouped under this candidate are deliberately **not** problems this PRD solves — see Decisions 5 and 7. The thread+queue machinery already lives in `processing/workers.py`; the log-file lifecycle is left in tk.)

## Solution

Extract each trapped *decision* into a tk-free unit the window calls, leaving `app.py` as the adapter that reads widgets and applies results. The invariant is **tk-free**, and placement follows the nature of the code:

- **Domain logic → `processing/`.** synB0 validation, readout resolution, and pre-flight validation join `processing/validators.py`; the discovery fallback and session-dedup join `processing/discovery.py`. The engine gains testable functions and stays GUI-agnostic.
- **Presentation logic → a new `gui/result_model.py`.** The result-dispatch model lives GUI-side (it shapes treeview/log updates) but holds no Tkinter — it returns plain-data **view-intents** the adapter applies.

All extracted functions return **structured data**, never user-facing strings; `app.py` owns every bit of phrasing, colour, truncation, and widget mutation. This keeps the engine free of GUI text and is consistent with the distributability through-line of PRDs 0002–0003.

The work lands as **five behavior-preserving commits**, smallest pure functions first so the test rhythm is established before the larger result-dispatch rewire:

1. synB0 validation → `validators.py`
2. readout resolution → `validators.py`
3. discovery + dedup → `discovery.py`
4. pre-flight validation → `validators.py`
5. result dispatch → new `gui/result_model.py`

No scientific output, no return contract, and no observable GUI behavior changes.

## User Stories

1. As a maintainer, I want the "is this folder new / is this batch runnable / what does this message mean" decisions out of the `tk.Tk` subclass, so that they can be unit-tested without a display.
2. As a developer, I want `validate_synb0_output_dir`, `resolve_readout_time`, and `validate_runnable` as pure functions in `processing/validators.py`, so that the domain rules live in the engine and a future CLI can reuse them.
3. As a developer, I want the subdir-fallback scan and the dedup-by-`dwi_path` as functions in `processing/discovery.py`, so that "discover the runs under this folder" is one tested thing.
4. As a developer, I want the thirteen-branch result dispatch reduced to a model that maps a worker message to a list of typed view-intents, so that the dispatch is a tested map instead of imperative widget pokes.
5. As a reviewer, I want each extraction to be its own behavior-preserving commit, so that the five moves can be read and reverted independently.
6. As a developer running the GUI, I want discovery, the synB0 label, readout handling, the run-button validation dialogs, and the live results treeview to behave exactly as before, so that this refactor is invisible to users.
7. As a maintainer, I want the extracted validators to return structured data and the GUI to own all phrasing/colour/truncation, so that the engine carries no GUI message text.
8. As a maintainer, I want `app.py`'s edits to be mechanical delegation only — read widgets, call the unit, apply the result — so that behavior preservation is reviewable by eye.
9. As a maintainer, I want the pieces the review bundled but we chose not to extract (a thread/queue coordinator, the log-file lifecycle, the 40-field batch-config copy) recorded as out-of-scope with reasons, so that "why didn't this get dissolved too" is answered.
10. As a future contributor, I want `gui/result_model.py` to depend on no Tkinter, so that the presentation model stays unit-testable and a future viewer can reuse it.

## Implementation Decisions

### 1. One PRD, five behavior-preserving commits; tk-free is the invariant

The candidate is dissolved as a single PRD whose commits each extract one responsibility. The rule every commit obeys: extracted code imports no Tkinter. Placement is decided per piece by nature — domain → `processing/`, presentation → `gui/result_model.py` (Decision 8).

- **Rejected — one PRD per slice:** the five extractions share one pattern and one risk profile (mechanical delegation behind a tk-free seam); five PRDs is ceremony. They are independent *commits*, not independent *designs*.
- **Rejected — a single sweeping rewrite of `app.py`:** loses the per-commit revert story and the behavior-preservation guarantee the user requires of refactors.

### 2. The seam pattern for presentation is view-intents (data out)

`ResultModel.handle(msg)` returns a `list[Intent]` of plain frozen dataclasses describing what should change (`AppendLog`, `SetRowStatus`, …). The `app.py` adapter interprets each intent into a widget call. Tests assert on the returned intent list — pure data, no mocks.

- **Rejected — controller + view protocol (callbacks):** a tk-free controller holding a `View` protocol (`append_log`, `set_row_status`, …), prod-injected with the app, test-injected with a recording fake. It is a smaller translation from today's imperative code, but it tests *interactions* (call logs) rather than *values*, and it reintroduces an injection seam where a pure function suffices. The review's own framing — "the dispatch shrinks to a map" — is literally a `msg → list[intent]` map.

### 3. Extracted validators return structured data, not baked message strings

`validate_synb0_output_dir(path) -> (ok: bool, missing: list[str])`; `validate_runnable(subjects, output_dir) -> (ok: bool, kind: str | None, payload)`. The adapter turns these into label text+colour, messagebox bodies, and the invalid-subjects name truncation.

- **Rejected — return `(ok, message)` with baked user-facing text** (matching `validators.py`'s existing convention, e.g. `validate_readout_time`): simplest call sites, and it would make the first-5 + "(and N more)" truncation tested logic. But it puts GUI phrasing inside the engine, against the distributability grain this refactor shares with PRDs 0002–0003. We accept that the truncation/format becomes adapter-side (untested) presentation, the same status as the label colour.

### 4. Commit 1 — synB0 validation → `processing/validators.py`

New `validate_synb0_output_dir(path) -> (ok, missing)` reproduces today's checks exactly: required `topup_fieldcoef.nii.gz` and `topup_movpar.txt` in `path`; `acqparams.txt` accepted in `path` **or** `path/../INPUTS`. `_validate_synb0_output_dir` becomes: call it, then set the label to `"Missing: " + ", ".join(missing)` in red or `"All required files found"` in green.

- **Rejected — also fold the label update into the unit:** the colour and the "Missing: " phrasing are presentation (Decision 3).

### 5. Commit 2 — readout resolution scalpel; the batch-config copy stays in tk

New `resolve_readout_time(auto: bool, raw: str, default: float) -> float | None`: `auto → None`; else `float(raw)`, falling back to `default` on `ValueError`. **No range validation** — today's code does none here, and adding it would change behavior. `_collect_batch_state` calls it; the remaining ~40 `tk.Var.get()` → `BatchConfig` field copies stay inline.

- **Rejected — a `FormSnapshot` dataclass + `build_batch_state(snapshot) -> BatchState`:** wrapping forty 1:1 field copies to unit-test two branches is ceremony far exceeding the nugget. The only real logic in `_collect_batch_state` is the readout resolution and a one-line `synb0_output_dir` guard; the former is worth a function, the latter is not.
- **Note on naming:** `resolve_readout_time` sits beside the existing `validate_readout_time` (which range-checks a *string* for the GUI). Different concern, same module; the names disambiguate.

### 6. Commit 3 — discovery fallback + session dedup → `processing/discovery.py`

Two functions: `discover_with_subdir_fallback(folder) -> list[SubjectFiles]` (run `SubjectDiscovery(folder).discover_files()`; if empty, scan each immediate subdirectory and concatenate — preserving order) and `new_unique_runs(existing, discovered) -> list[SubjectFiles]` (drop any whose `dwi_path` matches an entry already in `existing`). `_discover_and_add_folder` calls both, then does treeview inserts, the `_log`, and the warnings.

- **Rejected — also move the synB0 batch warning and the "No Data Found" messagebox:** these are UI dialogs. The warning's *trigger* (`use_synb0 and count_before <= 1 and now_multiple`) stays in the adapter where `count_before`/`now_multiple` are known; it is not a discovery rule.
- **Behavior pinned:** subdir fallback fires only when the top-level scan is empty; dedup is by `dwi_path`; the returned/added counts and warning trigger are unchanged.

### 7. Commit 4 — extract `validate_runnable`, not a RunCoordinator

New `validate_runnable(subjects, output_dir) -> (ok, kind, payload)` reproduces `_run_pipeline`'s pre-flight checks in order: no subjects → `("no_subjects", None)`; any `not s.is_valid` → `("invalid_subjects", [ids])`; falsy `output_dir` → `("no_output_dir", None)`; first failure wins. The adapter maps each `kind` to its messagebox (including the first-5 + "(and N more)" truncation for invalid subjects).

- **Rejected — extract a `RunCoordinator` owning queue/cancel_event/worker-lifecycle/`drain()`:** the review named "thread + queue + polling" as trapped, but `processing/workers.py` (`BatchWorker`/`PipelineWorker`) already owns the thread and queue and is testable in principle. The only genuinely tk-bound remainder is the `self.after(100, _check_results)` polling loop — ~10 lines of irreducible glue. A coordinator would be a thin lifecycle wrapper adding a layer without removing untested logic; the real untested logic in `_run_pipeline` is the pre-flight validation, which is what we extract.
- **Rejected — leave the validation in tk too:** then the one piece of `_run_pipeline` that *is* a testable decision stays untested, for no benefit.

### 8. Commit 5 — result dispatch → `ResultModel` in new `gui/result_model.py`

`ResultModel(subject_ids: list[str])` with `handle(msg) -> list[Intent]`. `Intent` is a small set of frozen dataclasses: `AppendLog(text)`, `SetRowStatus(index, text, tag)`, `UpdateStageStatus(stage, status)`, `ResetStageButtons()`, `ShowBatchResults(batch_state)`, `ShowResults(data)`. The model knows the subject count (for "i/N" strings) and addresses rows by index; it holds no widgets. The adapter owns the **stage-name → button-index map** (including the synB0 10-stage variant) and the **index → treeview-item** map, and applies each intent. All thirteen current branches — including the legacy single-subject `complete`/`failed`/`cancelled` path and every exact log string — are reproduced.

- **Rejected — a stateless `handle_message(msg, total_subjects)`:** works (per-message state need is light today), but the caller must re-thread `total_subjects` on every poll tick and there is nowhere to grow per-run state without a signature change. A run has a lifecycle; the model is its object.
- **Rejected — tuple / NamedTuple intents:** frozen dataclasses give self-documenting test assertions (`SetRowStatus(index=2, text="Completed", tag="completed")`) and match this codebase's dataclass-heavy `state.py`; positional tuples are a decoding puzzle in test failures.
- **Rejected — moving the stage→button map into the model:** that map is widget-layout knowledge (which button index a stage lights up, and the synB0 variant). It stays in the adapter so the model never reasons about buttons.

### 9. `app.py` edits are mechanical delegation; no behavior change

Every touched method follows the same shape: read the relevant `tk.Var`s/widgets, call the extracted unit, apply the structured result (format text, set colours, insert rows, raise dialogs). No control flow, default, dialog text, or output changes. The observable GUI is identical.

## Testing Decisions

**What makes a good test here:** it asserts the *external behavior of the extracted decision* — given these inputs, this structured output / this intent list — never a widget internal. The tests name no Tkinter object and never instantiate the window.

**The seams:** the pure-function call boundaries in `validators.py`/`discovery.py`, and the `ResultModel.handle` boundary. These are value-in/value-out; they need no fakes or injection (unlike the `*_seam.py` suites, which fake an *execution* seam this change does not have).

**New test files:**
- `tests/test_app_logic.py` — unit tests for `validate_synb0_output_dir` (present/missing/`../INPUTS` fallback), `resolve_readout_time` (auto, valid string, `ValueError` fallback — asserting **no** range rejection), `discover_with_subdir_fallback` + `new_unique_runs` (top-level hit, subdir fallback, dedup by `dwi_path`), and `validate_runnable` (each `kind` and the first-failure-wins ordering).
- `tests/test_result_model.py` — a **golden replay**: feed a representative full worker-message sequence (`batch_start → subject_start → stage → subject_complete → … → batch_success`, plus the legacy single-subject path) and assert the entire concatenated intent stream. This is the dispatch regression net.

**No Tk instantiation.** `import dti_alps.gui.app` succeeds headless (the class body instantiates nothing), but `DTIALPSApplication()` needs a display/root, so the window itself is not driven in tests. The `app.py` adapters are verified by manual GUI smoke, noted here as the accepted coverage boundary.

**Prior art:** these follow the plain pure-unit suites (`tests/test_discovery.py`, `tests/test_alps_calculation.py`) — focused modules, no external tools — not `tests/fakes.py` / the `*_seam.py` injection suites.

## Out of Scope

- **The log-file lifecycle** (`_init_log_file` / `_close_log_file`): the testable nugget is a timestamp filename plus a one-line keep/discard boolean; not worth a commit. Stays in tk. (Considered and dropped during grilling.)
- **A `RunCoordinator` / any thread+queue wrapper:** `processing/workers.py` already owns the worker and queue; the `self.after` polling loop stays in `app.py` as irreducible Tkinter glue (Decision 7).
- **The full `FormSnapshot → BatchState` builder:** the ~40-field `tk.Var → BatchConfig` copy stays inline; only `resolve_readout_time` is extracted (Decision 5).
- **`viewer.py` (1237 lines) and its future reuse of `ResultModel`:** the review flags it as a downstream beneficiary; not touched here.
- **Any GUI behavior, dialog text, default, or scientific-output change.** This refactor is invisible to users and to prior results.
- **Headless-Tk / xvfb integration tests** driving `DTIALPSApplication` end-to-end.

## Further Notes

- **Sequencing:** commits 1 → 5 as listed (synB0 validation, readout resolution, discovery+dedup, pre-flight validation, result dispatch). Each leaves the suite green; the four pure-function extractions establish the extract→test→delegate rhythm before the larger result-dispatch rewire.
- **Relationship to the review:** Candidate 4 was explicitly sequenced *after* the ToolRunner seam (Candidate 1, implemented) so the extracted modules sit on a testable base. Candidates 1–3 are done; this is the largest-lift candidate, scoped down to its honest testable nuggets.
- **Counts:** the review cited "2744 lines / 15-way dispatch"; the current `_handle_result` has thirteen `msg_type` branches. The PRD reflects the verified shape.
- The grilling-session decisions behind this PRD are recorded in agent memory (`candidate4-dissolve-god-object-design`).
