# PRD 0008 — Cut the dormant internal synB0 backend

Status: Accepted · Date: 2026-06-20 · Source: Architecture review Candidate 5 ("settle the fate of the internal synB0 backend"), settled in a grilling session. Resolves the keep/wire/cut decision the review demanded *before* the PySide6 port, so the port does not silently inherit an unreachable feature.

This PRD also serves as the ADR of record for the decision below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

The repository carries two unrelated things that both answer to the name "synB0", and only one of them is alive.

- **The live external-outputs route (kept).** The shipped feature: the user runs synB0-DISCO *externally* (Docker/Singularity), points the GUI at the OUTPUTS directory (`use_synb0` + `synb0_output_dir`), and the pipeline consumes the pre-computed topup outputs and runs FSL `eddy` via `PipelineRunner.run_eddy_with_synb0`. It needs only FSL `eddy`/`topup`. This route is wired end-to-end through the GUI, `state`, `batch`, `validators.validate_synb0_output_dir`, and `pipeline`, and is exercised by `tests/test_app_logic.py`.
- **The internal `Synb0Backend` (dead).** A full in-process orchestration of the synB0 pipeline — `dti_alps/processing/synb0/` (1,832 LOC across `backend.py` 907, `commands.py` 403, `inference.py` 328, `model.py` 162, `__init__.py` 32), plus `dti_alps/data/synb0/` (393 MB of git-LFS assets: five 74 MB `.pth` ensemble weights, MNI templates, and `synb0.cnf`).

The internal backend has **zero callers outside `tests/`**. It is not re-exported from `processing/__init__.py`, not imported by `pipeline.py`, and not referenced anywhere in `gui/`. The only construction of `Synb0Backend` / call to `run_topup_eddy` lives in `tests/test_synb0_seam.py`, whose own docstring states "synB0 is dormant in production." It cannot even run in a default install: `inference.py` requires `torch`, which is **not a declared dependency**, and `__init__.py` uses a PEP 562 lazy import specifically so the package stays importable *without* torch — i.e. it is deliberately structured to remain dormant.

This is the residue of an abandoned product direction. Git history shows exactly two commits ever touched `processing/synb0/`: `397982e` ("initial synb0 integration") — the full-integration attempt — and `f960b87`, a mechanical ToolRunner-seam refactor that swept it along with everything else. Full in-process integration was dropped in favour of the external-outputs route; the internal backend was never wired to the GUI/pipeline and never extended. It is the single largest pool of stale code in the tree, and it drags a documentation tail with it: both `CLAUDE.md` and `README.md` instruct users to optionally install **FreeSurfer, ANTs, Convert3D**, and FSL `epi_reg`/`fslmerge` — tools that, after this backend is gone, are invoked by **no live code** (verified: live brain extraction uses MRtrix3 `dwi2mask`, not BET2; live distortion correction is `dwifslpreproc` or external-outputs + `eddy`).

Carrying this into the PySide6 port would migrate an unreachable feature and keep telling users to install heavy toolchains for it. The review asked for a deliberate keep/wire/cut call; this PRD makes it.

## Solution

**Cut the internal backend.** It passes the deletion test cleanly — removing it requires no compensating logic anywhere in live code — and the live external-outputs route is left 100% intact.

The cut is one atomic, behavior-preserving concern:

- **Delete `dti_alps/processing/synb0/`** (the four modules + `__init__.py`, 1,832 LOC).
- **Delete `dti_alps/data/synb0/`** (the five `.pth` weights, the MNI templates, and `synb0.cnf` — 393 MB of git-LFS assets; `synb0.cnf` is referenced only by the dead backend, never by the live `dwifslpreproc`/`eddy` paths).
- **Delete `tests/test_synb0_seam.py`** (it covers only the dormant `Synb0Backend`/`run_topup_eddy`; no live coverage is lost).
- **Trim the documentation tail** in `CLAUDE.md` and `README.md`: remove the internal-`Synb0Backend` description and the "optional — synB0-DISCO only" external-tool sections (FreeSurfer, ANTs, Convert3D, `epi_reg`, `fslmerge`). Keep every word about the live external-outputs route.
- **Drop the now-orphaned `*.pth filter=lfs` rule** from `.gitattributes` (the five synB0 weights are the only `.pth` files in the repo). The `*.nii.gz` LFS rule **stays** — the JHU ROI templates under `templates/` still need it.

