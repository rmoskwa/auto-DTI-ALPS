"""
Toolkit-free presentation model for the Quality Report page (PRD 0022).

``QualityReportModel`` is the read-side sibling of :class:`~dti_alps.gui.viewer_model.ViewerModel`
over the same **results-on-disk contract**, and the in-app companion to the
``--report`` CLI (``processing/report.py``). Like ``ViewerModel`` it holds no GUI
toolkit: it loads an output folder, answers shape/subject queries with plain data,
composes the *unchanged* ``report.py`` leaf functions over a chosen **subject
subset**, and shapes the numbers into a :class:`QualityReportView`. Subsetting is
the one capability the whole-folder CLI lacks; it costs nothing in the engine
because ``calculate_subject_metrics`` already works one subject at a time.

Split of responsibilities (mirroring ``ViewerModel``):

* the **model** owns the loaded folder and the discovered shape tokens, and turns
  a *(shape, subject-subset)* into a view;
* the **adapter** (``gui/app.py``) owns the widgets, the background
  :class:`~dti_alps.processing.report_worker.ReportWorker` drain loop, and the
  two-tier grouped-header table layout.

The view-building here (display labels via :func:`~dti_alps.gui.viewer_model.roi_display_name`,
``.6f`` cell formatting) is the GUI-side step that must **not** live in the engine
worker -- so the worker emits engine-native ``SubjectReportData`` and this module
builds the view from them, keeping the ``gui -> processing`` arrow one-way.
"""

from dataclasses import dataclass
from operator import gt, lt
from pathlib import Path

from ..processing import report
from ..processing.constants import (
    QUALITY_WARN_ANGULAR_DISPERSION_MAX,
    QUALITY_WARN_DIRECTIONAL_ALIGNMENT_MIN,
    QUALITY_WARN_FA_MIN,
    QUALITY_WARN_RADIAL_ASYMMETRY_MAX,
)
from ..processing.report import SubjectReportData, write_report_csv
from .viewer_model import roi_display_name

# The metric groups, in the CLI CSV's order, paired with the ``ROIMetrics``
# attribute each reads. The display labels are byte-for-byte the CSV's category
# headers (including the ``λ2/λ3`` in Radial Asymmetry).
_METRIC_GROUPS: tuple[tuple[str, str], ...] = (
    ("Directional Alignment (V1)", "directional_alignment"),
    ("Angular Dispersion (V1)", "angular_dispersion"),
    ("Fractional Anisotropy", "fa_mean"),
    ("Radial Asymmetry (λ2/λ3)", "radial_asymmetry"),
)

# The four ROI sub-columns under every metric group, in the CLI CSV's order.
# These are also the ``SubjectReportData`` attribute names, so a column key
# doubles as the getattr target for its subject row.
_ROI_COLUMNS: tuple[str, ...] = ("l_proj", "l_assoc", "r_proj", "r_assoc")

# Per-metric warning rule: ``(comparison, threshold)`` keyed by ``ROIMetrics``
# attribute. ``lt``/``gt`` encode the direction -- alignment and FA warn when
# they fall below a floor, dispersion and radial asymmetry when they rise above a
# ceiling. The thresholds are the engine's (``processing/constants.py``), so the
# app and any future CLI flag the same cells. A ``None`` value (e.g. radial
# asymmetry under a LAB-only run) never warns.
_WARN_RULES = {
    "directional_alignment": (lt, QUALITY_WARN_DIRECTIONAL_ALIGNMENT_MIN),
    "angular_dispersion": (gt, QUALITY_WARN_ANGULAR_DISPERSION_MAX),
    "fa_mean": (lt, QUALITY_WARN_FA_MIN),
    "radial_asymmetry": (gt, QUALITY_WARN_RADIAL_ASYMMETRY_MAX),
}


def _fmt(value: float | None) -> str:
    """Format a metric cell exactly as ``write_report_csv`` does: ``.6f`` or blank."""
    return f"{value:.6f}" if value is not None else ""


