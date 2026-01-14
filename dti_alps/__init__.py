"""
DTI-ALPS - Automatic ROI Detection for DTI-ALPS Analysis

This package provides tools for automatic detection of optimal ROI locations
for DTI-ALPS (Diffusion Tensor Imaging Along the Perivascular Space) analysis.
"""

__version__ = "0.1.0"

from .detector import DTIALPSDetector, FiberZone, ROICandidate
