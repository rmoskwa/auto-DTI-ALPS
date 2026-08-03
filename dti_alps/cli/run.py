"""
The ``run`` verb: execute the full pipeline headlessly.

Everything the GUI can start, from a terminal, over a whole cohort::

    dti-alps run --subjects /data/cohort --output /data/out

Configuration is **hybrid**. The knobs that vary per invocation -- where the data
is, where output goes, ROI shapes, ALPS method, acquisition parameters -- are
flags. The deep, rarely-touched surface (61 per-stage tool options across eight
stages, plus 15 output-retention booleans) lives in an optional **protocol
file**, which the GUI exports. A flat argparse mirror of ~95 knobs would be
unusable to type and a permanent maintenance liability; a protocol file plus
``--opt`` covers the same ground with no per-option maintenance.

**Precedence is defaults < protocol file < flags.** Every flag that also exists
in the protocol carries ``default=None`` in argparse and is applied only when
not ``None``. This is load-bearing: a flag defaulting to, say, ``FA_THRESHOLD``
would silently clobber a protocol that set ``0.35``, and it would look for all
the world like the file had been ignored.
"""

import argparse
import os
import signal
import sys
from dataclasses import fields

from ..processing.constants import (
    ALPS_METHODS,
    FA_THRESHOLD,
    ROI_METHOD_OPTIONS,
    AdaptiveSearchConfig,
)
from ..processing.state import BatchConfig
from .validators import (
    SEARCH_MAX,
    SEARCH_MIN,
    SPHERE_RADIUS_MAX,
    SPHERE_RADIUS_MIN,
    validate_search_value,
    validate_sphere_radii,
)

# --- The --opt stage vocabulary ---------------------------------------------
# Derived from BatchConfig's own `*_options` field names -- `dwifslpreproc_options`
# -> `dwifslpreproc`, `synb0_eddy_options` -> `synb0_eddy`. Deriving rather than
# transcribing means the vocabulary cannot drift as stages are added, and it is
# why the CLI does not need to import the GUI's option catalogs (which would make
# the second front end a client of the first).
_OPTIONS_SUFFIX = "_options"


def option_stages() -> dict[str, str]:
    """Map each ``--opt`` stage name to the ``BatchConfig`` field it fills."""
    return {
        f.name[: -len(_OPTIONS_SUFFIX)]: f.name
        for f in fields(BatchConfig)
        if f.name.endswith(_OPTIONS_SUFFIX)
    }


# The stages whose tool accepts ``-nthreads``: the MRtrix3 commands. FLIRT and
# FNIRT are FSL and take no such flag, and the synB0 eddy options are passed to
# `eddy`, which spells its thread control differently. A per-stage `--opt` beats
# this fan-out, so an unusual case is still expressible.
NTHREADS_STAGES = ("dwidenoise", "mrdegibbs", "dwifslpreproc", "dwi2tensor", "tensor2metric")
NTHREADS_FLAG = "-nthreads"

EPILOG = """
Examples:
  %(prog)s --subjects /data/cohort --output /data/out
      The simplest complete run: discovery, defaults, one command.

  %(prog)s --subjects /bids/sub-*/ses-1/dwi --output /data/out \\
      --config study-protocol.json --id-depth 3 --sphere 2,3 --nthreads 8
      A BIDS cohort (expanded by the shell), a protocol exported from the GUI,
      ids deep enough to stay distinct, and tuned ROIs.

  %(prog)s --subjects /bids/sub-*/ses-1/dwi --output /data/out --dry-run
      Resolve the subject list and report preflight without processing --
      ten seconds instead of a wasted weekend on a mis-typed glob.

  %(prog)s --subjects /data/cohort --output /data/out --resume
      Skip subjects already completed under this exact protocol.

Exit codes:
  0  every subject completed        2  usage or configuration error
  1  finished with >=1 failure      3  preflight failure (missing tool)
                                  130  interrupted (Ctrl-C)
"""


