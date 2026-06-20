# PRD 0001 — The ToolRunner seam

Status: Accepted · Date: 2026-06-19 · Source: Architecture review Candidate 1 ("Put one seam under the external tools"), settled in a grilling session.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

---

## Problem Statement

Every pipeline stage builds and runs its own `subprocess` call. Nothing past command construction can run in a test without FSL, MRtrix3, FreeSurfer, ANTs and Convert3D all installed and on `PATH`. As a result:

- There is **no CI that can be stood up** — the existing `tests/` are integration scripts that shell out to real binaries.
- The command-builder logic (the argv handed to `dwifslpreproc`, `fnirt`, `antsRegistration`, …) has **no unit coverage**, so a malformed flag or a wrong phase-encode direction is only caught by running the real tool on real data.
- Failure and cancellation behavior (non-zero exit aborts the stage; user-cancel terminates the process) is **untested**, and is implemented inconsistently across call sites.
- Two streaming loops are independently maintained — `_run_command` in `pipeline.py` and `_run_fsl_command` in `registration/fsl.py` — and a third blocking style (`subprocess.run(capture_output=True)`) is copied across ~20 more sites.

The engine cannot be made "professional" or "distributable" while its core execution path is unverifiable.

## Solution

Introduce **one deep module that owns external command execution** — a `ToolRunner` with a single `run()` method. Production injects a real subprocess-backed adapter; tests inject a stateless recording fake. Because two real implementations exist from day one (real + fake), the seam is a fact, not a hypothesis.

With the seam in place:

- **Command-construction** is testable: build the pipeline with the fake, run it, and assert on the exact argv each stage issued — no binaries, no files on disk.
- **Control-flow** is testable: script a non-zero exit or a cancel through the fake and assert the pipeline aborts / terminates / reports correctly.
- The duplicate streaming loop is **deleted**; cancellation, heartbeat, and exit-code handling live in exactly one place.
- CI runs with **zero toolchain binaries installed**.

Explicitly, the seam does **not** deliver end-to-end "run the whole pipeline and assert an ALPS index" tests — the fake intercepts the *command*, not the `.nii.gz` it would have produced, so the next stage has no file to load. That capability belongs to the later pure-module work (the arrays-in ALPS module and the pure ROI-geometry module) and to the real-binary integration smoke, both of which this seam unblocks but does not itself provide.

## User Stories

1. As a developer, I want to assert that a given `PipelineState` causes `dwifslpreproc` to be invoked with `-pe_dir AP -rpe_pair …`, so that I can catch command-builder regressions without installing MRtrix3.
2. As a developer, I want to assert that an `AP`/`PA` reverse-phase-encode configuration changes the argv the pipeline issues, so that phase-encode bugs are caught in CI.
3. As a developer, I want to inject a non-zero exit code at the denoising step and assert the pipeline aborts and reports a denoise failure, so that error-handling is covered without a tool that actually fails.
4. As a developer, I want to inject a "binary not found" outcome and assert the pipeline reports the missing tool cleanly, so that the not-installed path is tested without uninstalling anything.
5. As a developer, I want to simulate a user cancel mid-stage and assert the running process is terminated and the run reports as cancelled, so that the cancel path is verified deterministically.
6. As a developer, I want to construct `PipelineRunner` with a single `runner=fake` argument and have *every* command across all nine stages routed through that fake, so that a full-pipeline test needs one injection point rather than five.
7. As a developer, I want the fake to record every argv it was handed, so that I can write order-free assertions about what the pipeline tried to run.
8. As a developer, I want to script a failure by *what the command is* (a predicate over argv) rather than by its position in a sequence, so that tests do not break when a 15-call chain like synB0 is reordered.
9. As a developer, I want the fake to drive the streaming line callback with scripted output lines, so that a stage's log-handling logic is itself under test.
10. As a maintainer, I want command execution (streaming, heartbeat, cancellation, exit-code handling) to live in exactly one module, so that a fix lands once instead of being copied across call sites.
11. As a maintainer, I want a lint rule that forbids importing `subprocess` outside the runner module, so that a future 24th call site cannot silently bypass the seam.
12. As a maintainer, I want the reanalysis CLI and the batch runner to share the same runner abstraction as the pipeline, so that all three entry points are testable the same way.
13. As a CI operator, I want the unit suite to run with no FSL/MRtrix3/FreeSurfer/ANTs/Convert3D installed, so that the project has a CI that can actually be stood up.
14. As a release engineer, I want the engine's execution path verifiable without a toolchain, so that "distributable" stops being blocked on environment setup.
15. As a developer converting a call site, I want the real adapter's streaming/exit/cancel behavior unit-tested against POSIX coreutils (`echo`, `false`, `sleep`, a bogus binary), so that the refactor has a regression net before any caller is touched.
16. As a developer, I want each `subprocess.run(capture_output=True)` site in synB0 and b0-extraction to become a `runner.run()` call that returns captured output, so that those blocking callers go through the seam without changing what they inspect.
17. As a developer, I want the integration scripts kept as a real-binary smoke test, so that there is still one true end-to-end check on a machine that has the tools and the sample data.
18. As a developer, I want a small cropped/downsampled real-data fixture committed, so that the future pure-module tests (ALPS index, ROI geometry) can assert plausible real numbers in CI.
19. As a developer, I want the runner to live under `processing/`, never under `gui/`, so that the engine does not depend upward on the GUI package.
20. As a developer, I want the cancelled run to be disambiguated by the caller's own cancel flag rather than a special result field, so that the result type stays minimal and the existing cancel semantics are preserved.

