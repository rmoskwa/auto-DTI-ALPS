# PRD 0021 — Radiological image interaction for the Results Viewer

Status: Draft · Date: 2026-07-12 · Grilled: 2026-07-12 · Source: The viewer's zoom/fit controls (`-`/`+`/`Fit` buttons) are awkward. This PRD replaces them with a fit-on-load default plus a zoom slider, and adds the standard PACS mouse conventions (left-drag window/level, right-drag zoom, middle-drag pan, wheel slice) to the image pane.

This PRD is the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the rejected alternative, and why.

> **Process note.** Unlike PRD 0010 (a behavior-preserving toolkit port), this is a **deliberate UX redesign** of the viewer's image interaction. There is no "old behavior" to diff the new mouse handling against; the new pixel math (window/level) is verified at the pure-function seam, and the interaction glue by manual smoke.

---

## Problem Statement

The Results Viewer's zoom UX is clumsy. Zoom is driven by `-`/`+`/`Fit` push-buttons and a `100%` label (`gui/viewer.py`, the zoom row in `_create_controls`), and the image pane supports no direct manipulation — the mouse wheel changes the slice, but there is no pan, no drag-zoom, and no brightness/contrast control. A researcher reviewing DEC images gets none of the manipulations they expect from a radiology viewer.

Two specific limitations:

- **Zoom is button-driven, not fit-first.** The viewer already computes a best-effort fit (`_zoom_fit`, run on subject-select), but the user then adjusts it with `-`/`+` clicks. There is no continuous, discoverable zoom affordance.
- **There is no window/level at all.** `render_dec_slice` (`gui/viewer_model.py`) normalizes FA by *each slice's own max* and hands back a finished RGB picture. Brightness therefore jumps silently as the user scrubs, and there is no way to adjust contrast/brightness — the single most-used control in any radiology viewer.

## Solution

Redesign the image interaction around the standard PACS convention, keeping all pixel math in the pure `render_dec_slice` and all cursor/interaction state in the adapter (the existing model/adapter split, PRDs 0005/0010).

**Mouse contract** (the OsiriX/Horos/RadiAnt scheme):

| Input | Action |
|---|---|
| **Left-drag** | Window/level — vertical = level (brightness), horizontal = width (contrast/range) |
| **Right-drag** | Zoom — up = in, down = out (center-anchored, geometric) |
| **Middle-drag** | Pan |
| **Wheel** | Change slice (unchanged) |

**Window/level** operates on the **FA channel** — FA is the intensity channel of an FA-modulated DEC image; hue (from `|V1|`) is untouched. Today's per-slice `FA / slice-max` auto-normalize is replaced by a linear window remap `clip((FA − (center − width/2)) / width, 0, 1)`, computed inside `render_dec_slice`. The default window is **volume-derived** (`center = FA-volume-max / 2`, `width = FA-volume-max`), computed once per subject — reproducing today's brightness *feel* but **stable across slices** instead of jumping per-slice.

**Zoom** keeps a visible affordance: the `-`/`+`/`Fit` row is deleted and replaced by a **geometric zoom slider (10 %–800 %)** with a zoom-% label. The slider and right-drag drive one shared, **center-anchored** zoom value and stay bidirectionally synced. Best-effort fit runs on subject-load (existing) and now also on view-switch; a **"Reset view"** button re-fits zoom **and** restores the default window in one press (replacing `Fit`).

**Pan** is middle-drag; the `QGraphicsView` scrollbars from PRD 0010 are **kept as an `AsNeeded` fallback** so users without a middle button (trackpads, 2-button mice) can still reach the edges of a zoomed slice.

The work lands as **three single-concern commits (1 → 2 → 3)**, each leaving the suite green and the viewer runnable:

1. **Windowing in the pure layer** — add `wl_center`/`wl_width` to `render_dec_slice`, replace the per-slice auto-normalize with the window remap, add `ViewerModel.default_window()`, update/extend `tests/test_viewer_model.py`. The adapter passes the default window, so the only visible change is the stabilized (volume-max) brightness.
2. **Zoom affordance swap** — delete the `-`/`+`/`Fit` row and `100%` label; add the geometric 10–800 % zoom slider + zoom-% label + "Reset view" button; wire slider→zoom; keep fit-on-load and add fit-on-view-switch. Mouse untouched.
3. **Radiological mouse conventions** — extend `_ImageView` with left-drag window/level, right-drag zoom (syncing the slider), and middle-drag pan; wheel stays slice; scrollbars stay as fallback. The manual-smoke commit.

## User Stories

1. As a researcher, I want the image to open at a best-effort fit with no button-clicking, so that I see the whole slice immediately.
2. As a researcher, I want a zoom slider as the visible zoom control, so that zoom is discoverable and continuous rather than `-`/`+` steps.
3. As a researcher, I want left-drag to adjust window/level (brightness/contrast) on the image, so that I can bring out white-matter structure the way I do in any radiology viewer.
4. As a researcher, I want right-drag to zoom and middle-drag to pan, so that my established PACS muscle memory works here.
5. As a researcher, I want the mouse wheel to keep changing the slice, so that my existing interaction is preserved.
6. As a researcher, I want brightness to stay stable as I scrub through slices, so that the image does not flicker darker/brighter slice-to-slice.
7. As a researcher, I want a single "Reset view" button that re-fits the zoom and restores the default brightness, so that I have one escape hatch after adjusting.
8. As a user on a laptop trackpad, I want scrollbars to remain available, so that I can still reach the edges of a zoomed-in slice without a middle mouse button.
9. As a maintainer, I want the window/level pixel math to live in the pure `render_dec_slice` and be unit-tested, so that the adapter stays thin glue verified by manual smoke.
10. As a maintainer, I want window/level, zoom, and slice to remain adapter-owned transient cursor state (never in the model), so that `render_slice` stays a pure function of its inputs.

## Implementation Decisions

### 1. A deliberate UX redesign adopting the PACS mouse convention

The image pane adopts left-drag = window/level, right-drag = zoom, middle-drag = pan, wheel = slice. This is an explicit interaction redesign, not a behavior-preserving change; it is recorded here as such so verification is understood to rest on the pure-function seam plus manual smoke, not on a diff against prior behavior.

- **Rejected — keep the `-`/`+`/`Fit` buttons and add nothing:** leaves the awkward control the request is about, and gives no brightness/contrast control at all.
- **Rejected — invent a non-standard binding:** the OsiriX/Horos/RadiAnt scheme is the established target; a bespoke scheme would defeat the "works like a radiology viewer" goal.

### 2. Window/level operates on the FA channel, inside `render_dec_slice`

The displayed image is FA-modulated DEC: hue from `|V1|`, brightness from FA. Window/level therefore remaps **FA** (the intensity channel) via `clip((FA − (center − width/2)) / width, 0, 1)`, replacing the current `fa_norm = FA / max(FA in slice)`. Hue is untouched. The math lives in the pure `render_dec_slice`; the adapter owns only the mouse→(center, width) mapping and the transient values.

- **Rejected — brightness/contrast on the final composited RGB:** simpler as a toolkit transform, but it distorts hue, blows out/clips the pure-white ROI overlay, and windows *after* the overlay — semantically wrong for a DEC image.
- **Rejected — no real windowing (a loose "make it adjustable"):** would not give the stable, per-subject window a radiologist expects and would leave the per-slice brightness jump unfixed.
- **Note — re-render cost.** Window/level is applied by re-running `render_dec_slice` on each mouse-move, not as a GPU transform. The slices are small, so a full numpy re-render per drag event is smooth; this is an accepted, deliberate trade for keeping the math pure.

### 3. Volume-derived default window, recomputed per subject

The default is `center = FA-volume-max / 2`, `width = FA-volume-max`, computed once at subject-load by the new pure `ViewerModel.default_window()`. This reproduces today's brightness feel but computed volume-wide, so brightness is **stable across slices** (the point of moving to a window). It is unchanged by view/slice/ROI-type/zoom, and recomputed fresh only when a new subject is selected; window settings are **not** carried across subjects.

