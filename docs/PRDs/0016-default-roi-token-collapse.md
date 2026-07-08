# PRD 0016 — Route the default ROI through the `rois` token

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges the latent bug flagged Out-of-Scope in PRD 0015 (the `rois`-vs-`sphere3` naming mismatch) and overlaps the architecture review's Candidate 4 wrong-filename symptom. The `results_layout` on-disk contract (PRD 0007) defines the default 3.0 mm sphere as the bare `rois` token, but the write path never honored it — it wrote `rois_sphere3/`. This PRD makes the writers honor the contract.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why.

> **Process note.** Taken through a grilling session (2026-07-08) using the domain-modeling skill. The session established that the review's "sphere3 vs rois drift" is not catalog duplication (PRD 0015) but a **write-path contract violation with duplicated token logic**, and confirmed the fix direction (writers honor the contract) against the alternative (adopt `sphere3` as canonical). It also surfaced that honoring the default collapse forces a fix to the refined-default directory name (Decision 3) and to the report discovery (Decision 4).

---

## Problem Statement

`results_layout` (PRD 0007) is the single home for the results-on-disk contract, and it defines a **default ROI token**: `DEFAULT_ROI_TOKEN = "rois"`, mapping the default 3.0 mm sphere to the bare `rois/` directory and `alps_results.csv`. Every other shape gets the `rois_{token}/` / `alps_results_{token}.csv` form.

The **write path never routed through that contract.** Two callers hand-computed the token instead:

- **`registration/fsl.py`** — `r_str = str(sphere_radius).replace(".", "p").rstrip("0").rstrip("p"); shape_name = f"sphere{r_str}"`, so the default 3.0 mm sphere became `sphere3`, then `roi_dir_name("sphere3")` → `rois_sphere3/`.
- **`reanalysis.py::ROIShape.name`** — a near-identical formula (`.rstrip("p0")`), also yielding `sphere3`.

Because neither asked `results_layout` for the token, the default collapse it defines was **applied by nothing.** Consequences:

- **The documented canonical default form is written by no one.** A normal default run lands in `rois_sphere3/` + `alps_results_sphere3.csv`; the bare `rois/` + `alps_results.csv` the contract calls canonical is produced only by `batch.py`'s legacy `_write_single_csv` fallback.
- **A live wrong-filename bug.** `app.py`'s batch-results footer builds its "Results saved to:" label from `alps_csv_name(DEFAULT_ROI_TOKEN)` → `alps_results.csv` — a file a modern default run never writes (it writes `alps_results_sphere3.csv`).
- **Two live tokens for one shape.** `rois` and `sphere3` both denote the default; readers must special-case both (`test_results_layout` even pins them coexisting), and a folder holding both an old `rois/` and a new `rois_sphere3/` shows two identical "Sphere 3.0mm" viewer entries.
- **Two hand-rolled formulas that already diverge.** `fsl.py`'s `.rstrip("0").rstrip("p")` and `reanalysis.py`'s `.rstrip("p0")` agree only inside the current `[1.0, 4.0]` radius range; outside it they part ways — a drift the duplication invites.

## Solution

Give `results_layout` — the contract owner — the single **geometry → token** function, apply the default collapse there, and route both writers through it. Fix the two contract functions and the one discovery site that the collapse touches.

- **`results_layout.shape_token(shape_type, sphere_radius) -> str`** is the one home for geometry → base token: the default 3.0 mm sphere (`DEFAULT_SPHERE_RADIUS`) collapses to `DEFAULT_ROI_TOKEN`; every other sphere is `sphere{radius}`; squares pass through by type. Both `fsl.py` and `reanalysis.py::ROIShape.name` call it; both hand-rolled formulas are deleted.
- **`roi_dir_name` / `parse_roi_dir` handle the refined default.** Honoring the collapse means the default's refined variant is now `roi_dir_name("rois", refined=True)`, which previously produced the nonsensical `rois_rois_refined/`. It now yields `rois_refined/`, and `parse_roi_dir` round-trips it as the whole token `rois_refined` (which `roi_display_name` already renders "Sphere 3.0mm (r)").
- **`report.py` discovers the bare `rois/`.** Its shape discovery globbed `rois_*` and hardcoded `f"rois_{shape}"`, so it would miss the bare default. It now enumerates every subdirectory through `parse_roi_dir` and builds paths through `roi_dir_name`.

