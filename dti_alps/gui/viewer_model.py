"""
tk-free presentation model for the Results Viewer.

``ViewerModel`` is the viewer's session object: it loads a results folder and
answers queries with plain data and finished NumPy pictures, holding no
Tkinter. It is the genuine twin of ``gui/result_model.py`` in *role* (tk-free,
GUI-side, owns presentation logic) but not in *shape* -- the viewer has no
worker-queue message stream to translate, so this is a stateful session with
command/query methods rather than a ``msg -> [Intent]`` translator.

Split of responsibilities (Decision 2):

* the **model** owns the loaded session -- subject records, the per-ROI-type CSV
  cache, the current selection, and the *current* subject's decoded FA/V1/ROI
  arrays;
* the **adapter** owns the transient view cursor (view, slice, zoom, show-ROIs)
  and passes it into ``render_slice`` as explicit parameters, so the render
  stays a pure function of its inputs.

The on-disk contract (ROI-dir / CSV naming, the ALPS column schema, the canonical
``ROI_NAMES`` and the ROI-mask glob) lives in ``processing/results_layout``; this
module consumes it and adds the GUI-side display-name mapping. The FA/V1 globs
stay here -- their only consumer is this loader.
"""

from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from ..processing import results_layout
from ..processing.results_layout import METHOD_LAB, ROI_NAMES, AlpsTable, read_alps_csv

# The three orthogonal views the viewer renders.
VIEWS = ("axial", "coronal", "sagittal")


# --------------------------------------------------------------------------- #
# Display-name mapping (GUI text; lives in the model, never in the engine)
# --------------------------------------------------------------------------- #
def roi_display_name(token: str) -> str:
    """Convert an ROI token to its human-readable display name.

    ``rois`` -> ``Sphere 3.0mm``; ``sphere2p5`` -> ``Sphere 2.5mm``;
    ``squarev9`` -> ``Square 3x3``; ``squarev4`` -> ``Square 2x2``. A
    ``_refined`` suffix becomes a trailing `` (r)``.
    """
    is_refined = token.endswith("_refined")
    base_type = token[:-8] if is_refined else token
    refined_suffix = " (r)" if is_refined else ""

    if base_type == "rois":
        return f"Sphere 3.0mm{refined_suffix}"
    elif base_type.startswith("sphere"):
        # e.g. "sphere2p5" -> "Sphere 2.5mm", "sphere2" -> "Sphere 2.0mm"
        radius_str = base_type[6:].replace("p", ".")
        radius = float(radius_str)
        return f"Sphere {radius:.1f}mm{refined_suffix}"
    elif base_type == "squarev9":
        return f"Square 3x3{refined_suffix}"
    elif base_type == "squarev4":
        return f"Square 2x2{refined_suffix}"
    else:
        return base_type.replace("_", " ").title() + refined_suffix


def discover_roi_options(output_folder: Path) -> list[str]:
    """
    Discover the available ROI tokens in an output folder.

    Looks in the first subject folder that has any, recognising the default
    ``rois`` directory and the ``rois_*`` reanalysis directories via
    ``results_layout.parse_roi_dir``. Returns tokens ordered with ``rois``
    first, then the rest alphabetically.
    """
    roi_options: list[str] = []

    for subject_folder in output_folder.iterdir():
        if not subject_folder.is_dir():
            continue

        # Default rois/ directory.
        if (
            subject_folder / results_layout.roi_dir_name(results_layout.DEFAULT_ROI_TOKEN)
        ).exists():
            if results_layout.DEFAULT_ROI_TOKEN not in roi_options:
                roi_options.append(results_layout.DEFAULT_ROI_TOKEN)

        # Reanalysis directories (rois_*).
        for roi_dir in subject_folder.glob(f"{results_layout._ROI_DIR_PREFIX}*"):
            if roi_dir.is_dir():
                token = results_layout.parse_roi_dir(roi_dir.name)
                if token and token not in roi_options:
                    roi_options.append(token)

        # Only need to inspect one subject folder.
        if roi_options:
            break

    # Sort with the default token first, then alphabetically.
    if results_layout.DEFAULT_ROI_TOKEN in roi_options:
        roi_options.remove(results_layout.DEFAULT_ROI_TOKEN)
        roi_options = [results_layout.DEFAULT_ROI_TOKEN] + sorted(roi_options)
    else:
        roi_options = sorted(roi_options)

    return roi_options


