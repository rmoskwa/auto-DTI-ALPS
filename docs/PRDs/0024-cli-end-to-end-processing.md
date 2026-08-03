# PRD 0024 — CLI end-to-end processing

## Problem Statement

autoDTI-ALPS is GUI-centric by design, and the GUI is the right front door for a
researcher meeting the pipeline for the first time. But a neuroimaging researcher who
already understands DTI-ALPS preprocessing has the opposite need: point the tool at a
cohort, walk away, and come back to a results CSV. Today that is impossible.

The CLI has four modes, dispatched by a hand-rolled `sys.argv[1]` switch in
`__main__.py`: `--gui`, `--viewer`, `--report`, and `--reanalyze`. Every one of them
except `--gui` operates on an **output directory the GUI already produced**.
`--reanalyze` re-runs stages 8–9 (ROI placement + ALPS calculation) over existing
tensors and warps; `--report` reads finished ROI masks. **There is no CLI path to
stages 1–7 at all.** Denoising, Gibbs removal, eddy/topup, tensor fitting, metric
extraction and registration can only be started by clicking Run in the Qt app.

The machinery is all there and none of it is wired to a command line:

- `BatchRunner` takes a `BatchState` and a plain `progress_callback(WorkerMessage)`.
  It is Qt-free and already handles per-subject failure isolation, cancellation, and
  CSV writing.
- `discovery.discover_with_subdir_fallback()` turns a folder into `SubjectFiles`.
- `messages.py` is a closed, typed worker→front-end union.

The one missing link is that the **only** thing in the codebase that builds a
`BatchState` is `gui/form_model.build_batch_state(FormState, subjects)`, and a
`FormState` is a snapshot of Qt widgets. To run headlessly you must first have a GUI.

The obstacle to simply "adding flags" is surface area. Counting the option catalogs in
`gui/config.py`: 61 pass-through MRtrix3/FSL options across eight stages, plus 15
output-retention booleans, 5 ROI-shape selections, the 5-integer adaptive search
envelope, and roughly ten scalars. A flat argparse mirror of ~95 knobs would be
unusable to type and a permanent maintenance liability — every new GUI option would
need a hand-written twin, and the two would drift.

## Solution

Add a **`run` verb** that executes the full pipeline headlessly, and restructure the
CLI into subcommands to hold it.

Configuration is **hybrid**. The knobs that vary per invocation — where the data is,
where output goes, ROI shapes, ALPS method, acquisition parameters — are flags. The
deep, rarely-touched surface — the 61 per-stage tool options and the output-retention
flags — lives in an optional **protocol file**: a JSON serialization of the analysis
itself, which the GUI can now export. Flags override the file; the file overrides
defaults; omitting the file entirely still yields a complete, working run.

```bash
# Simplest complete run
dti-alps run --subjects /data/cohort --output /data/out

# A BIDS cohort, a protocol exported from the GUI, tuned ROIs
dti-alps run --subjects /bids/sub-*/ses-1/dwi --output /data/out \
    --config study-protocol.json --sphere 2,3 --roi-method Both --nthreads 8

# Check what would happen before committing 40 CPU-hours
dti-alps run --subjects /bids/sub-*/ses-1/dwi --output /data/out --dry-run
```

The central new domain distinction is **Protocol vs Run placement**. `BatchConfig`
currently mixes two unlike things: *what the analysis is* (portable, shareable,
citable) and *where this invocation lands* (`output_dir`, staging — machine-specific).
The protocol file carries only the former, so a `study-protocol.json` can be attached
to a methods section or handed to a collaborator without carrying somebody's home
directory into their cluster run.

Because the application is pre-release, the restructure is a **clean break**: the
`--viewer` / `--report` / `--reanalyze` flag spellings become the `view` / `report` /
`reanalyze` verbs with no aliases. Bare `dti-alps` still launches the GUI — that is a
product requirement (the AppImage double-click path), not a compatibility shim.

## User Stories

1. As a DTI researcher, I want to process a whole cohort with one command, so that I
   can run an overnight batch on a compute node with no display attached.
2. As a DTI researcher, I want a run with no config file to still use sensible
   defaults, so that my first headless command is short enough to type from memory.