def _is_warning(attr: str, value: float | None) -> bool:
    """Whether ``value`` for metric ``attr`` is outside its quality threshold.

    ``None`` (a metric that was not computed) never warns, so a LAB-only run's
    blank Radial-Asymmetry cells are never flagged.
    """
    if value is None:
        return False
    compare, threshold = _WARN_RULES[attr]
    return compare(value, threshold)


@dataclass(frozen=True)
class QualityReportRow:
    """One subject's row: its id, the flattened cells, and per-cell warning flags.

    ``cells`` is group-major, ROI-minor: for four groups over four ROI columns it
    holds sixteen strings, ``cells[g * 4 + r]`` being group ``g`` / ROI ``r``.
    Under a LAB-only run the Radial-Asymmetry group's four cells are blank
    strings, exactly as the CLI CSV leaves them. ``warnings`` is the parallel
    tuple of booleans: ``warnings[i]`` is ``True`` when ``cells[i]`` is outside
    its quality threshold and should be highlighted for manual inspection.
    ``has_warning`` is ``True`` when any cell warns (the adapter highlights the
    subject too).
    """

    subject_id: str
    cells: tuple[str, ...]
    warnings: tuple[bool, ...]

    @property
    def has_warning(self) -> bool:
        return any(self.warnings)


@dataclass(frozen=True)
class QualityReportView:
    """The plain-data grouped table for one *(shape x subject-subset)*.

    ``metric_groups`` (the four category labels) and ``roi_columns`` (the four ROI
    sub-columns) drive the adapter's two-tier header -- a band per group spanning
    its four ROI columns. ``rows`` is one :class:`QualityReportRow` per subject.
    ``subjects_data`` carries the engine-native source records so
    :meth:`QualityReportModel.save_csv` can persist byte-for-byte with the CLI.
    """

    shape_token: str
    shape_label: str
    metric_groups: tuple[str, ...]
    roi_columns: tuple[str, ...]
    rows: tuple[QualityReportRow, ...]
    subjects_data: tuple[SubjectReportData, ...] = ()


@dataclass(frozen=True)
class FolderScan:
    """The plain-data result of a successful :meth:`QualityReportModel.load_folder`.

    ``shapes`` is the ordered ``(token, display label)`` pairs discovered in the
    folder, labelled by the same mapping the viewer uses.
    """

    folder: Path
    shapes: list[tuple[str, str]]


@dataclass(frozen=True)
class LoadError:
    """A load failure as data; the adapter maps ``kind`` to a messagebox.

    ``kind`` is ``folder_missing`` (not a directory) or ``no_shapes`` (a folder
    with no discoverable ROI shapes); ``payload`` is the offending folder.
    """

    kind: str
    payload: Path


def build_quality_report_view(
    shape_token: str,
    subjects_data: list[SubjectReportData],
) -> QualityReportView:
    """Shape engine-native per-subject rows into a :class:`QualityReportView`.

    The single home for the GUI-side view construction: the display label
    (via :func:`roi_display_name`) and the ``.6f``/blank cell formatting. Both
    the model's synchronous :meth:`QualityReportModel.generate` and the adapter's
    background-worker completion path funnel through here, so a report built on
    either path is identical.
    """
    rows: list[QualityReportRow] = []
    for data in subjects_data:
        cells: list[str] = []
        warnings: list[bool] = []
        for _label, attr in _METRIC_GROUPS:
            for roi_key in _ROI_COLUMNS:
                metrics = getattr(data, roi_key)
                value = getattr(metrics, attr)
                cells.append(_fmt(value))
                warnings.append(_is_warning(attr, value))
        rows.append(
            QualityReportRow(
                subject_id=data.subject_id,
                cells=tuple(cells),
                warnings=tuple(warnings),
            )
        )

    return QualityReportView(
        shape_token=shape_token,
        shape_label=roi_display_name(shape_token),
        metric_groups=tuple(label for label, _attr in _METRIC_GROUPS),
        roi_columns=_ROI_COLUMNS,
        rows=tuple(rows),
        subjects_data=tuple(subjects_data),
    )


