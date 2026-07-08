# PRD 0014 — Unify the "place ROIs in native space" orchestration

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges Candidate 1 of the post-migration architecture review (`architecture-review-20260708`). PRD 0009 made the ROI placement *science* pure and tested; it deliberately left the IO orchestration that composes that science duplicated across the two callers. This PRD closes that duplication — the IO-shell twin of the pure-leaf move.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** Drafted from the architecture review and taken through a grilling session (2026-07-08) using the domain-modeling skill. The session sharpened three claims the review overstated: the "third copy in the tests" (it is the deliberately-kept e2e smoke, and PRD 0009 already de-drifted its geometry — see Decision 6), the `applywarp` PATH-resolution simplification (it would regress FSLDIR-only installs — see Decision 4), and the transform-caching divergence (it is correct-by-construction, not merely an optimization — see Decision 5). It also surfaced that the review's literal "put it in `roi_placement.py`" would break the pure-leaf invariant PRD 0009 established (see Decision 2). The decisions below reflect those resolutions; `Status` is promoted to `Accepted`.

---

## Problem Statement

PRD 0009 relocated the ROI-placement **science** — mask geometry, quality scoring, and joint pair-refinement — into the dependency-free leaf `processing/roi_placement.py` (arrays in → masks/tuples out) and gave it an independent-oracle test suite. What it explicitly left behind (PRD 0009 Decision 6) is the **IO shell** that composes that science: the loop that transforms the four ROI templates into native space, finds each centroid, jointly refines the projection/association pairs, creates the masks, and saves them. That shell is duplicated:

- **`registration/fsl.py::_transform_rois_to_native`** (fsl.py:520–780) — the pipeline's copy. One shape, one refinement mode per call; driven by `PipelineState`; verbose logging; returns `ROIPlacementResult`.
- **`reanalysis.py::reanalyze_subject`** (reanalysis.py:280–423) — the reanalysis CLI's copy. One shape, one refinement bool per call; driven by globbing the processed-subject directory; terse logging; folds results into `ReanalysisResult` and continues to ALPS calculation.

The two are the same `applywarp → find_mask_centroid → refine_roi_pair_placement → create_*_mask → nib.save` body around the same PRD 0009 kernels, pasted twice. Consequences:

- **The 260-line body has never had an interface test.** PRD 0009's oracle suite covers the pure kernels; the *orchestration that sequences them* — the applywarp argv, the conditional V1/L2/L3 loading, the transform-then-centroid ordering, the save-under-`roi_mask_name` step — is asserted nowhere. The seam PRD 0001 built (the `ToolRunner`) makes this testable without FSL, but no test exists.
- **The two copies have already drifted in ways that matter.** The conditional-load dance (`needs_v1 = refine or shape == "squarev4"`; load L2/L3 only when refining) is terser and subtly different in `reanalysis.py`; the transform step is cache-skipped in `reanalysis.py` (line 289) but always re-run in `fsl.py` (line 590); the V1-missing fallback logging is explicit in `fsl.py` and silent in `reanalysis.py`. Each is a place where a fix to one copy will not reach the other.
- **The identical `applywarp` argv is pasted in both.** `--ref / --in / --warp / --out / --interp=nn` appears twice, verbatim.

This is the exact "nothing catches a drift, and one fix misses the other copy" failure mode PRD 0009 removed one layer down — now applied to the shell PRD 0009 left standing.

Note the review's "third copy in the tests" claim is **stale**: `tests/test_registration.py` is the project's deliberately-kept real-binary e2e smoke (PRD 0001; pinned in `pyproject.toml` with a `TID251` per-file ignore so it may call binaries directly), PRD 0009 commit 3 already repointed its geometry to the leaf, and its `bet2` is a *registration/skull-strip* drift, not a copy of this placement shell. It is out of scope here (Decision 6).

## Solution

Lift the single-shape/single-mode placement shell into one **new sibling module**, `processing/native_placement.py`, and have both callers invoke it. The new module is the IO twin of `roi_placement.py`: where `roi_placement.py` is *arrays in → masks out* (pure), `native_placement.py` is *paths in → mask files on disk* (the composed IO shell).

