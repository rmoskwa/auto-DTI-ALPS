# PRD 0019 — Collapse the single-adapter registration seam

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges the architecture review's Candidate 3 ("Collapse the single-adapter registration seam"), the item PRD 0018 explicitly deferred here rather than fragment. The registration step ships one backend (FSL) behind a full pluggable-backend apparatus — an ABC, a name→class registry, a factory, and a never-called registration hook — justified only by an `"ants … in future"` comment that has outlived at least 18 PRDs with zero lines of ANTs code.

This PRD also serves as the ADR of record for the decisions below and for the scope it deliberately excludes. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why.

> **Process note.** Taken through a grilling session (2026-07-08) using the domain-modeling skill. The session walked the full design tree — premise, config field, file identity, package boundary, public surface, tests, recording — resolving each branch before the next. It confirmed the seam is genuinely single-adapter (the `name` property is never read, `state.registration_backend` is never set to anything but `"fsl"`, `register_backend` has zero callers) and drew a firm line between *the seam* and the adjacent dead FSL compat wrappers, which are a different concern left for a dead-code pass.

---

## Problem Statement

`processing/registration/` is built as a pluggable-backend package, but nothing plugs in. Verified against live code on 2026-07-08:

- **`RegistrationBackend` (ABC, `base.py`)** — five abstract methods. Its `name` property is **never read** anywhere; `get_template_path` is never called through the interface.
- **`_BACKENDS = {"fsl": FSLRegistration}` + `get_backend(name, runner)` (`__init__.py`)** — a one-entry registry and a factory that dispatches on a name. The pipeline calls `get_backend(backend_name, runner=self.runner)` twice, where `backend_name = self.state.registration_backend`, a field **never assigned anything but `"fsl"`** across the whole codebase (GUI, CLI, batch). The factory's `ValueError`-on-unknown-name branch is unreachable in practice and pinned by no test.
- **`register_backend(name, cls)` (`__init__.py`)** — a runtime registration hook with **zero callers**.
- **`FSLRegistration`** — the sole concrete backend.

This is textbook speculative generality: an extension mechanism carrying real reading, testing, and maintenance cost to serve an extension that does not exist. The only argument for it is that ANTs is a *documented* intent — but an ABC written against a single implementation cannot be trusted to fit a second one, and re-introducing a polymorphic seam *when ANTs actually arrives* (with two concrete implementations to factor against) is a well-understood, low-risk refactor. Git history and this PRD preserve the intent.

## Solution

Delete the polymorphic apparatus and let the pipeline construct the one backend directly. Keep the tool-agnostic value types and template helper that the apparatus happened to co-locate, under an honest filename.

- **The pipeline constructs `FSLRegistration(runner=self.runner)` directly**, in both `run_registration` and `run_roi_placement`. No name lookup, no factory, no `ValueError` branch.
- **`state.registration_backend` is deleted** from `PipelineState` and `BatchConfig`, and the field stops being threaded through `batch.py`. A dispatch key with nothing to dispatch to reads as a knob the user can turn when they can't.
- **`base.py` is renamed `results.py`.** The `RegistrationBackend` ABC is deleted; `RegistrationResult`, `ROIPlacementResult`, and `get_roi_template_paths` — the tool-agnostic result contract and template helper, none of them FSL-specific — stay grouped there. A file named `base.py` with no base class is a naming lie; the collapse is the moment to fix it.
- **`register_backend`, `get_backend`, `_BACKENDS`, and `RegistrationBackend` leave `__init__.py`'s imports and `__all__`.** `FSLRegistration`, the two result dataclasses, and `get_roi_template_paths` stay exported.
- **The seam tests are preserved and renamed** to protect the property that actually matters — a single injected `ToolRunner` threads all the way into every FSL command — re-expressed against direct construction.

The work lands **PRD-first, then a single code commit** — the collapse is one concern whose facets do not stand alone (a `results.py` still holding an ABC, or a `base.py` with no base class, are incoherent intermediate states):

1. `docs: PRD 0019` — this document.
2. `refactor: collapse the registration backend seam` — the ABC/registry/factory/`register_backend` deletion, the `base.py → results.py` rename, the pipeline's direct construction, the dropped `registration_backend` field, and the test rewrites, together.

## Implementation Decisions

### 1. Full collapse — delete the ABC, registry, factory, and `register_backend`

The polymorphic apparatus goes entirely; `FSLRegistration` becomes a plain class the pipeline instantiates.

- **Grill resolved — one implementation, zero extension pressure.** A registry with one entry, a factory that only ever returns that entry, a hook nobody calls, and an interface property nobody reads are cost without benefit. The abstraction cannot even be *designed* correctly against a single implementation.
- **Rejected — keep the ABC as living documentation of the contract a second backend would implement.** An untested, unimplemented-against interface documents an aspiration, not a contract; the concrete `FSLRegistration` methods and this PRD document the real shape. When ANTs lands, the ABC gets designed against two implementations — the only time it can be right.

### 2. Delete `state.registration_backend`; pipeline constructs `FSLRegistration` directly

The field leaves `PipelineState` and `BatchConfig`; `batch.py` stops forwarding it; the pipeline's `backend_name` variable, the `get_backend` call, and the surrounding `try/except ValueError` all go. The stage log becomes the constant `"Starting FA-to-template registration using FSL..."`.

- **Grill resolved — a dispatch key with nothing to dispatch to is misleading dead config.** It is not surfaced in the GUI or CLI and is recorded in no output metadata (verified), so nothing reads it as data; it existed only to select a backend. Keeping it is the same speculative generality being removed. This widens the blast radius slightly into `state.py` and `batch.py`, but a half-collapse that leaves the field is worse than either extreme.
- **Rejected — keep the field as a `"fsl"` default for forward-compat.** Preserves a hook for a dispatch that no longer exists; reintroduce it with the seam when ANTs arrives.