def equivalent_command(
    subject_paths: list[str],
    output_dir: str,
    config_path: str = "",
    id_depth: int = 1,
) -> str:
    """
    The ``dti-alps run`` command line equivalent to a given set of paths.

    Rendered here rather than in the GUI so the flag spellings have one home: a
    hand-written twin in the Qt adapter would drift the first time a flag was
    renamed, and it would drift silently -- the string looks fine either way.
    (A test asserts the rendered command parses back through the real grammar,
    which is what actually makes drift impossible.)

    This is the bridge the whole feature exists for: configure interactively
    once, copy the line, scale on the cluster. Without it, a researcher who
    exports a protocol still has to go and read ``run --help`` to learn how to
    consume it.

    >>> equivalent_command(["/data/cohort"], "/data/out")
    'dti-alps run --subjects /data/cohort --output /data/out'
    """
    parts = ["dti-alps", "run"]
    for path in subject_paths:
        parts += ["--subjects", _quote(path)]
    if not subject_paths:
        parts += ["--subjects", "<SUBJECT_FOLDER>"]
    parts += ["--output", _quote(output_dir) if output_dir else "<OUTPUT_DIR>"]
    if id_depth != 1:
        parts += ["--id-depth", str(id_depth)]
    if config_path:
        parts += ["--config", _quote(config_path)]
    return " ".join(parts)


def _quote(path: str) -> str:
    """
    Shell-quote a path only when it needs it, so the common case stays readable.

    Placeholders are rendered unquoted by their callers -- ``'<OUTPUT_DIR>'`` in
    quotes reads as a literal path somebody forgot to fill in, which is the
    opposite of the hint it is meant to be.
    """
    import shlex

    return shlex.quote(path)


def _validate_opt(value: str) -> tuple[str, str, str]:
    """Parse and validate one ``--opt STAGE:NAME=VALUE``."""
    stages = option_stages()

    if ":" not in value or "=" not in value.split(":", 1)[1]:
        raise argparse.ArgumentTypeError(
            f"expected STAGE:NAME=VALUE, got '{value}' (e.g. dwifslpreproc:-eddy_options='--repol')"
        )

    stage, rest = value.split(":", 1)
    name, raw = rest.split("=", 1)

    if stage not in stages:
        raise argparse.ArgumentTypeError(
            f"unknown stage '{stage}'. Valid stages: {', '.join(sorted(stages))}"
        )
    if not name:
        raise argparse.ArgumentTypeError(f"missing option name in '{value}'")

    return (stage, name, raw)


