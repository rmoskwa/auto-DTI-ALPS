# PRD 0011 — Extract a tk-free Input/Form Model from `app.py`

Status: Grilled · Date: 2026-07-06 · Grilled: 2026-07-06 · Source: Repo readiness sweep for the Tkinter→PySide6 migration. The sweep found the **output** side of `app.py` already modeled out (worker messages → view-intents via the tk-free `ResultModel`), but the **input** side — form/widget state → `BatchConfig`/`BatchState` — still reads `.get()` directly off Tk Variables. This PRD extracts the symmetric input model so the later `app.py` port stays a thin adapter on *both* sides.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** Drafted from a codebase sweep (2026-07-06) that assessed whether the repo is in an acceptable state for an "easy" PySide6 port. The sweep's verdict: the architectural boundaries are in good shape (`processing/` is Qt-free; the viewer is fully ported per PRD 0010; the output/message side of `app.py` delegates to the tk-free `ResultModel`), with one asymmetry — the input/form side of `app.py` was never given the same tk-free-model treatment. This PRD closes that asymmetry. It is a preparation leaf, deliberately landed *before* the `app.py` port rather than inside it. It has since been grilled (2026-07-06); see the *Grill outcomes* note at the end for the changes that landed against the original draft.

---

## Problem Statement

The DTI-ALPS main app (`gui/app.py`, ~2400 lines) is still Tkinter and is the next-largest slice of the PySide6 migration after the viewer (PRD 0010). The port will be a large widget-rebuild no matter what, but its cost turns sharply on how much *non-widget logic* is tangled into the widget layer.

The **output** side of that logic is already clean. Worker→GUI messages are absorbed by `ResultModel` (`gui/result_model.py`), a tk-free presentation model that returns *view-intents* (`AppendLog`, `UpdateStageStatus`, `SetRowStatus`, `ResetStageButtons`, `ShowBatchResults`); the Tk adapter only translates each intent to a widget call. When `app.py` is ported, that adapter is the only part that changes, and `tests/test_result_model.py` keeps guarding the logic across the swap.

The **input** side is not clean. Form state lives in Tk Variables (`StringVar`/`BooleanVar`/`IntVar`/`DoubleVar`) scattered across the widget tree, and the logic that turns that state into domain objects reads `.get()` off those variables directly:

- `_collect_batch_state()` assembles `BatchConfig` + `BatchState` from a dozen-plus Tk Variables.
- `_collect_output_config()` builds `OutputConfig` from a dict of `BooleanVar`s (`output_option_vars`).
- `_collect_roi_shapes()` maps a dict of checkbox `BooleanVar`s (`roi_shape_vars`) to the `roi_shapes` list, applying the "default to sphere 3 mm if nothing selected" rule.
- `_collect_cli_options(stage)` walks the fiddliest structure — `cli_option_vars[stage][option] = {enabled_var, value_var, type}` — applying flag-vs-value logic, int coercion, and skip-empty rules.
- `_update_run_button_state()` computes the live "can the pipeline run?" decision from several Tk Variables, and re-derives readout-time validity *inline* (a bare `float()` try/except) rather than through a single named predicate. (Note: this inline check is a *different policy* from the build-path `resolve_readout_time()` — the button **blocks** on unparseable input, whereas the resolver **coerces** it to a default and runs. They are not duplicates; see Decision 4.)

Some input logic *was* already extracted and is tk-free (`processing/validators.py`: `validate_runnable`, `resolve_readout_time`, `validate_synb0_output_dir`; `processing/discovery.py`: subject discovery), and `tests/test_app_logic.py` exercises those. But the *mapping* logic above — widget state → `BatchState`, and widget state → run-readiness — still only exists entangled with Tk Variables. Whoever ports `app.py` would have to rewrite that mapping against Qt widgets from scratch, untested, in the middle of the largest diff of the migration. That is exactly the risk the `ResultModel` extraction removed on the output side, still present on the input side.

## Solution

Extract a **tk-free input/form model** that is the mirror image of `ResultModel`: instead of *worker messages → view-intents*, it maps *a form-state snapshot → domain objects*.

Introduce `gui/form_model.py` (tk-free, alongside `gui/result_model.py` and `gui/viewer_model.py`) containing:

- **`FormState`** — a frozen dataclass snapshot of all widget-backed scalar form fields (booleans, strings, and the small nested value-objects for the CLI-option and ROI-shape/output-flag structures). It holds *raw widget values*, not domain config — e.g. `readout_auto: bool` + `readout_raw: str`, not a resolved float.
- **`build_batch_state(form_state, subjects) -> BatchState`** — the pure builder that today lives across `_collect_batch_state`/`_collect_output_config`/`_collect_roi_shapes`/`_collect_cli_options`, moved verbatim and re-expressed against `FormState` fields instead of `.get()` calls. It reuses `resolve_readout_time()` unchanged and produces the identical `BatchConfig`/`BatchState`.
- **`compute_readiness(form_state, subjects) -> Readiness`** — the pure "can_run" decision from `_update_run_button_state`, returning a small structured result (an overall `can_run` plus the individual condition flags). Readout validity moves out of the inline `float()` try/except into a new named predicate `is_readout_valid(auto, raw)` in `processing/validators.py` (True in auto mode; else whether `raw` parses) — **not** `resolve_readout_time()`, which encodes the opposite (coerce-to-default) policy; see Decision 4.

The Tk `app.py` becomes a thin adapter on the input side too: it reads its Tk Variables into a `FormState`, then calls `build_batch_state`/`compute_readiness`. When `app.py` is later ported, the Qt adapter reads *Qt widgets* into the **same** `FormState` and calls the **same** model — no input-mapping logic is rewritten during the port.

This is a **behavior-preserving refactor** with one deliberate, called-out cleanup: the inline `float()` readout check in `_update_run_button_state` becomes a single named predicate `is_readout_valid(auto, raw)` that `compute_readiness` calls. This preserves the Run-button behavior exactly (it does **not** reuse `resolve_readout_time()`, which would invert it — see Decision 4). `BatchConfig`, `BatchState`, and `OutputConfig` are unchanged; the pipeline runs identically.

The work lands as **two behavior-preserving commits**, each leaving the suite green and the app runnable:

1. **Add `gui/form_model.py`** — `FormState`, `build_batch_state`, `compute_readiness`, and `tests/test_form_model.py`. Wired into nothing; importable, unreferenced, green.
2. **Flip `app.py` to the model** — the `_collect_*` bodies and the readiness computation become "read Tk Variables → `FormState` → call the model → apply the result," and the now-dead inline logic is deleted. Manual smoke of the run flow.

It is independent of the viewer port (0010, done) and of the typed worker→GUI message stream, and it precedes the `app.py` port, which is out of scope here.

## User Stories

