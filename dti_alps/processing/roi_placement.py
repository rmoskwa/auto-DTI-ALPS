"""
Pure ROI-placement geometry, quality scoring, and search for DTI-ALPS.

A dependency-free leaf: this module imports only numpy, so it can never take
part in an import cycle or drag a registration backend into the placement
science. It owns the three cohesive sub-concerns that decide which voxels the
ALPS formula reads:

- shape geometry -- create_sphere_mask, create_square_v9_mask, and the
  V1-optimized create_square_v4_mask (best of four 2x2 corner configurations);
- quality scoring -- calculate_roi_quality (fiber purity, direction strength,
  FA, and the lambda2/lambda3 > 1.8 crossing-fiber penalty, Georgiopoulos et
  al. 2024);
- placement search -- find_mask_centroid and the joint adaptive_roi_pair_placement
  that maximizes paired fiber purity within a Y/Z-drift constraint.

The functions are pure (arrays in -> masks/tuples out); the IO shells that load
FA/V1/L2/L3 and save masks live in the registration backend and reanalysis.
Lifted out of registration/base.py, the sibling of the pure ALPS
module (alps_calculation.py) and constants.py.
"""

import numpy as np


def create_sphere_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[float, float, float],
    radius_mm: float,
    voxel_size: tuple[float, float, float],
) -> np.ndarray:
    """
    Create a spherical binary mask centered at given voxel coordinates.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of float
        Center of sphere in voxel coordinates
    radius_mm : float
        Radius of sphere in millimeters
    voxel_size : tuple of float
        Voxel dimensions in millimeters (x, y, z)

    Returns
    -------
    np.ndarray
        Binary mask with sphere
    """
    x, y, z = np.ogrid[: shape[0], : shape[1], : shape[2]]

    # Calculate squared distance from center in mm
    dist_sq = (
        ((x - center_voxel[0]) * voxel_size[0]) ** 2
        + ((y - center_voxel[1]) * voxel_size[1]) ** 2
        + ((z - center_voxel[2]) * voxel_size[2]) ** 2
    )

    return dist_sq <= radius_mm**2


def create_square_v9_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[int, int, int],
) -> np.ndarray:
    """
    Create a 3x3 square binary mask (9 voxels) in the axial plane.

    The mask is a 3x3 block centered at the given voxel coordinates,
    all in the same axial (Z) slice. This provides a simple, reproducible
    ROI shape that is less sensitive to voxel size variations.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of int
        Center of square in voxel coordinates (x, y, z)

    Returns
    -------
    np.ndarray
        Binary mask with 3x3 square (9 voxels)
    """
    mask = np.zeros(shape, dtype=bool)

    cx, cy, cz = center_voxel

    # Create 3x3 block in axial plane (±1 in X and Y, same Z)
    for dx in range(-1, 2):
        for dy in range(-1, 2):
            x, y, z = cx + dx, cy + dy, cz
            # Ensure within bounds
            if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
                mask[x, y, z] = True

    return mask


