# PRD 0007 — Finish the results-on-disk contract on `results_layout`

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 2 ("finish the results-on-disk contract on `results_layout`"), settled in a grilling session. Executes the follow-up recorded by PRD 0005 (Decision 4, Decision 10, User Story 14).

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

PRD 0005 created `processing/results_layout` as the single home for the **results-on-disk contract** — the ROI-directory naming (`rois` / `rois_{token}` / `rois_{token}_refined`), the ALPS-results CSV naming (`alps_results.csv` / `alps_results_{token}.csv`), and the ALPS column schema with one typed reader (`read_alps_csv -> AlpsTable`). But it deliberately repointed **only the viewer's consumers** onto it (PRD 0005, Decision 4), to keep a GUI refactor away from the scientific-output writers. The recorded consequence:

- The **ALPS column schema** (the per-method LAB/PAS/Both column lists and the `.6f` cell formatting) is still copied — byte-identical today — inside the two engine modules that *write scientific output* (the batch writer and the reanalysis writer). `results_layout` owns a typed *reader* but no *writer*; the duplication that remains is exactly the writer half of the contract.
- The **`rois_{token}` / `alps_results_{token}.csv` naming** and the magic `name[5:]` token strip are still open-coded across the engine — the directory/CSV name builders in the batch, reanalysis, registration, and pipeline modules, and the token parse in the report and pipeline modules — plus one stranded `alps_results.csv` literal left GUI-side by PRD 0006 with a comment marking it as this work's to claim.
- The **four canonical ROI-mask names** (`left_proj`, `right_proj`, `left_assoc`, `right_assoc`) and the on-disk **mask filename pattern** (`{subject}_{roi_name}.nii.gz` written by the registration/reanalysis backends, `*_{roi_name}.nii.gz` globbed by the viewer) live as repeated literals: the registration backend's template-dict keys, the viewer's name tuple and glob, and the writers' inline f-strings. This is a genuine producer/consumer pair with no shared home.

Because nothing owns these conventions centrally, the writers can silently drift from the reader and from each other, and "where is this convention defined" has no single answer. This is live duplication on scientific output — the viewer already reads through the one seam; the writers do not yet write through it.

## Solution

Finish the contract: repoint every remaining engine writer/parser, and the one stranded GUI naming literal, onto `processing/results_layout`, and give the module the **writer twin** of its existing reader so the column schema and cell formatting have one home.

- **Add the writer half.** `results_layout` gains `write_alps_csv` (the symmetric twin of `read_alps_csv`, taking the same `AlpsTable` value both directions) and `alps_columns(method)` (the canonical ordered header). The batch and reanalysis writers convert their result objects into `AlpsTable` and write through this one function; their hand-rolled headers and `.6f` row builders are deleted.
- **Centralise the naming.** The directory/CSV name builders and the token strips are repointed at the existing `roi_dir_name` / `parse_roi_dir` / `alps_csv_name`, killing the `name[5:]` magic and the duplicated `rois_{...}` / `alps_results_{...}.csv` f-strings — including the one `alps_results.csv` literal PRD 0006 parked GUI-side.
- **Centralise the ROI-mask identity.** The canonical four-name set moves into `results_layout` as `ROI_NAMES`, and the mask filename pattern becomes a producer/consumer pair (`roi_mask_name` for writers, `roi_mask_glob` for the viewer) over one private template, so the written name and the glob that finds it cannot drift.

This is **behavior-preserving**: no ALPS CSV changes by a single byte, no ROI directory or mask file is renamed, no observable engine behavior changes. It is the engine-side completion of the line PRD 0003 began (domain constants out of the GUI) and PRD 0005 continued (the on-disk contract, scoped to the viewer). It stays strictly within the established **engine/GUI split** (PRD 0005, Decision 6): `results_layout` speaks ROI *tokens* and machine names only; the GUI keeps its display labels and its display table.

