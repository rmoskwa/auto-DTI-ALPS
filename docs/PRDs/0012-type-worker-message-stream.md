# PRD 0012 — Type the Worker→GUI Message Stream

Status: Grilled · Date: 2026-07-06 · Grilled: 2026-07-06 · Source: Pre-port readiness for the Tkinter→PySide6 migration. This is roadmap step 3 of the port plan (the typed message stream), taken *before* the `app.py` port so the port leans on an explicit, tested contract instead of ad-hoc tuples.

This PRD also serves as the ADR of record for the decisions below. Each numbered Implementation Decision states the choice, the alternative that was rejected, and why — read it as the rationale trail, not just a task list.

> **Process note.** Grilled 2026-07-06 in the same session that planned the `app.py` port sequencing. The port plan lists this typed stream as step 3, independent of the form-model work (PRD 0011) and of the port itself (a later PRD). It is landed first because it is the lowest-risk, still-Tk item on the board, and because it hardens the exact seam — `ResultModel` and the worker message kinds — that the port's output side will lean on.

---

## Problem Statement

The background workers talk to the GUI over a `queue.Queue` carrying ad-hoc
`(msg_type: str, data: Any)` tuples. `PipelineRunner` and `BatchRunner` emit them
through a `progress_callback: Callable[[str, Any], None]`; `PipelineWorker` /
`BatchWorker` forward them onto the queue; the Tk `app.py` drains the queue and hands
each tuple to `ResultModel.handle(msg)`, which reads `msg[0]`/`msg[1]` and switches on
the string.

Two problems:

1. **Stringly-typed, unenforced contract.** The message kind is a bare string and the
   payload is `Any`. A typo in a `msg_type`, or a payload of the wrong shape, is caught
   nowhere — not at the producer, not on the wire, not until (if ever) it mismatches
   inside `handle()`. There is no single place that names the legal messages or their
   payloads.
2. **Silent drop on the unknown.** `ResultModel.handle` ends in `return []` — any message
   it has no case for produces no intents and vanishes without a trace. That fallback is
   currently *load-bearing for dead code*: `PipelineWorker` still emits
   `complete`/`cancelled`/`failed`, none of which `handle()` consumes (they were removed
   from `ResultModel` in PRD 0006), so they hit the silent `return []` on every
   single-subject run — except the GUI never starts a `PipelineWorker` at all. It only
   ever starts `BatchWorker` (`app.py:2037`); `PipelineWorker` is uninstantiated
   throughout `dti_alps/` and `tests/`, surviving only in `__all__`.

So the live message contract is 10 kinds, all flowing through `BatchWorker` and its
inner `PipelineRunner`: `log`, `stage`, `batch_start`, `subject_start`,
`subject_complete`, `batch_complete`, `batch_success`, `batch_partial`,
`batch_cancelled`, `error`. Three more kinds (`complete`, `cancelled`, `failed`) exist
only on the dead `PipelineWorker`.

When `app.py` is ported to PySide6, its output side rewrites `_apply_intent` and the
queue drain (`after(100)` → `QTimer`) against this same stream. A typed, closed contract
makes that rewrite reason about known messages; the current tuples + silent fallback
make it inherit an ambiguity in the middle of the largest diff of the migration.

## Solution

Replace the `(str, Any)` tuples with a closed set of frozen **message dataclasses** that
both the producers and `ResultModel` import, and make an unhandled message a **loud,
test-caught error** instead of a silent drop.

First, **delete the dead `PipelineWorker`** and its three orphan message kinds, so the
typed union describes only live reality. Then introduce `processing/messages.py`
containing the frozen dataclasses and the `WorkerMessage` union. Then flip the stream:
producers construct instances, the `progress_callback` signature becomes
`Callable[[WorkerMessage], None]`, the queue carries `WorkerMessage` objects, and
`ResultModel.handle` dispatches with a `match` whose `case _` raises.

The dependency arrow (`gui → processing`, never the reverse; pinned by the
import-guard test) forces the dataclasses into `processing/` — the producers live there
and may not import `gui`. `processing/messages.py` is stdlib-only (`dataclasses`), so the
Qt-free guardrail holds trivially. Payloads that carry domain objects
(`SubjectResult`, `BatchState`) import them from `processing/state.py` (same package, no
cycle — `state` does not import `messages`).

The registration backends' separate `log_callback: Callable[[str], None]` is a
*different* channel (plain string logging, not the progress stream) and is **not** part
of this union; it is untouched.

The work lands as **three behavior-preserving commits**, each leaving the suite green
and the app runnable:

1. **Delete dead `PipelineWorker`** + its `complete`/`cancelled`/`failed` kinds, and trim
   the `__all__` exports in `processing/__init__.py` and `processing/pipeline.py`. Pure
   deletion; nothing instantiates it.
2. **Add `processing/messages.py`** — the frozen message dataclasses + the `WorkerMessage`
   union, with a small construction test. Wired into nothing; importable, unreferenced,
   green.
3. **Flip the stream** — `pipeline._log`/`_report_stage`, `batch._notify`, and
   `workers.py` emit typed instances; `progress_callback` is retyped to
   `Callable[[WorkerMessage], None]`; `ResultModel.handle` dispatches via `match` with a
   fail-fast `case _`. Update `tests/test_result_model.py` (convert the golden lifecycle
   to typed messages) and the one `progress` callback in `tests/test_pipeline_seam.py`.
   This commit is **necessarily atomic**: producer and consumer share the queue, so the
   type on the wire flips in lockstep.

It is independent of the form-model extraction (PRD 0011, done) and precedes the `app.py`
port (a later PRD), which is out of scope here.

## User Stories

1. As a maintainer, I want the worker→GUI messages to be a closed set of typed
   dataclasses that both the producer and `ResultModel` import, so that the legal
   messages and their payloads are named in exactly one place.
2. As a maintainer, I want a producer that emits a malformed or misnamed message to fail
   at construction against a dataclass, not silently mismatch downstream, so that the
   contract is enforced at the source.
3. As a maintainer, I want `ResultModel.handle` to **raise** on a message type it does not
   handle, so that a future added-message-without-a-handler is caught by the suite rather
   than dropped at runtime.
4. As a maintainer, I want the dead `PipelineWorker` and its `complete`/`cancelled`/
   `failed` messages deleted, so that the typed union describes only the live batch
   route and carries no orphan kinds.
5. As a maintainer, I want the message dataclasses in `processing/messages.py` (Qt-free,
   stdlib-only), so that the `gui → processing` dependency arrow is preserved and the
   producers can import them.
6. As a maintainer, I want the flip to be behavior-preserving — `ResultModel.handle`
   produces the identical view-intents for the identical lifecycle — so that the GUI's
   observable behavior (log phrasing, row status, stage buttons, batch results) is
   unchanged.
7. As a maintainer, I want the registration `log_callback` (plain string logging) left
   out of this union and untouched, so that the change is scoped to the progress stream
   only.
8. As the eventual `app.py` porter, I want the output side to rewrite its adapter against
   a closed, typed message contract, so that the Qt drain reasons about known messages
   rather than inheriting stringly-typed tuples and a silent fallback.

## Implementation Decisions

### 1. Delete the dead `PipelineWorker` first, as its own commit