3. As a DTI researcher, I want to configure a study once in the GUI and export it, so
   that I do not have to re-derive 61 tool options as command-line flags.
4. As a DTI researcher, I want the exported protocol to contain no paths from my
   machine, so that I can commit it beside my analysis code and a collaborator can run
   it unchanged.
5. As a DTI researcher, I want the GUI to show me the exact command that reproduces my
   current form, so that I can copy it straight into a job script.
6. As a DTI researcher, I want to point at a BIDS tree with a shell glob, so that I do
   not have to reorganise my data to match a discovery convention.
7. As a DTI researcher, I want to see the resolved subject list before anything runs,
   so that a mis-typed glob costs me ten seconds rather than a wasted weekend.
8. As a DTI researcher, I want the run to refuse to start when two subjects would
   resolve to the same identifier, so that a BIDS glob cannot silently overwrite one
   subject's results with another's.
9. As a DTI researcher, I want to control how much of the path becomes the subject id,
   so that `sub-01/ses-1/dwi` and `sub-02/ses-1/dwi` stay distinguishable.
10. As a DTI researcher, I want the run to tell me immediately that `fnirt` is not on
    my PATH, so that I find out in the first second rather than three hours into
    stage 7.
11. As a DTI researcher, I want to see the raw MRtrix3 and FSL output by default, so
    that I can diagnose an eddy failure without re-running with a flag.
12. As a DTI researcher, I want a log file written for a headless run, so that an
    unattended overnight batch leaves the same record the GUI console would have.
13. As a DTI researcher, I want to resume a batch that was interrupted at subject 180
    of 200, so that a preempted node does not cost me days of recomputation.
14. As a DTI researcher, I want a resume to silently reprocess everything if I have
    edited the protocol, so that a cohort can never end up half-processed one way and
    half the other.
15. As a DTI researcher, I want the process exit code to distinguish "all subjects
    succeeded" from "some failed", so that my job script can branch on it.
16. As a DTI researcher, I want Ctrl-C to stop cleanly and still write the results CSV,
    so that I keep the numbers for the subjects that finished.
17. As a DTI researcher, I want to set an arbitrary MRtrix3 option without authoring a
    JSON file, so that adding `-nthreads 8` is a flag and not a detour.
18. As a DTI researcher, I want `--nthreads` to apply to every stage at once, so that I
    can wire it to `$SLURM_CPUS_PER_TASK` in one place.
19. As a DTI researcher, I want the ROI and ALPS flags on `run` to be spelled exactly
    as they are on `reanalyze`, so that I only learn one vocabulary.
20. As a DTI researcher, I want `--pe-dir AP` to actually take effect, so that an
    explicit flag is never silently overruled by a JSON sidecar.
21. As a DTI researcher, I want the CLI and the GUI to produce identical results from
    the same settings, so that a headless batch is a scaling decision and not a
    scientific one.

## Implementation Decisions

### The domain split — Protocol vs Run placement

`BatchConfig` gains no new structure, but its fields are formally partitioned:

- **Protocol** — `run_denoising`, `run_degibbs`, `pe_direction`, `auto_pe_direction`,
  `readout_time`, `rpe_scheme`, the eight `*_options` dicts, `use_synb0`,
  `synb0_output_dir`, `roi_shapes`, `fa_threshold`, `alps_method`,
  `adaptive_roi_placement`, `adaptive_search`, `output_config`.
- **Run placement** — `output_dir`, `staging_enabled`, `staging_dir`.

The protocol file serializes the first set only; run-placement keys are omitted on
write and ignored on read. `run` therefore **requires** `--output`, which removes the
"does an absent `--output` mean use the file's path, or error?" ambiguity entirely.

Splitting `BatchConfig` into two dataclasses is the cleaner long-term expression of
this, but it churns `batch.py`, `form_model.py` and the Qt adapter in one go and is
recorded as a follow-up. In the meantime the partition is pinned by an exhaustiveness
test (see *Testing Decisions*), so a newly added `BatchConfig` field fails the suite
until somebody classifies it.

`synb0_output_dir` is deliberately classified as **protocol**, not placement: it names
an input dataset the analysis depends on, in the same way the subject folders do, and a
protocol that silently dropped it would produce a different pipeline (10 stages vs 9).
It is overridable per-run by `--synb0-dir`.