The work lands as a short PRD-then-code sequence: this PRD first, then one branch carrying **four single-concern, behavior-preserving commits**, leaf-first, green at every commit.

## User Stories

1. As a maintainer, I want the ALPS column schema defined in exactly one place, so that the batch writer, the reanalysis writer, and the viewer reader can never drift apart.
2. As a maintainer, I want `results_layout` to own a typed CSV *writer* as well as its reader, so that "what an ALPS results file looks like" is one decision rather than three copies.
3. As a developer, I want the writer to take the same `AlpsTable` value the reader returns, so that "read then write then read" is a provable round-trip and the two halves of the contract are literally the same currency.
4. As a maintainer, I want the `rois_{token}` directory naming built in one place, so that the registration backend, the reanalysis module, and the viewer agree on it by construction.
5. As a maintainer, I want the `alps_results_{token}.csv` filename built in one place, so that the batch writer, the reanalysis writer, and the stranded GUI literal all name results files identically.
6. As a maintainer, I want the `name[5:]` token strip replaced by one `parse_roi_dir`, so that recovering an ROI token from a directory name is done one way and the magic offset disappears.
7. As a maintainer, I want the four canonical ROI-mask names in one home, so that the registration template keys, the viewer's name set, and the writers no longer each carry their own copy.
8. As a maintainer, I want the ROI-mask filename pattern expressed as a producer/consumer pair over one template, so that the name a backend writes and the glob the viewer uses to find it cannot drift.
9. As a scientist relying on prior results, I want every emitted ALPS CSV to stay byte-identical, so that re-running the pipeline after this refactor produces exactly the files I already have.
10. As a scientist, I want my existing ROI directories and mask files to keep their names, so that already-processed output keeps loading in the viewer and the reanalysis/report tooling.
11. As a reviewer, I want the work in four single-concern, leaf-first commits, so that the pure additions, the low-risk repoints, and the scientific-output writer change can be read and reverted independently.
12. As a reviewer, I want a byte-level safety net landed *before* the writer change and green on the current code, so that "the file did not change" is proven against what ships today, not merely against the refactor's own self-consistency.
13. As a maintainer, I want the writer to emit only the suffixed ALPS columns while the reader keeps resolving the legacy no-suffix columns, so that old result files still read but the written format is unchanged.
14. As a maintainer, I want the backward-compatible bare-`rois` / `alps_results.csv` write path preserved, so that the single-file fallback the batch writer already has keeps working through the new naming helper.
15. As a maintainer, I want the quality-report CSV schema untouched, so that this dedup of the ALPS contract does not perturb the separate quality-report output.
16. As a future contributor, I want `results_layout` to remain a dependency-free stdlib leaf, so that adding the writer cannot introduce an import cycle and the module still imports nothing from the package.
17. As a future contributor, I want the writer to do file I/O only — no directory creation, no logging, no error-swallowing — so that the leaf stays pure and each caller keeps owning its own logging and failure handling.
18. As a maintainer, I want the GUI display table left to its owner, so that the engine CSV schema and the GUI's display columns stay separate as the engine/GUI split requires.
19. As a developer, I want each writer's mapping from its own result object onto `AlpsRow` pinned by a test, so that a wrong field mapping (e.g. the bilateral/combined or error-message rename) surfaces as a byte diff rather than a silent corruption.
20. As a maintainer, I want the unreachable defensive fallbacks in the token parse to adopt the cleaner shared semantics with a note, so that preserving a quirk no input can reach does not re-import the ugliness this work removes.
21. As a future contributor, I want the writer twin available as the obvious sink for any later CSV writer (such as a results-panel export), so that new writers join the contract instead of starting a sixth copy.

## Implementation Decisions

### 1. `results_layout` gains the writer twin of its reader

`results_layout` adds a typed writer alongside `read_alps_csv`. The reader already returns an `AlpsTable` (a detected method plus per-subject `AlpsRow` values); the writer is its inverse and takes the **same `AlpsTable`**:

```
write_alps_csv(path, table: AlpsTable) -> None
alps_columns(method: str) -> list[str]   # the canonical ordered header for a method
```

`alps_columns` is the single source of the header (`Filename`, the per-method LAB/PAS columns, `Status`, `Error`); `write_alps_csv` formats each `AlpsRow` against it (`.6f`, missing value → empty string) using the stdlib CSV writer so line terminators match today's output exactly.

- **Rejected — a schema accessor only, leaving each writer's `.6f` row-building in place:** the row formatting is half of what drifts; centralising the header but not the cell rule leaves the writers able to diverge on precision and missing-value handling. The whole point of "finish the contract" is that the writer half stops being copied.
- **Rejected — naming-only, no writer at all:** that leaves the column schema duplicated in the two scientific-output writers, which is the most important duplication to close.

### 2. `AlpsTable` is the single currency in both directions

The writer takes the value type the reader produces, so the contract test is a literal round-trip — `read_alps_csv(write_alps_csv(table)) == table` (to `.6f` precision). Callers assemble an `AlpsTable` from their own result objects before writing; the small per-caller mapping (the batch/reanalysis `bilateral` field onto the table's `combined`, the `error_message` field onto `error`, and the batch per-shape path reading its shape-keyed dict) lives at the call site, where that adaptation belongs.

- **Rejected — a lighter `(method, list[rows])` signature:** marginally less ceremony at the call site, but the reader and writer would then speak different shapes, weakening the round-trip property to a hand-reassembled comparison. One currency type, provably invertible, is worth a dict-comprehension at each writer.
- **Rejected — the writer taking the engine's own result objects directly:** that would force `results_layout` to import the result dataclasses, breaking the dependency-free-leaf property (Decision 6). The conversion stays caller-side; the leaf keeps importing nothing from the package.

### 3. The writer is a pure leaf: file I/O only

`write_alps_csv` opens the file, writes the header and rows, and nothing else. Directory creation, progress/logging callbacks, and `OSError` handling stay in the callers, exactly as the reader does no logging today. The module's stdlib-only, no-package-imports character is preserved.

- **Rejected — folding logging / `makedirs` / error handling into the writer:** that would drag caller concerns and a logging seam into a value-in/value-out leaf, and each caller already has its own logging idiom and failure policy to keep.

### 4. Centralise directory/CSV naming and the token parse

The open-coded `rois_{...}` and `alps_results_{...}.csv` f-strings and the `name[5:]` strips are repointed at the existing `roi_dir_name(token, refined)`, `alps_csv_name(token, refined)`, and `parse_roi_dir(name)`. This covers the batch CSV-name builder, the reanalysis directory and CSV-name builders, the registration backend's directory builder, the report module's shape discovery parse, the pipeline module's two shape-name strips, and the one `alps_results.csv` literal PRD 0006 left GUI-side and annotated as this work's to claim. Call sites reference the helpers module-qualified to avoid shadowing where a local of the same name exists.

- **Rejected — leaving the naming open-coded because it is "only f-strings":** the f-strings and the `[5:]` offset are precisely the magic that drifts and that PRD 0005 named as a follow-up; the naming home already exists and is tested.

### 5. Behavior-preserving on reachable paths; unreachable fallbacks adopt the cleaner semantics

Two of the token strips carry an `else` fallback (`else "default"` and `else <name unchanged>`) for inputs that the producing backend never emits — those directory names always originate as `rois_{shape}` / `rois_{shape}_refined`. The rewrites use the clean `parse_roi_dir(x) or <fallback>` form. On every input that can actually occur the result is identical; on the unreachable input the semantics shift (and become slightly more correct), and each site is pinned with a one-line "unreachable; backend only writes `rois_*`" comment.

- **Rejected — strict byte-for-byte on all paths, including the dead branches:** contorting the new code to preserve a quirk that no input can reach would re-import the ugliness this work exists to remove, to protect behavior nothing exercises.

### 6. Lift the canonical ROI-mask name set into `results_layout`

The four canonical names move into `results_layout` as `ROI_NAMES` (keeping the existing order; order is functionally inert — every consumer keys by name or combines masks). The two places that encode the *set as a unit* repoint onto it: the viewer's loader imports it, and the registration backend builds its template-path dict from it. Individual in-context references (the registration backend's four prose log lines, the report module's abbreviation map, docstrings and comments) are left alone — they are not copies of the set, and rebuilding the prose from a machine name would require a display-name map, which is GUI text the engine deliberately does not carry.

- **Rejected — keeping the name set viewer-side (PRD 0005, Decision 10):** Decision 10 deferred the set to *this* follow-up precisely because it is a real cross-codebase duplication (the registration keys, the viewer set, the writers); with the writers now in scope, its home is the contract leaf. Reversing Decision 10 here is the recorded plan, not a contradiction of it.
- **Rejected — routing every name reference through the constant:** the prose log lines and the abbreviation map are different structures; forcing them through the tuple is contortion for no dedup.

### 7. Own the ROI-mask filename pattern as a producer/consumer pair

The on-disk mask filename becomes a pair over one private template, so the written name and the glob that finds it share a single source and cannot drift:

```
roi_mask_name(subject, roi_name) -> str   # producers: "{subject}_{roi_name}.nii.gz"
roi_mask_glob(roi_name) -> str            # viewer:    "*_{roi_name}.nii.gz"
```

The registration and reanalysis backends produce mask names through `roi_mask_name`; the viewer globs through `roi_mask_glob`. The two-function shape mirrors the module's existing build/recover pairing for directory names.

- **Rejected — a single function with a defaulted wildcard subject:** one function returning a glob when the subject is omitted conflates "produce a concrete name" with "build a match pattern"; two intent-named functions over one template are clearer and equally drift-proof.
- **Rejected — pulling the registration `_transformed` intermediate into the contract:** that intermediate is read back only within the registration backend and never by the viewer, so it is not results-on-disk; it stays an inline name.

### 8. The GUI display table stays with its owner

The contract writer is engine-side and speaks the CSV schema (the full `Left Hemisphere ALPS-LAB`-style column names, `.6f`); the GUI's batch-results display table (its short display labels, `.4f`, no error column) stays where PRD 0006 deepened it. They are deliberately different schemas, not duplicates, and the engine/GUI split is load-bearing.

- **Rejected — making `alps_columns` the single source for both the CSV and the display table:** that would couple the engine's on-disk schema to GUI presentation text, breaking the split PRD 0005 (Decision 6) established. The small "which metrics exist per method" overlap is the acceptable cost of the split. The writer twin is, however, the intended sink for any *future* CSV writer (such as a results-panel export), so new file writers join the contract rather than starting a sixth copy.

## Testing Decisions

**What makes a good test here:** it asserts the *external behavior* of the contract — given this `AlpsTable`, these exact CSV bytes; given these CSV bytes, this `AlpsTable`; given this token, this directory/CSV/mask name — never an implementation detail of a writer. The byte-identity tests assert the emitted file content, not the private header/row helpers (which are being deleted).

**The seams (highest first):**

1. **The `results_layout` pure-function boundary** — value-in/value-out, no fakes and no injection. The new `write_alps_csv`, `alps_columns`, `roi_mask_name`, and `roi_mask_glob` join the existing `read_alps_csv`, `detect_method`, and naming helpers at the seam `tests/test_results_layout.py` already exercises. This is where the new behavior concentrates, so it is where most new tests live, including the `read(write(table)) == table` round-trip.
2. **The existing writer entry points** — the reanalysis writer (a free function) and the batch writer's per-shape and single-file paths, driven over constructed result objects for the LAB, PAS, and Both methods, asserting the emitted file equals a hand-authored expected byte string. Green on the current code first (proving the literal equals today's bytes), then held byte-for-byte across the refactor (the entry points survive and delegate inward). No new seam is introduced.