1. As a maintainer, I want the input/form logic of `app.py` extracted into a tk-free model, so that the eventual PySide6 port rewrites only a thin adapter on the input side, exactly as `ResultModel` did for the output side.
2. As a maintainer, I want the model to consume a plain `FormState` snapshot rather than reach into widgets, so that it has no toolkit dependency and can be driven identically by a Tk or a Qt adapter.
3. As a maintainer, I want `FormState` to hold raw widget values (e.g. the auto-flag and the raw string for readout time), not pre-resolved domain values, so that the adapter stays dumb and all interpretation lives in one tested place.
4. As a maintainer, I want `build_batch_state(form_state, subjects)` to produce a `BatchState` byte-for-byte identical to today's `_collect_batch_state()`, so that the extraction changes no processing behavior.
5. As a maintainer, I want the ROI-shape mapping (checkbox selections → `roi_shapes`, including the "default to sphere 3 mm when nothing is selected" rule) preserved exactly, so that a user's shape choices still drive the same ROI directories.
6. As a maintainer, I want the CLI-option collection logic (enabled-vs-disabled, flag-vs-value, int coercion, skip-empty-values) preserved exactly for every stage (`dwidenoise`, `mrdegibbs`, `dwifslpreproc`, `dwi2tensor`, `tensor2metric`, `flirt`, `fnirt`, `synb0_eddy`), so that the assembled external-tool command lines are unchanged.
7. As a maintainer, I want `OutputConfig` assembly (the output-file-retention checkboxes, including the per-key default-true fallback) preserved exactly, so that which artifacts are kept or pruned is unchanged.
8. As a maintainer, I want the synB0 fields handled identically (the output directory becoming `None` when empty, the eddy options collected only in synB0 mode), so that both preprocessing routes behave as before.
9. As a maintainer, I want `compute_readiness(form_state, subjects)` to reproduce today's Run-button enable/disable decision (subjects present, all subjects valid, output dir set, readout valid, synB0 dir set when in synB0 mode), so that the button's behavior is preserved.
10. As a maintainer, I want the readiness computation's readout check extracted into a single named predicate `is_readout_valid(auto, raw)` (in `processing/validators.py`) instead of the inline `float()` try/except, so that the "is the readout usable for the button?" rule lives in exactly one tested place — kept distinct from the build-path `resolve_readout_time()`, which encodes the opposite coerce-to-default policy.
11. As a maintainer, I want `compute_readiness` to return a structured result (overall `can_run` plus the individual condition outcomes), so that a future adapter can surface *why* the run is blocked without re-deriving the conditions, even though today's adapter only reads the overall flag.
12. As a maintainer, I want the model to reuse the already-extracted `processing/validators.py` functions rather than reimplement them (and to add the one genuinely-new input rule, `is_readout_valid`, to that same module), so that there is one home for each input decision and no fork.
13. As a maintainer, I want `subjects` passed as an argument (a list of already-tk-free `SubjectFiles`) rather than folded into `FormState`, so that the snapshot stays a pure scalar-form representation and domain objects are not duplicated into it.
14. As a maintainer, I want the new model to live in `gui/form_model.py` beside `result_model.py` and `viewer_model.py`, so that the tk-free presentation models cluster together and `processing/` stays free of presentation-shaped logic.
15. As a maintainer, I want `form_model.py` to import only `processing` domain types, `processing.validators`, and stdlib `dataclasses` — never `tkinter` or `PySide6` — so that the tk-free guardrail is enforced structurally, matching the other view-models.
16. As a maintainer, I want `tests/test_form_model.py` to build `FormState` fixtures and assert the resulting `BatchState`/`OutputConfig`/`roi_shapes`/CLI-option dicts and `Readiness`, importing no toolkit, so that the model is the regression net across the later Tk→Qt swap, exactly as `test_result_model.py` is for the output side.
17. As a maintainer, I want the extraction delivered as two small commits (add the model + tests; flip `app.py` and delete the dead inline logic), so that each step is independently runnable, reviewable, and revertible.
18. As a developer, I want the Tk app to keep collecting form values and starting the pipeline exactly as before after the flip, so that the refactor costs users nothing.
19. As a future contributor, I want new input logic added to `form_model.py` (and tested) rather than buried in widget callbacks, so that the input side never re-accumulates toolkit-coupled logic.
20. As the eventual `app.py` porter, I want to write a Qt adapter that fills the same `FormState` and calls the same builder/readiness functions, so that the port introduces no untested input-mapping code in its largest diff.

## Implementation Decisions

### 1. A stateless `FormState` snapshot + pure builder/readiness functions — not a stateful input model

The seam is a frozen `FormState` dataclass plus two pure functions: `build_batch_state(form_state, subjects) -> BatchState` and `compute_readiness(form_state, subjects) -> Readiness`. The adapter snapshots its widgets into a `FormState` at the moments it needs a decision (on "Run", and on any input change for the live button), and calls the model. The model holds no state between calls.

- **Rejected — a stateful `InputModel` with setters (mirroring how `ViewerModel` holds a session):** form values naturally live in the widgets, so a mutable model would create a second source of truth and a widget↔model sync burden on every keystroke and toggle. `ViewerModel` is legitimately stateful because it *accumulates* a session (loaded CSVs, current selection, cached renders); the form is a *snapshot at decision time*, and readiness is a pure function of that snapshot. The stateless shape is the honest fit and the smaller surface.
- **Rejected — keep the `_collect_*` methods but pass them a widget-reading callable:** that leaves the mapping logic on the `DTIALPSApplication` class, still un-portable and only testable by instantiating (or faking) the window. The point is to move the logic *off* the widget class entirely.

### 2. `FormState` holds raw widget values, not resolved domain config

