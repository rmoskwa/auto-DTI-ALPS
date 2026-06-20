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

__all__ = [
    "Synb0Backend",
    "check_synb0_available",
    "run_inference",
]


def __getattr__(name: str):
    # ``run_inference`` pulls in torch (an optional dependency). Re-export it
    # lazily via PEP 562 so ``import dti_alps.processing.synb0`` -- and the
    # seam tests that import the backend submodule -- work with no torch
    # installed, while ``from ...synb0 import run_inference`` still resolves.
    if name == "run_inference":
        from .inference import run_inference

        return run_inference
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
