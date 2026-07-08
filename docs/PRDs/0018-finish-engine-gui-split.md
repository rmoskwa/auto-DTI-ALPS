# PRD 0018 — Finish the engine/GUI split: the last stranded constant + an honest guard

Status: Accepted · Date: 2026-07-08 · Grilled: 2026-07-08 · Source: Discharges the architecture review's Candidate 5 ("Finish the engine/GUI split"). The Tkinter→PySide6 port is complete and the split holds; this closes the one true seam leak the review found and makes the guard that protects the seam assert the property it names.

This PRD also serves as the ADR of record for the two decisions below and for the scope it deliberately rejects.

> **Process note.** Taken through a grilling session (2026-07-08) using the domain-modeling skill. The session **materially narrowed** the review's Candidate 5 from an eight-item "finishing" grab-bag to the two items that actually concern the `gui → processing` arrow. The verification and the reasoning for each cut are recorded under *Scope — what this PRD deliberately excludes*, so the narrowing is auditable rather than silent.

---

## Problem Statement

The engine/GUI split is clean in the arrow direction — `processing/` is tk-free and does not import `dti_alps.gui` (PRD 0003 lifted the domain constants down; `tests/test_engine_independence.py` pins the arrow). Two gaps remain, both verified against live code.

**1. One domain constant stranded on the GUI side of the seam — a real leak.** The ROI sphere-radius *validation bound* lives in `gui/config.py`:

```python
# gui/config.py:53
ROI_SPHERE_RADIUS_RANGE = (1.0, 4.0)  # Range for ROI sphere radius (mm)
```

But the engine's own CLI (`python -m dti_alps --reanalyze … --sphere …`) validates radii and **cannot import GUI config** without violating the split, so it re-hardcodes the same numbers and pins them with a comment:

```python
# __main__.py:45-47
# Sphere radius range (must match config.ROI_SPHERE_RADIUS_RANGE)
SPHERE_RADIUS_MIN = 1.0
SPHERE_RADIUS_MAX = 4.0
```

This is exactly the leak the PRD 0003 constants-extraction was meant to remove — a domain fact the engine consumes, kept on the GUI side, forced to duplicate-by-comment because the arrow can't be crossed. It is the one constant the extraction missed. `READOUT_TIME_RANGE`, its structural twin (a `(min, max)` validation range), already lives in `processing/constants.py`.

**2. The import-guard asserts a proxy, not the property.** `test_engine_independence` asserts, in a fresh child interpreter, that importing each engine module leaves `dti_alps.gui` **not** resident in `sys.modules`. But the property the split actually guarantees — the reason the engine is distributable — is that the engine runs **with no GUI toolkit installed** (e.g. batch/reanalysis on a display-less cluster). A processing module could `import PySide6` *directly*, without ever importing `dti_alps.gui`; the current test would stay green while the real "runs headless" guarantee broke. The guard asserts the likely *path* of a regression, not the *property* it protects.

## Solution

**Decision 1 — Move `ROI_SPHERE_RADIUS_RANGE` into `processing/constants.py`; re-export from GUI; consume by unpacking in the CLI.**

- `processing/constants.py` gains `ROI_SPHERE_RADIUS_RANGE = (1.0, 4.0)`, beside `READOUT_TIME_RANGE`, as the single source of truth.
- `gui/config.py` **re-exports** it in the existing `from ..processing.constants import (...)` block and deletes the local literal — GUI code keeps saying `config.ROI_SPHERE_RADIUS_RANGE` unchanged (the same re-export pattern already used for `FA_THRESHOLD`, `READOUT_TIME_RANGE`, the tensor indices).
- `__main__.py` imports it and unpacks: `SPHERE_RADIUS_MIN, SPHERE_RADIUS_MAX = ROI_SPHERE_RADIUS_RANGE`. The readable scalar names stay in the validation code; the duplicated literals and the "must match" comment both go. The engine now reads the bound from the engine, crossing no arrow.

*Rejected alternative — also move `DEFAULT_SPHERE_RADIUS` (3.0) into `constants.py` to "consolidate the sphere-radius domain."* The grilling session opened this and closed it: `DEFAULT_SPHERE_RADIUS` is not a member of "the sphere-radius domain" beside the range — in the code it is one half of **the default-ROI collapse rule** (`shape_token`: the 3.0 mm sphere collapses to the bare `rois` token), paired with `DEFAULT_ROI_TOKEN` and doctested against it. It already lives engine-side in `results_layout.py`, so it is **not a leak**; moving it would fix no seam and would split a value from the doctested rule that consumes it. Value-next-to-its-rule wins. Only the *range* — a pure validation bound, genuinely duplicated across the seam — moves.

**Decision 2 — Widen the guard from proxy to property.**

`test_engine_independence` keeps its child-interpreter mechanism and its `ENGINE_MODULES` list; it additionally asserts that importing each engine module leaves **no GUI toolkit** resident — `PySide6` and `tkinter`, not only `dti_alps.gui`. The test now pins the property it names (a headless engine), and goes red on a *direct* toolkit import in `processing`, not just on a `from ..gui import …`. It still deliberately does **not** name which module holds which constant, so it stays green however `processing` reorganizes its internals.

The work lands **PRD-first, then two single-concern commits**:

1. `docs: PRD 0018 + CONTEXT.md` — this document and the one-line sharpening of the engine-independence invariant in the glossary.
2. `refactor: move ROI_SPHERE_RADIUS_RANGE into the engine` — **commit A**, Decision 1: the constant into `processing/constants.py`, re-exported from `gui/config.py`, unpacked in `__main__.py`, "must match" comment deleted.
3. `test: assert the engine imports no GUI toolkit, not just no gui package` — **commit B**, Decision 2: the widened assertion + its docstring update.

No ADR beyond this PRD: both moves *finish* the established engine/GUI-split contract rather than introducing a new, hard-to-reverse shape.

## Scope — what this PRD deliberately excludes

Candidate 5 bundled eight items under one title. Verified against live code on 2026-07-08, six are not part of "finish the split":

- **`form_model` docstring "when app.py is later ported"** — *already fixed*. The docstring now reads "the Tk-to-Qt port (PRD 0013) reused it unchanged." No work.
- **Orphaned `processing/synb0/__pycache__`** — *not code*. Stale `.pyc` bytecode from the PRD 0008 synB0 cut, untracked by git. Removed as housekeeping (`git clean`), not in any commit.
- **`register_backend` (zero callers)** — *belongs to Candidate 3*. It is the never-called hook that "Collapse the single-adapter registration seam" proposes to delete along with the ABC, registry, and factory. Deleting it here would pre-empt and fragment that card's scope. Left for Candidate 3.
- **`NIFTI_FILETYPES` / `JSON_FILETYPES` still in Tk `(label, patterns)` shape** and **`--gui` flag that only works by fallthrough** — *Qt-port polish, not the engine/GUI arrow*. Real but off-theme; spun to a "port-polish" backlog, not mixed into a seam PRD.
- **Dead `_placeholder` (`app.py:444`)** — a standalone dead method; goes to the same port-polish/dead-code backlog.

The narrowing is the point: the card's title is earned by Decisions 1 and 2 only. The rest are either done, not code, another card's, or a different concern.
