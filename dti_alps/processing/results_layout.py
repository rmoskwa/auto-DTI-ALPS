"""
The results-on-disk contract for the DTI-ALPS engine.

A dependency-free leaf (stdlib only -- ``csv``, ``dataclasses``, ``pathlib``):
it imports nothing from the package, so it can never take part in an import
cycle, and it carries **no GUI text** -- it speaks ROI *tokens*, never display
names. This is the single home for the convention that the engine writes and
the viewer/reports read:

* the ROI-directory naming (``rois`` / ``rois_{token}`` / ``rois_{token}_adaptive``)
  via :func:`roi_dir_name` / :func:`parse_roi_dir`,
* the ALPS-results CSV naming (``alps_results.csv`` / ``alps_results_{token}.csv``)
  via :func:`alps_csv_name`, and
* the ALPS column schema (:func:`alps_columns`) plus the symmetric reader/writer
  pair over the same :class:`AlpsTable` value -- :func:`read_alps_csv` detects
  the ALPS method from the present columns and parses the per-subject rows
  (preserving the legacy no-suffix ``... ALPS`` fallback the old viewer carried,
  even though the writers never emit it), and :func:`write_alps_csv` emits the
  suffixed schema so ``read(write(table))`` round-trips, and
* the canonical ROI-mask identity: the four :data:`ROI_NAMES` and the mask
  filename pattern as a producer/consumer pair (:func:`roi_mask_name` /
  :func:`roi_mask_glob`).

Only the viewer's consumers are repointed onto this module for now;
repointing the processing-side writers/parsers (``batch``,
``reanalysis``, ``report``, ``pipeline``, ``registration/fsl``) and the
canonical ROI-name set is a recorded follow-up, mirroring how
``processing/constants.py`` was created, repointing only the consumers it needed.

A **token** is the machine name of an ROI configuration as it appears on disk:
``rois`` (the default 3.0 mm sphere), ``squarev9``, ``squarev4``, ``sphere2p5``,
``sphere3``, optionally suffixed ``_adaptive``. The default ``rois`` token maps to
the bare ``rois`` directory and ``alps_results.csv``; every other token gets the
``rois_{token}`` / ``alps_results_{token}.csv`` form.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

# --- ALPS method labels (detected from the CSV column set) ------------------
METHOD_LAB = "ALPS-LAB"
METHOD_PAS = "ALPS-PAS"
METHOD_BOTH = "Both"

# --- ALPS column schema -----------------------------------------------------
# The canonical column names the writers emit and the reader consumes.
COL_FILENAME = "Filename"
COL_STATUS = "Status"
COL_ERROR = "Error"

COL_LAB_LEFT = "Left Hemisphere ALPS-LAB"
COL_LAB_RIGHT = "Right Hemisphere ALPS-LAB"
COL_LAB_COMBINED = "Combined ALPS-LAB"

COL_PAS_LEFT = "Left Hemisphere ALPS-PAS"
COL_PAS_RIGHT = "Right Hemisphere ALPS-PAS"
COL_PAS_COMBINED = "Combined ALPS-PAS"

# Legacy no-suffix columns. Read-only fallback: the current writers never emit
# these, but older result CSVs used them, so the reader still resolves them.
COL_LEGACY_LEFT = "Left Hemisphere ALPS"
COL_LEGACY_RIGHT = "Right Hemisphere ALPS"
COL_LEGACY_COMBINED = "Combined ALPS"

# The default ROI token: the bare ``rois/`` directory and ``alps_results.csv``.
DEFAULT_ROI_TOKEN = "rois"
_ROI_DIR_PREFIX = "rois_"

# The default sphere radius (mm). A sphere of this radius is *the* default ROI
# and collapses to the bare ``rois`` token (see :func:`shape_token`).
DEFAULT_SPHERE_RADIUS = 3.0

# --- ROI-mask identity ------------------------------------------------------
# The four canonical ROI-mask names (projection/association x left/right). The
# single home for the set: the registration backend builds its template-path
# dict from it and the viewer globs each name. Order is functionally inert --
# every consumer keys by name or combines masks.
ROI_NAMES = ("left_proj", "right_proj", "left_assoc", "right_assoc")

# The on-disk ROI-mask filename pattern, shared by the producer
# (:func:`roi_mask_name`) and the consumer (:func:`roi_mask_glob`) so the name a
# backend writes and the glob the viewer uses to find it cannot drift.
_ROI_MASK_TEMPLATE = "{subject}_{roi_name}.nii.gz"

# --- Registration outputs ---------------------------------------------------
# The subdirectory the registration backend writes its artifacts into (brain
# mask, skull-stripped FA, transforms). The one home for the directory name.
REGISTRATION_DIR = "registration"

# The brain-mask filename pattern (``dwi2mask`` output, native grid), shared by
# the producer (:func:`brain_mask_name`) and the consumer (:func:`brain_mask_glob`)
# so the name the backend writes and the glob the viewer uses to find it cannot
# drift. Lives in :data:`REGISTRATION_DIR`.
_BRAIN_MASK_TEMPLATE = "{subject}_brain_mask.nii.gz"


def shape_token(shape_type: str, sphere_radius: float | None) -> str:
    """
    Map an ROI geometry to its base on-disk token (before adaptive placement).

    The single home for *geometry -> token*, including the **default collapse**:
    the default 3.0 mm sphere is the bare ``rois`` token, every other sphere is
    ``sphere{radius}`` (``2.5 -> sphere2p5``, ``2.0 -> sphere2``), and squares
    pass through by type. Writers call this instead of hand-formatting the token,
    so the default 3.0 mm sphere cannot bypass the collapse and land in
    ``rois_sphere3/`` (the bug this replaced).

    >>> shape_token("sphere", 3.0)
    'rois'
    >>> shape_token("sphere", 2.5)
    'sphere2p5'
    >>> shape_token("sphere", 2.0)
    'sphere2'
    >>> shape_token("squarev9", None)
    'squarev9'
    """
    if shape_type != "sphere":
        return shape_type  # squarev9, squarev4
    if sphere_radius == DEFAULT_SPHERE_RADIUS:
        return DEFAULT_ROI_TOKEN
    r_str = str(sphere_radius).replace(".", "p").rstrip("0").rstrip("p")
    return f"sphere{r_str}"


def roi_dir_name(token: str, adaptive: bool = False) -> str:
    """
    Build the on-disk ROI-directory name for ``token``.

    ``adaptive`` is a convenience for writers that hold the base token and the
    adaptive-placement flag separately; when set, ``_adaptive`` is appended to
    the token first. The viewer passes whole tokens (with ``_adaptive`` already
    baked in) and leaves ``adaptive`` at its default.

    The default token maps to the bare ``rois/`` directory; its adaptive variant
    is ``rois_adaptive/`` (not ``rois_rois_adaptive/``). Every other token gets the
    ``rois_{token}`` form.

    >>> roi_dir_name("rois")
    'rois'
    >>> roi_dir_name("rois", adaptive=True)
    'rois_adaptive'
    >>> roi_dir_name("squarev9")
    'rois_squarev9'
    >>> roi_dir_name("squarev9", adaptive=True)
    'rois_squarev9_adaptive'
    """
    if adaptive:
        token = f"{token}_adaptive"
    if token == DEFAULT_ROI_TOKEN:
        return DEFAULT_ROI_TOKEN
    # The adaptive default ("rois_adaptive") is already a directory name — it
    # carries the ``rois`` base, so it must not gain a second ``rois_`` prefix.
    if token.startswith(_ROI_DIR_PREFIX):
        return token
    return f"{_ROI_DIR_PREFIX}{token}"


def parse_roi_dir(name: str) -> str | None:
    """
    Recover the ROI token from a directory name, or ``None`` if it is not one.

    The inverse of :func:`roi_dir_name` for the whole-token form:
    ``parse_roi_dir(roi_dir_name(token)) == token``. A returned token keeps any
    ``_adaptive`` suffix. Replaces the scattered ``name[5:]`` strip.

    >>> parse_roi_dir("rois")
    'rois'
    >>> parse_roi_dir("rois_adaptive")
    'rois_adaptive'
    >>> parse_roi_dir("rois_squarev9_adaptive")
    'squarev9_adaptive'
    >>> parse_roi_dir("registration") is None
    True
    """
    if name == DEFAULT_ROI_TOKEN:
        return DEFAULT_ROI_TOKEN
    # The adaptive default keeps its ``rois`` base rather than stripping to a bare
    # ``adaptive`` token (which would not round-trip and would mis-display).
    if name == f"{DEFAULT_ROI_TOKEN}_adaptive":
        return name
    if name.startswith(_ROI_DIR_PREFIX):
        return name[len(_ROI_DIR_PREFIX) :]
    return None


def alps_csv_name(token: str, adaptive: bool = False) -> str:
    """
    Build the ALPS-results CSV filename for ``token``.

    ``adaptive`` behaves as in :func:`roi_dir_name`.

    >>> alps_csv_name("rois")
    'alps_results.csv'
    >>> alps_csv_name("squarev9")
    'alps_results_squarev9.csv'
    >>> alps_csv_name("squarev9", adaptive=True)
    'alps_results_squarev9_adaptive.csv'
    """
    if adaptive:
        token = f"{token}_adaptive"
    if token == DEFAULT_ROI_TOKEN:
        return "alps_results.csv"
    return f"alps_results_{token}.csv"


def alps_csv_names(tokens) -> list[str]:
    """
    Enumerate the ALPS-results CSV filenames a run produces, in stable order.

    The single home for "which CSVs a batch writes": ``batch._write_csv_results``
    loops it to write and ``build_batch_results_table`` takes ``len()`` of it for
    the footer count, so the files on disk and the count on screen come from one
    function and cannot drift.

    Empty ``tokens`` -> the single backward-compat default name
    (``alps_results.csv``); otherwise one suffixed name per token, ordered by
    ``sorted(tokens)`` to match the writer's per-shape loop.

    >>> alps_csv_names([])
    ['alps_results.csv']
    >>> alps_csv_names(["squarev9"])
    ['alps_results_squarev9.csv']
    >>> alps_csv_names({"squarev9", "rois", "sphere2p5"})
    ['alps_results.csv', 'alps_results_sphere2p5.csv', 'alps_results_squarev9.csv']
    """
    if not tokens:
        return [alps_csv_name(DEFAULT_ROI_TOKEN)]
    return [alps_csv_name(token) for token in sorted(tokens)]


def roi_mask_name(subject: str, roi_name: str) -> str:
    """
    Build the on-disk ROI-mask filename a backend writes for one subject + ROI.

    The producer half of the mask-filename pair; :func:`roi_mask_glob` is the
    consumer half over the same private template.

    >>> roi_mask_name("sub-01", "left_proj")
    'sub-01_left_proj.nii.gz'
    """
    return _ROI_MASK_TEMPLATE.format(subject=subject, roi_name=roi_name)


def roi_mask_glob(roi_name: str) -> str:
    """
    Build the glob the viewer uses to find an ROI mask regardless of subject.

    The consumer half of the mask-filename pair; the wildcard stands in for the
    subject prefix :func:`roi_mask_name` writes.

    >>> roi_mask_glob("left_proj")
    '*_left_proj.nii.gz'
    """
    return _ROI_MASK_TEMPLATE.format(subject="*", roi_name=roi_name)


def brain_mask_name(subject: str) -> str:
    """
    Build the on-disk brain-mask filename the backend writes for one subject.

    The producer half of the brain-mask-filename pair; :func:`brain_mask_glob`
    is the consumer half over the same private template. The file lives in
    :data:`REGISTRATION_DIR`.

    >>> brain_mask_name("sub-01")
    'sub-01_brain_mask.nii.gz'
    """
    return _BRAIN_MASK_TEMPLATE.format(subject=subject)


def brain_mask_glob() -> str:
    """
    Build the glob the viewer uses to find the brain mask regardless of subject.

    The consumer half of the brain-mask-filename pair; the wildcard stands in for
    the subject prefix :func:`brain_mask_name` writes.

    >>> brain_mask_glob()
    '*_brain_mask.nii.gz'
    """
    return _BRAIN_MASK_TEMPLATE.format(subject="*")


# --- Per-subject completion marker ------------------------------------------
# `_write_csv_results` runs after the whole subject loop, so ALPS numbers live
# only in memory until the batch ends -- and nothing on disk records that a
# subject is done. A 200-subject cohort killed at 180 therefore redoes all 180.
# The marker fixes both: it lands as each subject completes (so a hard kill keeps
# the finished subjects' numbers) and `--resume` reads it to decide what to skip.
#
# It sits *inside* the subject's own directory, beside the data it describes --
# unlike the batch CSV, which is one row keyed by id at the cohort level.
COMPLETION_MARKER = "alps_result.json"


def completion_marker_path(subject_dir: str | Path) -> Path:
    """
    The completion marker's path inside one subject's output directory.

    >>> completion_marker_path("/out/sub-01").name
    'alps_result.json'
    """
    return Path(subject_dir) / COMPLETION_MARKER


@dataclass(frozen=True)
class CompletionMarker:
    """
    What one finished subject recorded: its status, its ALPS values, and the
    hash of the protocol that produced them.

    ``protocol_hash`` is what makes ``--resume`` safe. Skipping on the marker's
    mere *existence* would be a heuristic: a subject killed mid-FNIRT leaves an
    output directory that looks populated, so a cohort could silently carry one
    subject whose ROI masks came from a half-finished warp. Matching the hash
    also means an edited protocol reprocesses everything, so a cohort can never
    end up half-processed one way and half the other.
    """

    subject_id: str
    status: str
    protocol_hash: str
    error: str = ""
    # ALPS values keyed by shape name, e.g.
    # {"rois_adaptive": {"alps_lab_left": 1.42, ...}}.
    alps_by_shape: dict[str, dict] = field(default_factory=dict)


def write_completion_marker(subject_dir: str | Path, marker: CompletionMarker) -> None:
    """
    Write ``marker`` into ``subject_dir``.

    A pure file-I/O leaf, like :func:`write_alps_csv`: it creates the directory
    if needed but logs nothing and swallows no errors -- each caller keeps its
    own failure policy.
    """
    path = completion_marker_path(subject_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "subject_id": marker.subject_id,
        "status": marker.status,
        "protocol_hash": marker.protocol_hash,
        "error": marker.error,
        "alps_by_shape": marker.alps_by_shape,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
        f.write("\n")


def read_completion_marker(subject_dir: str | Path) -> CompletionMarker | None:
    """
    Read the completion marker from ``subject_dir``, or ``None`` if there is
    none (or it is unreadable).

    An unreadable marker reads as absent rather than raising: the only consumer
    is ``--resume``, and the safe answer to "is this subject done?" when the
    record is damaged is *no*.
    """
    path = completion_marker_path(subject_dir)
    try:
        with open(path, encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(document, dict):
        return None

    return CompletionMarker(
        subject_id=document.get("subject_id", ""),
        status=document.get("status", ""),
        protocol_hash=document.get("protocol_hash", ""),
        error=document.get("error", ""),
        alps_by_shape=document.get("alps_by_shape") or {},
    )


def is_complete_for_protocol(subject_dir: str | Path, protocol_hash: str) -> bool:
    """
    Whether ``subject_dir`` holds a *completed* result for ``protocol_hash``.

    The precise question ``--resume`` asks. False for an absent or damaged
    marker, for a subject that failed or was skipped, and for one processed
    under a different protocol.
    """
    marker = read_completion_marker(subject_dir)
    return bool(marker and marker.status == "completed" and marker.protocol_hash == protocol_hash)


@dataclass(frozen=True)
class AlpsRow:
    """One subject's parsed ALPS values from a results CSV.

    ``None`` marks a value that was absent or unparseable. Which of the
    LAB/PAS pairs are populated depends on the table's detected method.
    """

    subject_id: str
    status: str
    error: str
    lab_left: float | None = None
    lab_right: float | None = None
    lab_combined: float | None = None
    pas_left: float | None = None
    pas_right: float | None = None
    pas_combined: float | None = None


@dataclass(frozen=True)
class AlpsTable:
    """A parsed ALPS-results CSV: the detected method and per-subject rows."""

    method: str  # METHOD_LAB, METHOD_PAS, or METHOD_BOTH
    rows: dict[str, AlpsRow]  # keyed by subject id, in file order


def detect_method(fieldnames: list[str]) -> str:
    """Detect the ALPS method from a CSV's column names.

    ``Both`` when both the LAB and PAS left-hemisphere columns are present,
    otherwise whichever single suffix is present. A CSV with neither (the
    legacy no-suffix format) is read as ``ALPS-LAB``.
    """
    has_pas = COL_PAS_LEFT in fieldnames
    has_lab = COL_LAB_LEFT in fieldnames
    if has_pas and has_lab:
        return METHOD_BOTH
    if has_pas:
        return METHOD_PAS
    return METHOD_LAB


def _to_float(value) -> float | None:
    """Parse a CSV cell to float, or ``None`` if blank/missing/non-numeric."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def read_alps_csv(path: str | Path) -> AlpsTable:
    """
    Read an ALPS-results CSV into a typed :class:`AlpsTable`.

    The method is detected from the column set (:func:`detect_method`). LAB
    values resolve through the legacy no-suffix columns when the suffixed ones
    are absent; PAS values use the suffixed columns only. Rows with no
    ``Filename`` are skipped.
    """
    rows: dict[str, AlpsRow] = {}

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        method = detect_method(fieldnames)

        # LAB columns, falling back to the legacy no-suffix names.
        lab_left_col = COL_LAB_LEFT if COL_LAB_LEFT in fieldnames else COL_LEGACY_LEFT
        lab_right_col = COL_LAB_RIGHT if COL_LAB_RIGHT in fieldnames else COL_LEGACY_RIGHT
        lab_combined_col = (
            COL_LAB_COMBINED if COL_LAB_COMBINED in fieldnames else COL_LEGACY_COMBINED
        )

        for row in reader:
            subject_id = row.get(COL_FILENAME, "")
            if not subject_id:
                continue

            lab_left = lab_right = lab_combined = None
            if method in (METHOD_LAB, METHOD_BOTH):
                lab_left = _to_float(row.get(lab_left_col, ""))
                lab_right = _to_float(row.get(lab_right_col, ""))
                lab_combined = _to_float(row.get(lab_combined_col, ""))

            pas_left = pas_right = pas_combined = None
            if method in (METHOD_PAS, METHOD_BOTH):
                pas_left = _to_float(row.get(COL_PAS_LEFT, ""))
                pas_right = _to_float(row.get(COL_PAS_RIGHT, ""))
                pas_combined = _to_float(row.get(COL_PAS_COMBINED, ""))

            rows[subject_id] = AlpsRow(
                subject_id=subject_id,
                status=row.get(COL_STATUS, ""),
                error=row.get(COL_ERROR, ""),
                lab_left=lab_left,
                lab_right=lab_right,
                lab_combined=lab_combined,
                pas_left=pas_left,
                pas_right=pas_right,
                pas_combined=pas_combined,
            )

    return AlpsTable(method=method, rows=rows)