### The protocol file — `processing/config_io.py`

- **Serialized type is `BatchConfig`, not `FormState`.** `FormState` is shaped by how
  the GUI *edits* — raw strings, an `readout_auto` editing flag, `OptionState(enabled,
  value, type)` triples where `type` is a widget type. A CLI consuming it would have to
  fabricate widget state (`OptionState(enabled=True, value="8", type="int")`) purely to
  run it back through `_collect_cli_options` and recover `{"-nthreads": 8}`.
  `BatchConfig` is already the engine's own vocabulary: ROI shapes as geometry dicts,
  tool options as the exact `dict[str, Any]` `commands.py` consumes.
- **Direction stays one-way through the existing seam.** GUI export is
  `build_batch_state(form_state, []).config` → `write_protocol`. CLI is
  `read_protocol` → `BatchState(config, discovered_subjects)`. `build_batch_state`
  remains the single tested place where widget values become domain values.
- **No import-protocol-into-the-GUI.** `BatchConfig` cannot faithfully restore a
  `FormState`, and a half-working import is worse than none.
- **Format is JSON.** No new runtime dependency (the project has only numpy, nibabel,
  scipy, PySide6), it is already the house format (`~/.dti-alps/user_config.json`, BIDS
  sidecars), and it needs no PyInstaller hook in the AppImage bundle. YAML would be
  nicer to hand-comment; the file is generated in the common case, so that was not
  judged worth a dependency.
- Writing is `dataclasses.asdict(config)` restricted to the protocol keys — it recurses
  into `OutputConfig` and `AdaptiveSearchConfig` for free. Reading rebuilds the two
  nested dataclasses explicitly and rejects unknown keys with a message naming the key.
- The module also owns `protocol_hash(config)` — a stable digest over the serialized
  protocol, used by `--resume`.
- Lives in `processing/` because both front ends need it and the engine is the shared
  floor. It imports stdlib and `state` only, so the Qt-free guardrail holds.

### CLI structure — `dti_alps/cli/`

`__main__.py` shrinks to `from .cli.main import main`. The new package holds
`main.py` (subparser wiring and dispatch), one module per verb (`run.py`,
`reanalyze.py`, `report.py`, `view.py`), and `render.py`.

- **Subcommands, not flag-verbs.** `run` carries ~25 flags across five groups; only
  subparsers give `dti-alps run --help` that lists exactly those without polluting
  `reanalyze --help`. It also retires the docstring-as-help problem: `dti-alps --help`
  becomes a generated verb list instead of a 40-line module docstring that had to be
  hand-synced with `_parse_reanalysis_args`'s epilog.
- **Clean break, no aliases.** Pre-release; a compat layer would be a second grammar to
  document and test, defending a contract nobody depends on. The README's `--viewer`
  references are updated in the same change.
- **Bare `dti-alps` still launches the GUI.** Product requirement, not a shim.
- **The Qt-free discipline extends to `cli/`.** Only `view.py` and the `gui` verb
  import PySide6, and only inside the function body.
- **`render.py` is the terminal presentation model**, the exact mirror of
  `gui/result_model.py`: one presenter per front end, both dispatching over the same
  closed `WorkerMessage` union, neither owning the other's wording.
- **`cli/` never imports `gui/`.** The point of the package is that it is the *second*
  front end, not a client of the first. Where the CLI needs a vocabulary the GUI also
  has, it derives it from the engine (see `--opt` below).

### Subject discovery and identity

- `--subjects PATH` is **repeatable**, and each value goes through
  `discover_with_subdir_fallback` — identical semantics to the GUI's "Add folder".
  `new_unique_runs` dedupes across repeats.
- **BIDS is handled by the shell, not by new discovery code.**
  `--subjects /bids/sub-*/ses-1/dwi` covers a tree that `discover_with_subdir_fallback`
  (one level of fallback only) cannot reach. Shell expansion is more expressive than
  any `--depth` flag we would design, and — decisively — a recursive walk would be
  CLI-only behaviour, so GUI and CLI discovery would diverge.
