# Placeholder
$FSL directory used. For distribution, add an mrtrix3 and fsl check

- Note: ANTS registration planned for future. Multithreading/gpu support?
Metrics in --report:
  1. Directional Alignment (V1): Mean absolute component along expected fiber direction
    - Projection ROIs: Mean(abs(V1_z)) - ideal value ~1.0
    - Association ROIs: Mean(abs(V1_y)) - ideal value ~1.0
  2. Angular Dispersion (V1): Standard deviation of fiber angles in degrees
    - Low values = coherent parallel fibers (good)
    - High values = fanning/diverging fibers (bad)
  3. Fractional Anisotropy: Mean FA within ROI
    - Target: > 0.4, preferably > 0.5