After this, a default run writes `rois/` + `alps_results.csv`, the viewer discovers it as the `rois` token → "Sphere 3.0mm", and `app.py`'s footer label is correct for the default (the common case). No ALPS value, mask, or centroid changes — only directory/CSV names for the default shape.

The work lands as **four commits, PRD-first, contract-then-callers**:

1. `docs: PRD 0016 + CONTEXT.md` — this document and the geometry→token contract note.
2. `feat: add results_layout.shape_token + refined-default naming + tests` — the new function, the `roi_dir_name`/`parse_roi_dir` fix, and their tests. Contract-only; no callers repointed.
3. `fix: route ROI writers through shape_token` — `fsl.py` and `reanalysis.py` call `shape_token`; the two hand-rolled formulas are deleted. Default runs now write `rois/`.
4. `fix: discover the bare rois/ default in report.py` — generalize the two discovery sites so reports include the default shape.

## User Stories

1. As a researcher, I want a default-shape run to write the canonical `rois/` + `alps_results.csv` the contract documents, so the on-disk layout matches what every reader (viewer, reports, the results footer) expects.
2. As a user reading the batch-results screen, I want the "Results saved to:" filename to be a file that actually exists, so I can find my results.
3. As a maintainer, I want geometry → token computed in exactly one place (`results_layout.shape_token`), so the pipeline and reanalysis writers cannot drift on it the way their two hand-rolled formulas already had.
4. As a user refining the default shape, I want its directory to be `rois_refined/`, not `rois_rois_refined/`, so the name is sensible and the viewer labels it "Sphere 3.0mm (r)".
5. As a user generating a quality report, I want the default shape included, so the report is not silently missing the most common ROI.
6. As a developer, I want ALPS values, masks, and centroids unchanged, so this is a naming-only fix.

## Implementation Decisions

### 1. Writers honor the contract (the default is the `rois` token), not the reverse

The default 3.0 mm sphere is written as the bare `rois` token, restoring the `results_layout` (PRD 0007) design. The writers change; the contract does not.

- **Grill resolved — the contract is the source of truth for the on-disk format.** `results_layout` was deliberately designed with `rois` as the canonical default; the writers predate it and never routed through it. Fixing the writers to honor the established contract is the faithful reading of "fix the bug."
- **Rejected — adopt `sphere3` as the canonical default token** (demote `rois`/`alps_results.csv` to a read-only legacy alias, leave writers as-is): lower write-path churn, but abandons the documented design, leaves `DEFAULT_ROI_TOKEN` as a legacy-only concept, and still requires fixing the readers that assume the bare default (the `app.py` footer, the viewer init). It trades a coherent contract for the status quo on disk.

### 2. `shape_token` is the single geometry→token home, in `results_layout`

The geometry → token mapping (including the default collapse) lives once, in the contract leaf, and both writers call it.

- **Grill resolved — centralize where the naming contract already lives.** `results_layout` owns `roi_dir_name` / `alps_csv_name`; the token those consume must be produced by the same owner, or a caller can (and did) bypass the collapse. This mirrors how `roi_dir_name` centralized the scattered `f"rois_{...}"` literals.
- **Rejected — keep the formula in the callers but fix each to collapse the default:** two copies of the collapse rule is the exact duplication that caused the bug; centralizing is the point.

### 3. The default collapse is by radius; the refined default is `rois_refined`

