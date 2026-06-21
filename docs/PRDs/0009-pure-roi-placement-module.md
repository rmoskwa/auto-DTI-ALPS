# PRD 0009 — Make the ROI placement a pure, tested module

Status: Accepted · Date: 2026-06-20 · Grilled: 2026-06-20 · Source: Discharges the named-but-undelivered follow-up of PRD 0001 ("the pure ROI-geometry module") — the sibling of PRD 0002 ("Make the ALPS index a pure module"). This closes the last untested core-science pocket the ToolRunner seam unblocked.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** This PRD was drafted at the user's request and then taken through a grilling session (2026-06-20). The session resolved the three flagged grill targets (the cut-line membership, the dead-code confirmation, the degenerate-behavior intent) **and** surfaced three things the draft missed: a third copy of the geometry hiding in the test suite, the true intent behind the Y/Z search-range drift, and a second dead function in the same file. The decisions below reflect those resolutions; `Status` is promoted to `Accepted`.

---

## Problem Statement

The DTI-ALPS index has two scientific halves: the diffusivity formula (made pure and tested by PRD 0002) and the **ROI placement** that decides *which voxels the formula reads*. The second half has **zero unit tests**. The placement science lives in `processing/registration/base.py` and decides:

- **Where each ROI sits** — `find_mask_centroid` (base.py:374), and the joint projection/association refinement `refine_roi_pair_placement` (base.py:632–802) that searches a neighborhood for the position maximizing fiber purity while holding the paired ROIs within a Y/Z-drift constraint (so both sample the same X-direction pathway — the validity condition for ALPS).
- **What shape each ROI is** — `create_sphere_mask` (base.py:200), `create_square_v9_mask` (base.py:237), and the V1-optimized `create_square_v4_mask` (base.py:275), which picks the best of four 2×2 corner configurations by maximizing mean `|V1_z|` (projection) or `|V1_y|` (association).
- **How good a placement is** — `calculate_roi_quality` (base.py:401), the fiber-purity / direction-strength / FA score with the λ2/λ3>1.8 crossing-fiber penalty (Georgiopoulos et al. 2024).

These three sub-concerns — shape geometry, quality scoring, and placement search — are one cohesive cluster: the refiner composes the mask creators and the quality scorer, and is never used apart from them. Collectively they are the most error-prone, method-specific science in the tree after the ALPS formula itself — the squarev4 corner-selection and the joint pair-refinement are exactly the ROI-side analog of PRD 0002's "eigenvector-sort." A wrong corner rule, a transposed purity component, an off-by-one in the drift constraint, or an inverted penalty would silently move every ROI and quietly bias every reported index, with **nothing to catch it**. Consequences:

- The published placement logic (purity-maximizing search, the ±drift pairing constraint, the squarev4 V1 optimization, the crossing-fiber penalty) is **never asserted** against a known-correct value.
- The code is **buried in a backend module.** `base.py` mixes the pure placement science with the `RegistrationBackend` ABC, the result dataclasses, and `get_roi_template_paths` (a filesystem helper). The science reads as registration plumbing, not as testable science with its own home — the opposite of where PRD 0002 put the ALPS math.
- **Two dead functions are carried, one even re-exported.** `refine_roi_placement` (the single-ROI refiner, base.py:506–629) has no live caller — both real callers use `refine_roi_pair_placement` — yet it sits in `registration/__init__.py.__all__`. It is the placement-science twin of PRD 0003's dead `validate_pipeline_state`. The grilling session also turned up a second dead symbol in the same file: the package-level `register_fa_to_template` (registration/__init__.py:129), exported in `__all__` as "backward compatibility" but called by nothing (the only invocation is a same-file *local copy* inside `tests/test_registration.py`).
- **A third copy of the geometry hides in the tests.** `tests/test_registration.py` reimplements `create_sphere_mask` (line 154) and `find_mask_centroid` (line 167) as local copies rather than importing them, so the sphere/centroid math already lives in *three* places and the integration smoke validates its own private definition — the exact "nothing catches a drift" failure mode this PRD exists to close.
- **The Y/Z search-range drift is real, and the code is right.** The callers pass `search_y=1, search_z=2` (±1 Y, ±2 Z) while CLAUDE.md's "ROI Refinement Algorithm" section and the live log string (fsl.py:552) say "±2 Y, ±1 Z." The grilling session confirmed with the domain owner that **±1 Y / ±2 Z (the code) is the intended behavior** — so the doc and the log string are simply wrong text describing correct code, not an open scientific question.