`FormState` mirrors what the widgets carry: e.g. `readout_auto: bool` and `readout_raw: str` (not a resolved `readout_time: float`), the ROI-shape checkbox booleans keyed by shape token, the output-retention booleans keyed by artifact, and the CLI options as `stage -> {option -> OptionState(enabled, value, type)}`. All interpretation — resolving readout time, defaulting the ROI shapes, coercing/skipping CLI values, `None`-ing an empty synB0 dir — happens **inside** the builder, the one tested place.

- **Rejected — pre-resolve values in the adapter before building `FormState`:** that pushes interpretation back into the widget layer (and would have to be re-implemented in the Qt adapter), which is the coupling this PRD removes. The adapter's only job is a mechanical widget-value read.
- **Rejected — make `FormState` *be* the `BatchConfig`:** they are deliberately different shapes. `BatchConfig` is the resolved domain contract the pipeline consumes; `FormState` is the raw pre-resolution snapshot. Collapsing them would force resolution into the adapter (above) and lose the raw values readiness needs (e.g. the auto-flag + raw string).

### 3. `OptionState` value-object for the CLI-option structure

The CLI-option snapshot is `dict[str, dict[str, OptionState]]` — stage → option name → a small frozen `OptionState(enabled: bool, value: str, type: str)`. `build_batch_state` applies the existing rules verbatim: skip when not enabled; for a `flag` type emit `True`; otherwise emit the value, coercing `int` types via `int(...)` and silently skipping empty strings and un-parseable ints. This reproduces `_collect_cli_options` exactly for all eight stages.

- **Rejected — carry the live Tk `enabled_var`/`value_var` objects through:** that is the coupling being removed; the snapshot must be plain Python.
- **Rejected — resolve the option dict in the adapter and hand the model a finished `dict[str, Any]`:** re-implements the flag/coerce/skip rules in the widget layer (and again in Qt later). Those rules are input logic and belong in the tested builder.

### 4. `compute_readiness` returns a structured `Readiness`; readout validity is its own predicate, **not** `resolve_readout_time`

`compute_readiness` reproduces the five conditions behind today's Run button — subjects present, all subjects valid, output dir set, readout valid, and (in synB0 mode) synB0 output dir set — and returns a `Readiness` carrying an overall `can_run` plus each condition's individual outcome (`has_subjects`, `all_subjects_valid`, `has_output_dir`, `readout_valid`, `synb0_dir_valid`).

Readout validity is decided by a **new** named predicate `is_readout_valid(auto, raw) -> bool` in `processing/validators.py` (True in auto mode; otherwise whether `raw` parses as a float), replacing the inline `float(self.readout_var.get())` try/except in `_update_run_button_state`.

- **Why not reuse `resolve_readout_time()` (the trap the grill caught).** An earlier draft routed readout validity through `resolve_readout_time()` and treated a `None` result as invalid. That is **backwards** and would silently change the Run-button behavior:
  - `resolve_readout_time` returns `None` only for **auto** mode (the value is resolved downstream from JSON) — so "None → invalid" would *disable* the button in auto mode, the common case.
  - For unparseable **manual** input it returns the **default** (a non-`None` float), so "None → invalid" would *enable* the button on garbage input.
  Both edge cases invert. The root cause: `resolve_readout_time` (build path) **coerces** bad input to a default and runs; the button (readiness path) **blocks** on bad input. They encode *different policies* and only look like duplication. `is_readout_valid` gives the readiness policy its own single home; `resolve_readout_time` stays the build-path resolver. This is the one deliberate cleanup — behavior is genuinely preserved, and the inline check is retired.
- **Rejected — return a bare `bool`:** the adapter today only needs the boolean, but a structured result lets a future Qt adapter show *which* precondition is unmet (a natural UX improvement) without re-deriving the conditions. The structure costs one dataclass and is free to ignore.
- **Rejected — change `resolve_readout_time` to return `None` on unparseable manual input:** that would fix the readiness inversion but alter the *build* path — `_collect_batch_state` currently feeds bad manual input through as the default and runs; making it `None` would change `BatchConfig.readout_time`. Out of scope and not behavior-preserving. A separate predicate is the correct seam.
- **Rejected — leave readiness in `app.py` and extract only `build_batch_state`:** readiness reads the same Tk Variables and encodes the same input rules; leaving it behind would keep half the input logic un-portable and re-duplicate the readout check. Both decisions move together.