- **`place_rois_in_native(...)`** owns: cache-if-exists `applywarp` (via the injected `ToolRunner`), `find_mask_centroid`, the conditional V1/L2/L3 loading with the V1-missing fallbacks, the joint pair-refinement, mask creation, and saving under `results_layout.roi_mask_name`. It takes resolved **paths** and one concrete shape spec + one `refine` bool, returns `(roi_mask_paths, roi_centroids)`, and **raises `ROIPlacementError`** on a failed transform or an empty centroid.
- **Each caller keeps its own outer loop and input-sourcing.** `fsl.place_rois()` keeps the `refinement_modes × roi_shapes` loop, the `roi_dir` naming, and the `ROIPlacementResult` / `all_roi_results` envelope. `reanalyze_subject()` keeps the glob-discovery, its `roi_dir` suffix logic, and the `ReanalysisResult` + ALPS-calc tail. Each translates a raised `ROIPlacementError` into its own failure representation.
- **Add the two-part `FakeToolRunner` interface test** the seam unblocks: a cache-pre-seeded happy path that runs the full body on real tiny NIfTIs (proving cache-if-exists by asserting zero `applywarp` calls), and a fake-driven seam path asserting the `applywarp` argv and the non-zero → `ROIPlacementError` mapping.

This is behavior-preserving for the pipeline and behavior-preserving-except-logging for reanalysis (its CLI output gains the verbose refinement lines — Decision 7). No mask, centroid, score, or refined position changes.

The work lands as **four commits, PRD-first, callers-separate**:

1. `docs: PRD 0014 + CONTEXT.md` — this document and the `native_placement` glossary entry (records the decision before the code, per the house pattern).
2. `feat: add processing/native_placement.py + interface test` — the new module (`place_rois_in_native`, `ROIPlacementError`) and `tests/test_native_placement_seam.py`. No callers yet; green and self-contained.
3. `refactor: route fsl.place_rois() through native_placement` — replace `_transform_rois_to_native` with a `place_rois_in_native(...)` call inside the existing loop; delete the old body.
4. `refactor: route reanalyze_subject() through native_placement` — replace its inline placement block with the single call.

## User Stories

1. As a researcher, I want the pipeline and reanalysis to build native-space ROIs through **one** implementation, so that a placement fix or bugfix cannot land in one route and miss the other.
2. As a maintainer, I want the 260-line native-placement shell covered by an interface test that runs with **no FSL installed**, so that the registration output path has a regression net for the first time.
3. As a maintainer, I want the `applywarp` argv, the transform→centroid ordering, the conditional V1/L2/L3 loading, and the save step asserted, so that a drifted flag or a reordered step surfaces immediately.
4. As a developer running the pipeline, I want ROI masks, centroids, and refined positions byte-for-byte identical to today, so that this change is invisible to any pipeline user.
5. As a developer running reanalysis, I want it to reach the same shell as the pipeline, so that the two placement paths cannot drift at the IO layer the way they already have.
6. As a maintainer on an FSLDIR-only install (FSL not on `PATH`), I want ROI placement to keep resolving `applywarp` exactly as it does today, so that this refactor does not introduce a green-then-red mid-run regression (Decision 4).
7. As a future contributor, I want `roi_placement.py` to stay the dependency-free numpy leaf, so that the IO shell living in a separate module cannot drag `nibabel` or the `ToolRunner` into the pure science (Decision 2).
8. As a reviewer, I want the module added first (no callers) and each caller repointed in its own commit, so that the change is read and reverted one concern at a time.

## Implementation Decisions

### 1. The unit is the single-shape/single-mode placement body; the callers keep their outer loops

`place_rois_in_native` does the placement for **one** shape spec (`shape_type`, `sphere_radius`) and **one** `refine` bool, over the four ROI templates. It does *not* absorb the `shapes × refinement_modes` looping. That loop stays in `fsl.place_rois()` (which supports multiple shapes and the `"Both"` refinement mode); reanalysis is already invoked once per shape by the CLI, with a single `enable_refinement`.

