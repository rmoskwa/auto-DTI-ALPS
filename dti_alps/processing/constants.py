"""
Domain constants for the DTI-ALPS analysis engine.

A dependency-free leaf: this module imports nothing, so it can never take part
in an import cycle. The values here are domain facts the processing pipeline
consumes -- the MRtrix3 tensor-component indices, the FA threshold that filters
CSF voxels, the preprocessing defaults, and the readout-time validation range.

They were lifted out of ``gui/config.py`` so the engine no longer
reaches up into the GUI package; ``gui/config.py`` re-exports these names, so
GUI code keeps referring to them as ``config.X`` unchanged. This module is the
single source of truth -- change a value here, not in the GUI.
"""

# Default preprocessing parameters
DEFAULT_READOUT_TIME = 0.05  # seconds
DEFAULT_PE_DIRECTION = "AP"
DEFAULT_RPE_SCHEME = "none"

# FA threshold for filtering CSF voxels from ROIs
FA_THRESHOLD = 0.2  # Minimum FA value for ROI voxels (filters out CSF)

# Readout time validation range (seconds)
READOUT_TIME_RANGE = (0.001, 1.0)

# MRtrix3 tensor volume indices
# dwi2tensor outputs: D11, D22, D33, D12, D13, D23
TENSOR_DXX_INDEX = 0  # D11
TENSOR_DYY_INDEX = 1  # D22
TENSOR_DZZ_INDEX = 2  # D33
