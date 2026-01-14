"""
Visualization functions for DTI-ALPS results.
"""

import os
from typing import Optional, Dict, Tuple

import numpy as np
import nibabel as nib
from scipy import ndimage

from .detector import DTIALPSDetector


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
