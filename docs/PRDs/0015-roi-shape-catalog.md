# PRD 0015 — One ROI shape catalog (input side)

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges Candidate 2 of the post-migration architecture review (`architecture-review-20260708`). The review flagged the set of ROI shapes — token, display name, geometry, default — as "one concept, three hand-kept copies" across `gui/app.py`, `gui/form_model.py`, and `gui/viewer_model.py`. This PRD unifies the part of that concept that is genuinely one table, and records precisely why the third "copy" is not one.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** Drafted from the architecture review and taken through a grilling session (2026-07-08) using the domain-modeling skill. The session's central finding reframes the candidate: what the review called one concept in three copies is really **two vocabularies**, and only one of them is a table (see the Problem Statement and Decision 1). The scope was deliberately narrowed to the input side; the viewer's display-name function is kept as a standalone parser (Decision 1). `Status` is promoted to `Accepted`.

---

## Problem Statement

The review named three sites that each encode "the ROI shapes":

- **`gui/app.py:1197`** — the ROI-placement checkbox tuples `(token, label, default)` for `sphere2 / sphere2p5 / sphere3 / squarev4 / squarev9`.
- **`gui/form_model.py:110`** — `_ROI_SHAPE_CONFIGS`, a token → geometry-dict map (`"sphere3": {"type": "sphere", "radius": 3.0}`, …) over the same five tokens, with a comment pinning its order to the checkbox order by hand.
- **`gui/viewer_model.py:42`** — `roi_display_name(token)`, a token → display-name function.

The grilling session established that these are **not** three copies of one table. They are two different vocabularies:

- The **input-selection vocabulary** is a *closed set of five*. `app.py`'s checkboxes and `form_model`'s geometry map speak exactly these five tokens; adding a selectable shape means editing both, in matching order. This is a genuine one-concept-two-copies fragmentation.
- The **on-disk / viewer vocabulary** is *open*. The reanalysis CLI (`python -m dti_alps --reanalyze … --sphere 2,3,4`, `--sphere 2.5`) accepts **any** radius in the validated range `[1.0, 4.0]`; the default 3.0 mm sphere is written under the `rois` token, not `sphere3`; every token may carry a `_refined` suffix. `roi_display_name` is therefore an *algorithmic parser over an open set* (`base_type[6:].replace("p", ".")`, `_refined → " (r)"`), not a lookup in a fixed table. A static catalog cannot own it — it would have to enumerate an unbounded set.

So the real duplication is the two **input-side** sites. Consequences today:

- **Adding a shape edits two files.** A new selectable shape needs a checkbox tuple in `app.py` *and* a geometry row in `form_model`, kept in the same order by a hand-maintained comment (`form_model.py:108`).
- **The "3 mm is the default" fact is written twice.** `app.py` pre-checks the `sphere3` box (`default=True`); `form_model._collect_roi_shapes` independently hardcodes `{"type": "sphere", "radius": 3.0}` as the fallback when nothing is checked. Two expressions of one fact.
- **Display names are formatted in two independent places** — the checkbox labels in `app.py` and the parser in `viewer_model`. They agree today (for all five input tokens `roi_display_name(token)` already equals the checkbox label), but nothing guards that agreement.

The review also noted a `sphere3`-vs-`rois` "drift." Grilling found this is **not** a catalog-duplication issue but a real latent naming bug in the write path — `fsl.py` names the default sphere's directory `rois_sphere3/` while `results_layout`/the viewer treat bare `rois/` as canonical. It is out of scope here (see Out of Scope).

## Solution

Introduce one ordered **ROI shape catalog** on the GUI side and have the two input-side consumers read the columns they need. Leave the viewer's parser alone.

- **`gui/config.py` gains `ROI_SHAPES`** — an ordered tuple of frozen `RoiShape(token, label, geometry, default)` rows, one row per selectable shape. This is the single source for the *closed* input set: its token, its human label, the geometry dict handed to the engine, and whether it is the canonical default.
- **`app.py` builds its checkboxes from `ROI_SHAPES`** — token, label, checked-state, and *order* all come from the catalog, so the fragile "order matches the checkbox order" hand-coupling disappears.
- **`form_model` reads the catalog for token → geometry** and derives its empty-selection fallback from the one `default=True` row — deleting `_ROI_SHAPE_CONFIGS` and the hardcoded `3.0`.
- **`viewer_model.roi_display_name` is unchanged** — it stays the standalone parser for the open on-disk vocabulary. A read-only test pins that it agrees with the catalog labels for the five shared tokens (Decision 5), catching label drift without coupling product code.

This is behavior-preserving: the same five checkboxes in the same order with the same default, the same geometry dicts into `BatchConfig`, the same "nothing selected → 3 mm sphere" fallback. Both existing `test_form_model` assertions stay green unchanged.

The work lands as **four commits, PRD-first, consumers-separate**:

