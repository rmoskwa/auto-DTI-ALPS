>[!NOTE]
>This is currently a personal tool. While completely usable in its current state, the application will be updated and guidance docs will be created to allow outside users ease-of-use.

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
| `eddy` | Preprocessing | Eddy current and motion correction |
| `topup` | Preprocessing | Susceptibility-induced distortion field estimation |
| `applytopup` | Preprocessing | Apply topup distortion correction |

Installation: https://fsl.fmrib.ox.ac.uk/fsl/fslwiki/FslInstallation

## Installation

### Download (recommended)

Grab the latest Linux **AppImage** from the [Releases page](https://github.com/rmoskwa/auto-DTI-ALPS/releases), make it executable, and run it — no Python install required:

```bash
chmod +x dti-alps-*-x86_64.AppImage
./dti-alps-0.1.0-x86_64.AppImage
```

The AppImage bundles the app and its Python dependencies. It does **not** bundle
MRtrix3 or FSL — those must still be installed and on your `PATH` (see
[External Neuroimaging Software](#external-neuroimaging-software)).

To update, download the newer AppImage and replace the old file.

> **Qt runtime note:** the GUI uses Qt 6, which needs `libxcb-cursor0` on the
> host. If the app fails to start with an `xcb` platform-plugin error, install
> it: `sudo apt install libxcb-cursor0` (Debian/Ubuntu).

### From source

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

When running the AppImage, substitute `./dti-alps-*.AppImage` for `dti-alps`;
all CLI flags are forwarded (e.g. `./dti-alps-*.AppImage --viewer /path`).

## Releasing (maintainers)

Releases are built automatically by the [`Release` workflow](.github/workflows/release.yml)
when a version tag is pushed:

```bash
# 1. Bump `version` in pyproject.toml, commit, and merge to main.
# 2. Tag and push:
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions then freezes the app with PyInstaller (`dti-alps.spec`), wraps it
into an AppImage (`packaging/build-appimage.sh`), and publishes a GitHub Release
with the `.AppImage` attached. To build one locally instead:

```bash
pip install -e ".[gui,build]"
packaging/build-appimage.sh          # writes dist/dti-alps-<version>-x86_64.AppImage
```