**Modules tested:** `results_layout` (the new surface and the round-trip), the batch writer's file output, and the reanalysis writer's file output. The naming/parse repoints and the ROI-name/mask-pattern repoints are covered transitively by the existing `results_layout` naming tests plus the byte-identity tests; the viewer's continued ability to find masks is covered by the existing viewer-model tests.

**Prior art:** the value-in/value-out, class-grouped, tools-free style of `tests/test_results_layout.py` and `tests/test_alps_calculation.py` for the pure functions; `tests/test_reanalysis_seam.py` for driving the reanalysis writer — with the difference that its writer-to-no-op monkeypatch is replaced here by a real byte assertion, since the emitted file is exactly what this PRD must pin.

**Coverage boundary:** as in PRDs 0004–0006, GUI adapter wiring (the viewer and `app.py` paths that merely consume the repointed helpers) is verified by manual smoke, not by instantiating a display.

## Out of Scope

- **Any change to scientific output.** No ALPS CSV changes by a byte; no ROI directory or mask file is renamed; no column is added, removed, reordered, or reprecised. This is purely behavior-preserving.
- **The legacy no-suffix ALPS columns as a *written* format.** The reader keeps resolving them for old files; the writer emits only the suffixed columns (it never emitted the legacy ones).
- **The quality-report CSV schema.** The report module adopts only the token parse for shape discovery; its quality columns are untouched, and it continues to discover `rois_*` directories only (it still does not cover the bare-`rois` default — closing that is a behavior change and a separate ticket).
- **The GUI batch-results display table** (`build_batch_results_table` and its row builder): owned and already deepened by PRD 0006; it stays a separate display schema.
- **The registration `_transformed` intermediate filename:** a within-backend intermediate, not part of the results-on-disk contract.
- **Docstring and comment references to the four ROI names** (in the ALPS calculation and state modules): left as-is; they are prose, not copies of the set.
- **Any results-panel CSV export refactor:** the writer twin is designed to be its future sink, but wiring it is Candidate 1's concern, not this PRD's.