`PipelineWorker` is uninstantiated across `dti_alps/` and `tests/`; the GUI runs every
job — even one subject — as a batch (`BatchWorker`, `app.py:2037`), consistent with the
"no single-subject results view" decision in PRD 0006. Its `complete`/`cancelled`/
`failed` messages are exactly the kinds `handle()` drops silently. **Rejected:** keeping
it and typing all 13 kinds — that would either bloat the union with three unconsumed
members or force a "typed-but-unhandled" caveat, re-introducing the very ambiguity this
PRD removes. Deleting first (mirroring PRD 0009's "delete the dead single-ROI refine and
the back-compat API before building the clean module") makes the typed union exactly the
live set. `PipelineWorker` living in `__all__` is not treated as a frozen public API —
consistent with deleting `register_fa_to_template` in PRD 0009.

### 2. Typed end-to-end, not typed-at-the-boundary

Producers construct instances (`self._notify(BatchStart(total))`); the
`progress_callback` signature becomes `Callable[[WorkerMessage], None]`; the queue
carries `WorkerMessage` objects. **Rejected:** keeping `(str, Any)` tuples on the wire
and parsing to a typed object only at `handle()`'s entry. That is a smaller diff but
leaves the producer side stringly-typed — a typo'd `msg_type` or wrong-shaped `data` is
still uncaught at the source, which is half the point. The emit surface is small and
contained: `pipeline._log`/`_report_stage` and `batch._notify` are the only helpers, so
call sites like `self._log("…")` are unchanged and only the two helper bodies plus
`workers.py` construct instances.

### 3. Home: a dedicated `processing/messages.py`

The dependency arrow forces the dataclasses into `processing/` (producers import them;
`processing` may not import `gui`). Within `processing/`, a dedicated `messages.py` beats
folding into `state.py`: the worker→GUI *protocol* is a distinct concern from domain
*state*, and a file named `messages.py` is self-documenting for the porter. Stdlib
`dataclasses` only — the Qt-free guardrail holds trivially. No import cycle: `messages`
imports `SubjectResult`/`BatchState` from `state`; `state` does not import `messages`.

### 4. Fail-fast on the unknown message; the one-of-each test is the enforcement

`ResultModel.handle` dispatches with `match` and a `case _:` that raises
(`raise ValueError(f"unhandled worker message: {msg!r}")`). With a closed, typed union
and every producer emitting a known type, a fallthrough is *only* possible as a
programmer error (a new message without a handler). **Rejected:** keeping a lenient
log-and-ignore. The project has no mypy/pyright (ruff only), so there is no static
exhaustiveness check; the runtime `raise` plus a unit test that constructs one instance
of every `WorkerMessage` member and asserts `handle()` accepts it *is* the exhaustiveness
enforcement. Because the closed union + test guarantee the raise cannot fire in the
field, running inside the Tk `after()` poll loop is safe: it is a test-time failure, never
a runtime one.

### 5. Message set and payloads (the live contract)

The `WorkerMessage` union is exactly:

| Message | Payload | Emitted by |
|---|---|---|
| `Log` | `text: str` | `PipelineRunner._log` |
| `Stage` | `stage: str, status: str` | `PipelineRunner._report_stage` |
| `BatchStart` | `total: int` | `BatchRunner` |
| `SubjectStart` | `index: int, subject_id: str` | `BatchRunner` |
| `SubjectComplete` | `index: int, result: SubjectResult` | `BatchRunner` |
| `BatchComplete` | `batch_state: BatchState` | `BatchRunner` |
| `BatchSuccess` | `batch_state: BatchState` | `BatchWorker` |
| `BatchPartial` | `batch_state: BatchState` | `BatchWorker` |
| `BatchCancelled` | *(none)* | `BatchWorker` |
| `Error` | `message: str` | `BatchWorker` |

All frozen dataclasses. `BatchCancelled` carries no payload (the old `None` data). This is
the whole union; the port's output side dispatches over exactly these.

### 6. Behavior-preserving; identical intents out

The flip must not change any view-intent `ResultModel` produces for a given lifecycle —
same log phrasing, same `SetRowStatus`/`UpdateStageStatus`/`ResetStageButtons`/
`ShowBatchResults`. The converted golden lifecycle test is the guard: it asserts the
intent list is byte-identical before and after the flip.

## Testing Decisions

Consistent with the project's deliberate pattern — no pytest-qt, no `QApplication` test
infra; the tk-free models carry the logic and the tests.

- **Convert the golden lifecycle** (`test_result_model.py::test_batch_lifecycle_golden`)
  from tuple messages to typed `WorkerMessage` instances. Same expected intents — this is
  the behavior-preservation guard.
- **Add a one-of-each exhaustiveness test** — construct every member of `WorkerMessage`
  and assert `handle()` accepts each without raising. This is the real enforcement behind
  the fail-fast `case _`; the golden alone misses `BatchPartial`, `BatchCancelled`, and
  `Error`.
- **Touch the seam callback** — `test_pipeline_seam.py`'s local `progress` callback checks
  `msg_type == "log"`; it becomes an `isinstance(msg, Log)` check.
- A small **construction test** for `processing/messages.py` in commit 2 (frozen, fields
  present).
- **No producer-side test** (e.g. asserting `batch._notify` emits the right instance) —
  the seam test's existing pipeline coverage plus the consumer golden are enough; a
  dedicated producer test would mostly re-test dataclass construction.
- Manual smoke of a real batch run after the flip.

## Out of Scope

- **The `app.py` port itself** (roadmap step 4) — a later PRD with its own grill; this
  PRD only makes the contract it consumes explicit.
- **Retyping the registration `log_callback`** — a different channel; untouched.
- **Any change to view-intents or `ResultModel`'s output side** — `BatchResultsView`,
  the intents, and `build_batch_results_table` are unchanged (PRDs 0006/0007).
- **`git filter-repo` history rewrites, LFS reclamation, or other unrelated cleanups.**

## Further Notes

- Follows the single-concern / behavior-preserving / green-at-each-step working style
  (see PRDs 0001–0006, 0009, 0011): delete dead first, add the clean type, flip in one
  atomic behavior-preserving commit.
- `CONTEXT.md` gains a *worker message stream* section (the `WorkerMessage` union, the
  closed-union raise policy, and the single live producer path) alongside the
  results-on-disk contract.
- Roadmap position: step 3 of the port plan. Precedes the `app.py` port; independent of
  PRD 0011.
