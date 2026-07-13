# PRD 0023 — Configurable Adaptive search envelope

## Problem Statement

The **Adaptive** ROI method (see the "ROI method: Refine→Adaptive rename") improves
ALPS ROI placement by searching a small grid of candidate positions around each
template centroid and keeping the (projection, association) pair that maximises the
combined fibre-purity score, subject to a Y/Z-drift constraint that keeps both ROIs
on the same X-direction pathway. That search is governed by five integers —
**±3 X, ±1 Y, ±2 Z** for the per-ROI search window, and **±1 Y-drift, ±1 Z-drift**
for the pair constraint — and today they are **hard-coded** at the
`adaptive_roi_pair_placement` call site in `native_placement.py`.

Different datasets warrant different envelopes: a coarser acquisition or a subject
with more registration slop benefits from a wider window; a tightly-registered study
benefits from a narrow one that resists wandering off the tract. A researcher
currently has no way to tune this without editing source. The GUI even advertises a
**wrong** value ("±2 Y", while the code searches ±1 Y), so the one visible hint is
misleading.

The user wants to set each of the five values themselves, within a safe range, from
the same place they already choose the ROI method — and to do the same from the CLI
reanalyser.

## Solution

Expose the five search parameters as an **Adaptive search envelope** the user
controls, defaulting to the historical `3 / 1 / 2 / 1 / 1` so existing behaviour is
unchanged unless the user opts to change it.

- In the GUI's **8. ROI Placement** page, a new **"Adaptive Search Range"** group of
  five bounded spin boxes (each ±1–4) appears directly beneath the **ROI Method**
  selector. It is visible only while **ROI Method** is **Adaptive** or **Both** —
  the two modes that actually run the adaptive search — and hidden for **Standard**.
  The stale, incorrect description label is removed; the live spin boxes are now the
  source of truth on screen.
- On the CLI, `python -m dti_alps --reanalyze … --adaptive` gains five matching
  flags (`--search-x/-y/-z`, `--max-y-drift`, `--max-z-drift`), each validated to
  1–4, defaulting to the same envelope, and silently ignored when `--adaptive` is
  absent (Standard placement runs no search).

The envelope is a **per-run tuning knob**, not a new ROI shape: the results-on-disk
token stays `_adaptive`, so re-running a subject with a different envelope overwrites
the previous result in place — the same behaviour every other placement parameter
already has.

## User Stories

1. As a DTI researcher, I want to widen the Adaptive X search from ±3 to ±4, so that
   ROIs on a coarsely-registered subject can reach a better-aligned voxel.
2. As a DTI researcher, I want to narrow the Adaptive search to ±1 on every axis, so
   that placement on a tightly-registered study stays close to the template centroid.
3. As a DTI researcher, I want to set the Y and Z drift constraints independently, so
   that I can allow more association-ROI divergence in one axis than the other.
4. As a DTI researcher, I want the five search fields to appear only when I have
   chosen an ROI method that actually uses them, so that the form isn't cluttered
   with controls that do nothing in Standard mode.
5. As a DTI researcher, I want the search fields to appear when I pick **Both**, so
   that I can tune the adaptive half of a Both run.
6. As a DTI researcher, I want the fields to disappear when I switch to **Standard**,
   so that I'm not misled into thinking they affect a non-adaptive run.
7. As a DTI researcher, I want each field constrained to ±1–4, so that I cannot enter
   a value that is degenerate (0) or so wide it makes the search pointlessly slow.
8. As a DTI researcher, I want the fields to open at the proven defaults
   (`3 / 1 / 2 / 1 / 1`), so that leaving them untouched reproduces today's results.
9. As a DTI researcher, I want the on-screen labels to state the ± semantics, so that
   I understand a spin box showing "3" means a ±3-voxel search.
10. As a DTI researcher, I do not want the envelope to persist between launches, so
    that each session starts from the known-good defaults like every other analysis
    parameter.
11. As a DTI researcher running a batch, I want my chosen envelope applied to every
    subject in the batch, so that the run is internally consistent.
12. As a CLI user reanalysing an existing output, I want `--search-x/-y/-z` and
    `--max-y-drift/--max-z-drift` flags, so that I can re-place ROIs with a custom
    envelope without re-running the whole pipeline.
13. As a CLI user, I want the envelope flags validated to 1–4, so that a typo is
    rejected up front rather than producing a silently degenerate search.
14. As a CLI user, I want the envelope flags to default to `3 / 1 / 2 / 1 / 1` when
    omitted, so that `--adaptive` alone reproduces today's behaviour.
