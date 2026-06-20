# PRD 0003 — Lift the domain constants out of the GUI

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 3 ("Lift the domain constants out of the GUI"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

The `processing` package — the distributable analysis engine — reaches *up* into the `gui` package to read its science constants. Four processing modules begin with:

```
from ..gui import config
```

They are `processing/alps_calculation.py`, `processing/validators.py`, `processing/batch.py`, and `processing/state.py`. These four imports are the **entire** `processing → gui` dependency; there is no other.

The constants they pull down are not GUI concerns — they are domain facts: the MRtrix3 tensor-component indices (`TENSOR_DXX/DYY/DZZ_INDEX`), the FA threshold that filters CSF voxels (`FA_THRESHOLD`), the pipeline defaults (`DEFAULT_PE_DIRECTION`, `DEFAULT_READOUT_TIME`, `DEFAULT_RPE_SCHEME`), and a validation range (`READOUT_TIME_RANGE`). They happen to live in `gui/config.py` next to window dimensions, colours and tooltips.

Consequences:

- The dependency arrow points the wrong way. The engine cannot be reasoned about — or shipped — without dragging the `gui` subpackage along. Importing `dti_alps.processing` today executes `gui/__init__.py` and leaves `dti_alps.gui` resident in `sys.modules`.
- "Distributable as a library" stalls on this: the science layer literally imports a package named `gui`.
- The science constants have no single home; they are mixed into a GUI grab-bag.

(The GUI does *not* drag in Tkinter at this point — `gui/__init__.py` lazy-imports `app`/`viewer` inside functions — so the leak today is a *package* dependency, not a Tkinter one. It is still a leak: the engine depends on the GUI package.)

## Solution

Move the domain constants the engine consumes into a new processing-owned, dependency-free leaf module, `processing/constants.py`, and repoint the four processing modules at it. `gui/config.py` keeps everything genuinely GUI (window sizes, colours, tooltips, the CLI/widget option tables) and **re-exports** the six relocated names so the GUI's existing `config.X` references keep resolving and `gui/app.py` is untouched.

After this change the dependency arrow points one way only: `gui → processing`, never the reverse. The engine imports with no reference to `gui`. A subprocess import-guard test pins that invariant so it cannot silently regress.

This is a behavior-preserving, single-concern refactor: it relocates constants and adds a guard test. It changes no scientific output, no defaults, and no public return contract. It is the natural completion of PRD 0002 Decision 9, which deliberately *relocated* (rather than removed) the `gui.config` coupling in the ALPS path and named this candidate as the place to finish the job.

The work lands as **two commits**: (1) delete a dead, already-broken validation function that the move would otherwise drag into the light; (2) the relocation + re-export + guard test.

## User Stories

1. As a maintainer packaging the engine, I want `dti_alps.processing` to import without pulling in `dti_alps.gui`, so that the analysis core can ship and be reasoned about as a standalone library.
2. As a maintainer, I want the four `from ..gui import config` imports in `processing` gone, so that no module in the engine depends on the GUI package.
3. As a maintainer, I want a test that fails the day someone reintroduces a `processing → gui` import (directly or transitively), so that the dependency direction is enforced, not just hoped for.
4. As a developer, I want the science constants (tensor indices, FA threshold, pipeline defaults, the readout range) to live in one processing-owned module, so that a domain value is found and changed in one obvious place.
5. As a GUI developer, I want `gui/config.py` to keep exposing the same names it exposes today (`config.FA_THRESHOLD`, `config.DEFAULT_PE_DIRECTION`, …), so that none of the ~2700-line `app.py` needs editing.
6. As a GUI developer, I want `gui/config.py` to *import* the domain values it echoes from the engine, so that the GUI and the engine cannot disagree about the FA threshold or the tensor-index convention.
7. As a reviewer, I want each commit to tell exactly one story, so that the dead-code removal and the constants relocation can be read and reverted independently.
8. As a maintainer, I want the never-called, already-broken `validate_pipeline_state` removed rather than carried across, so that the move does not preserve a landmine or invent phantom constants to feed it.
9. As a developer running the GUI, I want every widget default, dropdown, and validation to behave exactly as before, so that this refactor is invisible to users.
10. As a developer running the pipeline or reanalysis, I want ALPS results, FA filtering, and pipeline defaults unchanged, so that prior outputs remain reproducible.
11. As a future contributor, I want the new module to be a dependency-free leaf, so that importing it can never create a cycle.
12. As a future contributor, I want the relocated constants to keep their explanatory comments (e.g. the MRtrix3 `D11/D22/D33` ordering note), so that the *why* travels with the value.
13. As a maintainer, I want the constants the GUI alone uses (PE/RPE/ALPS-method dropdown lists, ROI radius range) and the tool/CLI option tables to stay in `gui/config.py` for now, so that this PR moves only what the engine actually consumes.
14. As a maintainer, I want the known follow-ups (the duplicated `0.2` FA default in reanalysis, the GUI-only domain values, the domain glossary) recorded, so that they are discoverable and not silently dropped.

## Implementation Decisions

### 1. The cut line: move only the constants the engine consumes

Exactly six names move into `processing/constants.py`: `TENSOR_DXX_INDEX`, `TENSOR_DYY_INDEX`, `TENSOR_DZZ_INDEX`, `FA_THRESHOLD`, `DEFAULT_PE_DIRECTION`, `DEFAULT_READOUT_TIME`, `DEFAULT_RPE_SCHEME`, and `READOUT_TIME_RANGE`. (Eight symbols; the three tensor indices are counted as the `TENSOR_*` group.) This is precisely the set referenced by the four leaking modules.

- **Rejected — also move the GUI-only domain values** (`PE_DIRECTIONS`, `RPE_SCHEMES`, `ALPS_METHODS`, `ROI_REFINEMENT_OPTIONS`, `ROI_SPHERE_RADIUS_RANGE`, the `DEFAULT_*` method/refinement/radius values): these have **zero** processing consumers today. Moving them now is speculative tidying that widens the diff without advancing the one goal (engine importable without GUI). Recorded as a follow-up.
- **Rejected — also move the tool/CLI option tables** (`DWIDENOISE_OPTIONS`, `FLIRT_OPTIONS`, `FNIRT_OPTIONS`, `SYNB0_EDDY_OPTIONS`, …): despite naming external tools, these tuples encode *widget* hints (`"choice"`, `"file"`, `"flag"`) and are consumed only by the GUI form-builder. They are GUI-shaped. Leave them in `gui/config.py`.

### 2. New module: `processing/constants.py`, a dependency-free leaf

The relocated constants live in a single flat module with no imports of its own. Each value keeps its existing explanatory comment.

- **Rejected — `processing/domain.py`:** "constants" is the more legible label for the content and matches the architecture-review artifact, so the code and the review agree.
- **Rejected — split into themed modules:** over-engineering for eight symbols.

### 3. `processing/constants.py` is NOT re-exported from `processing/__init__.py`

The four internal modules import it directly (`from .constants import …`); `gui/config.py` imports it by path. It is deliberately kept off the package's public surface.

- **Rejected — add `from .constants import …` to `processing/__init__.py`:** that would invite `from dti_alps.processing import FA_THRESHOLD` and widen the public API for no current need. A curated public surface, if ever wanted, is a separate deliberate decision.

### 4. The four processing modules use named imports

Each module replaces `from ..gui import config` with `from .constants import <names it uses>` and drops the `config.` prefix at each use site (`config.FA_THRESHOLD` → `FA_THRESHOLD`). The live per-module usage is small: three `TENSOR_*` indices in `alps_calculation.py`; one `READOUT_TIME_RANGE` in `validators.py` (after Decision 6); one `DEFAULT_READOUT_TIME` in `batch.py`; a handful of dataclass field defaults in `state.py`.

- **Rejected — alias the module as `config`** (`from . import constants as config`, keeping every `config.X` byte-for-byte): the smallest diff, but it carries the literal word `config` into the engine, which reads as a half-finished move and undercuts the readability win.
- **Rejected — plain module import** (`from . import constants`, use `constants.FA_THRESHOLD`): touches the same use sites as named imports with no added clarity at this scale.

### 5. `gui/config.py` re-exports the six relocated names

`gui/config.py` deletes the six definitions and adds a single `from ..processing.constants import FA_THRESHOLD, TENSOR_DXX_INDEX, TENSOR_DYY_INDEX, TENSOR_DZZ_INDEX, DEFAULT_PE_DIRECTION, DEFAULT_READOUT_TIME, DEFAULT_RPE_SCHEME, READOUT_TIME_RANGE`. All existing `config.X` references in `gui/app.py` (e.g. `config.FA_THRESHOLD` at the FA-threshold widget, `config.DEFAULT_PE_DIRECTION`, `config.TENSOR_*`) continue to resolve unchanged.

- **Rejected — rewrite each `config.X` site in `app.py` to import from `processing.constants` directly and drop the re-export:** scatters the edit across a ~2700-line file for no gain and risks missing a site. The re-export line *is* the `gui → processing` dependency arrow stated once, in the right direction.
- **Note:** the re-export means `gui/config.py` still *appears* to own those names to a casual reader; this is an accepted trade for a near-zero-risk diff. The guard test (Decision 7) makes the true ownership unambiguous in CI.

### 6. Delete the dead, broken `validate_pipeline_state` first (commit 1)

`validators.py::validate_pipeline_state` is never called anywhere in the repo. It references `config.FA_THRESH_RANGE` and `config.ORIENT_THRESH_RANGE` (which exist in no module) and `state.fa_thresh` / `state.orient_thresh` (which do not exist on `PipelineState` — the field is `fa_threshold`). It would `AttributeError` on first reach if ever invoked. Removing it leaves `validators.py`'s only live constant use as `READOUT_TIME_RANGE` (in `validate_readout_time`).

- **Rejected — leave it untouched and just repoint the import:** knowingly carries a landmine across the refactor; the new `constants.py` would not define the phantom ranges, so the function stays broken-if-called for no reason.
- **Rejected — "fix" it** (define the phantom ranges, rename the fields): invents validation behavior nobody specified — scope creep.
- **Sequencing:** done as its **own precursor commit** so the relocation commit tells a single story (dead-code removal ≠ constants move).

### 7. A subprocess import-guard test pins the invariant (commit 2)

A new test spawns a fresh interpreter that imports `dti_alps.processing` and asserts `dti_alps.gui` is absent from `sys.modules`. It is red against today's code and green after the relocation, and it catches *transitive* re-introductions, not just literal `from ..gui` strings.

- **Rejected — a static grep test** (assert no `from ..gui` / `import …gui` text in `processing/*.py`): misses transitive leaks and is sensitive to import phrasing; redundant once the import guard exists.
- **Rejected — manual verification only:** leaves nothing to stop regression.
- **Why a subprocess:** running in-process is unreliable because another test (or a conftest) may import `gui` first and pollute `sys.modules`. A child interpreter gives a clean module table every run. The check needs no external binaries, consistent with the PRD 0001 CI goal.
- The guard test lands **in the relocation commit** (not a separate red commit first), so the suite is green at every commit — appropriate for a pure relocation rather than a behavior change.

### 8. No behavior change

Constant *values* are identical (`FA_THRESHOLD = 0.2`, the tensor indices `0/1/2`, the defaults, the `(0.001, 1.0)` readout range). GUI widget defaults, dropdowns, validation, and all pipeline/reanalysis outputs are byte-for-byte unchanged. The only observable difference is the module a constant is defined in.

## Testing Decisions

**What makes a good test here:** it asserts *external, architectural behavior* — "the engine package does not import the GUI package" — at a process boundary, never an implementation detail (it does not name which module holds which constant, nor assert on import internals). It must remain green regardless of how the constants are organized inside `processing`, and go red only if a real `processing → gui` dependency returns.

**The seam:** the **package import boundary** of `dti_alps.processing`, exercised in a child interpreter. This is the single, highest seam that expresses the invariant the refactor exists to establish. No new code seam, fake, or injection point is introduced — the change is a relocation, and the existing `*_seam.py` fake-injection suites are *not* the model for it.

**Modules covered:** the guard observes `dti_alps.processing` as a whole (and therefore the four repointed modules transitively). Equivalence of the relocated values is already covered indirectly by the existing pipeline/discovery/ALPS suites, which would break if a value changed; this PRD adds no per-constant value assertions.

**Test case (new file):**
- **Engine independence** — in a subprocess, `import dti_alps.processing`; assert the process exits 0 and that `dti_alps.gui` is not in `sys.modules`. Optionally also assert importing the four submodules directly does not resident `dti_alps.gui`.

**Prior art:** this is a new *kind* of test for this repo — there is no existing import-guard. Stylistically it follows the plain pure-unit-test suites (`tests/test_discovery.py`, `tests/test_alps_calculation.py`): a single focused module, no external tools. It is explicitly *not* modelled on `tests/fakes.py` / the `*_seam.py` suites, which fake an execution seam this change does not have.

## Out of Scope

- **The GUI-only domain values** (`PE_DIRECTIONS`, `RPE_SCHEMES`, `ALPS_METHODS`, `ROI_REFINEMENT_OPTIONS`, `ROI_SPHERE_RADIUS_RANGE`, and the corresponding `DEFAULT_*`): they have no processing consumer today and stay in `gui/config.py`. Relocating them is a follow-up to be done only when a non-GUI caller (e.g. a CLI) needs them.
- **The tool/CLI option tables** (`DWIDENOISE_OPTIONS`, `FLIRT_OPTIONS`, `FNIRT_OPTIONS`, `SYNB0_EDDY_OPTIONS`, the `*_CHOICES` lists, `OUTPUT_FILE_OPTIONS`, `PIPELINE_STAGES`): GUI-shaped, stay put.
- **De-duplicating the hardcoded `fa_threshold: float = 0.2` default in `reanalysis.py`** against `FA_THRESHOLD`: a separate "one home for the FA threshold" concern; `reanalysis.py` has no `gui` coupling and is not touched here. Recorded as a follow-up.
- **Seeding `CONTEXT.md` / a domain glossary** from the relocated constants: a documentation concern, not part of severing the dependency.
- **Any change to constant values, defaults, the ALPS formula, validation logic, or GUI behavior.**
- **Rewriting `app.py` call sites** — avoided by the re-export (Decision 5).

## Further Notes

- **Sequencing:** commit 1 (remove dead `validate_pipeline_state`) → commit 2 (create `processing/constants.py`, repoint the four modules to named imports, re-export the six names from `gui/config.py`, add the subprocess import-guard test). Every commit leaves the suite green.
- **Relationship to PRD 0002:** this completes PRD 0002 Decision 9, which relocated the `gui.config` import within the ALPS path and explicitly deferred its *elimination* to this candidate. After this PRD, `alps_calculation.py`'s loader reads the tensor indices from `processing.constants` instead of `gui.config`.
- **Verification of the leak (pre-state):** `python -c "import dti_alps.processing, sys; print('dti_alps.gui' in sys.modules)"` prints `True` today and must print `False` after this change.
- The grilling-session decisions behind this PRD are recorded in agent memory (`candidate3-domain-constants-out-of-gui-design`).