# --------------------------------------------------------------------------- #
# Pure rendering (Decision 8): array-in / array-out, no model, no files
# --------------------------------------------------------------------------- #
def _extract_slice(vol: np.ndarray, view: str, s: int) -> np.ndarray | None:
    """Extract the 2D (FA/ROI) or 2D+channel (V1) slice, or ``None`` if out of range."""
    if view == "axial":
        if s >= vol.shape[2]:
            return None
        return vol[:, :, s] if vol.ndim == 3 else vol[:, :, s, :]
    elif view == "coronal":
        if s >= vol.shape[1]:
            return None
        return vol[:, s, :] if vol.ndim == 3 else vol[:, s, :, :]
    else:  # sagittal
        if s >= vol.shape[0]:
            return None
        return vol[s, :, :] if vol.ndim == 3 else vol[s, :, :, :]


def _orient(image: np.ndarray, view: str) -> np.ndarray:
    """Apply the per-view display orientation (rot90 / fliplr)."""
    if view == "sagittal":
        return np.rot90(image, k=1)
    # axial and coronal: rotate then mirror left-right.
    return np.fliplr(np.rot90(image, k=1))


def _overlay_rois(
    image: np.ndarray, roi_masks: dict[str, np.ndarray], view: str, s: int
) -> np.ndarray:
    """Paint all ROI voxels in this slice solid white onto a copy of ``image``."""
    result = image.copy()
    combined = None
    for roi_vol in roi_masks.values():
        roi_slice = _extract_slice(roi_vol, view, s)
        if roi_slice is None:
            continue
        mask = roi_slice > 0
        combined = mask if combined is None else (combined | mask)
    if combined is not None and np.any(combined):
        result[combined] = [255, 255, 255]
    return result


def render_dec_slice(
    fa: np.ndarray,
    v1: np.ndarray,
    roi_masks: dict[str, np.ndarray],
    view: str,
    slice_index: int,
    show_rois: bool,
) -> np.ndarray | None:
    """
    Render one display-ready FA-modulated direction-encoded-colour slice.

    Builds RGB from ``|V1|`` (``|x|``=R, ``|y|``=G, ``|z|``=B), normalises per
    voxel, modulates by FA, optionally paints ROI voxels solid white, then
    applies the per-view orientation. Returns a finished oriented ``uint8``
    H×W×3 array at native voxel resolution, or ``None`` when ``slice_index`` is
    out of range. Zoom and toolkit conversion stay in the adapter.
    """
    fa_slice = _extract_slice(fa, view, slice_index)
    if fa_slice is None:
        return None
    v1_slice = _extract_slice(v1, view, slice_index)
    if v1_slice is None:
        return None

    # RGB from V1 (absolute values; fibre direction is sign-agnostic).
    rgb = np.abs(v1_slice)
    rgb = np.nan_to_num(rgb, nan=0.0)
    fa_slice = np.nan_to_num(fa_slice, nan=0.0)

    # Normalise RGB per voxel.
    rgb_max = np.max(rgb, axis=-1, keepdims=True)
    rgb_max = np.where(rgb_max > 0, rgb_max, 1)
    rgb = rgb / rgb_max

    # Modulate by FA.
    fa_max = np.max(fa_slice)
    fa_norm = np.clip(fa_slice / fa_max if fa_max > 0 else fa_slice, 0, 1)
    fa_mod = fa_norm[:, :, np.newaxis]

    image = (np.clip(rgb * fa_mod, 0, 1) * 255).astype(np.uint8)

    if show_rois and roi_masks:
        image = _overlay_rois(image, roi_masks, view, slice_index)

    return _orient(image, view)


# --------------------------------------------------------------------------- #
# Value types returned across the seam
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SubjectRecord:
    """A subject's identity and file paths -- no decoded arrays, no metrics.

    ``status`` is the subject-tree status captured from the initially loaded CSV
    and is intentionally frozen: switching ROI type never changes it
    (behaviour-pinned, Decision 9). The per-ROI-type metrics live in the model's
    CSV cache and are fetched via :meth:`ViewerModel.current_metrics`.
    """

    subject_id: str
    folder: Path
    fa_path: Path
    v1_path: Path
    all_roi_paths: dict[str, dict[str, Path]]  # token -> {roi_name -> path}
    status: str


@dataclass(frozen=True)
class SessionView:
    """The plain-data result of a successful :meth:`ViewerModel.load_session`."""

    folder: Path
    roi_options: list[tuple[str, str]]  # ordered (token, display label) pairs
    method: str  # detected ALPS method of the initial ROI type
    subjects: list[SubjectRecord]  # ordered; empty is a valid (handled) case


