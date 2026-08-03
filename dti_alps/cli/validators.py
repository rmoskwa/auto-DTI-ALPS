"""
Argparse ``type=`` validators shared across the CLI verbs.

The ROI and ALPS flags are spelled identically on ``run`` and ``reanalyze`` so a
researcher only learns one vocabulary; that promise is only real if both verbs
validate them the same way. These live here rather than in either verb module so
neither owns the other's rules.

Every bound is read from :mod:`dti_alps.processing.constants` -- the same single
source of truth the GUI spin boxes and the ``AdaptiveSearchConfig`` guard use --
so the three cannot drift apart.
"""

import argparse

from ..processing.constants import ADAPTIVE_SEARCH_RANGE, ROI_SPHERE_RADIUS_RANGE

# Sphere radius validation bounds, read from the engine's single source of truth.
SPHERE_RADIUS_MIN, SPHERE_RADIUS_MAX = ROI_SPHERE_RADIUS_RANGE

# Adaptive search envelope bounds, from the same single source of truth the GUI
# and the AdaptiveSearchConfig guard use, so the three cannot drift apart.
SEARCH_MIN, SEARCH_MAX = ADAPTIVE_SEARCH_RANGE


def validate_search_value(value: str) -> int:
    """Validate an adaptive-search flag is an int within the allowed range."""
    try:
        parsed = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from err

    if parsed < SEARCH_MIN or parsed > SEARCH_MAX:
        raise argparse.ArgumentTypeError(
            f"must be between {SEARCH_MIN} and {SEARCH_MAX}, got {parsed}"
        )
    return parsed


def validate_sphere_radii(value: str) -> list[float]:
    """Validate comma-separated sphere radii are within allowed range."""
    radii = []
    for part in value.split(","):
        part = part.strip()
        try:
            radius = float(part)
        except ValueError as err:
            raise argparse.ArgumentTypeError(f"invalid float value: '{part}'") from err

        if radius < SPHERE_RADIUS_MIN or radius > SPHERE_RADIUS_MAX:
            raise argparse.ArgumentTypeError(
                f"radius must be between {SPHERE_RADIUS_MIN} and {SPHERE_RADIUS_MAX} mm, "
                f"got {radius}"
            )
        radii.append(radius)
    return radii