### 3. Rename `base.py → results.py`; keep the result types and template helper grouped

`RegistrationResult`, `ROIPlacementResult`, and `get_roi_template_paths` stay together in the renamed module; `fsl.py` and `__init__.py` update their import paths.

- **Grill resolved — the survivors are the tool-agnostic layer, not FSL internals.** The result dataclasses are the registration step's contract with the pipeline (`.success`, `.inverse_warp_path`, `.error_message`); `get_roi_template_paths` is used by `reanalysis.py`, which is not FSL-specific. They belong in a shared module, and `results.py` names what that module now is.
- **Rejected — keep the filename `base.py`:** minimal diff, permanent naming lie (no base class in it).
- **Rejected — fold the survivors into `__init__.py` and delete the file:** makes the package `__init__` both re-export hub and definition site — a smell — and bloats the first file a reader opens.
- **Rejected — sink the result types into `fsl.py`, leave the helper:** splits the tool-agnostic layer and forces `pipeline.py` to import its result contract from a tool-specific module.

### 4. Keep the `registration/` subpackage; do not flatten

The package stays `__init__.py` + `fsl.py` + `results.py`.

- **Grill resolved — the directory now names "the registration step's implementation," an honest reason to exist.** `fsl.py` is ~665 lines of real FSL orchestration and `results.py` is a genuine second concern; flattening to a single `processing/registration.py` is a large, blame-destroying move that buys nothing.
- **Rejected — flatten to one module** now that the per-backend-file rationale is gone: scope creep beyond killing the seam.

### 5. Seam-only — leave the dead FSL compat wrappers to a separate pass

Only `RegistrationBackend`, `get_backend`, `register_backend`, and `_BACKENDS` are removed. The module-level `check_fsl_registration_available`, `get_fsl_bin_dir`, and `get_jhu_template_path` (all zero-caller) and `get_fsldir` (one test caller) — legacy `FSLRegistration()` wrappers sitting in the same `__all__` — are left in place.

- **Grill resolved — dead code is a different concern.** Mixing a legacy-API sweep into the seam collapse muddies the commit and the ADR's story; this is the same narrowing discipline PRD 0018 used sending dead `_placeholder` to a backlog. The three dead wrappers join that dead-code backlog.
- **Rejected — prune them "while I'm in the file":** legitimate but off-theme; keeps this PRD's diff answering exactly one question.

### 6. Preserve and rename the seam tests against direct construction

The property under test — a single `ToolRunner` injected at the pipeline threads into every FSL command — is preserved, re-expressed without the factory:

- `test_registration_seam.py`: `test_get_backend_threads_runner_into_fsl_backend` → `FSLRegistration(runner=fake)` (renamed, e.g. `test_fsl_backend_threads_runner`); `test_get_backend_without_runner_defaults_to_real` → `FSLRegistration()` (renamed). Module docstring updated to stop narrating `get_backend`.
- `test_pipeline_seam.py::test_run_registration_forwards_runner_to_backend`: the spy repoints from `registration.get_backend` to `registration.FSLRegistration` (the pipeline's new construction site).

- **Grill resolved — a test named for a deleted function is its own lie.** The runner-threading guarantee is the load-bearing property; it survives, renamed. No test is added for the deleted `ValueError` path — nothing pinned it and the behavior is gone.
- **Rejected — restructure the pipeline-seam test to assert on the backend's runner instead of spying the constructor:** monkeypatching a class as a construction spy is slightly awkward, but it is the honest seam now; inventing a different injection point to avoid it is churn.

### 7. Record as PRD 0019 (its own ADR); no `docs/adr/`, no CONTEXT.md edit

- **The PRD is the ADR of record**, per this repo's convention (0017/0018 both say so; PRDs are the ADRs here). No separate `docs/adr/` tree.
- **No CONTEXT.md change.** The glossary never named this seam, and its one nearby reference — "the engine leaf never imports the `registration` result dataclasses" — is filename-agnostic and stays true after the rename. "There is one backend / no plugin abstraction" is an architecture fact, not a domain term; CONTEXT.md is a glossary "devoid of implementation details," so inventing an entry to match the 0017/0018 "+ CONTEXT.md" pattern would violate what the file is for.

## Testing Decisions

- **`tests/test_registration_seam.py`** — the two factory tests become direct-construction tests (renamed); the four FSL-command-routing and b0-helper tests are unaffected (they already construct `FSLRegistration(runner=fake)` directly).
- **`tests/test_pipeline_seam.py`** — `test_run_registration_forwards_runner_to_backend` spies `registration.FSLRegistration` instead of `registration.get_backend`; the assertion (pipeline forwards its own runner) is unchanged.
- **`tests/test_engine_independence.py`** — unaffected; the engine stays GUI-toolkit-free.
- No test exercised `register_backend`, the unknown-backend `ValueError`, or `backend.name`, so their deletion removes no coverage.

## Out of Scope

- **The dead FSL compat wrappers** (`check_fsl_registration_available`, `get_fsl_bin_dir`, `get_jhu_template_path`, and the one-test-caller `get_fsldir`) — a dead-code / legacy-API cleanup, deferred to the same backlog as PRD 0018's port-polish items (Decision 5).
- **Flattening the `registration/` package** to a single module (Decision 4).
- **Re-introducing any backend abstraction for ANTs** — explicitly out; that is future work to be designed against a real second implementation, not now.