- `--dwi/--bvec/--bval [--json] [--rpe]` is a mutually-exclusive single-subject escape
  hatch. It is the only way to override `_find_reverse_pe`'s greedy first-match guess,
  and the only way to run a subject whose gradient files do not stem-match the DWI.
  (A wrong RPE guess is inert unless `--rpe pair` is set: `commands.py:180` passes
  `-se_epi` only under the `pair` scheme.)
- **Subject-id collision is an engine-side hard error.** `discover_files` names a
  subject after its folder when the folder holds one run and after the DWI stem when it
  holds several. `batch.py:112` makes the output directory `output_dir/<subject_id>`
  and `_write_shape_csv` keys its rows by the same id. So `--subjects
  /bids/sub-*/ses-1/dwi` — where every folder is named `dwi` — would send every subject
  to `out/dwi/` and collapse the CSV to one row. `new_unique_runs` does not catch it,
  because it dedupes on `dwi_path`, which is genuinely distinct.

  This is a **latent bug in the GUI too** — two subject folders each holding
  `DTI64_b1300` and `DTI64_b2600` collide today by exactly the mechanism the comment at
  `discovery.py:164-168` says the folder-name rule exists to prevent. The guard
  therefore lands in the engine, where both front ends hit it, as its own commit.
- `--id-depth N` (default `1`) joins the last N path components with `_`, so
  `--id-depth 3` yields `sub-01_ses-1_dwi`. The multi-run DWI-stem suffix still applies.
  `N=1` reproduces today's naming byte-for-byte.
- **Auto-disambiguation was rejected.** Silently widening the path depth on collision
  would make output folder names a function of cohort composition — adding one subject
  could rename the output of subjects already processed, breaking re-runs and breaking
  `reanalyze` against an existing output directory.
- `--dry-run` resolves subjects, prints the table (id → dwi/bvec/bval/json/rpe → target
  output directory), reports preflight, and exits without processing. It is in scope
  rather than deferred: discovery is heuristic and the GUI's file-summary column is the
  thing being replaced.

### The `run` verb's flags

| Group | Flags |
|---|---|
| Data | `--subjects PATH` (repeatable) · `--dwi/--bvec/--bval [--json] [--rpe]` · `--id-depth N` |
| Placement | `--output DIR` (required) · `--staging` · `--staging-dir DIR` |
| Protocol source | `--config FILE` |
| Acquisition | `--pe-dir {AP,PA,LR,RL,SI,IS}` · `--readout SECONDS` · `--rpe {none,pair,all,header}` · `--synb0-dir DIR` |
| Stages | `--no-denoise` · `--no-degibbs` |
| ROI / ALPS | `--sphere R[,R,…]` · `--squarev9` · `--squarev4` · `--roi-method {Adaptive,Standard,Both}` · `--search-x/-y/-z N` · `--max-y-drift/--max-z-drift N` · `--method {ALPS-LAB,ALPS-PAS,Both}` · `--fa-threshold F` |
| Tooling | `--opt STAGE:NAME=VALUE` (repeatable) · `--nthreads N` |
| Execution | `--dry-run` · `--resume` · `--fail-fast` · `--quiet` |

- The ROI/ALPS row is spelled **identically to `reanalyze`** and reuses its validators
  (`_validate_sphere_radii`, `_validate_search_value`), which move into a shared CLI
  module.
- **Precedence is defaults < protocol file < flags.** Every flag that also exists in
  the protocol must carry `default=None` in argparse and be applied only when not
  `None`. If `--fa-threshold` carried `default=FA_THRESHOLD` — as
  `_parse_reanalysis_args` does today — argparse's default would silently clobber a
  protocol that set `0.35`, and it would look like the file was ignored.
- **`--opt STAGE:NAME=VALUE`** is the generic escape hatch, e.g.
  `--opt dwifslpreproc:-eddy_options='--repol --slm=linear'`. It drops the value into
  the matching `*_options` dict, needs no per-option maintenance as MRtrix3 grows flags,
  and means the CLI is never *blocked* on authoring a JSON file. **The valid stage
  vocabulary is derived from `BatchConfig`'s `*_options` field names**
  (`dwifslpreproc_options` → `dwifslpreproc`, `synb0_eddy_options` → `synb0_eddy`), not
  from the catalogs in `gui/config.py` — so it cannot drift and the CLI does not import
  the GUI package.