## Further Notes

- **Sequencing:** PRD first, then one branch with four single-concern commits, leaf-first, green at each: (1) grow the leaf — add `write_alps_csv`, `alps_columns`, `ROI_NAMES`, `roi_mask_name`, `roi_mask_glob` with their unit and round-trip tests, a pure addition with no consumers; (2) add the byte-level safety net — characterization tests pinning today's batch and reanalysis CSV bytes, green on current code; (3) repoint the identifier consumers — the naming/parse builders, the GUI `alps_results.csv` literal, and the ROI-name/mask-pattern uses, with no CSV-content change; (4) repoint the two CSV writers onto `write_alps_csv`, deleting their hand-rolled headers and row builders, with all scientific-output risk quarantined in this commit behind the net from commit 2.
- **Relationship to prior PRDs:** this completes the on-disk-contract line PRD 0003 began and PRD 0005 scoped to the viewer; it is the explicit discharge of PRD 0005's Decision 4 (repoint the writers), Decision 10 (lift the ROI-name set), and User Story 14. It honours PRD 0005's Decision 6 engine/GUI split throughout.
- **Domain model:** `CONTEXT.md` should record the writer twin (`write_alps_csv`/`alps_columns`) as part of the results-on-disk contract vocabulary, alongside the `roi_mask_name`/`roi_mask_glob` pair and `ROI_NAMES` as the canonical ROI-mask identity, mirroring how PRD 0005 recorded the reader and naming terms.
- The grilling-session decisions behind this PRD are the source for the Implementation Decisions above; the writer-twin/`AlpsTable`-currency choice, the reachable-paths behavior standard, the mask-pattern lift, and the char-first testing discipline were each settled there.