What is explicitly **preserved**: `PipelineRunner.run_eddy_with_synb0`, `validators.validate_synb0_output_dir`, the `use_synb0`/`synb0_output_dir`/`synb0_eddy_options` state fields, the GUI synB0 frame and stage mapping, `config.SYNB0_*`, `user_config.KEY_SYNB0_OUTPUT_DIR`, and all of FSL `eddy`/`topup` as live requirements. The external-outputs feature does not change by a single observable behavior.

The work lands as a short PRD-then-code sequence: this PRD first, then **one branch carrying a single deletion commit**, suite green before and after.

## User Stories

1. As a maintainer, I want the unreachable in-process synB0 backend removed before the port, so that the PySide6 migration does not hand-translate or carry a feature no code path reaches.
2. As a maintainer, I want the 393 MB of dormant `.pth`/MNI git-LFS assets out of the working tree, so that checkouts and fresh clones stop smudging weights for a removed feature.
3. As a new user setting up the tool, I want the install docs to list only the external tools the app actually invokes, so that I am not told to install FreeSurfer/ANTs/Convert3D for a capability that no longer exists.
4. As a scientist relying on the pipeline, I want the live external-outputs synB0 route (`use_synb0` + external OUTPUTS dir + `eddy`) completely untouched, so that my existing distortion-correction workflow keeps working byte-for-byte.
5. As a reviewer, I want the cut in one self-contained deletion commit with the suite green on both sides, so that it reads and reverts as a single behavior-preserving change.
6. As a future contributor, I want the abandoned direction recoverable from history rather than kept live, so that "why is this here" has a clear answer and the option is preserved without the carrying cost.

## Implementation Decisions

### 1. Cut, not wire-in or quarantine

The internal backend is deleted outright. The deletion test is decisive: nothing in live code imports it, so removing it forces no compensating change. The abandoned direction remains recoverable from commit `397982e` (cheaply — it is two commits) if it is ever revived.

- **Rejected — wire it in as a real preproc backend:** this re-opens a closed product decision. It would promote `torch` to a hard dependency and add FreeSurfer + ANTs + Convert3D as required external tools (today the app needs only MRtrix3 + FSL), plus new GUI surface and real-data validation. That is a feature, the opposite of pre-port cleanup, and it reverses the deliberate choice of the external-outputs route.
- **Rejected — quarantine with a CONTEXT.md note:** the package never touches the GUI, so keeping it buys the port nothing, while it remains 393 MB of LFS plus 1,832 LOC of code that cannot even run without an undeclared dependency. A doc note does not justify carrying the largest stale-code pool in the tree past a migration whose explicit goal is to not carry junk.

### 2. Working-tree deletion only; history rewrite is a separate, optional follow-up

The 393 MB lives in git-LFS. This PRD `git rm`s the files, so they leave the working tree and future checkouts/clones stop smudging them; the LFS blobs remain in history and LFS storage. Reclaiming that storage requires rewriting `main`'s history (git-filter-repo/BFG), which force-pushes the branch, rehashes already-merged PRs, and breaks every existing clone.

- **Rejected — bundling a history rewrite into this PRD:** rewriting shared, already-pushed history is not behavior-preserving at the repository level and is disproportionate to a code-cut. It is recorded as an explicit optional follow-up (see Further Notes), to be decided on its own terms, not smuggled in here.

### 3. The documentation tail is part of the cut, across both `CLAUDE.md` and `README.md`

Leaving the "optional — synB0-DISCO only" install sections would keep telling users to install FreeSurfer/ANTs/Convert3D/`epi_reg`/`fslmerge` for a feature that no longer exists — the exact stale-doc problem the cut exists to remove. The post-cut external-tool surface was verified directly: live code invokes MRtrix3 (`dwidenoise`, `mrdegibbs`, `dwifslpreproc`, `dwi2tensor`, `tensor2metric`, `dwi2mask`) and FSL (`flirt`, `fnirt`, `applywarp`, `invwarp`, `fslmaths`, `topup`, `eddy`) — and none of the synB0-only tools. The risk of removing a still-needed tool is therefore zero.

- **Rejected — trimming `CLAUDE.md` only:** `README.md` is the install-facing document; leaving its optional-tool sections behind preserves known-false setup instructions, which is precisely the cruft being eliminated.

### 4. One atomic deletion commit