Unlike PRD 0002's functions, these are **already pure** (arrays in → masks/tuples out; no `nib.load`, no paths). The only things standing between this science and a regression net are its *location* and the *absence of tests*. This PRD supplies both.

## Solution

Give the ROI placement the home and the tests the ALPS index already has. The moves are behavior-preserving except for one wrong log string (corrected to match the confirmed-correct code) and the removal of dead code:

- **Relocate the pure helpers into a dependency-free leaf, `processing/roi_placement.py`** — numpy-only, no package imports, a sibling of `processing/constants.py` (PRD 0003) and the pure half of `processing/alps_calculation.py` (PRD 0002). The six live functions move byte-for-byte; the two real callers (`fsl.py`, `reanalysis.py`), the `registration/__init__.py` re-exports, and the local copies in `tests/test_registration.py` are repointed. `base.py` keeps exactly the backend-interface and IO it owns: the `RegistrationBackend` ABC, `RegistrationResult` / `ROIPlacementResult`, and `get_roi_template_paths` (filesystem + `ROI_NAMES`).
- **Add the independent-oracle test suite** the ToolRunner seam (PRD 0001) unblocked — tiny hand-built arrays whose expected mask / centroid / purity / refined position is computed *by hand* from the placement rules, so the tests prove the science correct rather than echoing current output.

Two dead-code precursors clear the way: `refine_roi_placement` (single-ROI) and `register_fa_to_template` are deleted first, so the move carries no landmine and the public-surface cleanup does not step around a dead export.

This is a single-concern refactor that changes *where* the placement science lives and *adds tests*; it changes no mask, no centroid, no score, and no refinement result. The only observable change is a corrected log string (now matching the confirmed-correct ±1 Y / ±2 Z behavior). **It is independent of PRD 0002** (different functions, different file) and is the natural completion of PRD 0001's testability program: the seam unblocked *both* pure-module candidates; PRD 0002 delivered the ALPS one, this delivers the placement one.

The work lands as **five behavior-preserving commits** (one log-string fix aside), dead-code first, then leaf, then log fix, then tests:

1. Delete the dead `refine_roi_placement` (single-ROI) + drop it from the `registration` re-exports.
2. Delete the dead `register_fa_to_template` + its `__all__` entry (ruff-prune the orphaned imports).
3. Create `processing/roi_placement.py`; move the six pure helpers; repoint `fsl.py`, `reanalysis.py` (split import), `registration/__init__.py`, and the `tests/test_registration.py` local copies.
4. Fix the `fsl.py:552` log string to `±3 X, ±1 Y, ±2 Z` (match the confirmed-correct code).
5. Add `tests/test_roi_placement.py` (independent-oracle suite) + record the vocabulary in `CONTEXT.md`.

## User Stories

1. As a researcher relying on the ALPS index, I want the ROI-placement science covered by tests, so that I can trust that the formula is reading the right voxels — not just that the formula itself is right (PRD 0002).
2. As a researcher, I want the expected test values derived by hand from the placement rules (corner selection, purity, drift constraint, crossing-fiber penalty), so that the tests prove correctness rather than freezing whatever the code currently does.
3. As a maintainer, I want to run the ROI-placement tests with no FSL, MRtrix3, or `.nii.gz` files installed, so that the placement science is verifiable in plain CI (the PRD 0001 goal).
4. As a maintainer, I want the squarev4 V1-optimized corner selection tested in isolation — including its tie-break rule — so that the single most fiddly shape decision has its own regression net.
5. As a maintainer, I want the joint pair-refinement's Y/Z-drift constraint, its ±1 Y / ±2 Z search asymmetry, and its geometric-mean scoring tested, so that the rule that keeps paired ROIs on the same diffusion pathway cannot silently break.
6. As a maintainer, I want `calculate_roi_quality`'s purity, direction-strength, and the λ2/λ3>1.8 crossing-fiber penalty pinned, so that a transposed component or an inverted penalty surfaces immediately.
7. As a maintainer, I want the placement science out of the backend module and into a numpy-only leaf, so that it reads and tests as science, and importing it can never drag in a backend or create a cycle.
8. As a developer running the full pipeline, I want ROI masks, centroids, and refined positions to be byte-for-byte identical to today, so that this refactor changes nothing a user can observe (the corrected log string excepted).
9. As a developer running reanalysis, I want it to build ROIs through the same `roi_placement` functions as the pipeline — and the integration smoke to test against those same functions, not a private copy — so that no path can drift apart at the placement layer.
10. As a maintainer, I want the never-called `refine_roi_placement` and `register_fa_to_template` removed rather than relocated/carried, so that the move does not preserve dead code or invent test coverage for code nothing reaches.
11. As a reviewer, I want each step as its own small commit (two dead-code deletions, then relocation, then the log fix, then tests), so that they can be read and reverted independently.
12. As a future contributor, I want the deferred cleanups (the duplicated shape-dispatch in `fsl`/`reanalysis`, hoisting the placement tuning constants into `constants.py`) recorded, so that the follow-up work is discoverable and safe to do on top of the new tests.
13. As a maintainer, I want degenerate inputs (empty mask, out-of-bounds search positions, squarev4 with no V1 or all-corners-out-of-bounds, all-λ3-zero) to have pinned behavior with a "why" comment on each, so that nobody silently changes what a missing/degenerate ROI does.
14. As a future contributor, I want `processing/roi_placement.py` to remain a dependency-free leaf, so that adding to the placement science can never introduce a package import or a cycle.

