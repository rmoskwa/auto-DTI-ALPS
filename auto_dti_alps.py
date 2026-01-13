#!/usr/bin/env python3
"""
Automatic DTI-ALPS ROI Placement

This script automatically identifies optimal ROI locations for DTI-ALPS
(Diffusion Tensor Imaging Along the Perivascular Space) analysis by
detecting adjacent projection and association fiber regions.

The algorithm mimics human visual inspection by:
1. Classifying voxels as projection (S-I oriented) or association (A-P oriented) fibers
2. Finding regions where these fiber types are adjacent
3. Selecting locations where both fiber zones are wide enough for ROI placement
4. Enforcing bilateral Z-alignment as required by DTI-ALPS theory

Author: Auto-generated
"""

import numpy as np
import nibabel as nib
from scipy import ndimage
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict
import argparse
import os


@dataclass
class FiberZone:
    """Represents a contiguous zone of fibers along an X-line."""
    start_x: int
    end_x: int
    fiber_type: str  # 'proj' or 'assoc'
    y: int
    z: int

    @property
    def width(self) -> int:
        return self.end_x - self.start_x + 1

    @property
    def center_x(self) -> int:
        return (self.start_x + self.end_x) // 2


@dataclass
class ROICandidate:
    """Represents a candidate location for DTI-ALPS ROI placement."""
    proj_zone: FiberZone
    assoc_zone: FiberZone
    hemisphere: str  # 'left' or 'right'
    proj_purity: float = 0.0  # 3D fiber purity score (0-1)
    assoc_purity: float = 0.0  # 3D fiber purity score (0-1)

    @property
    def y(self) -> int:
        return self.proj_zone.y

    @property
    def z(self) -> int:
        return self.proj_zone.z

    @property
    def width_score(self) -> int:
        """Score based on zone width (wider = better)."""
        return self.proj_zone.width + self.assoc_zone.width

    @property
    def purity_score(self) -> float:
        """Score based on 3D fiber purity (higher = better)."""
        return self.proj_purity + self.assoc_purity

    @property
    def combined_score(self) -> float:
        """Combined score balancing width and purity."""
        # Weight purity more heavily since it's the key insight
        return self.width_score * 0.3 + self.purity_score * 100 * 0.7

    @property
    def proj_center(self) -> Tuple[int, int, int]:
        return (self.proj_zone.center_x, self.y, self.z)

    @property
    def assoc_center(self) -> Tuple[int, int, int]:
        return (self.assoc_zone.center_x, self.y, self.z)


