>[!NOTE]
>This is currently a personal tool. While completely usable in its current state, the application will be updated and guidance docs will be clarified soon to allow outside users ease-of-use.

# autoDTI-ALPS

Automated DTI-ALPS (Diffusion Tensor Imaging Along the Perivascular Space) analysis tool. Uses template-based registration to place ROIs in projection and association fiber regions, then calculates the DTI-ALPS index from diffusion tensor imaging data.

## Dependencies

### Python

Requires Python 3.10+.

Core dependencies (installed automatically):
- [NumPy](https://numpy.org/)
- [NiBabel](https://nipy.org/nibabel/)
- [SciPy](https://scipy.org/)

Optional GUI dependencies (`pip install -e ".[gui]"`):
- [Matplotlib](https://matplotlib.org/)
- [Pillow](https://python-pillow.org/)

### External Neuroimaging Software

The following third-party programs must be installed and available on your system PATH.

#### MRtrix3 (required)

[MRtrix3](https://www.mrtrix.org/) provides tools for diffusion MRI preprocessing and tensor fitting.

| Command | Pipeline Stage | Purpose |
|---------|---------------|---------|
| `dwidenoise` | Denoising | Marchenko-Pastur PCA thermal noise removal |
| `mrdegibbs` | Gibbs Removal | Gibbs ringing artifact correction |
| `dwifslpreproc` | Preprocessing | Eddy current, motion, and distortion correction |
| `dwi2tensor` | Tensor Fitting | Fit diffusion tensor model to DWI data |
| `tensor2metric` | Metric Extraction | Extract FA, eigenvectors (V1-V3), and eigenvalues (L1-L3) |
| `dwi2mask` | Preprocessing | Brain mask generation from DWI |
| `dwiextract` | B0 Extraction | Extract b=0 volumes from DWI |
| `mrmath` | B0 Extraction | Average multiple b=0 volumes |
| `mrconvert` | Format Conversion | Image format conversion and header manipulation |

Installation: https://www.mrtrix.org/download/

#### FSL (required)

[FSL](https://fsl.fmrib.ox.ac.uk/fsl/) provides tools for brain extraction, registration, and image manipulation.

| Command | Pipeline Stage | Purpose |
|---------|---------------|---------|
| `flirt` | Registration | Linear (affine) FA-to-template registration |
| `fnirt` | Registration | Non-linear FA-to-template registration |
| `invwarp` | Registration | Generate inverse warp field for ROI transformation |
| `applywarp` | ROI Placement | Transform ROI templates from standard to native space |
| `fslmaths` | Masking | Apply brain mask to FA image |
| `bet` / `bet2` | Brain Extraction | Skull stripping (used in synB0-DISCO pipeline) |
| `eddy` | Preprocessing | Eddy current and motion correction |
| `topup` | Preprocessing | Susceptibility-induced distortion field estimation |
| `applytopup` | Preprocessing | Apply topup distortion correction |
| `epi_reg` | Registration | EPI-to-T1 registration (synB0-DISCO pipeline) |
| `fslmerge` | Image Manipulation | Merge image volumes (synB0-DISCO pipeline) |

Installation: https://fsl.fmrib.ox.ac.uk/fsl/fslwiki/FslInstallation

#### FreeSurfer (optional — synB0-DISCO only)

[FreeSurfer](https://surfer.nmr.mgh.harvard.edu/) is only required when using the synB0-DISCO fieldmap-less distortion correction pipeline.

| Command | Purpose |
|---------|---------|
| `mri_convert` | NIfTI/MGZ format conversion |
| `mri_nu_correct.mni` | N3 bias field correction |
| `mri_normalize` | Intensity normalization |

Installation: https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall

#### ANTs (optional — synB0-DISCO only)

[ANTs](http://stnava.github.io/ANTs/) is only required when using the synB0-DISCO fieldmap-less distortion correction pipeline.

| Command | Purpose |
|---------|---------|
| `antsRegistrationSyNQuick.sh` | T1-to-MNI template registration |
| `antsApplyTransforms` | Apply forward and inverse spatial transformations |

Installation: https://github.com/ANTsX/ANTs

#### Convert3D (optional — synB0-DISCO only)

[Convert3D](http://www.itksnap.org/pmwiki/pmwiki.php?n=Convert3D.Documentation) is only required when using the synB0-DISCO fieldmap-less distortion correction pipeline.

| Command | Purpose |
|---------|---------|
| `c3d_affine_tool` | Convert FSL affine matrices to ITK/ANTs format |

Installation: http://www.itksnap.org/pmwiki/pmwiki.php?n=Downloads.C3D

## Installation

```bash
pip install -e ".[gui]"
```

## Usage

```bash
dti-alps                    # Launch GUI (default)
dti-alps --viewer           # Launch Results Viewer
dti-alps --viewer /path     # Launch viewer with specific output folder
dti-alps --report /path     # Generate quality reports
dti-alps --reanalyze /path --sphere 3  # Reanalyze with different ROI shapes
```
