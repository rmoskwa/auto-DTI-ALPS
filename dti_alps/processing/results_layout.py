"""
The results-on-disk contract for the DTI-ALPS engine.

A dependency-free leaf (stdlib only -- ``csv``, ``dataclasses``, ``pathlib``):
it imports nothing from the package, so it can never take part in an import
cycle, and it carries **no GUI text** -- it speaks ROI *tokens*, never display
names. This is the single home for the convention that the engine writes and
the viewer/reports read:

* the ROI-directory naming (``rois`` / ``rois_{token}`` / ``rois_{token}_refined``)
  via :func:`roi_dir_name` / :func:`parse_roi_dir`,
* the ALPS-results CSV naming (``alps_results.csv`` / ``alps_results_{token}.csv``)
  via :func:`alps_csv_name`, and
* the ALPS column schema plus the one typed reader :func:`read_alps_csv`, which
  detects the ALPS method from the present columns and parses the per-subject
  rows (preserving the legacy no-suffix ``... ALPS`` fallback the old viewer
  carried, even though the current writers never emit it).

Only the viewer's consumers are repointed onto this module for now (PRD 0005,
Decision 4); repointing the processing-side writers/parsers (``batch``,
``reanalysis``, ``report``, ``pipeline``, ``registration/fsl``) and the
canonical ROI-name set is a recorded follow-up, mirroring how PRD 0003 created
``processing/constants.py`` and repointed only the consumers it needed.

A **token** is the machine name of an ROI configuration as it appears on disk:
``rois`` (the default 3.0 mm sphere), ``squarev9``, ``squarev4``, ``sphere2p5``,
``sphere3``, optionally suffixed ``_refined``. The default ``rois`` token maps to
the bare ``rois`` directory and ``alps_results.csv``; every other token gets the
``rois_{token}`` / ``alps_results_{token}.csv`` form.
"""

import csv
from dataclasses import dataclass
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


def roi_dir_name(token: str, refined: bool = False) -> str:
    """
    Build the on-disk ROI-directory name for ``token``.

    ``refined`` is a convenience for writers that hold the base token and the
    refinement flag separately; when set, ``_refined`` is appended to the token
    first. The viewer passes whole tokens (with ``_refined`` already baked in)
    and leaves ``refined`` at its default.

    >>> roi_dir_name("rois")
    'rois'
    >>> roi_dir_name("squarev9")
    'rois_squarev9'
    >>> roi_dir_name("squarev9", refined=True)
    'rois_squarev9_refined'
    """
    if refined:
        token = f"{token}_refined"
    if token == DEFAULT_ROI_TOKEN:
        return DEFAULT_ROI_TOKEN
    return f"{_ROI_DIR_PREFIX}{token}"


def parse_roi_dir(name: str) -> str | None:
    """
    Recover the ROI token from a directory name, or ``None`` if it is not one.

    The inverse of :func:`roi_dir_name` for the whole-token form:
    ``parse_roi_dir(roi_dir_name(token)) == token``. A returned token keeps any
    ``_refined`` suffix. Replaces the scattered ``name[5:]`` strip.

    >>> parse_roi_dir("rois")
    'rois'
    >>> parse_roi_dir("rois_squarev9_refined")
    'squarev9_refined'
    >>> parse_roi_dir("registration") is None
    True
    """
    if name == DEFAULT_ROI_TOKEN:
        return DEFAULT_ROI_TOKEN
    if name.startswith(_ROI_DIR_PREFIX):
        return name[len(_ROI_DIR_PREFIX) :]
    return None


def alps_csv_name(token: str, refined: bool = False) -> str:
    """
    Build the ALPS-results CSV filename for ``token``.

    ``refined`` behaves as in :func:`roi_dir_name`.

    >>> alps_csv_name("rois")
    'alps_results.csv'
    >>> alps_csv_name("squarev9")
    'alps_results_squarev9.csv'
    >>> alps_csv_name("squarev9", refined=True)
    'alps_results_squarev9_refined.csv'
    """
    if refined:
        token = f"{token}_refined"
    if token == DEFAULT_ROI_TOKEN:
        return "alps_results.csv"
    return f"alps_results_{token}.csv"


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