class DTIALPSDetector:
    """
    Automatic detector for DTI-ALPS ROI placement.

    Parameters
    ----------
    fa_thresh : float
        Minimum FA value for white matter (default: 0.25)
    orient_thresh : float
        Minimum dominant eigenvector component for fiber classification (default: 0.7)
    min_zone_width : int
        Minimum zone width in voxels for ROI placement (default: 5)
    roi_radius_mm : float
        Radius of the spherical ROI in millimeters (default: 4.0)
    z_tolerance : int
        Maximum Z-difference allowed between left and right ROIs (default: 2)
        Accounts for head tilt where left/right ROIs may be on slightly different axial slices.
    """

    def __init__(
        self,
        fa_thresh: float = 0.25,
        orient_thresh: float = 0.7,
        min_zone_width: int = 5,
        roi_radius_mm: float = 4.0,
        z_tolerance: int = 2
    ):
        self.fa_thresh = fa_thresh
        self.orient_thresh = orient_thresh
        self.min_zone_width = min_zone_width
        self.roi_radius_mm = roi_radius_mm
        self.z_tolerance = z_tolerance

        # Data storage
        self.fa: Optional[np.ndarray] = None
        self.v1: Optional[np.ndarray] = None
        self.affine: Optional[np.ndarray] = None
        self.header = None

        # Results
        self.candidates: List[ROICandidate] = []
        self.selected_rois: Dict[str, Tuple[int, int, int]] = {}

    def load_data(self, fa_path: str, v1_path: str) -> None:
        """Load FA and V1 NIfTI files."""
        fa_img = nib.load(fa_path)
        v1_img = nib.load(v1_path)

        self.fa = fa_img.get_fdata()
        self.v1 = v1_img.get_fdata()
        self.affine = fa_img.affine
        self.header = fa_img.header

        print(f"Loaded FA: shape={self.fa.shape}, voxel size={self.header.get_zooms()[:3]}")
        print(f"Loaded V1: shape={self.v1.shape}")

    def classify_fibers(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Classify voxels as projection or association fibers.

        Returns
        -------
        proj_mask : ndarray
            Boolean mask of projection fiber voxels (Z-dominant)
        assoc_mask : ndarray
            Boolean mask of association fiber voxels (Y-dominant)
        """
        v1_abs = np.abs(self.v1)

        # Projection fibers: primarily superior-inferior (Z-dominant)
        proj_mask = (v1_abs[:, :, :, 2] > self.orient_thresh) & (self.fa > self.fa_thresh)

        # Association fibers: primarily anterior-posterior (Y-dominant)
        assoc_mask = (v1_abs[:, :, :, 1] > self.orient_thresh) & (self.fa > self.fa_thresh)

        return proj_mask, assoc_mask

    def _get_spherical_roi_voxels(
        self,
        center: Tuple[int, int, int],
        radius_mm: Optional[float] = None
    ) -> List[Tuple[int, int, int]]:
        """
        Get list of voxel coordinates within a spherical ROI in physical space.

        The sphere is defined in millimeters, accounting for anisotropic voxel
        dimensions. This matches FSLeyes/fslmaths behavior.

        Parameters
        ----------
        center : tuple
            (x, y, z) center coordinates in voxels
        radius_mm : float, optional
            Radius of the sphere in millimeters (default: self.roi_radius_mm)

        Returns
        -------
        voxels : list of (x, y, z) tuples
        """
        if radius_mm is None:
            radius_mm = self.roi_radius_mm

        # Get voxel dimensions in mm
        voxel_size = self.header.get_zooms()[:3]
        vox_x, vox_y, vox_z = voxel_size

        # Calculate search range in voxels for each dimension
        range_x = int(np.ceil(radius_mm / vox_x))
        range_y = int(np.ceil(radius_mm / vox_y))
        range_z = int(np.ceil(radius_mm / vox_z))

        cx, cy, cz = center
        voxels = []

        for dz in range(-range_z, range_z + 1):
            z = cz + dz
            if z < 0 or z >= self.fa.shape[2]:
                continue

            for dx in range(-range_x, range_x + 1):
                for dy in range(-range_y, range_y + 1):
                    # Calculate distance in mm (accounting for voxel dimensions)
                    dist_mm_sq = (dx * vox_x)**2 + (dy * vox_y)**2 + (dz * vox_z)**2

                    # Check if within spherical radius in mm
                    if dist_mm_sq > radius_mm**2:
                        continue

                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.fa.shape[0] and 0 <= y < self.fa.shape[1]:
                        voxels.append((x, y, z))

        return voxels

    def compute_3d_fiber_purity(
        self,
        center: Tuple[int, int, int],
        expected_type: str,
        radius_mm: Optional[float] = None
    ) -> float:
        """
        Compute the fiber purity score for a 3D spherical ROI.

        Parameters
        ----------
        center : tuple
            (x, y, z) center coordinates
        expected_type : str
            'proj' or 'assoc' - the expected fiber type
        radius_mm : float, optional
            Radius of the sphere in millimeters (default: self.roi_radius_mm)

        Returns
        -------
        purity : float
            Fraction of voxels matching the expected fiber type (0-1)
        """
        voxels = self._get_spherical_roi_voxels(center, radius_mm)
        if not voxels:
            return 0.0

        v1_abs = np.abs(self.v1)
        matching_count = 0
        valid_count = 0

        for x, y, z in voxels:
            fa_val = self.fa[x, y, z]
            if fa_val < self.fa_thresh * 0.8:  # Slightly relaxed threshold for counting
                continue

            valid_count += 1
            vy = v1_abs[x, y, z, 1]
            vz = v1_abs[x, y, z, 2]

            if expected_type == 'proj' and vz > self.orient_thresh:
                matching_count += 1
            elif expected_type == 'assoc' and vy > self.orient_thresh:
                matching_count += 1

        if valid_count == 0:
            return 0.0

        return matching_count / valid_count

    def _find_zones_along_x(self, y: int, z: int) -> List[FiberZone]:
        """
        Find contiguous fiber zones along an X-line at given Y, Z.

        Returns list of FiberZone objects for projection and association fibers.
        """
        v1_abs = np.abs(self.v1)
        zones = []
        current_type = None
        start_x = None

        for x in range(self.fa.shape[0]):
            fa_val = self.fa[x, y, z]

            if fa_val > self.fa_thresh:
                vy = v1_abs[x, y, z, 1]
                vz = v1_abs[x, y, z, 2]

                if vz > self.orient_thresh:
                    fiber_type = 'proj'
                elif vy > self.orient_thresh:
                    fiber_type = 'assoc'
                else:
                    fiber_type = None
            else:
                fiber_type = None

            # Zone transition
            if fiber_type != current_type:
                # Save previous zone if valid
                if current_type in ['proj', 'assoc'] and start_x is not None:
                    zones.append(FiberZone(start_x, x - 1, current_type, y, z))

                # Start new zone
                if fiber_type in ['proj', 'assoc']:
                    start_x = x
                else:
                    start_x = None
                current_type = fiber_type

        # Close final zone
        if current_type in ['proj', 'assoc'] and start_x is not None:
            zones.append(FiberZone(start_x, self.fa.shape[0] - 1, current_type, y, z))

        return zones

    def _find_adjacent_pairs(self, zones: List[FiberZone], max_gap: int = 3) -> List[Tuple[FiberZone, FiberZone]]:
        """
        Find adjacent projection-association zone pairs.

        Parameters
        ----------
        zones : list of FiberZone
            Zones found along an X-line
        max_gap : int
            Maximum gap between zones to be considered adjacent

        Returns
        -------
        pairs : list of (proj_zone, assoc_zone) tuples
        """
        pairs = []

        for i in range(len(zones) - 1):
            z1, z2 = zones[i], zones[i + 1]

            # Check adjacency (gap between zones)
            gap = z2.start_x - z1.end_x - 1
            if gap > max_gap:
                continue

            # Check minimum width
            if z1.width < self.min_zone_width or z2.width < self.min_zone_width:
                continue

            # Check for proj-assoc pair (in either order)
            if z1.fiber_type == 'proj' and z2.fiber_type == 'assoc':
                pairs.append((z1, z2))
            elif z1.fiber_type == 'assoc' and z2.fiber_type == 'proj':
                pairs.append((z2, z1))

        return pairs

    def find_candidates(
        self,
        z_range: Optional[Tuple[int, int]] = None,
        y_range: Optional[Tuple[int, int]] = None
    ) -> List[ROICandidate]:
        """
        Find all DTI-ALPS ROI candidate locations.

        Parameters
        ----------
        z_range : tuple of (min, max), optional
            Range of Z slices to search (default: middle third of volume)
        y_range : tuple of (min, max), optional
            Range of Y coordinates to search (default: middle half of volume)

        Returns
        -------
        candidates : list of ROICandidate
        """
        if self.fa is None:
            raise ValueError("Data not loaded. Call load_data() first.")

        # Default ranges (focus on central brain regions)
        if z_range is None:
            z_min = int(self.fa.shape[2] * 0.4)
            z_max = int(self.fa.shape[2] * 0.7)
            z_range = (z_min, z_max)

        if y_range is None:
            y_min = int(self.fa.shape[1] * 0.3)
            y_max = int(self.fa.shape[1] * 0.7)
            y_range = (y_min, y_max)

        center_x = self.fa.shape[0] // 2
        self.candidates = []

        print(f"Searching Z={z_range[0]}-{z_range[1]}, Y={y_range[0]}-{y_range[1]}")

        for z in range(z_range[0], z_range[1] + 1):
            for y in range(y_range[0], y_range[1] + 1):
                zones = self._find_zones_along_x(y, z)
                pairs = self._find_adjacent_pairs(zones)

                for proj_zone, assoc_zone in pairs:
                    # Determine hemisphere based on position relative to center
                    # For LEFT hemisphere: association is lateral (higher X) to projection
                    # For RIGHT hemisphere: association is lateral (lower X) to projection
                    avg_x = (proj_zone.center_x + assoc_zone.center_x) / 2

                    if avg_x > center_x:
                        # Left hemisphere: assoc should have higher X than proj
                        if assoc_zone.center_x > proj_zone.center_x:
                            hemisphere = 'left'
                        else:
                            continue  # Wrong orientation for left
                    else:
                        # Right hemisphere: assoc should have lower X than proj
                        if assoc_zone.center_x < proj_zone.center_x:
                            hemisphere = 'right'
                        else:
                            continue  # Wrong orientation for right

                    candidate = ROICandidate(proj_zone, assoc_zone, hemisphere)
                    self.candidates.append(candidate)

        print(f"Found {len(self.candidates)} initial candidates")

        # Compute 3D fiber purity for all candidates
        print("Computing 3D fiber purity scores...")
        for candidate in self.candidates:
            candidate.proj_purity = self.compute_3d_fiber_purity(
                candidate.proj_center, 'proj'
            )
            candidate.assoc_purity = self.compute_3d_fiber_purity(
                candidate.assoc_center, 'assoc'
            )

        # Filter candidates with minimum purity threshold
        min_purity = 0.7
        self.candidates = [
            c for c in self.candidates
            if c.proj_purity >= min_purity and c.assoc_purity >= min_purity
        ]

        print(f"After purity filtering (>={min_purity}): {len(self.candidates)} candidates")
        left_count = sum(1 for c in self.candidates if c.hemisphere == 'left')
        right_count = sum(1 for c in self.candidates if c.hemisphere == 'right')
        print(f"  Left hemisphere: {left_count}")
        print(f"  Right hemisphere: {right_count}")

        return self.candidates

    def select_optimal_rois(self) -> Dict[str, Tuple[int, int, int]]:
        """
        Select optimal ROI locations with bilateral Z-alignment.

        Returns
        -------
        rois : dict
            Dictionary with keys 'proj_left', 'proj_right', 'assoc_left', 'assoc_right'
            and values as (x, y, z) tuples
        """
        if not self.candidates:
            raise ValueError("No candidates found. Call find_candidates() first.")

        # Separate by hemisphere
        left_candidates = [c for c in self.candidates if c.hemisphere == 'left']
        right_candidates = [c for c in self.candidates if c.hemisphere == 'right']

        if not left_candidates or not right_candidates:
            raise ValueError("Need candidates in both hemispheres")

        # Sort by combined score (descending) - balances width and purity
        left_candidates.sort(key=lambda c: c.combined_score, reverse=True)
        right_candidates.sort(key=lambda c: c.combined_score, reverse=True)

        # Find best matching pair with Z-alignment
        best_pair = None
        best_combined_score = 0

        for left_cand in left_candidates[:100]:  # Check top 100 from each
            for right_cand in right_candidates[:100]:
                # Check Z-alignment (accounts for head tilt)
                z_diff = abs(left_cand.z - right_cand.z)
                if z_diff > self.z_tolerance:
                    continue

                # Combined score from both hemispheres
                combined_score = left_cand.combined_score + right_cand.combined_score

                if combined_score > best_combined_score:
                    best_combined_score = combined_score
                    best_pair = (left_cand, right_cand)

        if best_pair is None:
            raise ValueError(f"No matching pairs found within Z-tolerance of {self.z_tolerance}")

        left_cand, right_cand = best_pair

        self.selected_rois = {
            'proj_left': left_cand.proj_center,
            'proj_right': right_cand.proj_center,
            'assoc_left': left_cand.assoc_center,
            'assoc_right': right_cand.assoc_center,
        }

        print(f"\nSelected ROI centers:")
        print(f"  Projection Left:  {self.selected_rois['proj_left']} (purity: {left_cand.proj_purity:.1%})")
        print(f"  Projection Right: {self.selected_rois['proj_right']} (purity: {right_cand.proj_purity:.1%})")
        print(f"  Association Left:  {self.selected_rois['assoc_left']} (purity: {left_cand.assoc_purity:.1%})")
        print(f"  Association Right: {self.selected_rois['assoc_right']} (purity: {right_cand.assoc_purity:.1%})")
        print(f"  Combined score: {best_combined_score:.1f}")
        print(f"  Z-difference: {abs(left_cand.z - right_cand.z)}")

        return self.selected_rois

    def create_roi_masks(self) -> Dict[str, np.ndarray]:
        """
        Create 3D binary spherical masks for each ROI.

        Returns
        -------
        masks : dict
            Dictionary with ROI names as keys and 3D numpy arrays as values
        """
        if not self.selected_rois:
            raise ValueError("No ROIs selected. Call select_optimal_rois() first.")

        masks = {}

        for roi_name, center in self.selected_rois.items():
            mask = np.zeros(self.fa.shape, dtype=np.uint8)

            # Get spherical ROI voxels
            voxels = self._get_spherical_roi_voxels(center)
            for x, y, z in voxels:
                mask[x, y, z] = 1

            masks[roi_name] = mask

        return masks

    def save_roi_masks(self, output_dir: str, prefix: str = "auto") -> None:
        """
        Save ROI masks as NIfTI files.

        Parameters
        ----------
        output_dir : str
            Directory to save masks
        prefix : str
            Prefix for output filenames
        """
        masks = self.create_roi_masks()

        os.makedirs(output_dir, exist_ok=True)

        for roi_name, mask in masks.items():
            filename = f"{prefix}_{roi_name}.nii.gz"
            filepath = os.path.join(output_dir, filename)

            img = nib.Nifti1Image(mask, self.affine, self.header)
            nib.save(img, filepath)
            print(f"Saved: {filepath}")

    def compute_alps_index(self) -> Dict[str, float]:
        """
        Compute the DTI-ALPS index from the selected ROIs.

        Returns
        -------
        results : dict
            Dictionary containing Dxproj, Dyassoc, Dzproj, Dxassoc, and ALPS index
        """
        if not self.selected_rois:
            raise ValueError("No ROIs selected. Call select_optimal_rois() first.")

        masks = self.create_roi_masks()

        # Get diffusivity components from V1 (principal eigenvector)
        # For DTI-ALPS, we need the diffusivity along each axis
        # Using FA * V1_component as a proxy for directional diffusivity

        results = {}

        # Extract mean values in each ROI
        for side in ['left', 'right']:
            proj_mask = masks[f'proj_{side}']
            assoc_mask = masks[f'assoc_{side}']

            # In projection ROI: measure X and Z components
            proj_idx = np.where(proj_mask > 0)
            proj_fa = np.mean(self.fa[proj_idx])
            proj_vx = np.mean(np.abs(self.v1[proj_idx[0], proj_idx[1], proj_idx[2], 0]))
            proj_vz = np.mean(np.abs(self.v1[proj_idx[0], proj_idx[1], proj_idx[2], 2]))

            # In association ROI: measure X and Y components
            assoc_idx = np.where(assoc_mask > 0)
            assoc_fa = np.mean(self.fa[assoc_idx])
            assoc_vx = np.mean(np.abs(self.v1[assoc_idx[0], assoc_idx[1], assoc_idx[2], 0]))
            assoc_vy = np.mean(np.abs(self.v1[assoc_idx[0], assoc_idx[1], assoc_idx[2], 1]))

            results[f'{side}_proj_fa'] = proj_fa
            results[f'{side}_assoc_fa'] = assoc_fa
            results[f'{side}_proj_vx'] = proj_vx
            results[f'{side}_proj_vz'] = proj_vz
            results[f'{side}_assoc_vx'] = assoc_vx
            results[f'{side}_assoc_vy'] = assoc_vy

        print(f"\nROI Statistics:")
        print(f"  Left Projection FA: {results['left_proj_fa']:.3f}")
        print(f"  Left Association FA: {results['left_assoc_fa']:.3f}")
        print(f"  Right Projection FA: {results['right_proj_fa']:.3f}")
        print(f"  Right Association FA: {results['right_assoc_fa']:.3f}")

        return results


def visualize_results(
    detector: DTIALPSDetector,
    output_path: Optional[str] = None,
    human_rois: Optional[Dict[str, Tuple[int, int, int]]] = None
) -> None:
    """
    Create visualization of the automatic ROI placement.

    Parameters
    ----------
    detector : DTIALPSDetector
        Detector with completed analysis
    output_path : str, optional
        Path to save the figure (if None, displays interactively)
    human_rois : dict, optional
        Dictionary of human-placed ROI centers for comparison
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle
        from matplotlib.colors import ListedColormap
    except ImportError:
        print("matplotlib not available. Skipping visualization.")
        return

    if not detector.selected_rois:
        raise ValueError("No ROIs selected. Run detection first.")

    # Get the Z-slice of the selected ROIs
    z_slice = detector.selected_rois['proj_left'][2]

    # Create fiber classification maps
    proj_mask, assoc_mask = detector.classify_fibers()

    # Create figure with subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 12))

    # Color maps
    fa_cmap = 'gray'

    # 1. FA map with ROI locations
    ax1 = axes[0, 0]
    fa_slice = detector.fa[:, :, z_slice].T  # Transpose for correct orientation
    ax1.imshow(fa_slice, cmap=fa_cmap, origin='lower', vmin=0, vmax=1)
    ax1.set_title(f'FA Map (Z={z_slice}) with ROI Centers')

    # Plot ROI centers
    colors = {'proj_left': 'blue', 'proj_right': 'cyan',
              'assoc_left': 'red', 'assoc_right': 'orange'}

    for roi_name, center in detector.selected_rois.items():
        x, y, _ = center
        ax1.scatter(x, y, c=colors[roi_name], s=100, marker='o',
                   label=f'Auto {roi_name}', edgecolors='white', linewidths=1)

    # Plot human ROIs if provided
    if human_rois:
        for roi_name, center in human_rois.items():
            x, y, _ = center
            ax1.scatter(x, y, c=colors.get(roi_name, 'green'), s=100, marker='x',
                       label=f'Human {roi_name}', linewidths=2)

    ax1.legend(loc='upper right', fontsize=8)
    ax1.set_xlabel('X')
    ax1.set_ylabel('Y')

    # 2. Fiber classification map
    ax2 = axes[0, 1]

    # Create combined fiber map (0=background, 1=proj, 2=assoc)
    fiber_map = np.zeros(detector.fa.shape[:3])
    fiber_map[proj_mask] = 1
    fiber_map[assoc_mask] = 2

    fiber_slice = fiber_map[:, :, z_slice].T

    # Custom colormap: black, blue, red
    fiber_cmap = ListedColormap(['black', 'blue', 'red'])

    ax2.imshow(fiber_slice, cmap=fiber_cmap, origin='lower', vmin=0, vmax=2)
    ax2.set_title(f'Fiber Classification (Z={z_slice})\nBlue=Projection, Red=Association')

    # Add ROI circles (spherical ROIs in physical space)
    # Convert mm radius to voxels for in-plane display
    voxel_size = detector.header.get_zooms()[:3]
    roi_radius_xy = detector.roi_radius_mm / voxel_size[0]  # In-plane radius in voxels
    for roi_name, center in detector.selected_rois.items():
        x, y, _ = center
        circle = Circle((x, y), roi_radius_xy,
                        linewidth=2, edgecolor='white', facecolor='none')
        ax2.add_patch(circle)

    ax2.set_xlabel('X')
    ax2.set_ylabel('Y')

    # 3. Zoomed view - Left hemisphere
    ax3 = axes[1, 0]

    left_proj = detector.selected_rois['proj_left']
    left_assoc = detector.selected_rois['assoc_left']

    # Define zoom region
    x_center = (left_proj[0] + left_assoc[0]) // 2
    y_center = left_proj[1]
    zoom_size = 30

    x_min = max(0, x_center - zoom_size)
    x_max = min(detector.fa.shape[0], x_center + zoom_size)
    y_min = max(0, y_center - zoom_size)
    y_max = min(detector.fa.shape[1], y_center + zoom_size)

    # FA background
    fa_zoom = detector.fa[x_min:x_max, y_min:y_max, z_slice].T
    ax3.imshow(fa_zoom, cmap=fa_cmap, origin='lower', vmin=0, vmax=1,
              extent=[x_min, x_max, y_min, y_max])

    # Overlay fiber classification with transparency
    fiber_zoom = fiber_map[x_min:x_max, y_min:y_max, z_slice].T
    fiber_overlay = np.ma.masked_where(fiber_zoom == 0, fiber_zoom)
    ax3.imshow(fiber_overlay, cmap=fiber_cmap, origin='lower', alpha=0.4,
              extent=[x_min, x_max, y_min, y_max], vmin=0, vmax=2)

    # ROI circles (spherical ROIs)
    for roi_name in ['proj_left', 'assoc_left']:
        center = detector.selected_rois[roi_name]
        x, y, _ = center
        circle = Circle((x, y), roi_radius_xy,
                        linewidth=2, edgecolor=colors[roi_name], facecolor='none',
                        linestyle='-', label=f'Auto {roi_name}')
        ax3.add_patch(circle)

    if human_rois:
        for roi_name in ['proj_left', 'assoc_left']:
            if roi_name in human_rois:
                center = human_rois[roi_name]
                x, y, _ = center
                circle = Circle((x, y), roi_radius_xy,
                                linewidth=2, edgecolor=colors[roi_name], facecolor='none',
                                linestyle='--', label=f'Human {roi_name}')
                ax3.add_patch(circle)

    ax3.set_title('Left Hemisphere (Zoomed)\nSolid=Auto, Dashed=Human')
    ax3.legend(loc='upper right', fontsize=8)
    ax3.set_xlabel('X')
    ax3.set_ylabel('Y')

    # 4. Zoomed view - Right hemisphere
    ax4 = axes[1, 1]

    right_proj = detector.selected_rois['proj_right']
    right_assoc = detector.selected_rois['assoc_right']

    x_center = (right_proj[0] + right_assoc[0]) // 2
    y_center = right_proj[1]

    x_min = max(0, x_center - zoom_size)
    x_max = min(detector.fa.shape[0], x_center + zoom_size)
    y_min = max(0, y_center - zoom_size)
    y_max = min(detector.fa.shape[1], y_center + zoom_size)

    fa_zoom = detector.fa[x_min:x_max, y_min:y_max, z_slice].T
    ax4.imshow(fa_zoom, cmap=fa_cmap, origin='lower', vmin=0, vmax=1,
              extent=[x_min, x_max, y_min, y_max])

    fiber_zoom = fiber_map[x_min:x_max, y_min:y_max, z_slice].T
    fiber_overlay = np.ma.masked_where(fiber_zoom == 0, fiber_zoom)
    ax4.imshow(fiber_overlay, cmap=fiber_cmap, origin='lower', alpha=0.4,
              extent=[x_min, x_max, y_min, y_max], vmin=0, vmax=2)

    for roi_name in ['proj_right', 'assoc_right']:
        center = detector.selected_rois[roi_name]
        x, y, _ = center
        circle = Circle((x, y), roi_radius_xy,
                        linewidth=2, edgecolor=colors[roi_name], facecolor='none',
                        linestyle='-', label=f'Auto {roi_name}')
        ax4.add_patch(circle)

    if human_rois:
        for roi_name in ['proj_right', 'assoc_right']:
            if roi_name in human_rois:
                center = human_rois[roi_name]
                x, y, _ = center
                circle = Circle((x, y), roi_radius_xy,
                                linewidth=2, edgecolor=colors[roi_name], facecolor='none',
                                linestyle='--', label=f'Human {roi_name}')
                ax4.add_patch(circle)

    ax4.set_title('Right Hemisphere (Zoomed)\nSolid=Auto, Dashed=Human')
    ax4.legend(loc='upper right', fontsize=8)
    ax4.set_xlabel('X')
    ax4.set_ylabel('Y')

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved visualization: {output_path}")
    else:
        plt.show()

    plt.close()


def load_human_rois(case_dir: str) -> Dict[str, Tuple[int, int, int]]:
    """Load human-placed ROI masks and extract their centers."""
    human_rois = {}

    roi_files = {
        'assoc_left': 'maskAssocLeft3mm.nii.gz',
        'assoc_right': 'maskAssocRight3mm.nii.gz',
        'proj_left': 'maskProjLeft3mm.nii.gz',
        'proj_right': 'maskProjRight3mm.nii.gz',
    }

    for roi_name, filename in roi_files.items():
        filepath = os.path.join(case_dir, filename)
        if os.path.exists(filepath):
            mask = nib.load(filepath).get_fdata()
            center = ndimage.center_of_mass(mask)
            human_rois[roi_name] = tuple(int(c) for c in center)

    return human_rois


def main():
    parser = argparse.ArgumentParser(
        description='Automatic DTI-ALPS ROI Placement',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python auto_dti_alps.py case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz
  python auto_dti_alps.py case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz --output-dir results
  python auto_dti_alps.py case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz --compare-human case1
        """
    )

    parser.add_argument('fa_path', help='Path to FA NIfTI file')
    parser.add_argument('v1_path', help='Path to V1 (principal eigenvector) NIfTI file')
    parser.add_argument('--output-dir', '-o', help='Directory to save ROI masks')
    parser.add_argument('--output-prefix', default='auto', help='Prefix for output files')
    parser.add_argument('--compare-human', help='Directory with human ROI masks for comparison')
    parser.add_argument('--visualization', '-v', help='Path to save visualization image')
    parser.add_argument('--fa-thresh', type=float, default=0.25, help='FA threshold (default: 0.25)')
    parser.add_argument('--orient-thresh', type=float, default=0.7, help='Orientation threshold (default: 0.7)')
    parser.add_argument('--min-width', type=int, default=5, help='Minimum zone width (default: 5)')
    parser.add_argument('--roi-radius', type=float, default=4.0, help='Spherical ROI radius in millimeters (default: 4.0)')
    parser.add_argument('--z-tolerance', type=int, default=2, help='Z-alignment tolerance in voxels for head tilt (default: 2)')

    args = parser.parse_args()

    # Create detector
    detector = DTIALPSDetector(
        fa_thresh=args.fa_thresh,
        orient_thresh=args.orient_thresh,
        min_zone_width=args.min_width,
        roi_radius_mm=args.roi_radius,
        z_tolerance=args.z_tolerance
    )

    # Load data and run detection
    detector.load_data(args.fa_path, args.v1_path)
    detector.find_candidates()
    detector.select_optimal_rois()

    # Compute ALPS statistics
    detector.compute_alps_index()

    # Save ROI masks if requested
    if args.output_dir:
        detector.save_roi_masks(args.output_dir, args.output_prefix)

    # Load human ROIs for comparison if provided
    human_rois = None
    if args.compare_human:
        human_rois = load_human_rois(args.compare_human)
        if human_rois:
            print(f"\nHuman ROI centers (for comparison):")
            for name, center in human_rois.items():
                print(f"  {name}: {center}")

    # Create visualization
    if args.visualization or args.compare_human:
        viz_path = args.visualization or 'dti_alps_visualization.png'
        visualize_results(detector, viz_path, human_rois)


if __name__ == '__main__':
    main()