def alps_columns(method: str) -> list[str]:
    """
    The canonical ordered header an ALPS-results CSV carries for ``method``.

    The single source of the schema the writer emits and (by detection) the
    reader consumes: ``Filename``, the per-method LAB/PAS hemisphere columns,
    then ``Status`` and ``Error``. The writer emits only the suffixed columns;
    the legacy no-suffix names are a read-only fallback and never appear here.

    >>> alps_columns(METHOD_LAB)
    ['Filename', 'Left Hemisphere ALPS-LAB', 'Right Hemisphere ALPS-LAB', 'Combined ALPS-LAB', 'Status', 'Error']
    """
    columns = [COL_FILENAME]
    if method in (METHOD_LAB, METHOD_BOTH):
        columns += [COL_LAB_LEFT, COL_LAB_RIGHT, COL_LAB_COMBINED]
    if method in (METHOD_PAS, METHOD_BOTH):
        columns += [COL_PAS_LEFT, COL_PAS_RIGHT, COL_PAS_COMBINED]
    columns += [COL_STATUS, COL_ERROR]
    return columns


def _format_value(value: float | None) -> str:
    """Format an ALPS cell: ``.6f`` for a value, empty string for ``None``."""
    return f"{value:.6f}" if value is not None else ""


def write_alps_csv(path: str | Path, table: AlpsTable) -> None:
    """
    Write an :class:`AlpsTable` to an ALPS-results CSV (the inverse of
    :func:`read_alps_csv`).

    The header comes from :func:`alps_columns` for ``table.method``; each
    :class:`AlpsRow` is formatted against it (``.6f`` cells, ``None`` -> empty
    string) using the stdlib CSV writer, so the line terminators match the
    engine's existing output exactly. ``read_alps_csv(write_alps_csv(table))``
    round-trips to ``.6f`` precision.

    A pure file-I/O leaf: it creates no directories, logs nothing, and swallows
    no errors -- each caller keeps its own logging and failure policy.
    """
    method = table.method
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(alps_columns(method))

        for row in table.rows.values():
            cells = [row.subject_id]
            if method in (METHOD_LAB, METHOD_BOTH):
                cells += [
                    _format_value(row.lab_left),
                    _format_value(row.lab_right),
                    _format_value(row.lab_combined),
                ]
            if method in (METHOD_PAS, METHOD_BOTH):
                cells += [
                    _format_value(row.pas_left),
                    _format_value(row.pas_right),
                    _format_value(row.pas_combined),
                ]
            cells += [row.status, row.error]
            writer.writerow(cells)