- **Rejected — fixed constants (e.g. level 0.5, width 1.0):** `clip(FA, 0, 1)` is noticeably darker than today because real white-matter FA maxes near 0.8, not 1.0.
- **Rejected — keep the per-slice auto-normalize as the default:** it is exactly the slice-to-slice brightness jump this PRD removes (US-6).

### 4. "Reset view" is a visible button that resets zoom **and** window together

A single **"Reset view"** button replaces the deleted `Fit` button. It re-fits the zoom to the viewport **and** restores the default window in one press. It lives in the Navigation group beside the zoom slider.

- **Rejected — double-click on the image to reset:** the standard PACS reset gesture, but the user chose an explicit visible button over a hidden gesture. Double-click is therefore unbound.
- **Rejected — separate zoom-reset and window-reset controls:** one combined "get me back to sane" action is simpler and is what the user wanted.

### 5. Zoom is center-anchored; a geometric 10–800 % slider and right-drag drive one value

Zoom is a single scalar. The slider (no cursor) can only zoom around the **view center**, so right-drag zooms around the view center too — keeping the two controls identical in feel. The slider is mapped **geometrically** (equal slider steps = equal zoom *ratio*), spanning **10 %–800 %**; right-drag maps vertical movement geometrically (up = in, down = out). Right-drag moves the slider thumb live and vice-versa. `Fit` values clamp into the band; the fit value on load sets the slider's initial position.

- **Rejected — zoom-under-cursor for right-drag:** nicer for a mouse, but then the slider (center-anchored) and right-drag would feel inconsistent. The user chose consistency.
- **Rejected — linear slider over 0.25×–5.0×:** the old cap; `Fit` already computes values outside it (a small volume in a big viewport fits above 5×), and a linear scale crams 100 % at the low end.

### 6. Middle-drag pan; scrollbars kept as an `AsNeeded` fallback

Pan is middle-drag, implemented by translating the `QGraphicsView` scrollbars (left-button `ScrollHandDrag` is unavailable — left is window/level). The PRD 0010 scrollbars are **kept** (`AsNeeded`) as a fallback so a user without a middle button can still reach a zoomed slice's edges.

- **Rejected — hide the scrollbars for a pure PACS look:** cleaner, but a trackpad / 2-button-mouse user would be unable to pan at all. This PRD therefore does **not** reverse PRD 0010's scrollbars; it layers pan on top of them.

### 7. Auto-fit runs on subject-load and on view-switch; not on resize or ROI-switch

Best-effort fit runs on subject-select (existing) and now also on **view-switch** (axial/coronal/sagittal have very different in-plane dimensions; the view already resets to a middle slice, so "new view starts fit" is consistent). It does **not** re-run on window/panel resize (that would fight a manual zoom — "Reset view" is the deliberate re-fit) or on ROI-type switch (image dimensions do not change; zoom and window persist).

