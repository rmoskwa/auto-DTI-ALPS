# Automated ALPS App

Automated DTI-ALPS (Diffusion Tensor Imaging Along the Perivascular Space) analysis tool. Uses template-based registration to place ROIs in projection and association fiber regions, then calculates the DTI-ALPS index from diffusion tensor imaging data.

**In:** a folder of DWI subjects: NIfTI + `.bvec` / `.bval`, optionally a reverse phase-encode image for distortion correction.

**Out:** an `alps_results.csv` with one row per subject (left, right, and combined ALPS index), the ROI masks each number came from, and a timestamped run log.

The app can run in two ways:
1. **GUI** for setting up and inspecting a study,
2. **CLI** for headless batch processing on a cluster.

## Quick start

```bash
pipx install dti-alps                                      # or grab the AppImage
dti-alps                                                   # launch the GUI
dti-alps run --subjects /data/cohort --output /data/out     # or process a cohort headless
```

MRtrix3 and FSL are **not** bundled — see [Requirements](#requirements).

## Installation

Four routes. Pick by what you already have:

- **pipx** — you have Python 3.10+ and want `dti-alps` on your `PATH`
- **AppImage** — you want a double-click file and no Python setup
- **conda** — you already manage environments with conda
- **From source**

Whichever you choose, MRtrix3 and FSL must be installed separately and on your
`PATH` (see [External neuroimaging software](#external-neuroimaging-software)).

<details>
<summary><b>Install with pipx</b> — isolated environment, <code>dti-alps</code> on your PATH</summary>

If you have Python 3.10+, [`pipx`](https://pipx.pypa.io/) installs the app into
an isolated environment and puts the `dti-alps` command on your `PATH`:

```bash
pipx install dti-alps
dti-alps            # launch the GUI
```

Update with `pipx upgrade dti-alps`.

Plain `pip install dti-alps` inside a **virtualenv** works too and provides the
same `dti-alps` command.

</details>

<details>
<summary><b>Download the AppImage</b> — no Python needed</summary>

For a double-click file with no Python setup, grab the latest Linux **AppImage**
from the [Releases page](https://github.com/rmoskwa/auto-DTI-ALPS/releases):

```bash
chmod +x dti-alps-*-x86_64.AppImage
./dti-alps-*-x86_64.AppImage
```

The AppImage bundles the app and its Python dependencies. To update, download
the newer AppImage and replace the old file.

All CLI arguments are forwarded, so substitute `./dti-alps-*.AppImage` anywhere
this README says `dti-alps` (e.g. `./dti-alps-*.AppImage view /path`).

> **Qt runtime note:** the GUI uses Qt 6, which needs `libxcb-cursor0` on the
> host. See [Troubleshooting](#troubleshooting) if it fails to start.

</details>

<details>
<summary><b>Install into a conda environment</b></summary>

Take Qt (and the other compiled dependencies) from conda-forge, then let pip add
the app itself:

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

If the GUI fails to start with an `undefined symbol` error, see
[Troubleshooting](#troubleshooting).

</details>

<details>
<summary><b>From source</b> — development</summary>

```bash
pip install -e ".[gui]"
```

</details>

## Requirements

Python 3.10+, plus MRtrix3 and FSL on your `PATH`.

<details>
<summary><b>Python dependencies</b> — installed automatically</summary>

- [NumPy](https://numpy.org/)
- [NiBabel](https://nipy.org/nibabel/)
- [SciPy](https://scipy.org/)
- [PySide6](https://doc.qt.io/qtforpython/) — the Qt toolkit for the GUI and results viewer

The engine (`dti_alps.processing.*`) is Qt-free and imports PySide6 lazily, so
headless CLI use (`run`, `reanalyze`, `report`) and library use never load Qt.

</details>

### External neuroimaging software

These third-party programs must be installed and available on your system PATH.
`dti-alps run` checks for them before starting and exits with code `3` if any
are missing.

| Package | Install | Provides |
|---------|---------|----------|
| [MRtrix3](https://www.mrtrix.org/) (required) | https://www.mrtrix.org/download/ | Preprocessing, tensor fitting, brain masking |
| [FSL](https://fsl.fmrib.ox.ac.uk/fsl/) (required) | https://fsl.fmrib.ox.ac.uk/fsl/fslwiki/FslInstallation | Registration, warping, eddy/topup |

<details>
<summary><b>MRtrix3 commands used, by pipeline stage</b></summary>

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

</details>

<details>
<summary><b>FSL commands used, by pipeline stage</b></summary>

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

</details>

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

### Protocol files

Rather than retyping long argument lists, save the analysis settings once and pass the file. Export a **config file** from the GUI's **Output Setup** page, then hand it to any run:

```bash
dti-alps run --subjects /data/cohort --output /data/out --config study-protocol.json
```

A protocol file holds *what the analysis is*; it is portable and shareable across machines. Where the results land is never part of it, which is why `--output` is always required.

`--opt STAGE:NAME=VALUE` sets any tool option inline, so you are never blocked on authoring a file:

```bash
dti-alps run --subjects /data/cohort --output /data/out \
    --opt dwifslpreproc:-eddy_options='--repol --slm=linear'
```

<details>
<summary><b>Subject discovery and IDs</b> — <code>--subjects</code>, <code>--id-depth</code></summary>

Discovery scans a folder and, failing that, its immediate
subdirectories. Deeper trees are reached with a shell glob. If filenames
are similar (e.g. a scan protocol named all subfolders + subfiles for each
subject identically), use `--id-depth` or the "ID Depth" spinner in the GUI
to disambiguate. For example: `--id-depth 3` files `sub-01/ses-1/dwi` as `sub-01_ses-1_dwi`.
A run refuses to start if two subjects would resolve to the same identifier.

</details>

<details>
<summary><b>Exit codes</b> — 0 success, 1 partial failure, 2 usage, 3 preflight, 130 interrupted</summary>

For branching in a job script:

| Code | Meaning |
|------|---------|
| `0`  | Every subject completed |
| `1`  | Finished with at least one failure |
| `2`  | Usage or configuration error |
| `3`  | Preflight failure — a required MRtrix3/FSL command is missing |
| `130`| Interrupted with Ctrl-C (the results CSV is still written) |

</details>

<details>
<summary><b>Console output and logging</b> — <code>--quiet</code></summary>

Raw MRtrix3 and FSL output is shown by default, so an eddy failure can be
diagnosed without re-running; `--quiet` reduces it to stage- and subject-level
lines. Either way the run leaves a timestamped `dti_alps_*.log` in the output
directory, the same record the GUI console produces.

</details>

## Output

A run fills `--output` with one directory per subject plus the cohort-level results:

```
/data/out/
├── alps_results.csv              # one row per subject: left, right, combined ALPS
├── alps_results_sphere2.csv      # one more CSV per extra ROI shape
├── dti_alps_20260803_141200.log  # the full run log
└── sub-01/
    ├── alps_result.json          # per-subject completion marker; --resume reads this
    ├── registration/             # brain mask, skull-stripped FA, transforms
    ├── rois/                     # the four masks: sub-01_left_proj.nii.gz, ...
    └── ...                       # intermediate NIfTIs (denoised, tensor, FA)
```

- `dti-alps view /data/out` opens this folder in the results viewer
- `dti-alps report /data/out` adds a `quality_report_{shape}.csv` alongside the results.

## Troubleshooting

<details>
<summary><b>GUI fails to start with an <code>xcb</code> platform-plugin error</b></summary>

The GUI uses Qt 6, which needs `libxcb-cursor0` on the host:

```bash
sudo apt install libxcb-cursor0     # Debian/Ubuntu
```

</details>

<details>
<summary><b>GUI fails to start with an <code>undefined symbol</code> error naming <code>libharfbuzz</code> or <code>libfreetype</code></b></summary>

In a conda environment, this means the environment's font libraries disagree with each other:

```bash
conda install -c conda-forge freetype harfbuzz --update-deps
```

The headless verbs (`run`, `reanalyze`, `report`) never load Qt, so they keep working regardless.

</details>

<details>
<summary><b>A run exits immediately with code 3</b></summary>

Preflight found a required MRtrix3 or FSL command missing from your `PATH`. The message names the command; see
[External neuroimaging software](#external-neuroimaging-software) for which package provides it.

</details>