## Implementation Decisions

### 1. The cut line: move the pure placement helpers, keep the backend interface and IO in `base.py`

`processing/roi_placement.py` receives the six **live, already-pure** functions: `create_sphere_mask`, `create_square_v9_mask`, `create_square_v4_mask`, `find_mask_centroid`, `calculate_roi_quality`, and `refine_roi_pair_placement` (~470 lines across the six). `base.py` keeps the `RegistrationBackend` ABC, the `RegistrationResult` / `ROIPlacementResult` dataclasses, and `get_roi_template_paths` — the backend contract and the one filesystem helper. After the move `base.py` is ~210 lines of pure backend-interface code, and `roi_placement.py` imports only `numpy`.

- **Grill resolved — all six belong in one module.** The session weighed splitting `calculate_roi_quality` (fiber-quality scoring, arguably not "geometry") and/or `refine_roi_pair_placement` (placement search) into separate concerns, and rejected it: the refiner composes both the mask creators *and* the quality scorer, so a split would only manufacture intra-leaf import edges for ~470 lines that are always used together. One leaf.
- **Module name `roi_placement.py`, not `roi_geometry.py`.** Because the leaf spans three sub-concerns — shape geometry, quality scoring, *and* placement search — "geometry" under-describes two-thirds of its contents (a maintainer hunting for the crossing-fiber penalty or the pair-refinement would not open a file named `geometry`). "Placement" is the umbrella the User Stories and CLAUDE.md's "ROI Refinement Algorithm" section already use for exactly this cluster. PRD 0001's "ROI-geometry module" was a placeholder coined before the module existed; the better name is chosen now that all six functions are visible.
- **Rejected — also move `get_roi_template_paths`:** it does filesystem IO (`Path.exists`) and reads `ROI_NAMES` from `results_layout`; it is IO/contract, not placement science. Moving it would drag a package import into the leaf and break the dependency-free property. It stays beside the ABC it serves.
- **Rejected — a `processing/roi/` package, or a generic `geometry.py`:** a package is over-engineering for six functions; `geometry.py` is both too generic and (as above) too narrow for what the leaf actually holds. `roi_placement.py` matches the domain and the flat-leaf precedent of `constants.py` / `results_layout.py`.

### 2. The functions move byte-for-byte; no logic, signature, or default changes

This PRD is *relocation + tests*, not a rewrite. Each function body is moved unchanged; every signature, default argument (`search_x=3`, `search_y=1`, `search_z=2`, `max_y_drift=1`, `max_z_drift=1`, `radius_mm=3.0`, `radial_threshold=1.8`), and return shape is preserved exactly.

- **Rejected — "tidy while moving"** (rename `fiber_type`, factor the shape-dispatch): every such change is a behavior or interface change riding inside a relocation, against the single-concern grain of PRDs 0002–0008. The dispatch duplication is recorded as a follow-up (Decision 8, Out of Scope), made *safe* by the tests this PRD adds. (The one exception — the wrong log string — is *not* tidied inside the relocation; it gets its own commit, Decision 8 / commit 4.)

