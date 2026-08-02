# autoDTI-ALPS

Automated DTI-ALPS (Diffusion Tensor Imaging Along the Perivascular Space) analysis tool. Uses template-based registration to place ROIs in projection and association fiber regions, then calculates the DTI-ALPS index from diffusion tensor imaging data.

## Dependencies

### Python

Requires Python 3.10+.

Core dependencies (installed automatically):
- [NumPy](https://numpy.org/)
- [NiBabel](https://nipy.org/nibabel/)
- [SciPy](https://scipy.org/)
- [PySide6](https://doc.qt.io/qtforpython/) — the Qt toolkit for the GUI and results viewer

The engine (`dti_alps.processing.*`) is Qt-free and imports PySide6 lazily, so
headless CLI use (`--reanalyze`, `--report`) and library use never load Qt.

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

Whichever route you choose, MRtrix3 and FSL are **not** bundled and must be
installed separately and on your `PATH` (see
[External Neuroimaging Software](#external-neuroimaging-software)).

### Install with pipx (recommended)

If you have Python 3.10+, [`pipx`](https://pipx.pypa.io/) installs the app into
an isolated environment and puts the `dti-alps` command on your `PATH`:

```bash
pipx install dti-alps
dti-alps            # launch the GUI
```

Update with `pipx upgrade dti-alps`.

Plain `pip install dti-alps` inside a **virtualenv** works too and provides the
same `dti-alps` command.

> **Avoid installing into a conda/mamba environment.** The GUI's Qt libraries
> are loaded from the surrounding environment, and conda prefixes frequently
> carry a mismatched set (see
> [GUI fails to start with an `undefined symbol` error](#gui-fails-to-start-with-an-undefined-symbol-error)).
> Use pipx or the AppImage instead; both keep the GUI out of conda's library
> path. Run `conda deactivate` first if an environment is active.

### Download the AppImage (no Python needed)

For double-click file with no Python setup, grab the latest Linux **AppImage** from the [Releases page](https://github.com/rmoskwa/auto-DTI-ALPS/releases):

```bash
chmod +x dti-alps-*-x86_64.AppImage
./dti-alps-0.1.0-x86_64.AppImage
```

The AppImage bundles the app and its Python dependencies. To update, download
the newer AppImage and replace the old file.

> **Qt runtime note:** the GUI uses Qt 6, which needs `libxcb-cursor0` on the
> host. If the app fails to start with an `xcb` platform-plugin error, install
> it: `sudo apt install libxcb-cursor0` (Debian/Ubuntu).

### From source (development)

```bash
pip install -e ".[gui]"
```

### Troubleshooting the install

#### GUI fails to start with an `undefined symbol` error

```
ImportError: .../lib/libharfbuzz.so.0: undefined symbol: FT_Get_Colorline_Stops
```

PySide6 is installed, but the Qt libraries it loads are being resolved against a
mismatched set of system libraries — most often a conda/mamba prefix whose
`harfbuzz` was built against a newer `freetype` than the one in the same
environment. The symbol name varies; anything of the form *library X, undefined
symbol from library Y* is the same problem. It is not specific to this app: the
same environment breaks any Qt program.

Check whether the install landed in a conda environment:

```bash
which dti-alps      # .../miniconda3/envs/<name>/bin/dti-alps  →  it did
```

Fixes, in order of preference:

```bash
# 1. Reinstall outside conda, isolated (recommended)
conda deactivate
pipx install dti-alps

# 2. Or use the AppImage, which bundles its own Qt
#    https://github.com/rmoskwa/auto-DTI-ALPS/releases

# 3. Or, to stay in the conda environment, realign its font stack
conda install -c conda-forge freetype harfbuzz --update-deps
```

Confirm Qt loads before relaunching:

```bash
python -c "from PySide6.QtWidgets import QApplication; print('ok')"
```

The headless entry points (`--reanalyze`, `--report`) never load Qt, so they keep
working in an environment where the GUI cannot start.

#### GUI fails to start with an `xcb` platform-plugin error

Qt 6 needs `libxcb-cursor0` on the host — see the note under
[Download the AppImage](#download-the-appimage-no-python-needed).

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
