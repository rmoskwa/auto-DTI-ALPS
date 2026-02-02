"""
synB0-DISCO integration for DTI-ALPS pipeline.

This module provides an alternative preprocessing route using synB0-DISCO
to synthesize distortion-free b0 images from T1 data for susceptibility
distortion correction via topup+eddy.

The synB0-DISCO method trains a neural network to predict what the b0
image would look like without EPI distortions, using the T1 structural
image as input. This synthetic distortion-free b0 is then used with
FSL's topup tool to estimate and correct susceptibility-induced distortions.
"""

from .backend import Synb0Backend, check_synb0_available
from .inference import run_inference

__all__ = [
    "Synb0Backend",
    "check_synb0_available",
    "run_inference",
]