1. `docs: PRD 0015 + CONTEXT.md` — this document and the "ROI shape catalog" glossary entry (records the decision before the code, per the house pattern).
2. `feat: add ROI shape catalog to gui/config.py + test` — the `RoiShape` dataclass and `ROI_SHAPES` tuple, plus `tests/test_roi_catalog.py` (the label-agreement test and the exactly-one-default invariant). No consumers repointed yet; green and self-contained.
3. `refactor: build ROI checkboxes from the catalog` — `app.py` iterates `config.ROI_SHAPES`; the hardcoded `shape_labels` list is deleted.
4. `refactor: route form_model ROI geometry through the catalog` — delete `_ROI_SHAPE_CONFIGS`; `_collect_roi_shapes` looks geometry up in the catalog and falls back to the default row.

## User Stories

1. As a maintainer, I want to add or remove a selectable ROI shape by editing **one** catalog row, so that the checkbox, its label, its geometry, and its default can no longer drift apart across `app.py` and `form_model`.
2. As a maintainer, I want the "3 mm sphere is the default" fact stated **once** (the catalog's `default=True` row), so that the pre-checked box and the empty-selection fallback cannot disagree.
3. As a developer, I want the checkbox order to come from the catalog, so that the hand-maintained "order matches" comment in `form_model` is no longer load-bearing.
4. As a maintainer, I want a test that catches divergence between the catalog labels and the viewer's display-name parser, so that the labels we chose *not* to unify still cannot silently drift.
5. As a developer running the pipeline, I want the geometry dicts, checkbox defaults, and empty-selection fallback identical to today, so that this change is invisible at runtime.
6. As a future contributor, I want the viewer's `roi_display_name` to stay a parser over the open on-disk vocabulary, so that the catalog is not mistaken for a place that must enumerate every reanalysis radius.

## Implementation Decisions

### 1. Scope is the input side only; the viewer stays a parser

The catalog owns the **closed** input set of five shapes, consumed by `app.py`'s checkboxes and `form_model`'s builder. `viewer_model.roi_display_name` is **not** repointed onto the catalog and is not changed.

- **Grill resolved — the two vocabularies are different kinds of thing.** The input side is a fixed enumeration a table models perfectly. The viewer side is an open set (any radius in `[1.0, 4.0]` from the reanalysis CLI, `_refined` suffixes, the `rois` default alias) that only a parser can cover. Forcing the viewer to read a table would either cap the radii the viewer can name or push the algorithmic parsing into the catalog anyway.
- **Rejected — catalog + a shared `shape_display_name(token)` the viewer and catalog both call.** Considered and set aside: it would move the open-set parsing into a shared helper and derive the catalog's label column from it (true "one source of naming logic"). It is a larger change and couples the viewer's runtime to the GUI catalog; the labels already agree, and Decision 5's test guards them at far lower cost. Recorded here so the option is not re-proposed without weighing that cost.
- **Rejected — full token unification** (make the default sphere's on-disk token `sphere3` instead of `rois`, one vocabulary end to end): a much larger blast radius touching `results_layout.DEFAULT_ROI_TOKEN`, the `fsl.py` write path, reanalysis, and existing on-disk `rois/` folders (a data migration). A separate PRD if ever taken (see Out of Scope).

### 2. One row-per-shape table, not a split by column ownership

The catalog is a single ordered table whose rows carry all four columns. It is **not** split into an engine-side token+geometry table and a GUI-side label+default table, even though those columns have different natural owners.

- **Grill resolved — splitting defeats the headline benefit.** The whole point is "add a shape in one row." A split keyed by token re-creates a smaller version of the exact hand-sync seam being removed: adding a shape would again mean editing two token-keyed files. And the engine does **not** consume input tokens — `fsl.py`/`reanalysis.py` derive on-disk tokens from the *geometry dict*, never from `sphere3` — so a token→geometry table placed in `processing/` would be a table the engine itself never reads.
- **Rejected — mirror the `constants.py` pattern** (geometry as a domain fact in `processing/`, labels+defaults in `gui/`, joined by token): consistent with the codebase seam in the abstract, but adds an engine-side table nothing in the engine reads and re-splits "one shape" across two files.

### 3. Home is `gui/config.py`; a frozen `RoiShape` dataclass tuple

`ROI_SHAPES: tuple[RoiShape, ...]` lives in `gui/config.py`, with a small frozen `@dataclass`:

```python
@dataclass(frozen=True)
class RoiShape:
    token: str            # input-selection token: sphere2, sphere2p5, sphere3, squarev4, squarev9
    label: str            # GUI display text: "Sphere 3.0mm", "Square 3x3"
    geometry: dict        # engine contract value passed into BatchConfig.roi_shapes
    default: bool         # the one canonical default (see Decision 4)

ROI_SHAPES = (
    RoiShape("sphere2",   "Sphere 2.0mm", {"type": "sphere", "radius": 2.0}, False),
    RoiShape("sphere2p5", "Sphere 2.5mm", {"type": "sphere", "radius": 2.5}, False),
    RoiShape("sphere3",   "Sphere 3.0mm", {"type": "sphere", "radius": 3.0}, True),
    RoiShape("squarev4",  "Square 2x2",   {"type": "squarev4"},               False),
    RoiShape("squarev9",  "Square 3x3",   {"type": "squarev9"},               False),
)
```

`config.py` is toolkit-free (it imports only `processing.constants`), so `form_model` importing it stays toolkit-free, and there is no import cycle (`config` does not import `form_model`). The engine never imports the catalog — it only ever sees the geometry dict inside `BatchConfig`, so the "engine stays GUI-free" guardrail holds.

- **Grill resolved — `config.py` over `form_model.py`.** The catalog is UX configuration: the selectable set, its labels, and which box is pre-checked. `config.py` ("Configuration constants and default values for DTI-ALPS GUI") is the conceptual owner; the one engine-ish column (the geometry dict) is exactly what the GUI passes into `BatchConfig`, and `config.py` already re-exports engine constants, so the precedent for holding engine-adjacent values exists.
- **Rejected — `form_model.py`** (where `_ROI_SHAPE_CONFIGS` lives today): keeps geometry next to its builder, but `form_model`'s role is *logic* (FormState → domain objects), not presentation config; the label + default-checked columns are UX, not translation logic.
- **Rejected — a new `gui/roi_catalog.py`:** cleanest single-responsibility home, but the codebase consolidates rather than proliferating modules for ~15 lines of data.

### 4. One `default=True` row drives both the pre-check and the empty-selection fallback

Exactly one row has `default=True`. `app.py` uses it to set the checkbox's initial checked-state; `form_model._collect_roi_shapes` uses that same row's geometry as the "nothing selected" fallback, replacing the hardcoded `{"type": "sphere", "radius": 3.0}`.

- **Grill resolved — collapse the two expressions of one fact.** Today the default 3 mm sphere is asserted both as `app.py`'s pre-checked box and as `form_model`'s hardcoded fallback; the catalog makes it one row. The invariant *exactly one `default=True`* is pinned by a test (Testing Decisions). Behavior is identical today (`sphere3` is that row).
- **Rejected — a per-row `default_checked` plus a separate fallback pointer/hardcode:** more flexible (multiple boxes pre-checked) but keeps the fallback as a second expression of the default and admits an ambiguous "which is the fallback" state. The GUI pre-checks exactly one box; a single `default` models that precisely.

### 5. Geometry stays a plain `dict`; order stays driven by the flags iteration

The `geometry` column is a plain dict, matching the engine's `BatchConfig.roi_shapes: list[dict]` contract, and `form_model`'s existing `test_form_model` assertions (which compare against literal dicts). `form_model._collect_roi_shapes` keeps iterating `flags.items()` to build the selected list, so output order continues to follow the selection dict's insertion order (which now originates from the catalog-driven checkbox order). The catalog thus owns the *presentation* order without changing how the *output list* order is computed.

- **Grill resolved — dict, not a typed geometry.** A typed geometry object would force a conversion back to `dict` at the `form_model` boundary and churn the passing tests for no gain; the engine contract is `list[dict]`.
- **Rejected — a typed `Geometry` dataclass:** cleaner types, but a conversion layer at every boundary and a test rewrite, to express a value the engine already consumes as a dict.

## Testing Decisions

New file `tests/test_roi_catalog.py`:

- **Label-agreement (drift guard).** For every `row in config.ROI_SHAPES`, assert `viewer_model.roi_display_name(row.token) == row.label`. No runtime coupling — the viewer stays a standalone parser — but a CI tripwire on the labels we chose not to unify (Decision 1). Passes today for all five tokens.
- **Exactly-one-default invariant.** Assert `sum(r.default for r in ROI_SHAPES) == 1` (Decision 4), so a future edit cannot leave zero or two defaults and silently break the fallback or the pre-check.

The existing `tests/test_form_model.py` cases stay green unchanged:

- `test_roi_shapes_default_when_nothing_selected` — `roi_shape_flags={}` still yields `[{"type": "sphere", "radius": 3.0}]`, now via the default row rather than a literal.
- `test_roi_shapes_multiple_selected_preserve_order` — order still follows `flags.items()`; the catalog only changes where the geometry values are looked up.

## Out of Scope

- **The `rois`-vs-`sphere3` on-disk naming bug.** `fsl.py:464` names the default 3 mm sphere's directory `rois_sphere3/` (since `roi_dir_name("sphere3") != DEFAULT_ROI_TOKEN`), while `results_layout` and the viewer treat bare `rois/` as the canonical default. A real latent bug, but in the *write path / on-disk contract*, with a data-migration dimension (existing `rois/` folders). A separate, larger PRD if taken — not a catalog concern (Decision 1's rejected "full token unification").
- **The reanalysis-CLI shape surface** (`__main__.py`'s `--sphere/--squarev9/--squarev4` argparse flags building `ROIShape` objects). A third input surface, but a distinct vocabulary (argparse flags → `ROIShape`, not GUI checkbox tokens → geometry dicts) and not part of the GUI input side this PRD unifies.
- **The `sphere2` docs drift** (the architecture review's "also noted": CLAUDE.md/docs never mention the `sphere2` shape). A documentation fix, surfaced but not folded in; the catalog at least makes the selectable set discoverable in one place.
- **Sharing the display-name parser** (Decision 1's rejected "catalog + shared `shape_display_name`"). Recorded as a considered option, not undertaken.