- **`--nthreads N`** fans out to every stage that accepts `-nthreads`. A per-stage
  `--opt` wins over it.
- **`--pe-dir` implies `auto_pe_direction=False`.** `BatchConfig.auto_pe_direction`
  defaults `True` and `batch.py:103-109` lets a JSON sidecar override `pe_direction`,
  so without this an explicit `--pe-dir AP` would do nothing for any subject with a
  sidecar saying `j`. Making the explicit flag disable auto-detection is the least
  surprising reading and avoids a separate `--no-auto-pe`.
- **`--readout` omitted means auto-extract**, which maps directly onto
  `BatchConfig.readout_time = None`. No sentinel is invented.
- **`--synb0-dir` presence implies `use_synb0=True`**, so the 10-stage route is selected
  by supplying its input rather than by a redundant mode flag.
- **Known asymmetry:** `reanalyze` has a boolean `--adaptive` while `run` needs the
  tri-state `--roi-method`, because `adaptive_roi_placement` is a three-valued string
  and `run_reanalysis(enable_adaptive: bool)` has no "Both" path. Teaching reanalysis
  "Both" is a separate change; the asymmetry is documented rather than silently
  introduced.

### Progress, logging, and exit codes

- **Verbose by default, `--quiet` to reduce.** `Log` messages carry raw MRtrix3/FSL
  stdout (`pipeline.py:105` passes `on_line=self._log`), and researchers who know this
  pipeline diagnose failures by reading `eddy` and `fnirt` output. `--quiet` drops to
  stage- and subject-level lines.
- **JSON-lines output is deferred.** It is roughly a 20-line renderer given the typed
  union, so it is easy to add when cluster orchestration actually needs it; a
  speculative machine format would calcify early.
- **The log file moves into the engine.** Today `app.py:2168-2197` opens
  `output_dir/dti_alps_<timestamp>.log`, tees every `_log` line into it, and deletes it
  at close when `output_config.log_file` is false. Nothing in `processing/` writes a
  log, so a CLI run would produce none and `OutputConfig.log_file` — a key the protocol
  schema exposes — would be a knob the CLI silently ignored. A composable
  `LogFileSink` in `processing/run_log.py` wraps a `progress_callback`; both front ends
  compose it, the file lands with the same name and format either way, and it is
  testable without a Qt application.
- **`run` calls `BatchRunner.run_batch()` synchronously**, not through `BatchWorker`.
  It therefore never receives `BatchSuccess`/`BatchPartial`/`BatchCancelled`/`Error`
  (those are `BatchWorker`-emitted) and derives its verdict from the returned bool and
  `batch_state.success_count`.
- **SIGINT calls `runner.cancel()`** rather than dying. `run_batch` already handles that
  path properly: the loop breaks, `_mark_remaining_skipped` runs, and
  `_write_csv_results` still writes the CSV with the finished subjects' numbers.
- Exit codes: `0` every subject completed · `1` finished with ≥1 failure · `2`
  usage/config error (argparse's own convention, and what `main()` already returns for
  an unknown option) · `3` preflight failure · `130` interrupted by SIGINT.