The cut is a pure deletion with no sub-steps; every fragment (package, data, test, docs, `.gitattributes`) describes or supports the same dead backend. Doing it in one commit avoids any intermediate broken state (deleting the package while the test still imports it would redden the suite) and reverts as a single unit.

- **Rejected — splitting into "remove the test" then "remove the package":** the test exists only to exercise the thing being deleted, so a standalone "remove a test" commit reads as dropping coverage for no reason. One concern, one commit.

### 5. The live external-outputs route and pre-existing unrelated staleness are out of scope

Everything implementing `use_synb0` + external OUTPUTS + `eddy` stays exactly as-is. Separately, `CLAUDE.md`/`README.md`/`pipeline.py` docstrings describe the **registration** stage as using "BET2" though the code uses MRtrix3 `dwi2mask` — a pre-existing inaccuracy unrelated to synB0. It is left alone (only the synB0-attributed `bet` reference, which sits inside the synB0 install section, is removed with that section).

- **Rejected — fixing the registration-stage BET2 doc drift here:** it is a different staleness with its own cause; folding it in would violate single-concern scope. Noted as a possible separate cleanup.

## Testing / Verification Decisions

**What proves this cut safe:** the deletion test (nothing live compensates for the removal) plus a green suite on both sides of the change. There is no new behavior to test — only the absence of regressions in the live code that shares the synB0 *name*.

1. **Full suite, before and after.** Run `pytest tests/` on the current tree to capture the baseline, then after the cut. The only expected difference is the disappearance of `tests/test_synb0_seam.py`'s cases; every other test — including `tests/test_app_logic.py::TestValidateSynb0OutputDir` (the live validator) and `tests/test_pipeline_seam.py` (the live eddy/preproc seam) — stays green.
2. **Import-guard intact.** The subprocess `gui → processing` one-way import-guard test must stay green, confirming the package's removal introduces no import breakage.
3. **Live route still imports and resolves.** `import dti_alps.processing` and the GUI module import cleanly with the package gone (it was never re-exported), and `PipelineRunner.run_eddy_with_synb0` / `validate_synb0_output_dir` remain resolvable.

**Coverage boundary:** as in PRDs 0004–0007, GUI adapter wiring is verified by manual smoke, not by instantiating a display. No new tests are added; this PRD removes coverage of dead code only.

## Out of Scope

- **Any change to the live external-outputs synB0 route.** `run_eddy_with_synb0`, `validate_synb0_output_dir`, the `use_synb0`/`synb0_output_dir`/`synb0_eddy_options` fields, the GUI synB0 frame/stage mapping, and `config.SYNB0_*` are untouched.
- **Rewriting git history / reclaiming LFS storage.** Working-tree deletion only; the blobs stay in history. Recorded as an optional follow-up.
- **The registration-stage "BET2" doc drift.** Pre-existing and unrelated to synB0; left as-is apart from the synB0-attributed `bet` line inside the removed install section.
- **The undeclared `torch` dependency and any `pyproject` extras.** There is no synB0/torch entry in `pyproject.toml` to remove; nothing to change there.
- **Adding a seam test for the live `run_eddy_with_synb0`.** It currently has no dedicated seam test; closing that pre-existing gap is a separate ticket, not this cut's concern.

## Further Notes

- **Sequencing:** PRD first, then one branch (`refactor/cut-synb0-backend`, off the current `main`, which already carries PRDs 0006/0007 and Candidate 4) with a single deletion commit, green before and after. Commit 1: `docs: add PRD 0008 — cut the dormant internal synB0 backend`. Commit 2: `refactor: cut the dormant internal synB0 backend (PRD 0008)`.
- **Optional follow-up — LFS history rewrite:** if reclaiming the 393 MB from LFS storage is wanted, a later, separately-decided `git filter-repo` pass on `main` can purge the blobs. It force-pushes and rehashes merged PRs, so it is a deliberate, standalone operation — never bundled with a code change.
- **Relationship to the candidate backlog:** this discharges Candidate 5. It is independent of Candidate 1 (results-panel presenter / PRD 0006) and the engine-side Candidate 3 (typed message stream): the synB0 package shares no files with them, so the cut can land in any order relative to that work.
- **Domain model:** no `CONTEXT.md` change is needed — the internal backend was never part of the project's ubiquitous language (it is absent from `CONTEXT.md` today), and the live external-outputs route's vocabulary is unaffected.