`shape_token` collapses when `sphere_radius == DEFAULT_SPHERE_RADIUS` (3.0). Honoring that forces `roi_dir_name`/`parse_roi_dir` to handle the refined default: the collapse check ran *after* the `_refined` suffix was appended, so `roi_dir_name("rois", refined=True)` produced `rois_rois_refined/`. It now produces `rois_refined/`, and `parse_roi_dir("rois_refined")` returns the whole token `rois_refined` (round-tripping, and already rendered "Sphere 3.0mm (r)" by `roi_display_name`).

- **Grill resolved — `rois_refined`, the coherent extension.** The refined default gets a name that carries the `rois` base without the double `rois_` prefix; its CSV is `alps_results_rois_refined.csv`, mechanically consistent with the token.
- **Rejected — don't collapse when refined (keep `sphere3_refined` for the refined default):** asymmetric (`rois/` unrefined but `rois_sphere3_refined/` refined) and keeps the `sphere3` token alive, partially defeating the coherence goal.
- **Rejected — name the refined default `rois_refined` but parse it to a bare `refined` token:** `roi_display_name("refined")` would render "Refined", not "Sphere 3.0mm (r)"; keeping the `rois` base in the token is what makes the label correct.

### 4. `report.py` discovers every ROI directory through the contract

`discover_roi_shapes` now enumerates all subdirectories and keeps those `parse_roi_dir` recognizes (so the bare `rois/` and `rois_refined/` are included, and non-ROI dirs like `registration/` return `None`); `discover_subjects_for_shape` and `calculate_subject_metrics` build the directory name with `roi_dir_name(shape)` instead of `f"rois_{shape}"`.

- **Grill resolved — the discovery must not assume the `rois_` prefix.** Once the default is the bare `rois/`, a `rois_*` glob silently drops the most common shape from every report. Routing discovery through `parse_roi_dir`/`roi_dir_name` mirrors how the viewer already discovers options.
- **Rejected — special-case a bare `rois/` alongside the `rois_*` glob:** re-implements `parse_roi_dir`'s job inline; using the contract function is the single-home move.

## Testing Decisions

Added to `tests/test_results_layout.py`:

- **`TestShapeToken`** — the default sphere collapses to `rois`; non-default spheres get explicit tokens (`2.0 → sphere2`, `2.5 → sphere2p5`, `3.5 → sphere3p5`, `4.0 → sphere4`); squares pass through by type.
- **Refined-default naming** — `roi_dir_name("rois", refined=True) == "rois_refined"`, `parse_roi_dir("rois_refined") == "rois_refined"`, `alps_csv_name("rois", refined=True) == "alps_results_rois_refined.csv"`, and `rois_refined` added to the whole-token round-trip parametrization.

The existing `test_results_layout` round-trips, the `reanalysis` seam/CSV tests (which drive `ROIShape(sphere, 3.0)` but assert applywarp argv / a manual CSV path, not the token), and the `native_placement` seam (which names its own dir) all stay green. `report.py` has no test suite; its two discovery sites are fixed to route through the contract and exercised via the end-to-end naming chain.

## Out of Scope

- **The `app.py` footer for non-default / multi-shape runs.** The footer still hardcodes `DEFAULT_ROI_TOKEN`, so for a squarev9-only run it names `alps_results.csv` rather than `alps_results_squarev9.csv`. That is the architecture review's Candidate 4 (the model should carry the real CSV name); this PRD only makes the footer correct for the default. Fixing it for all shapes is Candidate 4's separate work.
- **Migration of existing `rois_sphere3/` folders.** Result folders written before this change keep their `rois_sphere3/` names; they remain readable (the viewer/report resolve the `sphere3` token to "Sphere 3.0mm"). No migration is performed. A folder holding both an old `rois_sphere3/` and a new `rois/` will list two "Sphere 3.0mm" entries — unavoidable across the change and not newly created by it.
- **The `batch.py` legacy `_write_single_csv` path.** Its bare-`alps_results.csv` fallback (used only when a run produced no per-shape results) is unchanged; it already writes the default name.