- `--fail-fast` (default off, matching `BatchRunner`'s current continue-on-error)
  aborts the batch on the first subject failure, because a bad protocol fails all 200
  subjects identically.

### Resume and the completion marker

`_write_csv_results()` runs **after** the whole subject loop, so ALPS numbers live only
in memory until the batch ends. SIGINT is safe (above), but a hard kill — preemption,
OOM — loses the completed subjects' numbers. They are recoverable by running
`reanalyze` over the surviving ROI masks and tensors, so this is a cost, not data loss.

What is *not* recoverable is restart cost: nothing on disk records that a subject is
done, so a 200-subject cohort killed at 180 redoes all 180.

- On each `SubjectComplete`, `BatchRunner` writes
  `<output>/<subject_id>/alps_result.json` carrying the subject's status, per-shape
  ALPS values, and the **protocol hash**.
- `--resume` skips a subject iff that file exists **and** its protocol hash matches the
  current run. A changed protocol means nothing is skipped — the safe default, with no
  extra flag.
- The marker doubles as the durability fix and as per-subject provenance the batch CSV
  cannot give (the CSV is one row keyed by id; the JSON sits beside the data it
  describes).
- **A heuristic `--skip-existing` was rejected.** A subject killed mid-FNIRT leaves an
  output directory that looks populated, so a heuristic would skip it and the cohort
  would silently carry one subject whose ROI masks came from a half-finished warp.
  Silent bad data is a worse failure mode than redoing work.
- The marker's name and read/write live in `processing/results_layout.py`, which is
  already "the single home for the convention the engine writes and the viewer/reports
  read".

### Preflight

`commands.py:344` and `commands.py:365` already define `check_mrtrix3_available()` and
`check_fsl_available()`, with **no callers anywhere**, and neither is usable as-is:

- The MRtrix list omits `dwi2mask`, `dwiextract`, `mrmath` and `mrconvert`, all of which
  the engine invokes.
- The FSL list checks `eddy`/`topup`/`applytopup` — commands `dwifslpreproc` calls
  internally — and omits `flirt`, `fnirt`, `applywarp`, `invwarp` and `fslmaths`, which
  this codebase invokes directly.
- `commands.py:383` has a real bug: the variant list `[cmd, f"fsl{cmd}", f"{cmd}_cuda",
  "eddy_openmp"]` includes the literal `"eddy_openmp"` for *every* command, so `topup`
  and `applytopup` are reported present whenever `eddy_openmp` is on PATH.

Both functions are corrected in place rather than joined by a third checker. `run`
calls them before touching data and exits `3` listing what is missing; `--dry-run`
reports the same. The required set depends on the route, so the check takes the synB0
mode as an argument (no `dwifslpreproc`/`topup`; `eddy` invoked directly).

**GUI adoption is a follow-up.** The GUI has no preflight today, so a missing `fnirt`
surfaces as a stage-7 failure hours in — clearly worth fixing, but it is a GUI change
riding on a CLI PRD. The corrected functions will be sitting there ready.

### GUI export

A button on the **Output Setup** page — which already hosts the output-retention
checkboxes — opens a save dialog (defaulting via `UserConfig`, like every other path
field) and writes the protocol through `config_io`. The logic is
`build_batch_state(self._read_form_state(), []).config` → `write_protocol(path, cfg)`.

Beside it, a read-only, copyable line rendering the equivalent command from the paths
already in the form:

```
dti-alps run --subjects /data/cohort --output /data/out --config /data/protocol.json
```

This is the actual bridge the feature exists for — configure interactively once, copy,
scale on the cluster. Without it a researcher who exports a protocol still has to go
read `run --help` to learn how to consume it.

### Prerequisite cleanups

Landed first, each on its own merit, so the CLI arrives on clean ground:

- **Four dead `BatchConfig`/`PipelineState` fields are deleted.** `eddy_options` and
  `topup_options` reach `commands.py:197-200`, which emits `-eddy_options` and
  `-topup_options` — the *same* flags `dwifslpreproc_options` already emits via
  `commands.py:35`, so if both were ever set `dwifslpreproc` would receive the flag
  twice. `generate_qc` reaches `commands.py:201` but nothing ever sets it true.
  `keep_intermediates` is copied at `batch.py:139` and never read anywhere. All four are
  unreachable today; freezing them into a published protocol schema would make them
  permanent.
- **`DEFAULT_ROI_METHOD` moves to `processing/constants.py`.** `BatchConfig` and
  `PipelineState` default `adaptive_roi_placement` to `"Adaptive"` (`state.py:152`,
  `state.py:287`) while the GUI defaults it to `"Both"` (`gui/config.py:53`, mirrored in
  `FormState`). The divergence is invisible today because the GUI always sets the field
  explicitly — but a CLI built on `BatchConfig`'s defaults would run `Adaptive` where
  the GUI runs `Both`: the same settings, two different analyses, no flag in sight.
  One constant, read by all three, in the manner of PRD 0003.

### Commit sequence

1. Delete the four dead legacy fields (+ `PipelineState` twins, `commands.py` branches)
2. Lift `DEFAULT_ROI_METHOD` into `processing/constants.py`
3. Replace hand-rolled dispatch with argparse subcommands in `dti_alps/cli/` — pure
   refactor, existing behaviour preserved under the new spellings
4. Engine: subject-id collision guard + depth-parameterised id derivation
5. Engine: `processing/run_log.py` `LogFileSink`; `app.py` composes it
6. Engine: `processing/config_io.py` — protocol/placement split, read/write, hash
7. Engine: per-subject completion marker in `results_layout.py`; `BatchRunner` writes it
8. Engine: fix `check_mrtrix3_available` / `check_fsl_available`
9. CLI: the `run` verb — flags, precedence, dry-run, resume, fail-fast, exit codes,
   `render.py`
10. GUI: export protocol + copyable command on Output Setup
11. Docs: README CLI section, CLAUDE.md, CONTEXT.md

## Testing Decisions

Four drift guards, because the whole risk of a second front end is that it quietly
stops agreeing with the first:

1. **Exhaustiveness** — `PROTOCOL_FIELDS | RUN_FIELDS == {f.name for f in
   fields(BatchConfig)}`. A new `BatchConfig` field fails the suite until classified.
2. **Round-trip** — `read_protocol(write_protocol(cfg)) == cfg` over a config with
   every field set off-default, covering the `OutputConfig`/`AdaptiveSearchConfig`
   rebuild.
3. **Convergence** — an all-non-default `FormState` → `build_batch_state` → export →
   `run --config` parse → assert the resulting `BatchConfig` matches on every protocol
   field. This is the test that actually asserts "GUI and CLI produce the same
   analysis".
4. **Vocabulary coverage** — every `config.ROI_SHAPES` token is reachable from `run`
   flags, and every stage accepted by `--opt` corresponds to a real `*_options` field.

Alongside those:

- Precedence tests asserting flags beat the protocol file, the file beats defaults, and
  an omitted flag does *not* clobber a file value (the `default=None` trap).
- Subject-id collision tests over a synthetic BIDS-shaped tree, including `--id-depth`
  resolving the collision and `--dry-run` surfacing it before the guard fires.
- Resume tests: marker written on completion, matching hash skips, mismatched hash
  reprocesses, absent marker reprocesses.
- Exit-code tests for all-success, partial failure, usage error, preflight failure, and
  SIGINT (including that the CSV is still written).
- Preflight tests with a faked PATH, covering the `eddy_openmp` variant bug in both
  directions and the differing standard vs synB0 required sets.
- `LogFileSink` tests without Qt: content, and deletion when `output_config.log_file`
  is false.
- The existing engine import-guard test extends to `dti_alps.cli` (minus `view`): no
  toolkit resident after import.

## Out of Scope

- **JSON-lines / machine-readable progress output.** Deferred until a real
  orchestration need appears.
- **Importing a protocol back into the GUI.** `BatchConfig` cannot faithfully restore a
  `FormState`; a half-working import is worse than none.
- **Recursive or BIDS-aware discovery in `discovery.py`.** Shell globbing covers it, and
  CLI-only discovery behaviour would diverge from the GUI.
- **Splitting `BatchConfig` into separate protocol and placement dataclasses.** The
  right long-term shape; too much churn to ride along here.
- **GUI adoption of preflight.** Recorded follow-up.
- **Teaching `run_reanalysis` a "Both" ROI method** so `reanalyze` and `run` can share
  one flag spelling.
- **Parallel subject processing.** `BatchRunner` is sequential; `--nthreads` tunes the
  tools, not the batch.
- **Any backward-compatibility shim** for the `--viewer`/`--report`/`--reanalyze` flag
  spellings.

## Further Notes

- The `Protocol` / `Run placement` distinction is added to `CONTEXT.md`; it is
  vocabulary the codebase will keep using, not a detail of this change.
- `CONTEXT.md`'s "engine / GUI split" section still describes the adapter as "the
  Tkinter (one day PySide6) layer" and says the GUI may hold "tk-free" presentation
  models — stale since PRD 0013 completed the port. Worth a separate docs pass; not
  touched here beyond adding the CLI as a second front end.
- CLAUDE.md and the README list **BET2** among the required FSL tools, but the engine
  uses `dwi2mask` for brain extraction and never invokes `bet2`. Corrected in the docs
  commit.
- Recorded follow-ups from this design: split `BatchConfig`; adopt preflight in the
  GUI; teach reanalysis the tri-state ROI method; JSON-lines renderer if cluster use
  demands it.