def _coerce(raw: str):
    """
    Turn a ``--opt`` value into what ``commands.py`` expects.

    An empty value is a flag (``True``), because ``--opt dwi2tensor:-ols=`` is
    how a no-argument option is spelled on a command line. Numbers are coerced
    so they reach the option dict the way the GUI's ``int`` widgets deliver
    them; everything else stays a string.
    """
    if raw == "":
        return True
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Wire the ``run`` flags onto ``parser``, in five groups."""
    data = parser.add_argument_group("data")
    data.add_argument(
        "--subjects",
        action="append",
        metavar="PATH",
        default=None,
        help=(
            "Folder to discover subjects in; repeatable. Each path is scanned "
            "exactly as the GUI's 'Add folder' does (one level of subdirectory "
            "fallback). Deeper trees are reached with a shell glob, e.g. "
            "--subjects /bids/sub-*/ses-1/dwi"
        ),
    )
    data.add_argument(
        "--dwi",
        metavar="FILE",
        help="Single-subject escape hatch: the DWI image (requires --bvec/--bval)",
    )
    data.add_argument("--bvec", metavar="FILE", help="Gradient directions for --dwi")
    data.add_argument("--bval", metavar="FILE", help="b-values for --dwi")
    data.add_argument("--json", metavar="FILE", help="BIDS JSON sidecar for --dwi")
    data.add_argument(
        "--rpe",
        metavar="FILE",
        help=(
            "Reverse phase-encoding image for --dwi. The only way to override "
            "discovery's first-match guess (inert unless --rpe-scheme pair)"
        ),
    )
    data.add_argument(
        "--id-depth",
        type=int,
        default=1,
        metavar="N",
        help=(
            "How many trailing path components form each subject id (default: 1, "
            "the folder name). --id-depth 3 turns sub-01/ses-1/dwi into "
            "sub-01_ses-1_dwi, which a BIDS cohort needs to stay distinguishable"
        ),
    )

    placement = parser.add_argument_group("placement")
    placement.add_argument(
        "--output",
        required=True,
        metavar="DIR",
        help="Where results land. Required -- a protocol file carries no paths",
    )
    placement.add_argument(
        "--staging",
        action="store_true",
        help="Stage data to fast local storage first (for slow network mounts)",
    )
    placement.add_argument(
        "--staging-dir", metavar="DIR", help="Staging base directory (default: system temp)"
    )

    protocol = parser.add_argument_group("protocol source")
    protocol.add_argument(
        "--config",
        metavar="FILE",
        help="Protocol file (JSON) exported from the GUI. Flags below override it",
    )

    acq = parser.add_argument_group("acquisition")
    acq.add_argument(
        "--pe-dir",
        choices=["AP", "PA", "LR", "RL", "SI", "IS"],
        default=None,
        help=(
            "Phase-encoding direction. Setting it explicitly also disables "
            "JSON-sidecar auto-detection, which would otherwise overrule it"
        ),
    )
    acq.add_argument(
        "--readout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Total readout time. Omitted: auto-extract from the JSON sidecar/NIfTI",
    )
    acq.add_argument(
        "--rpe-scheme",
        choices=["none", "pair", "all", "header"],
        default=None,
        help="Reverse phase-encoding scheme passed to dwifslpreproc",
    )
    acq.add_argument(
        "--synb0-dir",
        metavar="DIR",
        default=None,
        help=(
            "synB0-DISCO OUTPUTS directory. Supplying it selects the 10-stage "
            "route (eddy with synB0's topup outputs) instead of dwifslpreproc"
        ),
    )

    stages = parser.add_argument_group("stages")
    stages.add_argument("--no-denoise", action="store_true", help="Skip dwidenoise (stage 2)")
    stages.add_argument("--no-degibbs", action="store_true", help="Skip mrdegibbs (stage 3)")

    roi = parser.add_argument_group("ROI / ALPS")
    roi.add_argument(
        "--sphere",
        type=validate_sphere_radii,
        metavar="RADIUS[,RADIUS,...]",
        help=(
            f"Spherical ROIs of the given radius/radii "
            f"({SPHERE_RADIUS_MIN}-{SPHERE_RADIUS_MAX} mm), comma-separated"
        ),
    )
    roi.add_argument("--squarev9", action="store_true", help="3x3 voxel square ROIs")
    roi.add_argument("--squarev4", action="store_true", help="2x2 voxel square ROIs")
    roi.add_argument(
        "--roi-method",
        choices=ROI_METHOD_OPTIONS,
        default=None,
        help="ROI placement method (reanalyze takes the same flag)",
    )
    search_suffix = f"(±voxels, {SEARCH_MIN}-{SEARCH_MAX}; inert unless Adaptive placement runs)"
    for flag, dest_help in (
        ("--search-x", "Adaptive search window in X"),
        ("--search-y", "Adaptive search window in Y"),
        ("--search-z", "Adaptive search window in Z"),
        ("--max-y-drift", "Max association-ROI Y drift from projection ROI"),
        ("--max-z-drift", "Max association-ROI Z drift from projection ROI"),
    ):
        roi.add_argument(
            flag,
            type=validate_search_value,
            default=None,
            metavar="N",
            help=f"{dest_help} {search_suffix}",
        )
    roi.add_argument(
        "--method",
        choices=ALPS_METHODS,
        default=None,
        help="ALPS calculation method",
    )
    roi.add_argument(
        "--fa-threshold",
        type=float,
        default=None,
        metavar="F",
        help=f"FA threshold for filtering CSF voxels (default: {FA_THRESHOLD})",
    )

    tooling = parser.add_argument_group("tooling")
    tooling.add_argument(
        "--opt",
        action="append",
        type=_validate_opt,
        default=None,
        metavar="STAGE:NAME=VALUE",
        help=(
            "Set any tool option, repeatable, e.g. "
            "--opt dwifslpreproc:-eddy_options='--repol --slm=linear'. "
            f"Stages: {', '.join(sorted(option_stages()))}"
        ),
    )
    tooling.add_argument(
        "--nthreads",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Thread count for every MRtrix3 stage at once (wire it to "
            "$SLURM_CPUS_PER_TASK). A per-stage --opt wins over it"
        ),
    )

    execution = parser.add_argument_group("execution")
    execution.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve subjects, report preflight, print what would happen, and exit",
    )
    execution.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip subjects already completed under this exact protocol. An edited "
            "protocol reprocesses everything, so a cohort is never half-and-half"
        ),
    )
    execution.add_argument(
        "--fail-fast", action="store_true", help="Stop at the first subject failure"
    )
    execution.add_argument("--quiet", action="store_true", help="Suppress raw MRtrix3/FSL output")


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------


def build_config(args: argparse.Namespace) -> BatchConfig:
    """
    Assemble the ``BatchConfig`` for this invocation.

    Defaults < protocol file < flags. Placement comes from flags only -- the
    protocol carries none -- which is what removes the "does an absent --output
    mean use the file's path?" ambiguity.

    Raises
    ------
    ProtocolError
        The ``--config`` file is unreadable or carries unknown keys.
    """
    from ..processing.config_io import read_protocol

    config = BatchConfig()
    if args.config:
        config = read_protocol(args.config)

    # Placement: flags only.
    config.output_dir = os.path.abspath(args.output)
    config.staging_enabled = args.staging
    config.staging_dir = args.staging_dir

    _apply_acquisition(config, args)
    _apply_stages(config, args)
    _apply_roi(config, args)
    _apply_tooling(config, args)

    return config


def _apply_acquisition(config: BatchConfig, args: argparse.Namespace) -> None:
    if args.pe_dir is not None:
        config.pe_direction = args.pe_dir
        # `auto_pe_direction` defaults True and BatchRunner lets a JSON sidecar
        # override `pe_direction`, so without this an explicit --pe-dir AP would
        # do nothing for any subject carrying a sidecar that says `j`. Making the
        # explicit flag disable auto-detection is the least surprising reading,
        # and it avoids a separate --no-auto-pe nobody would think to reach for.
        config.auto_pe_direction = False
    if args.readout is not None:
        config.readout_time = args.readout
    if args.rpe_scheme is not None:
        config.rpe_scheme = args.rpe_scheme
    if args.synb0_dir is not None:
        # Supplying the input selects the route; a redundant --use-synb0 mode
        # flag would only create a state where one is set and the other is not.
        config.synb0_output_dir = args.synb0_dir
        config.use_synb0 = True


def _apply_stages(config: BatchConfig, args: argparse.Namespace) -> None:
    # These are `store_true`, so "absent" is False, and False must mean "say
    # nothing" rather than "run the stage" -- otherwise omitting --no-denoise
    # would re-enable a stage the protocol had switched off.
    if args.no_denoise:
        config.run_denoising = False
    if args.no_degibbs:
        config.run_degibbs = False


def _apply_roi(config: BatchConfig, args: argparse.Namespace) -> None:
    shapes: list[dict] = []
    if args.sphere:
        shapes.extend({"type": "sphere", "radius": r} for r in args.sphere)
    if args.squarev9:
        shapes.append({"type": "squarev9"})
    if args.squarev4:
        shapes.append({"type": "squarev4"})
    if shapes:
        config.roi_shapes = shapes

    if args.roi_method is not None:
        config.adaptive_roi_placement = args.roi_method
    if args.method is not None:
        config.alps_method = args.method
    if args.fa_threshold is not None:
        config.fa_threshold = args.fa_threshold

    # The envelope is a frozen value, so a partial override rebuilds it from the
    # current one -- setting only --search-x must not reset the other four.
    current = config.adaptive_search
    overrides = {
        "search_x": args.search_x,
        "search_y": args.search_y,
        "search_z": args.search_z,
        "max_y_drift": args.max_y_drift,
        "max_z_drift": args.max_z_drift,
    }
    if any(v is not None for v in overrides.values()):
        config.adaptive_search = AdaptiveSearchConfig(
            **{
                name: (value if value is not None else getattr(current, name))
                for name, value in overrides.items()
            }
        )


def _apply_tooling(config: BatchConfig, args: argparse.Namespace) -> None:
    stages = option_stages()

    # --nthreads first, so an explicit per-stage --opt overrides it.
    if args.nthreads is not None:
        for stage in NTHREADS_STAGES:
            getattr(config, stages[stage])[NTHREADS_FLAG] = args.nthreads

    for stage, name, raw in args.opt or []:
        getattr(config, stages[stage])[name] = _coerce(raw)


# ---------------------------------------------------------------------------
# Subject resolution
# ---------------------------------------------------------------------------


def resolve_subjects(args: argparse.Namespace) -> list:
    """
    Turn the data flags into a deduped subject list.

    ``--subjects`` goes through ``discover_with_subdir_fallback`` -- identical
    semantics to the GUI's "Add folder" -- and repeats are deduped by DWI path.
    BIDS trees are handled by the shell rather than by new discovery code: shell
    globbing is more expressive than any ``--depth`` flag, and a recursive walk
    here would be CLI-only behaviour, so GUI and CLI discovery would diverge.
    """
    from ..processing.discovery import (
        SubjectFiles,
        discover_with_subdir_fallback,
        new_unique_runs,
        subject_id_from_path,
    )

    if args.dwi:
        folder = os.path.dirname(os.path.abspath(args.dwi))
        return [
            SubjectFiles(
                folder_path=folder,
                subject_id=subject_id_from_path(folder, args.id_depth),
                dwi_path=args.dwi,
                bvec_path=args.bvec,
                bval_path=args.bval,
                json_sidecar_path=args.json,
                reverse_pe_path=args.rpe,
            )
        ]

    subjects: list = []
    for path in args.subjects or []:
        discovered = discover_with_subdir_fallback(path, args.id_depth)
        subjects.extend(new_unique_runs(subjects, discovered))
    return subjects


def validate_data_flags(args: argparse.Namespace) -> str | None:
    """Return a usage error for the data flags, or ``None`` if they are coherent."""
    single = [args.dwi, args.bvec, args.bval]

    if args.subjects and any(single):
        return "--subjects and --dwi/--bvec/--bval are mutually exclusive"
    if not args.subjects and not any(single):
        return "one of --subjects or --dwi/--bvec/--bval is required"
    if any(single) and not all(single):
        return "--dwi, --bvec and --bval must be given together"
    if args.id_depth < 1:
        return f"--id-depth must be at least 1, got {args.id_depth}"
    return None


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(use_synb0: bool) -> list[str]:
    """
    Return the external commands that are missing, in report order.

    A thin re-export of the engine's :func:`~dti_alps.processing.commands.preflight`
    so the GUI asks the same question (it cannot import a CLI module). Kept as a
    module-level name because the suite monkeypatches ``run.preflight`` to fake a
    broken PATH.
    """
    from ..processing.commands import preflight as engine_preflight

    return engine_preflight(use_synb0)


def _preflight_report(missing: list[str]) -> list[str]:
    if not missing:
        return ["Preflight: all required MRtrix3 and FSL commands found."]
    return [
        "Preflight FAILED. These commands are not on PATH:",
        *(f"  {cmd}" for cmd in missing),
        "",
        "Install MRtrix3 and FSL, or load the modules that provide them, and retry.",
    ]


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def partition_for_resume(subjects: list, config: BatchConfig) -> tuple[list, list]:
    """
    Split ``subjects`` into (already done under this protocol, still to do).

    The completion marker's protocol hash is what makes this safe rather than
    heuristic -- see ``results_layout.CompletionMarker``.
    """
    from ..processing.config_io import protocol_hash
    from ..processing.results_layout import is_complete_for_protocol

    digest = protocol_hash(config)
    done, todo = [], []
    for subject in subjects:
        subject_dir = os.path.join(config.output_dir, subject.subject_id)
        (done if is_complete_for_protocol(subject_dir, digest) else todo).append(subject)
    return done, todo


def _results_from_markers(subjects: list, config: BatchConfig) -> list:
    """
    Rebuild ``SubjectResult`` rows for resumed subjects from their markers.

    Without this the results CSV of a resumed run would hold only the subjects
    processed in *this* invocation -- so resuming at 180 of 200 would produce a
    20-row cohort file, which is a worse outcome than the recomputation resume
    exists to avoid.
    """
    from ..processing.results_layout import read_completion_marker
    from ..processing.state import SubjectResult

    rows = []
    for subject in subjects:
        marker = read_completion_marker(os.path.join(config.output_dir, subject.subject_id))
        result = SubjectResult(
            subject_id=subject.subject_id,
            folder_path=subject.folder_path,
            status="completed",
            alps_results_by_shape=dict(marker.alps_by_shape) if marker else {},
        )
        # The primary fields feed the single-CSV fallback path; take the first
        # shape, matching how BatchRunner populates them from a live run.
        for shape in result.alps_results_by_shape.values():
            result.alps_method = shape.get("alps_method")
            result.alps_lab_left = shape.get("alps_lab_left")
            result.alps_lab_right = shape.get("alps_lab_right")
            result.alps_lab_bilateral = shape.get("alps_lab_bilateral")
            result.alps_pas_left = shape.get("alps_pas_left")
            result.alps_pas_right = shape.get("alps_pas_right")
            result.alps_pas_bilateral = shape.get("alps_pas_bilateral")
            break
        rows.append(result)
    return rows


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def execute(args: argparse.Namespace) -> int:
    """Run the pipeline over the resolved cohort. Returns the process exit code."""
    from ..processing.batch import BatchRunner
    from ..processing.config_io import ProtocolError
    from ..processing.discovery import SubjectIdCollisionError, check_unique_subject_ids
    from ..processing.run_log import LogFileSink
    from ..processing.state import BatchState
    from . import render
    from .main import EXIT_FAILURES, EXIT_INTERRUPTED, EXIT_OK, EXIT_PREFLIGHT, EXIT_USAGE

    def fail(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return EXIT_USAGE

    usage_error = validate_data_flags(args)
    if usage_error:
        return fail(usage_error)

    try:
        config = build_config(args)
    except ProtocolError as err:
        return fail(str(err))

    subjects = resolve_subjects(args)
    if not subjects:
        return fail("no subjects found. Check the paths given to --subjects")

    try:
        check_unique_subject_ids(subjects)
    except SubjectIdCollisionError as err:
        return fail(str(err))

    missing = preflight(config.use_synb0)

    if args.dry_run:
        for line in render.format_subject_table(subjects, config.output_dir, args.id_depth):
            print(line)
        if args.resume:
            done, todo = partition_for_resume(subjects, config)
            print(f"--resume: {len(done)} already complete, {len(todo)} to process")
        for line in _preflight_report(missing):
            print(line)
        return EXIT_PREFLIGHT if missing else EXIT_OK

    if missing:
        for line in _preflight_report(missing):
            print(line, file=sys.stderr)
        return EXIT_PREFLIGHT

    invalid = [s.subject_id for s in subjects if not s.is_valid]
    if invalid:
        return fail("these subjects are missing required files: " + ", ".join(invalid[:10]))

    resumed: list = []
    if args.resume:
        resumed, subjects = partition_for_resume(subjects, config)
        if resumed:
            print(f"Resuming: skipping {len(resumed)} subject(s) already complete")
        if not subjects:
            print("Nothing to do -- every subject is already complete.")
            return EXIT_OK

    batch_state = BatchState(config=config, subjects=subjects)
    # Resumed subjects are seeded as results, not as work, so the cohort CSV
    # this run writes covers everyone -- not just the tail.
    batch_state.results.extend(_results_from_markers(resumed, config))

    renderer = render.TerminalRenderer(quiet=args.quiet)
    runner = BatchRunner(batch_state)
    if args.fail_fast:
        runner = _make_fail_fast(runner)

    interrupted = _install_sigint_handler(runner)

    with LogFileSink(config.output_dir, config.output_config) as sink:
        runner.progress_callback = sink.wrap(renderer.handle)
        runner.run_batch()

    if interrupted():
        print(f"\nInterrupted. {render.summarize(batch_state)}", file=sys.stderr)
        return EXIT_INTERRUPTED

    return EXIT_FAILURES if batch_state.failed_count else EXIT_OK


def _make_fail_fast(runner):
    """
    Make ``runner`` stop at the first subject failure.

    Off by default, matching ``BatchRunner``'s continue-on-error, because one
    subject failing on its own data should not cost the other 199. It is worth
    having because the opposite case is just as common: a bad protocol fails all
    200 identically, and watching that happen for six hours helps nobody.

    Implemented by wrapping the per-subject step rather than by threading a flag
    through the engine -- the policy is the front end's, so it lives here.
    """
    original = runner._process_single_subject

    def _step(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.status == "failed":
            runner.cancel()
        return result

    runner._process_single_subject = _step
    return runner


def _install_sigint_handler(runner):
    """
    Make Ctrl-C cancel the batch cleanly instead of killing the process.

    ``run_batch`` already handles cancellation properly: the loop breaks,
    remaining subjects are marked skipped, and the results CSV is still written
    with the finished subjects' numbers. Dying on the signal instead would throw
    those away. A second Ctrl-C restores the default handler's behaviour, so an
    unresponsive run is never un-killable.
    """
    state = {"seen": False}
    previous = signal.getsignal(signal.SIGINT)

    def _handler(signum, frame):
        if state["seen"]:
            signal.signal(signal.SIGINT, previous)
            raise KeyboardInterrupt
        state["seen"] = True
        print(
            "\nCancelling after the current subject (Ctrl-C again to abort now)...",
            file=sys.stderr,
        )
        runner.cancel()

    try:
        signal.signal(signal.SIGINT, _handler)
    except ValueError:
        # Not on the main thread (e.g. under a test runner): cancellation by
        # signal is simply unavailable, which is not a reason to refuse the run.
        pass

    return lambda: state["seen"]