- **Rejected — persist zoom across view-switch (today's behavior):** a zoom that fits axial can spill a sagittal view out of frame; re-fitting per view is less surprising.
- **Rejected — re-fit on every resize:** yanks the image back to fit and undoes any manual zoom on each resize.

### 8. The seam: two floats through the pure function + a `default_window` query; adapter owns the cursor state

`render_dec_slice(...)` and `ViewerModel.render_slice(...)` each gain two floats `wl_center, wl_width` (matching the existing plain-parameter style, not a new value object). `ViewerModel.default_window() -> (center, width)` is a pure query over `self._fa`. The adapter owns transient `wl_center`/`wl_width` alongside `current_slice`/`zoom_level`, seeds them from `default_window()` on subject-select, and clamps `wl_width` to a small positive minimum during a drag to avoid divide-by-zero. The model carries no mouse concepts.

- **Rejected — a `WindowLevel` value object:** the existing `render_dec_slice` signature is plain scalars/booleans; two floats fit that grain, and the user preferred it.
- **Rejected — hold window/level in the model:** window/level is transient view-cursor state, the twin of zoom and slice, which are already adapter-owned per PRD 0005's split.

### 9. No mouse-convention hint text; keep a zoom-% label; no numeric window/level readout

Per the user's choice: no on-screen hint describing the mouse conventions; a zoom-% label is kept beside the zoom slider (mirroring the `"0 / 0"` slice label); no numeric window/level readout (the values are abstract FA units, not clinically meaningful, and the live image is the feedback).

- **Rejected — a muted mouse-convention legend under the image:** discoverability insurance for non-PACS users, but the user declined it.
- **Rejected — a numeric `W: … L: …` readout:** noise in abstract FA units for this audience.

## Testing Decisions

**What makes a good test here:** it asserts the windowing math at the `render_dec_slice` / `ViewerModel` seam — FA/V1 arrays + window in, RGB out — never a widget detail. The mouse handling is thin adapter glue verified by manual smoke, exactly as PRD 0010 verified the port.

**The seam:** the pure `render_dec_slice` and the `ViewerModel` query surface. **No new test seam, no GUI test framework.** `tests/test_viewer_model.py` (imports neither tkinter nor Qt) is extended:

- `default_window()` returns `(volume-max / 2, volume-max)` for a known FA volume;
- the default window reproduces the intended brightness and leaves **hue (from `|V1|`) byte-identical**;
- a narrow window clips/brightens FA as expected;
- the ROI overlay and brain-mask blackening still composite correctly on top of a windowed image (overlay on top, mask before overlay — the PRD 0005 invariants hold under windowing).

The existing `render_dec_slice` tests that assert per-slice normalization **will change** — that is the deliberate behavior shift (Decision 3), not a regression.

**Manual smoke checklist (commit 3, against real output):** left-drag brightens/darkens and widens/narrows contrast; right-drag zooms in/out and moves the slider thumb; dragging the slider zooms and the image stays centered; middle-drag pans; the scrollbars still pan when used; the wheel still changes slice; switching subject re-fits and restores the default window; switching view re-fits; "Reset view" re-fits zoom and restores the default window; ROI-type switch preserves zoom and window.

- **Rejected — pytest-qt smoke tests:** introduces a dev dependency and offscreen-display CI to test intentionally-trivial glue, against the project's "test the model, smoke the adapter" pattern (PRDs 0005/0006/0010).

## Out of Scope

- **Any change to the DEC hue math, `|V1|` normalization, CSV parsing, ALPS values, or the ROI/brain-mask overlay semantics** — only the FA intensity normalization changes (per-slice max → windowed).
- **Per-view remembered zoom** — view-switch re-fits; it does not restore a per-view zoom the user had set.
- **A numeric window/level readout and a mouse-convention hint** — declined (Decision 9).
- **Zoom-under-cursor and double-click reset** — rejected in favor of center-anchored zoom and a visible "Reset view" button (Decisions 4, 5).
- **A GUI test framework / offscreen CI** — the model suite plus manual smoke is the verification (Testing Decisions).

## Further Notes

- **Sequencing (three commits, each green and runnable):** (1) windowing in the pure layer + tests; (2) zoom-affordance swap (slider + zoom-% label + "Reset view", fit-on-load/view-switch); (3) the mouse conventions in `_ImageView`. Order is 1 → 2 → 3: the window params must exist before left-drag can drive them, and the slider before right-drag can sync to it.
- **Relationship to PRD 0010:** this PRD supersedes 0010's `-`/`+`/`Fit` zoom controls and its "wheel == slice, no drag-zoom" note, but **keeps** its `QGraphicsView` + `QGraphicsPixmapItem` pane, its `FastTransformation` (nearest-neighbour) look, and its scrollbars (now demoted to a pan fallback, Decision 6).
- **Guardrail carried forward:** `render_dec_slice` lives in `gui/viewer_model.py` (GUI-side, tk-free) and stays pure; `processing/` is untouched and stays Qt-free.
- **Divide-by-zero guard:** the adapter clamps `wl_width` to a small positive minimum during a drag so a zero-width window never reaches the remap.
