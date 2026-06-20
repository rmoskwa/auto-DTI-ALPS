# PRD 0002 — Make the ALPS index a pure module

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 2 ("Make the ALPS index a pure module" — "the crown jewel"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

The DTI-ALPS index is the reason this tool exists, and the code that computes it has **zero unit tests**. The two calculation functions in `processing/alps_calculation.py` interleave file IO with the science:

- `calculate_alps_lab` takes a `tensor_path` (a file path), calls `nib.load()`, and slices the 4-D tensor into `Dxx/Dyy/Dzz` before doing any math.
- `calculate_alps_pas` takes four file paths (`l2/l3/v2/v3`), loads all four, runs the eigenvector-sort, then does the math.

Because the formula cannot run without real `.nii.gz` files on disk — and a full upstream pipeline (MRtrix3 + FSL) to produce them — the index that the entire tool reports has no regression net. A wrong numerator/denominator wiring, a transposed tensor index, or a broken eigenvector-sort would only surface as a subtly wrong number on real subject data, with nothing to catch it. Consequences:

- The published ALPS formula `mean(Dxx_proj, Dxx_assoc) / mean(Dyy_proj, Dzz_assoc)` is **never asserted** against a known-correct value.
- The FA-threshold CSF-filtering step (which voxels are included in each mean) is **untested**.
- The ALPS-PAS eigenvector-sort — the most error-prone, method-specific science — is **untested**.
- The module reaches *up* into `from ..gui import config` just to read tensor-component indices, so even the math path drags a GUI dependency.

The science is the asset; right now it is the least-verified part of the codebase.

## Solution

Push IO to the edge so the math becomes **pure**: `calculate_alps_lab` and `calculate_alps_pas` take pre-loaded NumPy arrays and return the same results dict they return today. All `nib.load` and all knowledge of on-disk *format* (the MRtrix tensor-component index convention, the eigenvector X-component slice) move into **one shared loader** that both real callers — the pipeline and reanalysis — pass through.

With arrays at the interface, the formula can be exercised with tiny synthetic arrays whose expected ALPS value is computed *by hand* from the published formula. That makes the test an **independent oracle** (it proves the science is correct), not merely a snapshot of current output (which would freeze any latent bug as "the spec").

This is a behavior-preserving, single-concern refactor. It changes *where* IO happens and adds tests; it changes no scientific output, no return contract, and no downstream consumer.

**This candidate is independent of PRD 0001 (the ToolRunner seam).** The ALPS path issues no `subprocess` calls — only `nib.load` — so it needs no execution seam and can land standalone. (The architecture review's claim that Candidate 1 "unblocks testing candidate 2" is incorrect for this candidate specifically.)

## User Stories

1. As a researcher relying on the ALPS index, I want the core formula covered by tests, so that I can trust the number the tool reports.
2. As a researcher, I want the expected test values derived from the published ALPS formula, so that the tests prove correctness rather than echoing whatever the code currently does.
3. As a maintainer, I want to run the ALPS tests with no FSL, MRtrix3, or sample `.nii.gz` files installed, so that the science is verifiable in plain CI.
4. As a maintainer, I want the FA-threshold CSF-filtering behavior tested, so that a change to which voxels enter the mean is caught immediately.
5. As a maintainer, I want the ALPS-PAS eigenvector-sort tested in isolation, so that the method-specific science has its own regression net.
6. As a maintainer, I want format bugs (which tensor index is Dxx, where the eigenvector X-component lives) to live in one place, separate from the math, so that I can reason about each kind of bug on its own.
7. As a maintainer, I want degenerate inputs (empty ROI after FA filtering, zero/`nan` denominator) to have pinned, documented behavior, so that nobody silently changes whether a missing hemisphere yields `nan`.
8. As a developer running the full pipeline, I want ALPS results to be byte-for-byte identical to today, so that this refactor changes nothing a user can observe.
9. As a developer running reanalysis, I want it to compute ALPS through the same loader and the same pure functions as the pipeline, so that the two paths cannot drift apart.
10. As a developer, I want the MRtrix tensor-component index convention defined in exactly one place, so that a format change is a one-line edit.
11. As a maintainer, I want the math module to stop importing `from ..gui import config`, so that the science path no longer depends on the GUI package.
12. As a reviewer, I want this change to be exactly one idea ("IO to the edge"), so that the diff is small and the rationale is obvious.
13. As a future contributor, I want the deferred cleanups (LAB/PAS dedup, return-contract tidy, per-shape load hoist) recorded, so that the follow-up work is discoverable and safe to do on top of the new tests.
14. As a researcher comparing ROI shapes, I want per-shape ALPS results to remain unchanged, so that prior outputs remain reproducible after the refactor.
15. As a maintainer, I want a property-based sanity check (e.g. all components equal → ALPS = 1.0), so that gross wiring errors are caught even where a golden value is not hand-computed.
16. As a maintainer, I want the new tests to follow the existing pure-unit-test style in the repo, so that they are consistent with `tests/test_discovery.py` and easy to extend.

## Implementation Decisions

### 1. The pure/IO boundary: pure `calculate_*`, IO stays in the runner

`calculate_alps_lab` and `calculate_alps_pas` become pure (arrays + masks + threshold → results dict). `run_alps_calculation` remains the orchestration/IO shell.

- **Rejected — a new shared `alps_core` module/dataclass:** renaming-driven churn on a pre-distribution module with two callers; the win (synthetic-array testability) does not require it.
- **Rejected — inject a loader into `run_alps_calculation` too (make the orchestrator pure):** testing the orchestrator without disk tests plumbing, not science; its job *is* to touch disk.

### 2. `calculate_alps_lab` takes three component arrays: `dxx, dyy, dzz`

The `tensor_data[:, :, :, INDEX]` slicing moves out to the loader. The pure function no longer needs the tensor-component indices and drops the `gui.config` import.

- **Rejected — pass the whole 4-D tensor array and slice inside:** keeps the MRtrix index convention (a *format* fact) welded to the math, and keeps the config dependency in the math path.

### 3. `calculate_alps_pas` takes `l2, l3, v2_x, v3_x`; the eigenvector-sort stays inside

The X-component slice (`v[:, :, :, 0]`) is *format* and moves to the loader. The eigenvector-sort (`|v2_x| > |v3_x|` → choose `l2`/`l3` for `diff_X`/`diff_perp`) is *science* and stays inside the tested unit. All four inputs are same-shape 3-D arrays.

- **Rejected — pass full `l2, l3, v2, v3` eigenvector arrays and slice inside:** mixed 3-D/4-D signature; tests would need 4-D eigenvector fixtures.
- **Rejected — pass pre-sorted `diff_X, diff_perp`:** this exiles the eigenvector-sort (the single most error-prone piece of PAS) to the untested IO shell — the opposite of the goal.

This sets a deliberate, consistent rule across both methods: **format slicing leaves to the edge; science stays in the pure unit.**

### 4. One shared loader is the single IO edge; both callers route through it

There are *two* real callers — the pipeline (via `run_alps_calculation`, paths from `PipelineState`) and reanalysis (which today calls `calculate_*` directly with its own path variables). A loader (LAB-input loader + PAS-input loader, plus the shared FA-map and ROI-mask load) owns `nib.load` and **all** format knowledge. Both callers do loader → pure-function.

- **Rejected — inline `nib.load` + slicing at each caller:** re-creates the format-knowledge duplication one layer up, smeared across `run_alps_calculation` and `reanalysis.py`; breaks the "IO at one edge" promise.
- **Rejected — convert the pipeline only, leave reanalysis on a path-based wrapper:** a partial win that leaves reanalysis's load path untested and divergent.

### 5. Signatures change in place; no backward-compatibility shims

Both `calculate_*` functions are internal (exported in `processing/__init__.py`, called only by the pipeline and reanalysis). Their signatures change directly and both callers are updated. No path-based wrappers are kept.

- **Rejected — keep path-based functions as thin wrappers alongside new pure ones:** two ways to do one thing; leaves the untested path-loading code alive; defeats the "interface shrinks to one input shape" win. Justified by the codebase being internal, pre-distribution, with exactly two callers.

### 6. The results-dict return contract is unchanged, byte-for-byte

The functions return the same `dict[str, float]` with the same string keys (`Dxx_proj_left`, `ALPS_left`, `ALPS_bilateral`, …) and the same `dict | None` type. These keys are load-bearing downstream: `batch.py` reads `LAB_Dxx_proj_left`, `LAB_ALPS_bilateral`, `PAS_ALPS_left`, etc.; reanalysis reads `ALPS_left/right/bilateral`; both feed CSV columns.

- **Rejected — return a typed dataclass/NamedTuple:** ripples into CSV-column generation and every `.get(...)` in `batch.py` and `reanalysis.py`; an output-contract change is a separate concern from input purity.
- **Rejected — rename the misleading PAS keys now** (PAS reuses `Dxx/Dyy/Dzz` names for eigenvalues): renaming still ripples to CSV/reanalysis; recorded as a follow-up instead.

### 7. Degenerate behavior is pinned exactly, not fixed

Current behavior is preserved and locked by tests:
- Empty ROI after FA filtering → `np.mean` of an empty selection → `nan` (with the existing `RuntimeWarning: Mean of empty slice`), propagating to a `nan` ALPS index for that hemisphere.
- Zero or `nan` denominator → `nan` (the existing `if denominator > 0` guard yields this, since `nan > 0` is `False`).

- **Rejected — fix the semantics now (raise / return a sentinel):** a behavior change with clinical meaning (`nan` = "this hemisphere could not be measured") does not belong inside a structural refactor whose value *is* being behavior-preserving.
- **Rejected — pin numbers but suppress the empty-slice warning:** even a log-only change is out of scope; recorded as a possible follow-up.

### 8. The `log_callback` parameter stays

The injected, default-no-op logging callback remains on the pure functions (it reports per-ROI voxel counts before/after FA filtering). Injected logging does not compromise purity and preserves existing log output.

### 9. The `gui.config` coupling is relocated, not removed

After Decision 2, the tensor-component indices are needed only by the loader, so the `from ..gui import config` import moves out of the math and into the loader/IO layer — it does **not** disappear from the module. Fully removing the GUI dependency (moving `TENSOR_*_INDEX` and friends into a processing-owned constants module) is **Candidate 3's** job, explicitly not this PRD's.

### 10. Pipeline keeps loading per ROI shape

`run_alps_calculation` is called once per ROI shape (the pipeline swaps `state.roi_mask_paths` each iteration), reloading the invariant tensor/FA/eigen maps each time. This wasteful re-read is preserved as-is for now (behavior-preserving, single-concern). Hoisting the invariant loads out of the per-shape loop is a recorded follow-up, made trivially safe by this PRD's explicit loads and new tests.

## Testing Decisions

**What makes a good test here:** it asserts *external behavior at the pure-function seam* — arrays in, results dict out — never an implementation detail. Expected values are computed by hand from the published ALPS formula so the test is an independent oracle, not a snapshot of current output.

**The seam:** the pure `calculate_alps_lab` / `calculate_alps_pas` interface. This is the single, highest test seam for the science. No injected/execution seam is introduced (no `subprocess` in this path). The shared loader is the single IO edge and is deliberately left thin and **unit-test-free** — it is covered, if at all, only by the existing integration scripts.

**Modules tested:** `processing/alps_calculation.py` — the two pure functions. (The loader and `run_alps_calculation` are IO shells and are not unit-tested.)

**Test cases (new file, alongside the existing pure unit tests):**
- **LAB golden value** — constant arrays (e.g. `dxx = 2.0`, `dyy = dzz = 1.0`) inside known masks → ALPS must equal exactly `2.0`, proving the `mean(Dxx_proj, Dxx_assoc) / mean(Dyy_proj, Dzz_assoc)` wiring matches Taoka's index, including which component lands in numerator vs denominator for projection vs association ROIs.
- **FA-threshold filter** — voxels set below threshold are excluded from the means; voxels above are included; the reported `nan`/value reflects only the surviving voxels.
- **PAS eigenvector-sort** — construct `v2_x`, `v3_x` so the per-voxel `|v2_x| > |v3_x|` decision is known, and assert `diff_X`/`diff_perp` select `l2` vs `l3` correctly per voxel.
- **PAS golden value** — a hand-computed ALPS value through the full PAS path.
- **Degenerate cases (pin, per Decision 7)** — empty ROI after FA filter → `nan`; zero/`nan` denominator → `nan`.
- **Property invariants (supplement)** — all components equal → ALPS = `1.0`; bilateral = mean of left/right; left/right computed independently.

**Prior art:** `tests/test_discovery.py` is the model — pure unit tests on a single module, class-grouped, no external tools, `tmp_path` only where a file is genuinely needed (not needed here, since inputs are arrays). The seam-style fakes in `tests/fakes.py` and the `*_seam.py` suites are *not* the model for this work — there is no execution seam to fake.

## Out of Scope

- **LAB/PAS body deduplication** — the FA-filter-then-mean loop and the ALPS-formula/bilateral block are near-identical across the two methods. Extracting shared helpers (`roi_mean_fa_filtered`, `alps_from_means`) is a follow-up, made safe by the tests this PRD adds. (Distinct from Candidate 6, which is about pipeline↔reanalysis sharing.)
- **Return-contract cleanup** — typed result object, renaming the misleading PAS keys, removing the vestigial `| None` branch (no code path inside the functions returns `None`).
- **Removing the `gui.config` dependency** — only relocated here; elimination is Candidate 3.
- **Per-shape load hoisting** — the pipeline's per-shape reloading of invariant maps stays; optimizing it is a follow-up.
- **Any change to scientific output, the FA-threshold default, the ALPS formula, or the eigenvector-sort logic.**
- **The ToolRunner seam (PRD 0001)** — unrelated; this path has no `subprocess` calls.

## Further Notes

- **Sequencing:** this PRD stands alone and can land independently. The recommended follow-up order is: dedup (now test-protected) → return-contract cleanup → per-shape load hoist — each safe because this change establishes the regression net first.
- **Two corrections to the architecture review** are baked into the decisions above: (1) Candidate 2 is independent of Candidate 1; (2) this candidate relocates rather than removes the `gui.config` coupling.
- The grilling-session decisions behind this PRD are recorded in agent memory (`candidate2-alps-pure-module-design`).