15. As a CLI user, I want envelope flags passed without `--adaptive` to be ignored
    rather than error, so that leftover flags in a script don't break the run.
16. As a maintainer, I want the five values bundled into one named type, so that they
    travel together through the config layers as a single cohesive concept.
17. As a maintainer, I want that type to reject out-of-range values on construction,
    so that no entry point (GUI, CLI, future callers, tests) can build an invalid
    envelope.
18. As a maintainer, I want the 1–4 range defined once, so that the GUI, CLI, and the
    type's own guard cannot drift apart.
19. As a maintainer, I want the engine's pure placement leaf to keep its existing
    parameter names, so that making the values configurable introduces no renaming
    churn at the search call site.
20. As a maintainer, I want the CLI reanalyser and the GUI pipeline to feed the same
    placement function the same envelope type, so that both entry points share one
    code path into the search.
21. As a researcher reading the glossary, I want "Adaptive search envelope" defined in
    the ubiquitous language, so that the term is unambiguous in code, commits, and
    review.

## Implementation Decisions

### The domain type — `AdaptiveSearchConfig`

- A **frozen dataclass** `AdaptiveSearchConfig` lives in `processing/constants.py`
  (the dependency-free leaf, so both `state.py` and the engine leaf import it without
  a cycle and the engine stays toolkit-free). Fields keep the engine's existing
  names: `search_x`, `search_y`, `search_z`, `max_y_drift`, `max_z_drift`.
- Defaults are `3 / 1 / 2 / 1 / 1` — the historical hard-coded values.
- The two drift knobs stay **independent** (not collapsed into one), so asymmetric
  Y vs Z drift is expressible.
- A shared constant `ADAPTIVE_SEARCH_RANGE = (1, 4)` is the single source of truth for
  the bound. `__post_init__` raises `ValueError` if any field falls outside it —
  frozen is compatible with this because the guard only reads `self`. This makes the
  range a property of the type, not a convention the two UIs happen to follow.

### The config thread (GUI → engine)

- **`FormState`** (`gui/form_model.py`) carries five loose ints, matching its
  "raw widget scalars" convention. `build_batch_state` — the single tested
  interpretation site — assembles them into an `AdaptiveSearchConfig` (which is also
  where the `__post_init__` guard fires for the GUI path).
- **`BatchConfig`** and **`PipelineState`** (`processing/state.py`) each gain one
  field, `adaptive_search`, defaulted via `field(default_factory=AdaptiveSearchConfig)`,
  sitting beside the existing `adaptive_roi_placement` method selector. `BatchRunner`'s
  field-by-field copy into `PipelineState` copies it across.
- **`place_rois_in_native`** (`processing/native_placement.py`) gains a keyword
  parameter `search: AdaptiveSearchConfig | None = None`; it builds a fresh default
  instance when `None`, and passes the five fields to `adaptive_roi_pair_placement`,
  replacing the hard-coded `search_x=3, search_y=1, …` literals at that call site.
- **`FSLRegistration.place_rois`** (`processing/registration/fsl.py`) reads
  `state.adaptive_search` and forwards it as `search=…`.

### GUI

- A new `QGroupBox` titled **"Adaptive Search Range"** is added to the ROI Placement
  page immediately below the ROI Method row, holding five `QSpinBox`es bounded to
  `ADAPTIVE_SEARCH_RANGE`, single-step 1, seeded from the defaults, labelled to carry
  the ± semantics (e.g. "Search X (±voxels)", "Assoc Y Drift (±)").
- Visibility is driven by the existing ROI-method combo's `currentTextChanged` signal
  through a handler that sets the group visible when the method is in
  `{"Adaptive", "Both"}`; the handler is also invoked once at build time so the
  initial state (default method "Both") is correct. This mirrors the established
  `_on_synb0_toggle` / `_on_rpe_combo_change` visibility idiom.
- The obsolete "Adaptive: ±3 X, ±2 Y, …" description label is deleted.
- The envelope is **not persisted** to `~/.dti-alps/user_config.json` (which stores
  only last-used directory paths); it resets to defaults each launch like every other
  analysis parameter.

### CLI reanalyser

- `_parse_reanalysis_args` gains `--search-x`, `--search-y`, `--search-z`,
  `--max-y-drift`, `--max-z-drift`, all `type=int`, validated to 1–4 against the same
  shared range, defaulting to the envelope defaults, with help text noting they apply
  only with `--adaptive`.