## Implementation Decisions

### 1. One method, returning a value object — not a method family
`ToolRunner` exposes a single `run(cmd, *, on_line=None, cancel_check=None) -> ToolResult`. `ToolResult` carries `returncode: int`, `output: str`, and the `cmd` that produced it.

- **Streaming and capture are served by one code path.** The real adapter runs `Popen` and, per line, calls `on_line(line)` if supplied **and** appends to a buffer. Streaming callers (pipeline, fsl) pass `on_line` and ignore `.output`; capture callers (synB0, b0-extraction, reanalysis) pass `on_line=None` and read `.output`.
- *Rejected:* a method family (`run` vs `stream`). The accumulate-while-streaming adapter makes both behaviors fall out of one method, so the fake and the seam stay singular.

### 2. stderr is merged into one `output` stream
The adapter redirects `stderr` into `stdout` (matching the two existing streaming helpers). `ToolResult.output` is a single merged string.

- This rests on the verified assumption that the capture-style callers inspect **returncode**, not parsed stdout structure. If any caller later needs structured stderr, that's a future change to the result type, not a thing this PRD preserves.
- *Rejected:* keeping stdout/stderr separate (a fatter result type and a fake that must model two streams) — no current caller needs it.

### 3. `run()` never raises; all failure is a `returncode`
Every outcome is a `ToolResult`. Non-zero exit → `returncode=N`. Missing binary (today's `FileNotFoundError`) → caught internally and returned as `returncode=127` with an explanatory `output`. No exception crosses the seam.

- This makes control-flow tests uniform: every failure mode is a scripted integer the fake hands back; tests never wrap calls in `pytest.raises`, and the fake never models exceptions.
- **Accepted cost:** b0-extraction's five `subprocess.run(..., check=True)` sites currently rely on `CalledProcessError` propagating to abort. Each is rewritten to `if runner.run(cmd).returncode != 0: <abort>`, and any surrounding `try/except CalledProcessError` is removed. This is the most behavior-bearing edit in the conversion and is sequenced late.
- *Rejected:* raise-on-non-zero (keeps b0-extraction as-is) — it would force a fake and every control-flow test to model exceptions, and would require `try/except` at the pipeline/fsl/synB0 callers that currently just inspect a boolean/dict.

### 4. Cancellation is in the signature but wired only at the pipeline this change
`run()` accepts `cancel_check: Callable[[], bool] | None`. The real adapter polls it during the streaming loop and, when true, calls `process.terminate()` then `wait(timeout=5)`.

- **Only `PipelineRunner` passes `cancel_check` in this work.** `fsl`, `synB0`, and `b0-extraction` pass `cancel_check=None` and keep today's run-to-completion behavior.
- **A cancelled run is not a special result field.** It returns the terminate exit code; the caller owns its own cancel flag (it supplied `cancel_check`) and disambiguates "cancel vs failure" exactly as it does today (`self.cancelled`).
- *Rejected (deferred to fast-follow):* extending real cancellation to synB0/fsl now. It's an independently valuable feature the seam makes trivial, but it mixes concerns and needs new flag-plumbing into modules that don't hold the cancel signal today. Filed as a follow-up, not part of this PRD.

### 5. One runner per top-level entry, default-to-real, threaded down
The runner is created once at each top-level entry — `PipelineRunner`, `BatchRunner`, and the reanalysis CLI entry — as a constructor/parameter default (`runner or SubprocessToolRunner()`), and **passed down** into everything those entries drive: the registration backend factory, the FSL backend, the synB0 backend, and the b0-extraction / reanalysis helper functions.

- Result: `PipelineRunner(state, runner=fake)` causes **every** command in all nine stages to flow through that one fake — a single injection point per entry.
- The backend factory and both backend constructors, plus ~6 helper-function signatures, gain a `runner` parameter. Default-to-real keeps every *production* construction site compiling unchanged; only signatures grow.
- *Rejected:* each component default-constructing its own runner. It minimizes plumbing but leaks the seam — injecting a fake into the pipeline would not fake registration/synB0 commands, so a full-pipeline test would need five separate injection points, undercutting the candidate's central value.

### 6. Toolchain commands only — the GUI's desktop-open calls are excluded
The seam covers the ~23 toolchain sites (pipeline 1, fsl 1, synB0 15, b0-extraction 5, reanalysis 1). The GUI's `open` / `xdg-open` / `explorer` file-browser calls are **not** routed through it — they are fire-and-forget desktop integration with nothing to stream, no exit-code logic, and no toolchain dependency. The earlier "29 call sites" figure included these six; the honest toolchain count is ~23.

- Consequence: the `select`-based streaming adapter is POSIX-only, which is fine because the toolchain runs where FSL/MRtrix live; the cross-platform desktop-open calls correctly stay outside the abstraction.

### 7. The fake is a stateless, predicate-based recorder
`FakeToolRunner` records every argv into a `.calls` list and returns scripted `ToolResult`s. Scripting is by **predicate over argv**, not by ordered queue or tool-name key. Default behavior: success (`returncode=0`, no output).

The shape (from the design session; the decision-bearing parts, not a working demo):

```python
class FakeToolRunner:
    def __init__(self):
        self.calls: list[list[str]] = []      # every argv, in order — for command-construction assertions
        self._rules = []                      # (predicate, returncode, lines, cancel)

    def on(self, pred, *, returncode=0, lines=(), cancel=False):
        self._rules.append((pred, returncode, lines, cancel)); return self

    def run(self, cmd, *, on_line=None, cancel_check=None) -> ToolResult:
        self.calls.append(cmd)
        rc, lines, cancel = 0, (), False
        for pred, r, l, c in self._rules:
            if pred(cmd):
                rc, lines, cancel = r, l, c
                break
        if cancel and cancel_check is not None:
            return ToolResult(returncode=-15, output="", cmd=cmd)   # caller's own flag disambiguates
        out = []
        for ln in lines:
            if on_line:
                on_line(ln)                   # exercise streaming callers' log handling
            out.append(ln)
        return ToolResult(returncode=rc, output="\n".join(out), cmd=cmd)
```

- **Stateless and path-free** by deliberate decision — see Testing Decisions. The fake produces no files.
- *Rejected:* an ordered-queue fake (brittle — every synB0 test would enumerate all 15 calls in order); a tool-name-keyed fake (can't distinguish synB0's five separate `antsApplyTransforms` calls and can't assert on args).

### 8. `Protocol`, not `ABC`; home is `processing/tool_runner.py`
`ToolRunner` is a `typing.Protocol`. The real `SubprocessToolRunner`, the `ToolResult` value object, and the Protocol live in `processing/tool_runner.py`. `FakeToolRunner` lives in `tests/` as test infrastructure.

- `Protocol` is structural: the fake satisfies it without importing or subclassing the real module, so `tests/` does not depend on runner internals.
- This is *not* the speculative-ABC case the review flagged elsewhere: there are two real implementations from day one, so the abstraction is earned.
- The module lives under `processing/`, never `gui/`, so the engine does not depend upward on the GUI.

### 9. A banned-`subprocess`-import guardrail, switched on after migration
After the last call site is converted, add a lint rule (ruff `flake8-tidy-imports` banned-api or equivalent) forbidding `import subprocess` anywhere except `processing/tool_runner.py`, with a single carve-out for the GUI's desktop-open helper. Not enabled mid-migration, where it would fail on every un-converted site.

### 10. Migration is a strangler in fidelity order
> **Progress:** steps 1–3 done (2026-06-19). Step 3 also converted the **2 live**
> b0-extraction sites early (the registration path's `create_brain_mask_from_dwi` /
> `apply_mask_to_image`), so step 5 now owns only the 3 dormant synB0-reached sites.
> See the **Implementation Progress** section below for what landed and the pick-up
> point. Steps 4–6 not started.

1. **[✅ DONE]** Build `ToolResult`, the `ToolRunner` Protocol, `SubprocessToolRunner`, and `FakeToolRunner`. Model the adapter on `pipeline.py`'s `_run_command` — the **superset** behavior (`select` + cancel + 30s heartbeat); the fsl helper's simpler loop is a subset and is subsumed.
2. **[✅ DONE]** **Pipeline first** — it's the caller the adapter was modeled on (highest-fidelity behavior check) and the only cancellable one. Land the first command-construction and control-flow tests here.
3. **[✅ DONE]** **fsl** — deletes the duplicate streaming loop (the locality win). Also threaded the runner through the **2 live** b0-extraction helpers (their `check=True` → returncode rewrite landed here, ahead of step 5) and closed deviation #2 for fsl: the pipeline now forwards `self.runner` into the backend, so a fake injected at the pipeline reaches FSL registration commands.
4. **[ ] TODO** **synB0** (15 sites) — mechanical bulk, all the same pattern.
5. **[ ] TODO** **b0-extraction** (the **3 remaining** dormant sites in `extract_and_average_b0`) — carries the `check=True` → returncode rewrite (Decision 3); done late, when the runner is proven.
6. **[ ] TODO** **reanalysis** (1 site).
- Known cosmetic diff: fsl/synB0 commands now emit the 30s "still processing…" heartbeat they didn't before.

## Testing Decisions

**What makes a good test here:** it asserts *external behavior at the seam* — the argv a stage issues, and how the stage reacts to an exit code / cancel — never the internal mechanics of how a command string was assembled. Tests script outcomes through the fake and read back `.calls` and the stage's observable result. They do not patch `subprocess`, do not touch the filesystem, and do not assume any binary is installed.

**The fake stays stateless and path-free — a deliberate testing decision.** Routing pre-baked output files through the fake (so a later stage could load them) was explicitly rejected: it would re-introduce a filesystem model, make the fake stateful and path-aware, and let it drift out of sync with what the real tools emit. End-to-end-with-real-files is the job of the integration smoke, not the fake.

**Modules under test via this seam:**
- `PipelineRunner` — command-construction across all nine stages; abort-on-non-zero; cancel-terminates. The single-injection-point property (Decision 5) is itself exercised by asserting that a fake injected at the pipeline captures registration and synB0 commands too.
- `BatchRunner` and the reanalysis CLI entry — same injection style, same assertions, lighter coverage.
- `SubprocessToolRunner` (the real adapter) — unit-tested against POSIX coreutils, requiring no toolchain: `echo` (streams lines, exit 0), `false` (exit 1), `sh -c 'sleep …'` with a `cancel_check` that flips true (terminate path), and a bogus binary name (missing → 127). This is the regression net that protects the refactor and is built **before** any caller is converted.

**Prior art and what changes:** today's `tests/test_pipeline.py` and `tests/test_registration.py` are real-binary integration scripts (`subprocess.Popen` over actual MRtrix3/FSL). They are **kept** as the end-to-end smoke test — the only true full-pipeline check, since the fake produces no files. New fake-based unit tests sit alongside them; the integration scripts gain real input-data fixtures (full data out-of-repo via env-var path, `skip-if-absent`). A small cropped/downsampled real-data fixture is committed for the future pure-module tests.

**Sample data placement (settled):** real sample data serves (b) the real-binary integration smoke and (c) the future pure-module fixtures — and **never** the `ToolRunner` fake.

## Out of Scope

- **End-to-end pipeline tests that assert an ALPS index.** The seam intercepts commands, not the `.nii.gz` they produce. Delivered later by the arrays-in ALPS module and the pure ROI-geometry module (separate candidates), which this seam unblocks.
- **Extending real cancellation to synB0/fsl/b0-extraction.** The signature supports it; only the pipeline is wired this change. Filed as a fast-follow.
- **The GUI's desktop file-browser calls** (`open`/`xdg-open`/`explorer`) — explicitly excluded from the seam.
- **Refactoring command *construction*** (the `commands.py` builders). The seam tests *that* the right argv is produced; it does not change how argv is built.
- **Windows support for the streaming adapter.** `select`-on-pipes is POSIX-only; the toolchain runs on Linux/macOS. Not addressed here.
- **Structured stderr.** Merged into one `output` stream; splitting it is a future change if a caller ever needs it.

## Implementation Progress

This section is the running record of what has shipped against the strangler plan
(Decision 10), so a future developer knows exactly where to resume. Update it as
each step lands.

### Increment 1 — foundation + pipeline (2026-06-19, branch `refactor/ToolRunner-seam`)

**Strangler steps 1–2 complete.** Steps 3–6 (fsl, synB0, b0-extraction, reanalysis)
and the Decision 9 guardrail are **not started**.

**Added**
- `dti_alps/processing/tool_runner.py` — `ToolResult` value object, `ToolRunner`
  `Protocol`, and `SubprocessToolRunner` (the real adapter, modeled on the
  pipeline's former `_run_command` superset: `select` streaming + cancel + 30s
  heartbeat; stderr merged into one `output`; never raises).
- `tests/fakes.py` — `FakeToolRunner` (stateless, predicate-scripted recorder).
- `tests/test_tool_runner.py` — the real-adapter regression net vs POSIX coreutils
  (`echo`/`printf`/`false`/`sh`/missing-binary/cancel). Built before any caller was
  touched, per User Story 15. **7 tests.**
- `tests/test_pipeline_seam.py` — fake-driven `PipelineRunner` tests covering User
  Stories 1–9, scoped to pipeline-issued commands. **9 tests.**

**Changed**
- `dti_alps/processing/pipeline.py` — `PipelineRunner.__init__` gained
  `runner: ToolRunner | None = None` (`runner or SubprocessToolRunner()`); the
  ~70-line duplicate streaming loop in `_run_command` collapsed to a delegation
  that preserves the `Running:` prelude, cancel reporting, and exit-code
  semantics. Removed now-unused `import select/subprocess/time`.

**Test status:** `pytest tests/` → 27 passed (7 adapter + 9 pipeline-seam + 11
pre-existing). `ruff check` / `ruff format --check` clean on all touched files.
Production path unchanged: GUI builds `BatchRunner(...)` → `PipelineRunner(...)`
with no `runner` arg → defaults to the real adapter.

### Increment 2 — fsl backend (2026-06-19, branch `refactor/ToolRunner-seam`)

**Strangler step 3 complete**, and with it the **2 live** b0-extraction sites and
the fsl half of deviation #2 (below). Steps 4 (synB0), 5 (the 3 dormant
b0-extraction sites), 6 (reanalysis), and the Decision 9 guardrail are **not
started**.

**Added**
- `tests/test_registration_seam.py` — fake-driven FSL-backend tests: `get_backend`
  threads the runner into the backend (and defaults to real without one); the four
  FSL commands route through `_run_fsl_command` (stream + exit-code); the two live
  b0 helpers issue `dwi2mask`/`fslmaths` through the runner and report a non-zero /
  127 outcome as a `(False, msg)` instead of raising. **7 tests.**
- `tests/test_pipeline_seam.py::test_run_registration_forwards_runner_to_backend`
  — asserts `PipelineRunner.run_registration` forwards `self.runner` to
  `registration.get_backend(...)`, the seam-crossing call. **1 test.**

**Changed**
- `dti_alps/processing/registration/fsl.py` — `FSLRegistration` gained
  `__init__(self, runner=None)` (`runner or SubprocessToolRunner()`); the ~40-line
  duplicate `Popen` streaming loop in `_run_fsl_command` collapsed to
  `self.runner.run(cmd, on_line=log).returncode == 0`; `register()` now passes
  `runner=self.runner` to the two b0 helpers. Removed now-unused `import subprocess`.
- `dti_alps/processing/b0_extraction.py` — `create_brain_mask_from_dwi` and
  `apply_mask_to_image` gained a `runner` param (default real); their
  `subprocess.run(..., check=True)` + `except CalledProcessError/FileNotFoundError`
  blocks collapsed to `result = runner.run(cmd); if result.returncode != 0: return
  False, f"… failed: {result.output}"` (Decision 3, early — these are the 2 live
  sites). `import subprocess` stays for the 3 still-dormant `extract_and_average_b0`
  sites (step 5).
- `dti_alps/processing/registration/__init__.py` — `get_backend(name, runner=None)`
  passes `runner` to `FSLRegistration(runner=...)`; `register_fa_to_template`
  (backward-compat, no production callers) gained a `runner` param it forwards.
- `dti_alps/processing/pipeline.py` — `run_registration` / `run_roi_placement`
  pass `runner=self.runner` into `registration.get_backend(...)`.

**Test status:** `pytest tests/` → 35 passed (7 adapter + 17 seam: 9 pipeline + 1
new pipeline-forwarding + 7 registration + 11 pre-existing). `ruff check` /
`ruff format --check` clean on all touched files. Production path unchanged:
`get_backend('fsl')` / `FSLRegistration()` / the four FSL backward-compat helpers
all default to the real adapter; `get_backend('ants')` still raises `ValueError`.

### Deviations from the PRD as written (read before continuing)

1. **`SubprocessToolRunner.run()` catches `OSError`, not only `FileNotFoundError`
   (Decision 3).** On the dev/CI machine (WSL) a *bare* missing command name
   raises `PermissionError`, not `FileNotFoundError`, so catching only the latter
   would let the engine crash here — violating the real invariant ("run() never
   raises"). The catch was widened to `OSError`; the `FileNotFoundError` case
   still yields the `"Command not found: …"` message, other launch failures yield
   `"Could not execute …: <err>"`. Both return `returncode=127`.

2. **`registration.get_backend(...)` is NOT yet threaded with the runner.**
   *(Increment 1 deviation — RESOLVED for fsl in Increment 2.)* As of Increment 2,
   `get_backend(name, runner=...)` threads the runner into the FSL backend and the
   pipeline forwards `self.runner`, so a fake injected at the pipeline reaches FSL
   registration / ROI-placement commands (`tests/test_registration_seam.py` +
   `test_run_registration_forwards_runner_to_backend`). The single-injection-point
   property is now **fully realized for fsl**; the only remaining gap is synB0's
   dormant backend (step 4) and reanalysis (step 6), neither of which is on the live
   pipeline path. Note the pipeline-seam tests still don't drive `register()`
   end-to-end (its real `check_available()` / file gates and nibabel loads need
   `.nii.gz` inputs the fake can't produce — that's the integration smoke's job);
   the seam is asserted at the command-issuing helpers instead.

### Findings that shape the remaining steps

- **synB0's 15 sites are dormant.** `Synb0Backend` and `run_topup_eddy`
  (`processing/synb0/backend.py`) have **no callers anywhere** — the live synB0
  route uses pre-computed external outputs via `PipelineRunner.run_eddy_with_synb0`,
  which issues its commands through `_run_command` (already on the seam). Step 4 is
  therefore a self-contained signature + call-site rewrite: add `runner` to
  `Synb0Backend.__init__` and `run_topup_eddy(...)` (default real), rewrite the 15
  `subprocess.run(...)` sites to `runner.run(...)`, and swap `result.stderr` →
  `result.output` in the error strings. Tests inject the fake directly; there is no
  "thread down from an entry" because nothing constructs it.
- **2 of the 5 b0-extraction sites are live** via `FSLRegistration.register`:
  `create_brain_mask_from_dwi` and `apply_mask_to_image` (`processing/b0_extraction.py`).
  *(Done in Increment 2 — both now take a `runner` param and use the returncode
  rewrite.)* The other 3 (`extract_and_average_b0`) are reached only through dormant
  synB0; step 5 finishes them with the same `check=True` → returncode rewrite
  (Decision 3).
- **`_run_fsl_command` has 4 call sites** (flirt, fnirt, invwarp, applywarp) all
  routing through the one helper. *(Done in Increment 2 — single body rewritten;
  `FSLRegistration` gained an `__init__` storing `runner`.)*
- **reanalysis** (step 6): the live entry is `__main__._run_reanalysis` →
  `run_reanalysis(...)` → `reanalyze_subject(...)` (the `applywarp` `subprocess.run`
  is at ~`reanalysis.py:287`, wrapped by a function-level `except CalledProcessError`
  at ~`:450`). Thread `runner` from the CLI entry down through both functions.

### Pick up here (next: step 4, synB0)

synB0 is dormant (see the first finding above): nothing constructs `Synb0Backend`
or calls `run_topup_eddy`, so there is no entry to thread the runner down from —
this is a self-contained signature + call-site rewrite, and tests inject the fake
directly into the backend.

1. Add `runner` (default real, `runner or SubprocessToolRunner()`) to
   `Synb0Backend.__init__` and to the `run_topup_eddy(...)` function signature in
   `processing/synb0/backend.py`.
2. Rewrite the 15 `subprocess.run(...)` sites to `runner.run(...)`. These are
   capture-style (`on_line=None`, read `.output`), so swap each `result.stderr` →
   `result.output` in the error strings and apply the `check=True` → returncode
   rewrite where any site used `check=True` (Decision 3 — no exception crosses the
   seam).
3. The two `extract_and_average_b0` calls inside synB0 (`_extract_b0`, and the
   `create_brain_mask_from_dwi` call at ~`backend.py:808`) should pass the
   backend's `runner` down. `create_brain_mask_from_dwi` already accepts `runner`
   (Increment 2); `extract_and_average_b0` gets its `runner` param in **step 5**, so
   sequence step 5 before/with this thread-through if you want that call faked too.
4. Add `tests/test_synb0_seam.py` injecting a `FakeToolRunner` into `Synb0Backend`
   / `run_topup_eddy` and asserting the 15 commands' argv and the failure/return
   handling (same style as `test_registration_seam.py`).

Then step 5 (the 3 dormant `extract_and_average_b0` sites — `dwiextract`,
`mrconvert`, `mrmath`) and step 6 (reanalysis, per the finding above), followed by
the Decision 9 banned-`subprocess`-import guardrail once the last site is converted.

## Further Notes

- This is the keystone candidate: it unblocks the later testability work (pure ALPS module, pure ROI-geometry module, reanalysis re-entry) by giving their tool calls a seam to cross.
- A cheap, same-direction precursor worth pairing: lifting the domain constants out of `gui.config` into a processing-owned module, so the engine imports without the GUI. Independent of this PRD but points the dependency arrow the same way (toward a shippable library).
- No `CONTEXT.md` domain glossary or `docs/adr/` existed at authoring time; this PRD doubles as the first ADR of record for the execution seam.
- The "~23 sites" / "29 call sites" discrepancy is intentional and documented in Decision 6 — the larger figure counted the excluded GUI desktop-open calls.