- **Grill resolved — the single-shape body is the true duplication.** The two copies are each single-shape/single-mode; only `fsl.py` wraps them in a shape×mode loop, and it does so for reasons (the GUI's multi-select, `"Both"`) reanalysis does not share. Pulling the loop into the shared function would force reanalysis to adopt a list interface it does not want and would not shrink the pasted body any further.
- **Rejected — extract the whole `place_rois` including the shape/mode loop:** it bundles a pipeline-only concern (multi-shape, `"Both"`) into the shared unit and complicates the reanalysis caller for no dedup gain.

### 2. Home is a new sibling module `processing/native_placement.py`, not `roi_placement.py`

The architecture review literally proposed placing the shell in `roi_placement.py`. That module is defined in CONTEXT.md as "a dependency-free, numpy-only leaf" whose science is pure (arrays in → masks/tuples out). The shell does `nib.load`, `applywarp` via `ToolRunner`, and `nib.save` — putting it in `roi_placement.py` would make that leaf import `nibabel` and `tool_runner` and do file IO, dissolving the pure/IO seam PRD 0009 drew.

`native_placement.py` sits at the `processing/` root, a sibling of both `roi_placement.py` (whose kernels it composes) and the two callers. It imports the pure kernels, `tool_runner`, `results_layout`, `nibabel`, and `registration.base.get_roi_template_paths` is **not** needed inside it (the caller passes the resolved template map — Decision 3).

- **Rejected — fold it into `roi_placement.py` (the review's literal text):** breaks the pure-leaf invariant the domain model just established; the pure kernels stop being independently importable without pulling in IO.
- **Rejected — put it in `registration/fsl.py` and have reanalysis import from there:** reanalysis would then depend on the registration *backend* to place ROIs; the shell is caller-agnostic and belongs beside the science, not inside one backend. (reanalysis today reaches into `registration.base` only for the template-path helper.)

### 3. Contract: resolved paths in; owns the nibabel loads and the conditional V1/L2/L3 dance

`place_rois_in_native` is keyword-only:

```python
def place_rois_in_native(
    *,
    runner: ToolRunner,
    applywarp_cmd: str,                 # required — see Decision 4
    fa_path: str,                       # ref grid, affine, header, fa_data
    inverse_warp: Path,
    roi_templates: dict[str, Path],
    reg_dir: Path,                      # transformed-template intermediates + cache
    roi_dir: Path,                      # output masks (caller pre-creates)
    prefix: str,                        # == subject_id; drives transformed + mask filenames
    shape_type: str,
    sphere_radius: float | None,
    refine: bool,
    v1_path: str | None = None,
    l2_path: str | None = None,
    l3_path: str | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[dict[str, str], dict[str, tuple[int, int, int]]]:
    ...
```

It owns the conditional-load logic that is itself duplicated today (`needs_v1 = refine or shape_type == "squarev4"`; load L2/L3 only when refining) plus the V1-missing fallbacks (refine → disabled with a log line; squarev4 → configuration-0 default). Each caller resolves paths its own way — `fsl.place_rois()` from `PipelineState`, `reanalyze_subject()` from globs — and hands them over.

- **Grill resolved — paths in, not arrays in.** Taking pre-loaded arrays would leave the conditional-load dance copied in both callers (exactly a drift site today) and make the shell shallow. Paths-in makes it a genuinely deep module that owns one loading contract.
- **Rejected — arrays in (caller loads):** shallow shell; the highest-drift-risk logic stays duplicated.

### 4. `applywarp` is a required parameter the caller resolves; the shell never falls back to `PATH`

The shell takes `applywarp_cmd: str` (required, no default). Both callers pass `str(fsl_bin / "applywarp")` from their own bin resolution. The pipeline's `register()` resolves *every* FSL command as `fsl_bin / cmd` and relies on `FSLDIR`, never on `PATH`; a shell that called bare `"applywarp"` would work only where FSL is on `PATH`, so on an FSLDIR-set-but-not-on-`PATH` install the registration step would pass and the very next ROI-placement step would fail to find `applywarp` — a green-then-red mid-run regression that only bites that configuration.

- **Grill resolved — required param, not a bare command or an optional default.** A required parameter documents that ROI transform needs FSL and preserves *both* callers' current resolution exactly (zero behavior change). An optional `applywarp_cmd="applywarp"` default was considered and rejected: it invites a future caller to silently take the `PATH` path and reintroduce the regression.
- **Rejected — hardcode bare `"applywarp"` and resolve via `PATH`/the runner:** cleaner in the abstract ("the `ToolRunner` seam resolves commands"), but regresses FSLDIR-only installs for the pipeline's placement step. Not shipping a mid-run regression wins over seam purity.

### 5. The shell caches transformed templates: skip `applywarp` when the output already exists

The four transformed templates depend only on `(inverse_warp, roi_template, fa reference grid)` — none of which vary by shape or refinement mode. So `reanalysis.py`'s `if not roi_transformed.exists()` skip (line 289) is not merely an optimization; it is correct-by-construction, and it saves four `applywarp` calls per additional shape. The unified shell adopts it for both callers.

The transformed intermediates live in `reg_dir` keyed by `prefix`; `prefix == subject_id` in every batch run (`batch.py:155` sets `output_prefix=subject_files.subject_id`) and reanalysis uses `subject_id` as its prefix, so a pipeline run's `{subject_id}_{roi}_transformed.nii.gz` is safely reused by a later reanalysis (same immutable warp → same output). Staleness cannot produce a wrong mask: the warp is immutable per subject.

- **Grill resolved — adopt cache-if-exists.** `fsl.py` always re-ran `applywarp`, but only ever on a fresh output dir where there was nothing to reuse, so the two behaviors never diverged observably; caching is strictly faster and provably equivalent.
- **Rejected — always re-run (fsl's behavior):** wastes four `applywarp` calls per extra shape (e.g. `--sphere 2,3,4` re-warps the same templates three times) for no correctness benefit.

### 6. `tests/test_registration.py` is left untouched; the `bet2` drift is noted separately

The architecture review framed deleting `tests/test_registration.py` as "kills the drifted `bet2` third copy." Three facts correct that: (a) it is the project's deliberately-kept real-binary e2e smoke (PRD 0001 keeps it as "the only true full-pipeline check"; `pyproject.toml` pins it with a `TID251` per-file ignore so it may call binaries directly); (b) PRD 0009 commit 3 already deleted its local geometry copies and repointed them to `roi_placement`, so it is not a copy of the placement kernels; (c) its `bet2` call is in the *skull-strip / registration* section, where live `fsl.py` uses MRtrix `dwi2mask` — a real drift, but in the registration path, not this placement shell.

So this candidate has **two** real copies, not three. Deleting the smoke would remove the only e2e real-binary check — an intent-contradicting move outside this candidate's scope.

- **Rejected — delete `test_registration.py`:** contradicts PRD 0001's explicit decision to keep it and removes the sole end-to-end binary check.
- **Rejected — fix its `bet2 → dwi2mask` drift here as a drive-by:** that is a registration/masking concern, not the placement-shell unification; it gets recorded as a separate finding (Out of Scope), not folded in.

### 7. Verbose (fsl-style) logging wins for both callers

The shell emits one set of log lines through the injected `log` callback; the callers supply the sink (`fsl` → GUI callback, `reanalysis` → `print`). The `fsl.py` phrasing — the `±3 X / ±1 Y / ±2 Z` window, per-ROI refined centroid + offset, `Purity: X% -> Y%`, `Drift from proj: Y=…, Z=…`, "no refinement needed", and per-mask voxel counts, plus the explicit V1-missing fallback lines — is kept. Reanalysis's terser output is dropped.

- **Grill resolved — verbose wins.** It is strictly more informative; it is what the GUI live-log panel already shows; and reanalysis is exactly where a user compares shapes and benefits from seeing offsets/purity deltas. Only *phrasing* unifies — routing stays per-caller.
- **Consequence accepted:** reanalysis CLI output gains lines. No test pins that text today (that gap is what this PRD closes), so nothing breaks; it is a visible, welcome CLI change.
- **Rejected — keep reanalysis terse (parameterize verbosity):** a verbosity flag is configuration surface invented to preserve an inferior output; one phrasing, kept in one place, is the point.

### 8. Failure is a raised `ROIPlacementError`; the callers own their envelopes

The shell returns a plain `(roi_mask_paths, roi_centroids)` on success and raises `ROIPlacementError` (a new exception defined in `native_placement.py`) on a failed transform (non-zero `applywarp`) or an empty centroid. `fsl.place_rois()` wraps each call in `try/except ROIPlacementError` and builds a failed `ROIPlacementResult`; `reanalyze_subject()` — which already wraps its whole body in `try/except Exception` and sets `status="failed"` — needs no new handling. `ROIPlacementResult` and `ReanalysisResult` stay entirely in their callers.

- **Grill resolved — raise, don't return a success flag.** The shell lives in a pure engine leaf and must not import `ROIPlacementResult` from `registration/base.py` (that inverts the dependency arrow — `registration` imports the placement modules, never the reverse). A neutral return + a raise keeps the leaf clean and fits reanalysis's existing `try/except` with zero friction.
- **Rejected — return a `success`/`error_message` object mirroring `ROIPlacementResult`:** either re-imports the registration dataclass (dependency inversion) or invents a near-duplicate value; the raise is simpler and each caller already has an envelope to translate into.

## Testing Decisions

The interface test is `tests/test_native_placement_seam.py`, using the existing `FakeToolRunner` (`tests/fakes.py`), in two parts:

- **Happy path (full body, no FSL).** Pre-seed the four `{prefix}_{roi}_transformed.nii.gz` in `reg_dir` as tiny real NIfTIs with a known blob. Cache-if-exists (Decision 5) then skips `applywarp` entirely, so the body runs on real `nibabel`: `find_mask_centroid` → joint refine (a crafted V1 volume, `refine=True`) → mask creation → save. Assert the four masks are written to `roi_dir` under `roi_mask_name`, the centroids are returned, and `fake.calls` contains **zero** `applywarp` invocations (which itself proves the cache path).
- **Seam path (argv + failure).** No pre-seed → `FakeToolRunner` records the `applywarp` argv. Assert `--ref= / --in= / --warp= / --out= / --interp=nn` and that the injected `applywarp_cmd` is used; assert a non-zero `applywarp` returncode raises `ROIPlacementError`. (As with the existing `*_seam.py` suites, the run then fails at the `nib.load` right after, which is fine — the seam is asserted from `fake.calls` before that.)

- **Rejected — minimal seam-only test (assert argv, let it fail at `nib.load`):** copies the existing reanalysis-seam pattern but leaves the refine/save body as uncovered as it is today. The cache-if-exists decision is precisely what lets the happy-path test reach the full body without FSL; use it.

The existing `tests/test_registration_seam.py` and `tests/test_reanalysis_seam.py` stay green: the `applywarp` argv is unchanged and `applywarp_cmd` carries the same resolved path, so their argv assertions still hold.

## Out of Scope

- **The `bet2 → dwi2mask` drift in `tests/test_registration.py`** (Decision 6). Recorded as a separate finding: the e2e smoke skull-strips with `bet2` while live `fsl.py` uses MRtrix `dwi2mask`. A registration/masking concern, not this placement shell.
- **The shape-dispatch duplication** (`if shape_type == "sphere"/"squarev4"/else` mask-creation blocks) — PRD 0009 Decision 8 already recorded it as a follow-up. Once both callers route through `place_rois_in_native`, the dispatch lives in exactly one place as a natural consequence; no separate extraction is needed here, and none is undertaken beyond what unifying the shell already delivers.
- **Multi-shape looping in reanalysis.** The CLI's one-shape-per-invocation model (Decision 1) is unchanged.
