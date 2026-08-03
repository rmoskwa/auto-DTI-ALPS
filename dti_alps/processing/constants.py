"""
Domain constants for the DTI-ALPS analysis engine.

A dependency-free leaf: this module imports nothing from within the project, so
it can never take part in an import cycle. The values here are domain facts the
processing pipeline consumes -- the MRtrix3 tensor-component indices, the FA
threshold that filters CSF voxels, the preprocessing defaults, and the
readout-time validation range.

They were lifted out of ``gui/config.py`` so the engine no longer
reaches up into the GUI package; ``gui/config.py`` re-exports these names, so
GUI code keeps referring to them as ``config.X`` unchanged. This module is the
single source of truth -- change a value here, not in the GUI.
"""

from dataclasses import dataclass

# Default preprocessing parameters
DEFAULT_READOUT_TIME = 0.05  # seconds
DEFAULT_PE_DIRECTION = "AP"
DEFAULT_RPE_SCHEME = "none"

# FA threshold for filtering CSF voxels from ROIs
FA_THRESHOLD = 0.2  # Minimum FA value for ROI voxels (filters out CSF)

# Quality-report warning thresholds. A per-ROI metric is flagged for manual
# inspection when it lands on the wrong side of its cutoff. The comparison
# direction differs per metric: directional alignment and FA warn when they fall
# BELOW a floor, angular dispersion and radial asymmetry warn when they rise
# ABOVE a ceiling. The FA floor sits just above the voxel-level ``FA_THRESHOLD``;
# the alignment, dispersion, and radial-asymmetry cutoffs are placement
# heuristics. (The engine's own crossing-fibre penalty in
# ``roi_placement.calculate_roi_quality`` uses a lower 1.8 ratio, Georgiopoulos
# et al. 2024; the report flags only the more clearly-crossing ROIs.)
QUALITY_WARN_DIRECTIONAL_ALIGNMENT_MIN = 0.80  # warn if mean |V1_target| < this
QUALITY_WARN_ANGULAR_DISPERSION_MAX = 10.0  # degrees; warn if dispersion > this
QUALITY_WARN_FA_MIN = 0.25  # warn if mean FA < this
QUALITY_WARN_RADIAL_ASYMMETRY_MAX = 2.0  # λ2/λ3; warn if mean ratio > this

# Readout time validation range (seconds)
READOUT_TIME_RANGE = (0.001, 1.0)

# ALPS calculation method: the closed vocabulary ``alps_method`` is drawn from,
# and its default.
ALPS_METHODS = ["ALPS-LAB", "ALPS-PAS", "Both"]
DEFAULT_ALPS_METHOD = "Both"

# ROI placement method: the closed vocabulary ``adaptive_roi_placement`` is drawn
# from, and its default. The default lives here rather than in ``gui/config.py``
# because all three consumers -- BatchConfig, PipelineState, and the GUI form --
# must agree on it. They did not: the dataclasses defaulted to "Adaptive" while
# the GUI defaulted to "Both", a divergence invisible only because the GUI always
# set the field explicitly. A second front end reading the dataclass default would
# have run a different analysis from the GUI with no flag in sight.
ROI_METHOD_OPTIONS = ["Adaptive", "Standard", "Both"]
DEFAULT_ROI_METHOD = "Both"

# ROI sphere radius validation range (mm)
ROI_SPHERE_RADIUS_RANGE = (1.0, 4.0)

# MRtrix3 tensor volume indices
# dwi2tensor outputs: D11, D22, D33, D12, D13, D23
TENSOR_DXX_INDEX = 0  # D11
TENSOR_DYY_INDEX = 1  # D22
TENSOR_DZZ_INDEX = 2  # D33

# Adaptive search envelope: the inclusive ±bound every field is constrained to.
# The single source of truth shared by the GUI spin boxes, the CLI flag
# validation, and the ``AdaptiveSearchConfig`` guard below, so the three cannot
# drift apart. Below 1 the search is degenerate; above 4 it grows pointlessly
# slow (see PRD 0023's performance note).
ADAPTIVE_SEARCH_RANGE = (1, 4)


@dataclass(frozen=True)
class AdaptiveSearchConfig:
    """The five-integer Adaptive search envelope (PRD 0023).

    Bundles the tuning of ``adaptive_roi_pair_placement`` into one cohesive
    concept that travels through the config layers as a single value. The field
    names match the engine leaf's parameter names exactly, so the search call
    site forwards them without renaming churn.

    - ``search_x`` / ``search_y`` / ``search_z``: the per-ROI search window --
      how far (in voxels) each ROI may independently move from its template
      centroid.
    - ``max_y_drift`` / ``max_z_drift``: the pair drift constraint -- how far the
      association ROI may diverge from the projection ROI on each axis. Kept
      independent so asymmetric Y-vs-Z drift is expressible.

    Defaults are the historical hard-coded ``3 / 1 / 2 / 1 / 1`` values, so an
    envelope left untouched reproduces today's placement. ``__post_init__``
    rejects any field outside :data:`ADAPTIVE_SEARCH_RANGE`, making the bound a
    property of the type rather than a convention each caller happens to follow;
    this is compatible with ``frozen=True`` because the guard only reads ``self``.
    """

    search_x: int = 3
    search_y: int = 1
    search_z: int = 2
    max_y_drift: int = 1
    max_z_drift: int = 1

    def __post_init__(self) -> None:
        low, high = ADAPTIVE_SEARCH_RANGE
        for name in ("search_x", "search_y", "search_z", "max_y_drift", "max_z_drift"):
            value = getattr(self, name)
            if not (low <= value <= high):
                raise ValueError(
                    f"AdaptiveSearchConfig.{name}={value} is out of range [{low}, {high}]"
                )