### 3. Signatures change in place at the call sites; no compatibility shims, but the names stay importable from `roi_placement`

The two callers (`fsl.py`, `reanalysis.py`) change their import source from `.base` / `.registration.base` to the new leaf; the call sites themselves are untouched (same names, same args). One wrinkle: `reanalysis.py` imports `get_roi_template_paths` on the *same* import block as the six geometry functions — since `get_roi_template_paths` stays in `registration.base` (Decision 1), that import becomes a **split** (six names from `roi_placement`, `get_roi_template_paths` still from `registration.base`), not a wholesale source swap. `registration/__init__.py` and the `tests/test_registration.py` local copies are repointed too (Decision 4). No path-based or alias wrappers are kept in `base.py`.

- **Rejected — re-export the moved names from `base.py` for back-compat:** the codebase is internal and pre-distribution with exactly the known callers; a back-compat alias layer is the "two ways to do one thing" PRD 0002 Decision 5 rejected. Update the callers directly.

### 4. Fix the `registration` public surface, and the test suite's hidden copy, while repointing

`registration/__init__.py` currently re-exports four geometry names in `__all__` (`create_sphere_mask`, `find_mask_centroid`, `calculate_roi_quality`, and the dead `refine_roi_placement`) but not the square or pair-refine helpers — an incoherent surface. After the move, the geometry names are dropped from the `registration` package's `__all__`; consumers import from `processing.roi_placement`. The `registration` package keeps surfacing only its backend API (`RegistrationBackend`, the result dataclasses, `FSLRegistration`, `get_backend`, `get_roi_template_paths`, the FSL helpers). Nothing in the tree imports geometry from the `registration` *package* top-level, so dropping those entries is safe.

In the same commit (commit 3), `tests/test_registration.py`'s local copies of `create_sphere_mask` (line 154) and `find_mask_centroid` (line 167) are deleted and replaced with imports from `processing.roi_placement`. They have identical signatures to the canonical functions, so this is a drop-in repoint — and it makes "one home" *literally* true (the integration smoke now exercises the same code the pipeline does, instead of a private clone that could silently drift).