@dataclass(frozen=True)
class LoadError:
    """A load failure as data; the adapter maps ``kind`` to a messagebox.

    ``kind`` is one of ``folder_missing`` / ``no_results`` / ``csv_missing``;
    ``payload`` is the offending path (the folder, or the missing CSV).
    """

    kind: str
    payload: Path


@dataclass(frozen=True)
class MetricsView:
    """The ALPS numbers for the current ``(roi_type, subject)``, shaped for display.

    ``method`` is the detected method of the current ROI type's CSV (drives the
    value layout). ``subject_method`` is the per-subject method label, which is
    empty when the subject has no row in that CSV (the adapter shows ``--``).
    """

    subject_id: str
    method: str
    subject_method: str
    status: str
    lab_left: float | None
    lab_right: float | None
    lab_combined: float | None
    pas_left: float | None
    pas_right: float | None
    pas_combined: float | None


class ViewerModel:
    """Stateful, tk-free session for the Results Viewer.

    Owns the loaded session (subject records, the per-ROI-type CSV cache, the
    current selection, and the current subject's decoded arrays) and answers
    queries with plain data and finished NumPy pictures.
    """

    def __init__(self):
        self.folder: Path | None = None
        self.roi_tokens: list[str] = []
        self.current_roi_type: str = results_layout.DEFAULT_ROI_TOKEN
        self.current_subject_id: str | None = None

        self._tables: dict[str, AlpsTable] = {}  # CSV cache, keyed by ROI token
        self._records: dict[str, SubjectRecord] = {}  # by subject id
        self._method: str = METHOD_LAB  # detected method of the current ROI type

        # Decoded arrays for the *current* subject only.
        self._fa: np.ndarray | None = None
        self._v1: np.ndarray | None = None
        self._roi_data: dict[str, np.ndarray] = {}

    # -- loading ----------------------------------------------------------- #
    def load_session(self, folder_path: str | Path) -> SessionView | LoadError:
        """Load a results folder. Returns a populated :class:`SessionView`,
        an empty-subjects :class:`SessionView`, or a :class:`LoadError`.

        State is committed only on success; a ``LoadError`` leaves the model
        unchanged.
        """
        folder = Path(folder_path)
        if not folder.exists():
            return LoadError("folder_missing", folder)

        tokens = discover_roi_options(folder)
        if not tokens:
            # No ROI directories: accept the folder only if the default CSV exists.
            default_csv = folder / results_layout.alps_csv_name(results_layout.DEFAULT_ROI_TOKEN)
            if not default_csv.exists():
                return LoadError("no_results", folder)
            tokens = [results_layout.DEFAULT_ROI_TOKEN]

        current = tokens[0]
        csv_path = folder / results_layout.alps_csv_name(current)
        if not csv_path.exists():
            return LoadError("csv_missing", csv_path)

        table = read_alps_csv(csv_path)

        records: list[SubjectRecord] = []
        by_id: dict[str, SubjectRecord] = {}
        for subject_folder in sorted(folder.iterdir()):
            if not subject_folder.is_dir():
                continue

            fa_files = list(subject_folder.glob("*_FA.nii.gz"))
            v1_files = list(subject_folder.glob("*_V1.nii.gz"))
            if not fa_files or not v1_files:
                continue  # skip folders without the required images

            all_roi_paths: dict[str, dict[str, Path]] = {}
            for token in tokens:
                roi_dir = subject_folder / results_layout.roi_dir_name(token)
                roi_paths: dict[str, Path] = {}
                if roi_dir.exists():
                    for roi_name in ROI_NAMES:
                        matches = list(roi_dir.glob(results_layout.roi_mask_glob(roi_name)))
                        if matches:
                            roi_paths[roi_name] = matches[0]
                if roi_paths:
                    all_roi_paths[token] = roi_paths

            subject_id = subject_folder.name
            row = table.rows.get(subject_id)
            record = SubjectRecord(
                subject_id=subject_id,
                folder=subject_folder,
                fa_path=fa_files[0],
                v1_path=v1_files[0],
                all_roi_paths=all_roi_paths,
                status=row.status if row else "",
            )
            records.append(record)
            by_id[subject_id] = record

        # Commit state.
        self.folder = folder
        self.roi_tokens = tokens
        self.current_roi_type = current
        self._tables = {current: table}
        self._records = by_id
        self._method = table.method
        self.current_subject_id = None
        self._unload_arrays()

        return SessionView(
            folder=folder,
            roi_options=[(t, roi_display_name(t)) for t in tokens],
            method=table.method,
            subjects=records,
        )

    # -- selection / ROI type --------------------------------------------- #
    def select_subject(self, subject_id: str) -> bool:
        """Make ``subject_id`` current and decode its FA/V1/ROI arrays.

        Returns ``True`` when both FA and V1 loaded. The subject is made current
        even on a load failure, so :meth:`current_metrics` still reflects it
        (the adapter turns ``False`` into a warning dialog).
        """
        record = self._records.get(subject_id)
        if record is None:
            return False
        self._unload_arrays()
        self.current_subject_id = subject_id
        return self._load_arrays(record)

    def set_roi_type(self, token: str) -> None:
        """Switch the current ROI type, loading its CSV (if present) and the
        current subject's ROI masks for that type."""
        if self.folder is None or token == self.current_roi_type or token not in self.roi_tokens:
            return

        self.current_roi_type = token
        if token not in self._tables:
            csv_path = self.folder / results_layout.alps_csv_name(token)
            if csv_path.exists():
                self._tables[token] = read_alps_csv(csv_path)

        # Re-apply the detected method from this token's table, whether freshly
        # read or already cached (so switching *back* restores it). A missing CSV
        # leaves no table, and the prior method is kept (matches the old viewer).
        table = self._tables.get(token)
        if table is not None:
            self._method = table.method

        if self.current_subject_id is not None:
            self._load_rois(self._records[self.current_subject_id], token)

    # -- queries ----------------------------------------------------------- #
    @property
    def current_alps_method(self) -> str:
        """The detected method of the current ROI type (drives the value layout)."""
        return self._method

    @property
    def current_shape(self) -> tuple[int, ...] | None:
        """Shape of the current subject's FA volume, or ``None``."""
        return self._fa.shape if self._fa is not None else None

    def num_slices(self, view: str) -> int:
        """Number of slices available in ``view`` for the current subject."""
        if self._fa is None:
            return 0
        shape = self._fa.shape
        if view == "axial":
            return shape[2] if len(shape) > 2 else 0
        if view == "coronal":
            return shape[1] if len(shape) > 1 else 0
        return shape[0]  # sagittal

    def default_slice(self, view: str) -> int:
        """The slice to show first in ``view`` -- the middle one."""
        return self.num_slices(view) // 2

    def render_slice(self, view: str, slice_index: int, show_rois: bool) -> np.ndarray | None:
        """Render the current subject's slice -- a thin wrapper over
        :func:`render_dec_slice` feeding it the loaded arrays."""
        if self._fa is None or self._v1 is None:
            return None
        return render_dec_slice(self._fa, self._v1, self._roi_data, view, slice_index, show_rois)

    def current_metrics(self) -> MetricsView | None:
        """The ALPS metrics for the current ``(roi_type, subject)``, or ``None``
        when no subject is selected."""
        if self.current_subject_id is None:
            return None
        table = self._tables.get(self.current_roi_type)
        row = table.rows.get(self.current_subject_id) if table else None
        return MetricsView(
            subject_id=self.current_subject_id,
            method=self._method,
            subject_method=self._method if row else "",
            status=row.status if row else "",
            lab_left=row.lab_left if row else None,
            lab_right=row.lab_right if row else None,
            lab_combined=row.lab_combined if row else None,
            pas_left=row.pas_left if row else None,
            pas_right=row.pas_right if row else None,
            pas_combined=row.pas_combined if row else None,
        )

    # -- internal: the one-subject-at-a-time array cache ------------------- #
    def _load_arrays(self, record: SubjectRecord) -> bool:
        try:
            if record.fa_path and record.fa_path.exists():
                self._fa = nib.load(record.fa_path).get_fdata()
            if record.v1_path and record.v1_path.exists():
                self._v1 = nib.load(record.v1_path).get_fdata()
            self._load_rois(record, self.current_roi_type)
            return self._fa is not None and self._v1 is not None
        except Exception as e:  # pragma: no cover - mirrors the old console print
            print(f"Error loading images for {record.subject_id}: {e}")
            return False

    def _load_rois(self, record: SubjectRecord, token: str) -> None:
        self._roi_data = {}
        for roi_name, roi_path in record.all_roi_paths.get(token, {}).items():
            if roi_path.exists():
                try:
                    self._roi_data[roi_name] = nib.load(roi_path).get_fdata()
                except Exception as e:  # pragma: no cover - mirrors the old print
                    print(f"Error loading ROI {roi_name}: {e}")

    def _unload_arrays(self) -> None:
        self._fa = None
        self._v1 = None
        self._roi_data = {}