class QualityReportModel:
    """Stateful, toolkit-free session for the Quality Report page.

    Owns the loaded output folder and its discovered shape tokens, and answers
    with plain data: the shapes in the folder, the subjects that have a given
    shape, and a :class:`QualityReportView` over a chosen subset.
    """

    def __init__(self):
        self.folder: Path | None = None
        self.shape_tokens: list[str] = []

    def load_folder(self, folder_path: str | Path) -> FolderScan | LoadError:
        """Scan an output folder for ROI shapes.

        Returns a :class:`FolderScan` (with the ordered ``(token, label)`` shapes)
        on success, or a :class:`LoadError` for a missing folder / a folder with
        no ROI shapes. State is committed only on success; a ``LoadError`` leaves
        the model unchanged (mirroring ``ViewerModel.load_session``).
        """
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            return LoadError("folder_missing", folder)

        tokens = report.discover_roi_shapes(folder)
        if not tokens:
            return LoadError("no_shapes", folder)

        # Commit state.
        self.folder = folder
        self.shape_tokens = tokens

        return FolderScan(
            folder=folder,
            shapes=[(token, roi_display_name(token)) for token in tokens],
        )

    def subjects_for_shape(self, shape_token: str) -> list[str]:
        """The subject ids that have ROIs for ``shape_token``, in on-disk order.

        The shape is the primary axis; the checkbox list follows it. Returns an
        empty list when no folder is loaded.
        """
        if self.folder is None:
            return []
        return [sid for sid, _dir in report.discover_subjects_for_shape(self.folder, shape_token)]

    def generate(
        self,
        shape_token: str,
        subject_subset: list[str],
        progress_cb=None,
        cancel=None,
    ) -> QualityReportView:
        """Compute a :class:`QualityReportView` over ``subject_subset`` for a shape.

        Composes ``report.py``'s per-subject ``calculate_subject_metrics`` over
        exactly the subset (in on-disk order), dropping subjects whose required
        files are missing -- so a report over 2 of 4 subjects has at most those 2
        rows, and this **writes nothing to disk**.

        ``progress_cb(index, total, subject_id)`` (optional) fires before each
        subject is computed; ``cancel()`` (optional) is polled at each subject
        boundary and, when truthy, stops early and returns the rows gathered so
        far. This is the synchronous seam the tests drive; the background path
        uses :class:`~dti_alps.processing.report_worker.ReportWorker` instead.
        """
        subjects_data: list[SubjectReportData] = []
        if self.folder is None:
            return build_quality_report_view(shape_token, subjects_data)

        pairs = report.discover_subjects_for_shape(self.folder, shape_token)
        wanted = set(subject_subset)
        selected = [(sid, subject_dir) for sid, subject_dir in pairs if sid in wanted]
        total = len(selected)

        for index, (subject_id, subject_dir) in enumerate(selected):
            if cancel is not None and cancel():
                break
            if progress_cb is not None:
                progress_cb(index, total, subject_id)
            data = report.calculate_subject_metrics(subject_id, subject_dir, shape_token)
            if data is not None:
                subjects_data.append(data)

        return build_quality_report_view(shape_token, subjects_data)

    def save_csv(self, view: QualityReportView, path: str | Path) -> None:
        """Write ``view`` to a ``quality_report_{shape}.csv`` via ``write_report_csv``.

        Persists the view's engine-native source rows, so a saved subset report is
        byte-for-byte identical to a CLI ``--report`` CSV over the same
        shape/subjects. The explicit, named Save is the *only* write in the whole
        page -- Generate never touches disk.
        """
        write_report_csv(Path(path), list(view.subjects_data))

    def default_csv_name(self, shape_token: str) -> str:
        """The standard ``quality_report_{shape}.csv`` filename for a shape.

        The one home for the report's on-disk name, so the Save-As dialog's
        pre-filled name matches what the CLI writes for the same shape.
        """
        return f"quality_report_{shape_token}.csv"