def create_square_v4_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[int, int, int],
    v1_data: np.ndarray | None = None,
    fiber_type: str = "proj",
) -> np.ndarray:
    """
    Create a 2x2 square binary mask (4 voxels) in the axial plane.

    The centroid is placed at one corner of the 2x2 square. There are 4 possible
    configurations for the 2x2 square with the centroid as a corner. The optimal
    configuration is selected by maximizing the average V1(z) for projection ROIs
    or V1(y) for association ROIs, helping the square encapsulate the track region
    and avoid boundaries where crossing fibers might exist.

    If v1_data is not provided, defaults to centroid at bottom-left corner.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of int
        Corner voxel coordinates (x, y, z) - one corner of the 2x2 square
    v1_data : np.ndarray, optional
        Primary eigenvector data (x, y, z, 3) for optimal configuration selection
    fiber_type : str
        Either 'proj' (maximize V1_z) or 'assoc' (maximize V1_y)

    Returns
    -------
    np.ndarray
        Binary mask with 2x2 square (4 voxels)
    """
    cx, cy, cz = center_voxel

    # Define all 4 possible 2x2 configurations with centroid as a corner
    # Each configuration is a list of (dx, dy) offsets from centroid
    configurations = [
        # Centroid at bottom-left: (cx, cy), (cx+1, cy), (cx, cy+1), (cx+1, cy+1)
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        # Centroid at bottom-right: (cx-1, cy), (cx, cy), (cx-1, cy+1), (cx, cy+1)
        [(-1, 0), (0, 0), (-1, 1), (0, 1)],
        # Centroid at top-left: (cx, cy-1), (cx+1, cy-1), (cx, cy), (cx+1, cy)
        [(0, -1), (1, -1), (0, 0), (1, 0)],
        # Centroid at top-right: (cx-1, cy-1), (cx, cy-1), (cx-1, cy), (cx, cy)
        [(-1, -1), (0, -1), (-1, 0), (0, 0)],
    ]

    def _get_voxels_for_config(offsets: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
        """Get valid voxel coordinates for a configuration."""
        voxels = []
        for dx, dy in offsets:
            x, y, z = cx + dx, cy + dy, cz
            if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
                voxels.append((x, y, z))
        return voxels

    def _calculate_v1_metric(voxels: list[tuple[int, int, int]]) -> float:
        """Calculate average V1 component for the given voxels."""
        if not voxels or v1_data is None:
            return 0.0

        component_idx = 2 if fiber_type == "proj" else 1  # Z for proj, Y for assoc
        total = 0.0
        for x, y, z in voxels:
            total += abs(v1_data[x, y, z, component_idx])
        return total / len(voxels)

    # Select optimal configuration
    if v1_data is not None:
        best_config = None
        best_metric = -1.0

        for config in configurations:
            voxels = _get_voxels_for_config(config)
            if len(voxels) < 4:
                continue  # Skip configs that go out of bounds
            metric = _calculate_v1_metric(voxels)
            if metric > best_metric:
                best_metric = metric
                best_config = config

        # Fallback to first config if none are valid
        if best_config is None:
            best_config = configurations[0]
    else:
        # Default to first configuration (centroid at bottom-left)
        best_config = configurations[0]

    # Create mask with selected configuration
    mask = np.zeros(shape, dtype=bool)
    for dx, dy in best_config:
        x, y, z = cx + dx, cy + dy, cz
        if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
            mask[x, y, z] = True

    return mask


def find_mask_centroid(mask_data: np.ndarray) -> tuple[int, int, int] | None:
    """
    Find the centroid of non-zero voxels in a mask, rounded to nearest integer.

    Parameters
    ----------
    mask_data : np.ndarray
        Binary mask array

    Returns
    -------
    tuple of int or None
        Centroid coordinates (x, y, z) rounded to nearest integer,
        or None if mask is empty
    """
    coords = np.where(mask_data > 0)
    if len(coords[0]) == 0:
        return None

    centroid = (
        int(round(coords[0].mean())),
        int(round(coords[1].mean())),
        int(round(coords[2].mean())),
    )
    return centroid


def calculate_roi_quality(
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    mask: np.ndarray,
    fiber_type: str,
    l2_data: np.ndarray | None = None,
    l3_data: np.ndarray | None = None,
    radial_threshold: float = 1.8,
) -> tuple[float, float, float, float]:
    """
    Calculate ROI quality based on fiber purity, direction strength, FA,
    and optionally radial asymmetry (λ2/λ3).

    The quality score rewards ROIs that:
    1. Have high fiber purity (% of voxels with correct dominant direction)
    2. Have strong directional alignment (mean magnitude of target V1 component)
    3. Have high FA values (strong fiber signal)
    4. Avoid crossing fiber contamination (λ2/λ3 above threshold)

    The radial asymmetry penalty is only applied when mean λ2/λ3 exceeds
    the threshold, to avoid penalizing genuine perivascular diffusion signal
    (which also elevates L2 relative to L3). The threshold of 1.8 is based
    on Georgiopoulos et al. (2024, Brain Communications) who used this
    eigenvalue ratio to identify crossing fiber regions in DTI-ALPS ROIs.

    Parameters
    ----------
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    mask : np.ndarray
        Binary ROI mask
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)
    l2_data : np.ndarray, optional
        Second eigenvalue data (x, y, z). Only available when alps_method
        is "ALPS-PAS" or "Both".
    l3_data : np.ndarray, optional
        Third eigenvalue data (x, y, z). Only available when alps_method
        is "ALPS-PAS" or "Both".
    radial_threshold : float, optional
        Mean λ2/λ3 ratio above which the crossing fiber penalty is applied.
        Default 1.8 (Georgiopoulos et al. 2024).

    Returns
    -------
    tuple of (purity, direction_strength, mean_fa, combined_score)
        purity: fraction of voxels with correct fiber orientation
        direction_strength: mean magnitude of target V1 component
        mean_fa: mean FA value in ROI
        combined_score: purity * direction_strength * mean_fa * radial_penalty
    """
    coords = np.where(mask)
    if len(coords[0]) == 0:
        return 0.0, 0.0, 0.0, 0.0

    n_correct = 0
    direction_strengths = []
    fa_values = []

    for i in range(len(coords[0])):
        x, y, z = coords[0][i], coords[1][i], coords[2][i]
        v1 = v1_data[x, y, z, :]
        fa = fa_data[x, y, z]
        abs_v1 = np.abs(v1)

        fa_values.append(fa)

        if fiber_type == "proj":
            # Projection fibers: Z-dominant (superior-inferior)
            is_correct = abs_v1[2] > abs_v1[1] and abs_v1[2] > abs_v1[0]
            direction_strength = abs_v1[2]
        else:
            # Association fibers: Y-dominant (anterior-posterior)
            is_correct = abs_v1[1] > abs_v1[2] and abs_v1[1] > abs_v1[0]
            direction_strength = abs_v1[1]

        if is_correct:
            n_correct += 1
        direction_strengths.append(direction_strength)

    purity = n_correct / len(coords[0])
    mean_direction_strength = np.mean(direction_strengths)
    mean_fa = np.mean(fa_values)

    # Combined score rewards ROIs with high purity, strong direction, and high FA
    combined_score = purity * mean_direction_strength * mean_fa

    # Apply crossing fiber penalty only when mean λ2/λ3 exceeds the threshold.
    # Below the threshold, asymmetry likely reflects genuine perivascular
    # diffusion signal rather than crossing fibers, so no penalty is applied.
    # Above the threshold, sqrt(threshold/ratio) penalizes proportionally.
    if l2_data is not None and l3_data is not None:
        l2_values = l2_data[coords]
        l3_values = l3_data[coords]
        valid = l3_values > 0
        if np.any(valid):
            radial_asymmetry = float(np.mean(l2_values[valid] / l3_values[valid]))
            if radial_asymmetry > radial_threshold:
                combined_score *= (radial_threshold / radial_asymmetry) ** 0.5

    return purity, mean_direction_strength, mean_fa, combined_score


def adaptive_roi_pair_placement(
    proj_centroid: tuple[int, int, int],
    assoc_centroid: tuple[int, int, int],
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    shape: tuple[int, int, int],
    voxel_size: tuple[float, float, float],
    radius_mm: float = 3.0,
    search_x: int = 3,
    search_y: int = 1,
    search_z: int = 2,
    max_y_drift: int = 1,
    max_z_drift: int = 1,
    shape_type: str = "sphere",
    l2_data: np.ndarray | None = None,
    l3_data: np.ndarray | None = None,
) -> tuple[tuple[int, int, int], tuple[int, int, int], float, float, float]:
    """
    Jointly adapt projection and association ROI placement as a pair.

    Instead of optimizing projection ROI first and then constraining association ROI
    to it, this function searches all valid (proj, assoc) pairs simultaneously and
    selects the pair that maximizes the combined quality score.

    This approach prevents suboptimal results where a locally-optimal projection ROI
    position severely limits the quality of the paired association ROI.

    Parameters
    ----------
    proj_centroid : tuple of int
        Initial projection ROI centroid from template registration (x, y, z)
    assoc_centroid : tuple of int
        Initial association ROI centroid from template registration (x, y, z)
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    shape : tuple of int
        Shape of the image volume
    voxel_size : tuple of float
        Voxel dimensions in mm
    radius_mm : float
        Sphere radius in millimeters (used for sphere shape)
    search_x : int
        Search range in X direction (voxels), default 3
    search_y : int
        Search range in Y direction (voxels), default 1
    search_z : int
        Search range in Z direction (voxels), default 2
    max_y_drift : int
        Maximum allowed Y-coordinate difference between proj and assoc ROIs (voxels).
        Default 1.
    max_z_drift : int
        Maximum allowed Z-coordinate difference between proj and assoc ROIs (voxels).
        Default 1.
    shape_type : str
        ROI shape type: "sphere", "squarev9", or "squarev4". Default "sphere".
    l2_data : np.ndarray, optional
        Second eigenvalue data for radial asymmetry penalty.
    l3_data : np.ndarray, optional
        Third eigenvalue data for radial asymmetry penalty.

    Returns
    -------
    tuple of (best_proj_center, best_assoc_center, best_proj_purity, best_assoc_purity, best_combined_score)
        best_proj_center: optimal projection ROI centroid position
        best_assoc_center: optimal association ROI centroid position
        best_proj_purity: fiber purity at optimal projection position
        best_assoc_purity: fiber purity at optimal association position
        best_combined_score: combined quality score (geometric mean of individual scores)
    """
    best_proj_center = proj_centroid
    best_assoc_center = assoc_centroid
    best_combined_score = -1.0
    best_proj_purity = 0.0
    best_assoc_purity = 0.0

    # Precompute quality scores for all projection ROI candidate positions
    # This avoids redundant mask creation and quality calculation in the pair search
    proj_scores: dict[tuple[int, int, int], tuple[float, float]] = {}
    for dx in range(-search_x, search_x + 1):
        for dy in range(-search_y, search_y + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    proj_centroid[0] + dx,
                    proj_centroid[1] + dy,
                    proj_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # Create mask and calculate quality
                if shape_type == "sphere":
                    mask = create_sphere_mask(shape, test_center, radius_mm, voxel_size)
                elif shape_type == "squarev4":
                    mask = create_square_v4_mask(shape, test_center, v1_data, "proj")
                else:  # squarev9
                    mask = create_square_v9_mask(shape, test_center)

                purity, _, _, score = calculate_roi_quality(
                    v1_data, fa_data, mask, "proj", l2_data, l3_data
                )
                if score > 0:
                    proj_scores[test_center] = (purity, score)

    # Precompute quality scores for all association ROI candidate positions
    assoc_scores: dict[tuple[int, int, int], tuple[float, float]] = {}
    for dx in range(-search_x, search_x + 1):
        for dy in range(-search_y, search_y + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    assoc_centroid[0] + dx,
                    assoc_centroid[1] + dy,
                    assoc_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # Create mask and calculate quality
                if shape_type == "sphere":
                    mask = create_sphere_mask(shape, test_center, radius_mm, voxel_size)
                elif shape_type == "squarev4":
                    mask = create_square_v4_mask(shape, test_center, v1_data, "assoc")
                else:  # squarev9
                    mask = create_square_v9_mask(shape, test_center)

                purity, _, _, score = calculate_roi_quality(
                    v1_data, fa_data, mask, "assoc", l2_data, l3_data
                )
                if score > 0:
                    assoc_scores[test_center] = (purity, score)

    # Search all valid (proj, assoc) pairs using precomputed scores
    for test_proj, (proj_purity, proj_score) in proj_scores.items():
        for test_assoc, (assoc_purity, assoc_score) in assoc_scores.items():
            # Check Y and Z drift constraint between proj and assoc
            y_drift = abs(test_assoc[1] - test_proj[1])
            z_drift = abs(test_assoc[2] - test_proj[2])
            if y_drift > max_y_drift or z_drift > max_z_drift:
                continue

            # Combined score: geometric mean of individual scores
            # This ensures both ROIs must have good quality
            combined_score = np.sqrt(proj_score * assoc_score)

            if combined_score > best_combined_score:
                best_combined_score = combined_score
                best_proj_center = test_proj
                best_assoc_center = test_assoc
                best_proj_purity = proj_purity
                best_assoc_purity = assoc_purity

    return (
        best_proj_center,
        best_assoc_center,
        best_proj_purity,
        best_assoc_purity,
        best_combined_score,
    )