### 5. Reuse the already-extracted validators; `compute_readiness` and `validate_runnable` agree by construction, not by delegation

`form_model.py` reuses `resolve_readout_time` and `validate_synb0_output_dir` from `processing/validators.py` as-is, and adds the one new predicate `is_readout_valid` (Decision 4) to that same module. `build_batch_state` uses `resolve_readout_time` unchanged.

`compute_readiness` computes its five condition flags **independently** — including the three trivial ones (`has_subjects`, `all_subjects_valid`, `has_output_dir`) that overlap with `validate_runnable`. It does **not** call `validate_runnable`, because that function is *first-failure-wins*: it returns only *which single condition failed first* and short-circuits, so it structurally cannot yield the five independent per-condition flags `Readiness` needs (User Story 11, Testing Decisions). The pre-flight `validate_runnable` call in the run flow, and its adapter-owned first-failure dialog phrasing, stay exactly where they are.

The two agree **by construction** — both are pure functions over the same `subjects`/`output_dir`, the shared conditions are primitive one-liners (`bool(subjects)`, `all(s.is_valid …)`, `bool(output_dir)`), and both are covered by tests. No validator is changed.

- **Rejected — have `compute_readiness` call `validate_runnable`:** its first-failure-wins shape can't populate independent per-condition flags; forcing it to would defeat User Story 11's "surface *why* a run is blocked."
- **Rejected — extract three shared leaf predicates (`has_subjects`/`all_subjects_valid`/`has_output_dir`) that both functions call:** a legitimate anti-drift option, but for three primitive one-liners the extra surface outweighs the near-zero drift risk. Agreement-by-construction plus tests is the chosen guarantee. (Revisit if either condition ever grows non-trivial.)

### 6. Module home: `gui/form_model.py`, tk-free, beside the other view-models

The new module joins `gui/result_model.py` and `gui/viewer_model.py` — the established cluster of tk-free presentation models — and imports only `processing` domain types, `processing.validators`, and `dataclasses`. It never imports `tkinter` or `PySide6`.

**Naming honesty:** unlike its two siblings, `form_model.py` is **not** a stateful `*Model` class — it is a module of *pure functions* (`build_batch_state`, `compute_readiness`) plus frozen dataclasses (`FormState`, `OptionState`, `Readiness`). The symmetry with `ResultModel`/`ViewerModel` is at the *seam* level (tk-free presentation logic the adapter delegates to), not the *shape* level (Decision 1 makes it deliberately stateless). The PRD and CONTEXT.md say so explicitly, so no future reader hunts for a `FormModel` object that mirrors `ViewerModel`.

- **Rejected — `processing/` (next to `validators.py`/`discovery.py`):** `build_batch_state` is presentation-shaped (a *widget snapshot* → config), not a domain rule like `validate_runnable`. Keeping it in `gui/` preserves the guardrail that `processing/` carries no GUI-presentation concern, and keeps the input model symmetric with the output model it mirrors.

### 7. Behavior-preserving, extract-then-flip in two commits

Commit 1 adds `form_model.py` and its tests, wired to nothing. Commit 2 rewrites the `_collect_*` and `_update_run_button_state` bodies to snapshot Tk Variables into a `FormState` and delegate, then deletes the dead inline logic. Each commit leaves the suite green and the app runnable, matching the smallest-leaf-first, build-alongside-then-swap discipline of PRDs 0009/0010.

**Flip-commit wrinkle — the partial-UI snapshot (grill finding).** `_update_run_button_state` is wired as a `trace` on `readout_var` (`app.py:583`) and can fire *during construction*, before `output_dir_var` (`app.py:630`), `synb0_output_dir_var` (`1190`), and `cli_option_vars` (`867`) exist — which is exactly why today's method carries `hasattr(self, …)` guards. The model must always receive a *fully-populated* `FormState`, so those guards do not disappear: commit 2 moves them into a new adapter method `_form_state() -> FormState` that reads each widget with a lifecycle-safe fallback (missing `output_dir_var` → `""`, missing `cli_option_vars` → `{}`, etc.), mirroring the current guards. `build_batch_state` is only ever called on Run (`app.py:2103`), when the UI is fully built, so it never faces a missing widget; only the readiness path does. Toolkit lifecycle stays in the adapter; `form_model.py` stays lifecycle-agnostic — and the future Qt adapter owns the analogous "widget not built yet" fallbacks the same way.

