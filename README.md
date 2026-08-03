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
headless CLI use (`reanalyze`, `report`) and library use never load Qt.

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

### Install into a conda environment

Take Qt (and the other compiled dependencies) from
conda-forge, then let pip add the app itself:

```bash
conda install -c conda-forge pyside6 numpy scipy nibabel
pip install dti-alps
```

pip recognises the conda-provided packages as satisfying its requirements and
installs only `dti-alps` on top, so Qt and its C libraries stay under conda's
control as one consistent set. Keeping the environment on a single channel
avoids mismatches between those libraries:

```bash
conda config --add channels conda-forge
conda config --set channel_priority strict
```

If the GUI fails to start with an `undefined symbol` error mentioning
`libharfbuzz` or `libfreetype`, the environment's font libraries disagree with
each other; `conda install -c conda-forge freetype harfbuzz --update-deps`
realigns them. The headless verbs (`run`, `reanalyze`, `report`) never load Qt,
so they keep working regardless.

### From source (development)

```bash
pip install -e ".[gui]"
```

## Usage

```bash
dti-alps                    # Launch GUI (default)
dti-alps run --subjects /data/cohort --output /data/out   # Process a cohort
dti-alps view               # Launch Results Viewer
dti-alps view /path         # Launch viewer with specific output folder
dti-alps report /path       # Generate quality reports
dti-alps reanalyze /path --sphere 3  # Reanalyze with different ROI shapes
```

`dti-alps VERB --help` lists the flags for any one verb.

When running the AppImage, substitute `./dti-alps-*.AppImage` for `dti-alps`;
all CLI arguments are forwarded (e.g. `./dti-alps-*.AppImage view /path`).

### Headless processing

`dti-alps run` executes the whole pipeline with no display attached.

```bash
# The simplest complete run: discovery, defaults, one command
dti-alps run --subjects /data/cohort --output /data/out

# A BIDS cohort, a protocol exported from the GUI, tuned ROIs
dti-alps run --subjects /bids/sub-*/ses-1/dwi --output /data/out \
    --config study-protocol.json --id-depth 3 --sphere 2,3 --nthreads 8

# Check what would happen before committing to a long run
dti-alps run --subjects /bids/sub-*/ses-1/dwi --output /data/out --dry-run

# Pick up where a preempted node left off
dti-alps run --subjects /data/cohort --output /data/out --resume
```
You can save protocol settings as **config files** which can be exported through the GUI within the **Output Setup** page.
Pass the config.json file into the CLI command to avoid typing out needlessly long arguments.

`--opt STAGE:NAME=VALUE` sets any tool option inline, so you are never blocked on
authoring a file:

```bash
dti-alps run --subjects /data/cohort --output /data/out \
    --opt dwifslpreproc:-eddy_options='--repol --slm=linear'
```

**BIDS layouts.** Discovery scans a folder and, failing that, its immediate
subdirectories. Deeper trees are reached with a shell glob. Because every BIDS
leaf folder is named `dwi`, use `--id-depth` so subjects stay distinguishable;
`--id-depth 3` files `sub-01/ses-1/dwi` as `sub-01_ses-1_dwi`. A run refuses
to start if two subjects would resolve to the same identifier.

**Exit codes**, for branching in a job script:

| Code | Meaning |
|------|---------|
| `0`  | Every subject completed |
| `1`  | Finished with at least one failure |
| `2`  | Usage or configuration error |
| `3`  | Preflight failure — a required MRtrix3/FSL command is missing |
| `130`| Interrupted with Ctrl-C (the results CSV is still written) |

Raw MRtrix3 and FSL output is shown by default, so an eddy failure can be
diagnosed without re-running; `--quiet` reduces it to stage- and subject-level
lines. Either way the run leaves a timestamped `dti_alps_*.log` in the output
directory, the same record the GUI console produces.