- The reanalysis entry points (`reanalyze_output` / `reanalyze_subject`) assemble an
  `AdaptiveSearchConfig` and pass it through to `place_rois_in_native`. Flags supplied
  without `--adaptive` are inert (Standard placement runs no search) rather than an
  error.

### On-disk contract (results layout)

- **Unchanged.** The ROI directory and CSV tokens stay `_adaptive`; the envelope is
  not encoded in any on-disk name. Re-running a subject with a different envelope
  overwrites the prior result under the same folder/CSV, with no on-disk record of
  which envelope produced which number. This is the deliberate, accepted trade-off:
  the envelope is a per-run tuning knob, not a distinct ROI shape, and encoding five
  ints into the token would expand the token grammar that `results_layout.parse_roi_dir`,
  the viewer's ROI-type selector, and the display-name mapping all depend on — surface
  area this feature does not require. If provenance later matters, recording the
  envelope beside the results (e.g. in the CSV) is the cheaper follow-up than changing
  the token.

## Testing Decisions

Good tests here assert **external behaviour at the highest existing seam** — the
values that arrive at the pure placement leaf, and the observable effect of changing
them — not the presence of particular widgets or private attributes. No new seams are
introduced; all three surfaces already have a home.

- **`AdaptiveSearchConfig` invariant** (`test_roi_placement.py`): a valid envelope
  constructs and exposes the expected defaults; each field out of range (0, 5) raises
  `ValueError`. Prior art: the pure-leaf assertions already in `test_roi_placement.py`.
- **Envelope steers the search** (`test_roi_placement.py`): `adaptive_roi_pair_placement`
  run with a widened vs. narrowed window reaches a different candidate set, and a
  tightened `max_y_drift`/`max_z_drift` excludes pairs the looser constraint admitted —
  confirming the knobs are not inert.
- **GUI-side mapping** (`test_form_model.py`): `build_batch_state` maps the five
  `FormState` ints onto `BatchConfig.adaptive_search` as an `AdaptiveSearchConfig`, and
  an unset form yields the defaults. Prior art: the existing `build_batch_state` mapping
  tests. This is the seam that stands in for GUI-widget tests.
- **CLI parse + threading** (`test_reanalysis_seam.py`, with `test_native_placement_seam.py`
  for the placement call): the new flags parse to ints, out-of-range is rejected, absence
  yields defaults, and the assembled envelope reaches `place_rois_in_native`. Prior art:
  the existing reanalysis and native-placement seam tests, which already drive these
  entry points with fakes.
- **Regression touch-up**: any existing test asserting the old hard-coded `3 / 1 / 2 / 1 / 1`
  at the call site is updated to reference the default `AdaptiveSearchConfig` instead.

## Out of Scope

- **Encoding the envelope in the on-disk token / CSV name.** Explicitly declined; the
  token stays `_adaptive` and reruns overwrite.
- **Envelope provenance / audit** — recording which envelope produced a given result
  (in the CSV, a sidecar, the Quality Report, or the viewer). A possible future
  follow-up, not built here.
- **Persisting the envelope across sessions** in `user_config.json`.
- **GUI-widget-level tests** — the tk-free `form_model` seam is the tested surface.
- **A full end-to-end pipeline integration test** for the envelope — the seam tests
  cover the wiring.
- **Any change to the Standard method** or to the scoring maths inside
  `calculate_roi_quality` / `adaptive_roi_pair_placement`.

## Further Notes

- **Domain glossary already updated** (`CONTEXT.md`): the "Adaptive search envelope"
  term (`AdaptiveSearchConfig`, its five fields, the 1–4 range, and the
  Adaptive-only applicability) is recorded, and the stale function name
  `refine_roi_pair_placement` in the ROI-placement section is corrected to
  `adaptive_roi_pair_placement`.
- **Pre-existing bug this fixes as a side effect**: the GUI's old description label
  claimed "±2 Y" while the engine searched ±1 Y. Replacing the label with live spin
  boxes removes the discrepancy.
- **Performance note (not a blocker)**: the pair search is roughly
  `O((2x+1)(2y+1)(2z+1))` candidate scores per ROI plus a pairwise combine. At the
  maximum `4 / 4 / 4` window this is materially larger than the default `3 / 1 / 2`,
  and `max_*_drift` beyond `2 · search_*` cannot admit new pairs (the association ROI
  can only move within its own window). Neither is enforced beyond the 1–4 bound;
  users own the cost of a wide envelope.