- **Rejected — one commit that both adds the model and rewrites `app.py`:** a larger, harder-to-revert diff with no green intermediate. The two-commit split lets the model and its tests land and be reviewed before any `app.py` line moves.
- **Rejected — port the input model *during* the `app.py` Qt port:** that reintroduces exactly the risk this PRD removes — untested input-mapping logic written fresh inside the migration's largest diff. Extract first, port second.

## Testing Decisions

**What makes a good test here:** it asserts external behavior at the `form_model` seam — a `FormState` (plus a `subjects` list) in, a `BatchState`/`OutputConfig`/`roi_shapes`/CLI-option dict or a `Readiness` out — never a widget detail and never the window. The Tk (and later Qt) adapter is thin glue; its correctness is the model's correctness plus a manual smoke pass, exactly as the output side is verified via `test_result_model.py`.

**The seam:** the `form_model` function interface (`build_batch_state`, `compute_readiness`) over the `FormState`/`OptionState`/`Readiness` shapes. This is a new seam, but it is the *highest* one available for input logic and it retires the only-testable-through-widgets state the logic sits in today; no lower or additional seam is introduced.

**Modules tested:** `gui/form_model.py` (plus the new `is_readout_valid` in `processing/validators.py`), via a new `tests/test_form_model.py` that imports neither `tkinter` nor `PySide6`. Coverage mirrors the branches being moved:

- `build_batch_state`: a fully-populated `FormState` produces the expected `BatchConfig`/`BatchState`; the ROI-shape default-to-sphere-3 fallback fires when nothing is selected; each CLI-option rule (disabled skipped, flag→`True`, value passthrough, int coercion, empty/unparseable skipped) holds; `OutputConfig` reflects the flags including per-key default-true; synB0 dir empties to `None` and eddy options appear only in synB0 mode.
- `compute_readiness`: each condition independently flips `can_run` (no subjects, an invalid subject, missing output dir, invalid readout in manual mode, missing synB0 dir in synB0 mode), and the structured per-condition flags match.
- `is_readout_valid` / the readout path specifically — the two cases that guard against the Decision 4 inversion: **auto mode is always readout-valid** (regardless of the raw string), and **unparseable manual input is readout-invalid** (whereas `resolve_readout_time` would return its default here). These two assertions are the regression net proving the button check was not silently merged into the coerce-to-default resolver.

**Prior art:** `tests/test_result_model.py` (the output-side view-intent model) and `tests/test_app_logic.py` (the already-extracted tk-free input decisions) are the direct templates — same "test the tk-free model, manually smoke the adapter" pattern (PRDs 0005/0006/0009/0010). No GUI test framework (pytest-qt) and no offscreen-display CI are added.

- **Rejected — pytest-qt tests that drive the widgets:** introduces a dev dependency and display-server CI to test intentionally-trivial read-widgets-into-a-dataclass glue. The model suite plus a manual smoke of the run flow is the chosen verification, consistent with the whole migration.
- **Rejected — assert against a live `DTIALPSApplication` instance:** that is the widget coupling being removed; the tests must stand without a window.

## Out of Scope

- **The `app.py` PySide6 port** — the large widget-rebuild this PRD prepares for. This PRD only extracts the input model and flips the *Tk* app to it; no widget is ported.
- **The typed worker→GUI message stream** — typing the `(msg_type, data)` output stream is an independent hardening on the *output* side; this PRD touches only the input side. The two can land in either order.
- **Any change to `BatchConfig`, `BatchState`, `OutputConfig`, the pipeline, or the external-tool command lines** — reused unchanged; the extraction is behavior-preserving.
- **Widening `FormState` beyond today's fields or adding new form options** — the snapshot captures exactly the current form; new options are separate features.
- **Surfacing readiness *reasons* in the UI** — `compute_readiness` returns the structured per-condition result so a future adapter *can*, but wiring that into a message is not done here; today's adapter still only enables/disables the button.
- **The frozen, downloadable bundle** (the migration end-state) — untouched.