- **Rejected — keep re-exporting all geometry names from `registration` too:** that makes `registration` a second public home for the placement science and re-creates the "where is this defined" ambiguity. One home: the leaf. `get_roi_template_paths` stays surfaced from `registration` because it stays *in* `registration`.
- **Rejected — leave the `test_registration.py` copies untouched (the draft's silence):** shipping a "one home" refactor while a stale third copy sits in the test file undercuts the entire thesis. The copies are true callers of the same logic; repointing them is the same edit already being applied to `fsl.py`/`reanalysis.py`.
- **Note:** `processing/__init__.py` does not re-export any geometry symbol today and will not start (matching PRD 0003 Decision 3 — a curated public surface is a separate, deliberate decision).

### 5. Delete the dead `refine_roi_placement` first (commit 1)

`refine_roi_placement` (single-ROI, base.py:506–629) has no live caller: `fsl.py` and `reanalysis.py` both refine via `refine_roi_pair_placement`. It is only *defined* and *re-exported* (`registration/__init__.py.__all__`); the other tree hits on that name (`state.py:153/285`, `batch.py:143`, `app.py:2086`) are an unrelated string field on the state dataclass that happens to share the name. Commit 1 removes the function and its re-export.

- **Additional rationale — it has divergent, untested degenerate semantics.** The dead single-ROI refiner accepts a candidate with `if score > best_score` (from `best_score = -1.0`), so it will move to a score-0 position; the *live* pair refiner filters candidates with `if score > 0` and never does. Deleting the dead function removes that silent divergence rather than relocating it into the new leaf.
- **Rejected — relocate and test it with the rest:** that carries a dead function into the new leaf and invents an independent-oracle test for code nothing calls — preserving a landmine and padding coverage. Direct parallel to PRD 0003 Decision 6 (delete dead `validate_pipeline_state` as a precursor commit).
- **Rejected — leave it untouched in `base.py`:** then `base.py` keeps a dead function after the live placement science has left — exactly the "science buried in the backend module" smell this PRD removes.
- **Grill resolved — confirmed dead.** A tree-wide grep shows the only references are its `def` and the `registration` re-export; it is absent from every caller and from `processing/__init__`. The codebase is internal/pre-distribution, so there is no out-of-tree consumer to preserve.

### 6. The pure/test boundary is the function interface; no IO is unit-tested

The seam is the `roi_placement` function boundary — arrays/tuples in, arrays/tuples out. The IO shells that call them (`fsl.py::_transform_rois_to_native`, `reanalysis.py::reanalyze_subject`) load FA/V1/L2/L3 and `nib.save` the masks; they stay exactly as they are and are **not** unit-tested here (they remain covered, if at all, by the existing real-binary integration smoke). This mirrors PRD 0002, where the pure `calculate_*` were tested and `run_alps_calculation` (the IO shell) was not.

- **Rejected — drive `_transform_rois_to_native` under a fake to test it without binaries:** that conflates the placement science's regression net with a registration-orchestration test; the orchestration's `applywarp` already crosses the ToolRunner seam (PRD 0001) and its end-to-end behavior is the integration smoke's job. The high-value, high-purity target is the placement math.

### 7. Degenerate behavior is pinned exactly, with a "why" comment, and filed as zero tickets

The grilling session classified each degenerate path as an intended invariant (not a latent bug), so the current behavior is preserved, locked by tests, and annotated with a one-line "why this is the intended conservative fallback" comment at each test — no separate tickets:

- Empty mask → `find_mask_centroid` returns `None`; `calculate_roi_quality` returns `(0.0, 0.0, 0.0, 0.0)`. *(Clean sentinel.)*
- Search positions outside the volume are skipped (the bounds check `continue`s); a refinement whose whole neighborhood is out of bounds — or whose every candidate scores ≤ 0 — keeps the original centroids and returns score `-1.0`. *(Intended conservative fallback.)*
- `create_square_v4_mask` with `v1_data is None`, or with every corner configuration partly out of bounds, falls back to configuration 0 (centroid at bottom-left). *(Documented default; the all-OOB case is unreachable on real in-brain centroids.)*
- The λ2/λ3 penalty applies only when both `l2_data` and `l3_data` are provided **and** mean(λ2/λ3 over `l3>0` voxels) exceeds `radial_threshold`; otherwise the score is `purity·direction·FA` unpenalized. *(Guards div-by-zero and avoids penalizing genuine perivascular signal.)*

- **Rejected — fix any degenerate semantics now** (e.g. raise on empty mask, or change the squarev4 fallback): a behavior change does not belong inside a relocation whose whole value is being behavior-preserving.
- **Rejected — file follow-up tickets for the squarev4 all-OOB truncation:** the session judged it unreachable on real in-brain centroids and a sensible fallback; a ticket would be noise. Pinned + commented instead.

### 8. The shape-dispatch duplication is recorded; the Y/Z drift is resolved (code is canonical, log fixed)

- **Shape-dispatch duplication (deferred).** The `if shape_type == "sphere" / "squarev4" / else` mask-creation block is duplicated across `fsl.py` (3×) and `reanalysis.py` (3×). Extracting a `create_roi_mask(shape_type, …)` dispatcher is attractive, but it edits orchestration in both callers and overlaps the separate **pipeline↔reanalysis sharing** candidate (named in PRD 0002's Out of Scope). Folding it in here would break single-concern scope. Recorded as a follow-up, made safe by this PRD's tests.
- **Y/Z search-range drift (resolved).** The draft deferred this as "a behavior question." It is not: the grilling session confirmed with the domain owner that **±1 Y / ±2 Z (the code) is the intended behavior**, so the doc and log are wrong text, not an open question. Consequences: (a) the wrong log string at `fsl.py:552` ("±3 X, ±2 Y, ±1 Z") is corrected to "±3 X, ±1 Y, ±2 Z" in its own small commit (commit 4) — a zero-risk string fix, kept out of the relocation commit to honor single-concern; (b) the pair-refinement tests now *assert* the ±1 Y / ±2 Z asymmetry as intended (US-5), turning a contested value into a real oracle; (c) CLAUDE.md's matching "±2 Y / ±1 Z" text is also wrong, but CLAUDE.md is gitignored in this repo, so it is corrected locally only and will not appear in any commit.

- **Rejected — extract the dispatcher in this PRD:** it is its own concern; bundling it re-creates the "one sweeping change" the user's working style rejects.
- **Rejected — fold the log fix into the relocation commit (commit 3):** the log fix is a distinct concern from the relocation; its own one-line commit keeps each change revertible (US-11).

### 9. Delete the dead `register_fa_to_template` too — its own commit (commit 2)

The package-level `register_fa_to_template` (`registration/__init__.py:129`, exported in `__all__` as "backward compatibility") has zero live callers: the only invocation is inside `tests/test_registration.py`, which calls its own in-file *local copy* (def at :181, call at :574), not this one. It is the registration-side twin of the dead `refine_roi_placement`. It is deleted in its own small commit (commit 2) — separate from the geometry dead-code (commit 1) and the relocation (commit 3) — so each concern is independently revertible. `ruff` prunes the imports the deletion orphans (`Callable`, the `PipelineState` TYPE_CHECKING import); `get_backend` and `ToolRunner` stay (still used by the factory and `register_backend`).

- **Rejected — leave it (the draft's silence):** it is dead code sitting in the exact `__all__` that Decision 4 already edits; leaving it ships a public-surface cleanup that knowingly steps around a dead export.
- **Rejected — fold it into commit 1 (with the dead refiner) or commit 3 (the move):** it is a different concern (registration backward-compat API, not ROI placement); its own commit keeps the single-concern discipline the rest of this PRD holds. Convenience-of-proximity ("the file's already open") is exactly the rationalization that discipline exists to resist.

## Testing Decisions

**What makes a good test here:** it asserts *external behavior at the pure-function seam* — arrays in, mask/centroid/score/refined-position out — never an implementation detail. Expected values are computed by hand from the placement rules so each test is an independent oracle, not a snapshot. Tests build tiny arrays (e.g. `(8,8,8)` volumes, `(N,N,N,3)` V1 fields) with `numpy`; no files, no tools, no `tmp_path`.

**The seam:** the `processing/roi_placement.py` function interface. No injection/execution seam is introduced (the placement science issues no `subprocess` and no `nib.load`). This is *not* modelled on `tests/fakes.py` / the `*_seam.py` suites; it follows the pure-science model of `tests/test_alps_calculation.py`.

**Test cases (new file `tests/test_roi_placement.py`, class-grouped):**
- **Sphere mask** — a known center/radius/voxel-size includes exactly the voxels within `radius_mm` and excludes those beyond; anisotropic `voxel_size` is respected (the mm-distance, not voxel-distance, decides membership). **Pin the inclusive boundary:** a voxel placed at *exactly* `radius_mm` is asserted **present** (membership is `dist_sq <= radius_mm**2`), so a `<` vs `<=` regression — a silent ROI-size change — is caught.
- **Square v9** — a 3×3 in-plane block (9 voxels, same Z) at the center; truncated correctly at a volume edge.
- **Square v4 corner selection (the gnarly one)** — construct `v1_data` so exactly one of the four corner configurations is the **strict unique max** of mean `|V1_z|` (proj) / `|V1_y|` (assoc), and assert that configuration's four voxels are chosen. **Pin the tie-break separately:** a second test deliberately ties two configurations and asserts the **lower-index** configuration wins (selection is strict `>` over configs in list order, so config 0 beats config 3 at equal metric). Assert the `v1_data is None` and all-corners-out-of-bounds fallbacks land on configuration 0.
- **Centroid** — integer-rounded mean of set voxels; empty mask → `None`.
- **Quality score** — hand-built purity (fraction of voxels with the target component dominant), direction strength (mean target magnitude), mean FA; the combined product; and the λ2/λ3 penalty firing **only** above 1.8 with the `sqrt(1.8/ratio)` factor (assert no penalty just below threshold, the exact factor just above, and no penalty when L2/L3 are absent).
- **Pair refinement** — a small field where the purity-optimal proj and assoc positions are known: assert the returned pair maximizes the geometric-mean score subject to `|Δy|≤1` and `|Δz|≤1`; assert a constraint-violating better-individual pair is rejected in favor of a constraint-satisfying one; assert an all-out-of-bounds neighborhood returns the original centroids. **Pin the ±1 Y / ±2 Z asymmetry as intended (US-5):** with the production search ranges, assert a `dz=±2` candidate is reachable while `dz=±3` and `dy=±2` are *not* — proving the search window's intended shape rather than freezing an unexamined value.
- **Degenerate cases (pin + "why" comment, per Decision 7)** — empty mask, out-of-bounds search, the score-≤0 / all-OOB refine fallback, squarev4 `v1_data=None` and all-OOB fallbacks, all-`l3==0` (penalty skipped).

**Prior art:** `tests/test_alps_calculation.py` is the model — pure unit tests on a single module, class-grouped, no external tools, hand-computed oracles. The existing `tests/test_registration.py` real-binary smoke stays the only end-to-end check of the IO shells; commit 3 repoints its two local geometry copies to the leaf (Decision 4) but otherwise leaves it unchanged.

## Out of Scope

- **Any change to ROI-placement output** — masks, centroids, refined positions, quality scores, the squarev4 corner rule, the search ranges, the drift constraint, and the λ2/λ3 threshold are all preserved exactly. (The corrected `fsl.py:552` log string is the lone observable change, and it only makes the log match the unchanged behavior.)
- **The shape-dispatch deduplication** across `fsl.py` and `reanalysis.py` — a `create_roi_mask` dispatcher overlaps the separate pipeline↔reanalysis sharing candidate; recorded as a follow-up (Decision 8).
- **Hoisting the placement tuning constants** (`radial_threshold=1.8`, the `3/1/2` search ranges, the `1/1` drifts) into `processing/constants.py` — a domain-constants concern in the spirit of PRD 0003; left as default args here. Follow-up.
- **Unit-testing the IO shells** (`_transform_rois_to_native`, `reanalyze_subject`) — they stay on the integration smoke (Decision 6).
- **The ToolRunner seam, the ALPS pure module, the results-layout contract** — unrelated; the placement science issues no `subprocess` and no `nib.load`.

## Further Notes

- **Sequencing (five commits, each leaving the suite green):**
  1. delete dead `refine_roi_placement` + its re-export;
  2. delete dead `register_fa_to_template` + its `__all__` entry (ruff-prune orphaned imports);
  3. create `roi_placement.py`, move the six helpers, repoint `fsl.py` / `reanalysis.py` (split import) / `registration/__init__.py` / `tests/test_registration.py` local copies;
  4. fix the `fsl.py:552` log string to `±3 X, ±1 Y, ±2 Z`;
  5. add `tests/test_roi_placement.py` + `CONTEXT.md`.
  Commit 4 touches `fsl.py` at line 552 while commit 3 repoints `fsl.py`'s imports — different lines, so the order between them is free; commit 4 is placed after the move (relocate, then polish). Commit 5 is a pure addition; commit 3's behavior-preservation is proven by the existing suite + the real-binary smoke staying green and `ruff` clean.
- **Why not tests-first:** the functions are already pure and the move is a byte-for-byte cut/paste, so an independent-oracle suite protects equally before or after; writing it against the new module (commit 5) avoids re-pointing test imports a commit later. Commits 3 and 5 may be squashed if a reviewer prefers the net to land with the move.
- **Relationship to prior PRDs:** PRD 0001 named *two* pure-module candidates the seam unblocked — the ALPS index and the ROI placement. PRD 0002 delivered the first; this delivers the second, using the same "science gets a pure home and a hand-computed oracle" pattern, and the same dead-code-precursor discipline as PRD 0003.
- **Domain model:** `CONTEXT.md` gains the ROI-placement vocabulary — the pure mask creators (sphere / squarev9 / squarev4), the V1-optimized corner selection (with its lower-index tie-break), the fiber-purity quality score with the crossing-fiber (λ2/λ3) penalty, and the joint pair-refinement with the ±1 Y / ±2 Z search window and the Y/Z-drift pairing constraint.
- **Counts (verified):** `base.py` is 802 lines; the movable pure placement science is ~470 lines across six functions, plus the ~124-line dead single-ROI refiner. The two real callers are `fsl.py::_transform_rois_to_native` (518+) and `reanalysis.py::reanalyze_subject` (~280–420). `registration/__init__.py.__all__` re-exports four geometry names (one of them dead) today, plus the dead `register_fa_to_template`. `tests/test_registration.py` carries local copies of `create_sphere_mask` (154) and `find_mask_centroid` (167).
- **Drafting status:** grilled 2026-06-20; all three flagged targets (cut-line membership, dead-code confirmation, degenerate-behavior intent) and three draft gaps (the test-suite geometry copy, the Y/Z drift intent, the second dead function) are resolved. Promoted to `Accepted`.