## Further Notes

- **Sequencing (two commits, each green and runnable):**
  1. add `gui/form_model.py` (`FormState`, `OptionState`, `Readiness`, `build_batch_state`, `compute_readiness`) and `tests/test_form_model.py`, wired to nothing;
  2. add `is_readout_valid` to `processing/validators.py` (with a test), then rewrite `app.py`'s `_collect_batch_state`/`_collect_output_config`/`_collect_roi_shapes`/`_collect_cli_options` and `_update_run_button_state` to read Tk Variables through a new adapter method `_form_state() -> FormState` (which owns the lifecycle-safe fallbacks for widgets that may not exist yet — the old `hasattr` guards) and delegate to the model, deleting the dead inline logic (including the inline readout check, Decision 4). Manual smoke of the run flow: add subjects, set output dir, toggle synB0, enter garbage in the manual readout field and confirm the Run button *disables* (and re-enables when auto is ticked), then run a batch and confirm it produces the same config.
- **Symmetry with the output side (the shape to copy):** `ResultModel.handle(msg) -> list[Intent]` turns worker output into view-intents the adapter applies; `build_batch_state(FormState, subjects) -> BatchState` turns adapter input into a domain object. After this PRD, `app.py` is a thin adapter in *both* directions, which is the precondition for a clean Qt port.
- **Roadmap placement:** PRD 0010's closing note pencilled a rough order of `viewer → typed message stream → app.py → bundle`. This PRD claims **0011** for the input/form-model extraction — a preparation leaf that is independent of the typed-stream work and, like it, lands *before* the `app.py` port. The typed-stream and `app.py`-port efforts take later numbers; their exact numbering is left to when they are drafted (neither exists yet).
- **Guardrail carried forward:** `gui/form_model.py` stays tk-free (imports only `processing` types, `processing.validators`, and `dataclasses`), the same discipline that keeps `result_model.py`/`viewer_model.py` toolkit-free and `processing/` (incl. `processing/workers.py`) Qt-free. The tk-free model suite is what stays green across the eventual Tk→Qt swap.
- **Counts (verified during the sweep, 2026-07-06):** `app.py` is ~2404 lines; the input-mapping logic to extract is `_collect_batch_state` (app.py:2033), `_collect_output_config` (1839), `_collect_roi_shapes` (1961), `_collect_cli_options` (1992), and the readiness computation `_update_run_button_state` (210); the already-extracted input decisions live in `processing/validators.py` (`validate_runnable`, `resolve_readout_time`, `validate_synb0_output_dir`) with tests in `tests/test_app_logic.py`; the output-side counterpart being mirrored is `gui/result_model.py` with `tests/test_result_model.py`. `readout_var`/`synb0_output_dir_var`/`fa_threshold_var`/`refine_roi_var` are `StringVar`/`StringVar`/`DoubleVar`/`StringVar` respectively (verified: `refine_roi` and `fa_threshold` pass through to `BatchConfig` as a string and a float unchanged); every CLI `value_var` is a `StringVar` (`app.py:881`), so `OptionState.value` is always a plain string.
- **Grill outcomes (2026-07-06):** five changes landed against the draft — (1) Decision 4 no longer routes readiness through `resolve_readout_time` (it would invert the button); readout validity is the new `is_readout_valid` predicate. (2) Decision 5 softened: `compute_readiness` computes its five flags independently and agrees with the first-failure-wins `validate_runnable` *by construction*, not by calling it. (3) the flip commit's partial-UI `hasattr` guards move into an adapter `_form_state()` snapshot method (named as a wrinkle). (4) `FormState` is a single flat frozen dataclass. (5) naming honesty — the "form model" is a module of pure functions, not a `*Model` class. Glossary entry added to `CONTEXT.md`.
- **Drafting status:** synthesized from the readiness sweep, then grilled (2026-07-06). The seam shape (stateless snapshot + pure builder/readiness), module home (`gui/form_model.py`), and the five grill outcomes above are confirmed with the user. Status: **Grilled**, ready to implement.
