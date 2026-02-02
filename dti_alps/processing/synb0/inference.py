"""
Neural network inference for synB0-DISCO.

This module handles loading the 5-fold ensemble model and running
inference to generate synthetic distortion-free b0 images.
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np

# Import nibabel for NIfTI file handling
try:
    import nibabel as nib
except ImportError:
    nib = None

# Import PyTorch
try:
    import torch
except ImportError:
    torch = None

from .model import UNet3D


def get_data_dir() -> Path:
    """Get the path to the bundled synb0 data directory."""
    return Path(__file__).parent.parent.parent / "data" / "synb0"


def get_model_paths() -> list[Path]:
    """Get paths to all 5 fold model weights."""
    data_dir = get_data_dir()
    return [data_dir / f"fold{i}.pth" for i in range(1, 6)]


def normalize_img(
    img: np.ndarray, max_val: float, min_val: float, target_max: float, target_min: float
) -> np.ndarray:
    """
    Normalize image intensities to a target range.

    Parameters
    ----------
    img : np.ndarray
        Input image array
    max_val : float
        Maximum value in original scale
    min_val : float
        Minimum value in original scale
    target_max : float
        Target maximum value
    target_min : float
        Target minimum value

    Returns
    -------
    np.ndarray
        Normalized image
    """
    # Scale between [0, 1]
    img = (img - min_val) / (max_val - min_val + 1e-8)
    # Scale between [target_min, target_max]
    img = img * (target_max - target_min) + target_min
    return img


def unnormalize_img(
    img: np.ndarray, max_val: float, min_val: float, target_max: float, target_min: float
) -> np.ndarray:
    """
    Unnormalize image intensities back to original scale.

    Reverses the normalize_img operation.

    Parameters
    ----------
    img : np.ndarray
        Normalized image array
    max_val : float
        Maximum value in original scale
    min_val : float
        Minimum value in original scale
    target_max : float
        Normalized maximum value
    target_min : float
        Normalized minimum value

    Returns
    -------
    np.ndarray
        Unnormalized image
    """
    img = (img - target_min) / (target_max - target_min + 1e-8) * (max_val - min_val) + min_val
    return img


def nii2torch(nii_img: np.ndarray) -> np.ndarray:
    """
    Convert NIfTI image array to PyTorch format.

    Input:  (x, y, z, channels) or (x, y, z)
    Output: (1, channels, z, x, y)

    Parameters
    ----------
    nii_img : np.ndarray
        NIfTI image array

    Returns
    -------
    np.ndarray
        Array in PyTorch format
    """
    # Add channel dimension if needed
    if nii_img.ndim == 3:
        nii_img = np.expand_dims(nii_img, axis=3)

    # Expand dims => (1, x, y, z, channels)
    torch_img = np.expand_dims(nii_img, axis=0)

    # Permute dimensions => (1, channels, z, x, y)
    torch_img = np.transpose(torch_img, axes=(0, 4, 3, 1, 2))

    return torch_img


def torch2nii(torch_img: np.ndarray) -> np.ndarray:
    """
    Convert PyTorch format array to NIfTI format.

    Input:  (1, channels, z, x, y)
    Output: (x, y, z) if channels==1 else (x, y, z, channels)

    Parameters
    ----------
    torch_img : np.ndarray
        Array in PyTorch format

    Returns
    -------
    np.ndarray
        NIfTI image array
    """
    # Remove first dim => (channels, z, x, y)
    nii_img = torch_img[0, :, :, :, :]

    # Permute dimensions => (x, y, z, channels)
    nii_img = np.transpose(nii_img, axes=(2, 3, 1, 0))

    # Squeeze if single channel
    if nii_img.shape[-1] == 1:
        nii_img = nii_img[..., 0]

    return nii_img


def select_device(preference: str = "auto") -> str:
    """
    Select compute device for inference.

    Parameters
    ----------
    preference : str
        Device preference: "auto", "cuda", or "cpu"

    Returns
    -------
    str
        Selected device string
    """
    if torch is None:
        return "cpu"

    if preference == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return preference


def run_inference(
    t1_atlas_path: str,
    b0_atlas_path: str,
    output_path: str,
    device: str = "auto",
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """
    Run synB0-DISCO inference using 5-fold ensemble.

    Parameters
    ----------
    t1_atlas_path : str
        Path to T1 image in atlas space (77x91x77 at 2.5mm)
    b0_atlas_path : str
        Path to distorted b0 image in atlas space (77x91x77 at 2.5mm)
    output_path : str
        Path for output synthetic b0 in atlas space
    device : str
        Compute device: "auto", "cuda", or "cpu"
    log : Callable[[str], None] | None
        Optional logging callback

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """
    if log is None:
        log = lambda x: None  # noqa: E731

    # Check dependencies
    if torch is None:
        return False, "PyTorch not installed. Install with: pip install torch"
    if nib is None:
        return False, "nibabel not installed. Install with: pip install nibabel"

    # Select device
    device_str = select_device(device)
    log(f"  Using device: {device_str}")

    try:
        device_obj = torch.device(device_str)
    except RuntimeError as e:
        return False, f"Failed to initialize device {device_str}: {e}"

    # Load images
    log(f"  Loading T1 atlas: {t1_atlas_path}")
    try:
        t1_nii = nib.load(t1_atlas_path)
        t1_img = t1_nii.get_fdata()
    except Exception as e:
        return False, f"Failed to load T1 atlas: {e}"

    log(f"  Loading b0 atlas: {b0_atlas_path}")
    try:
        b0_nii = nib.load(b0_atlas_path)
        b0_img = b0_nii.get_fdata()
    except Exception as e:
        return False, f"Failed to load b0 atlas: {e}"

    # Validate dimensions (should be 77x91x77 for 2.5mm atlas)
    expected_shape = (77, 91, 77)
    if t1_img.shape != expected_shape:
        log(f"  Warning: T1 atlas shape {t1_img.shape} differs from expected {expected_shape}")
    if b0_img.shape != expected_shape:
        log(f"  Warning: b0 atlas shape {b0_img.shape} differs from expected {expected_shape}")

    # Add channel dimension
    t1_img = np.expand_dims(t1_img, axis=3)
    b0_img = np.expand_dims(b0_img, axis=3)

    # Pad arrays to (80, 96, 80) for network (must be divisible by 8)
    t1_img = np.pad(t1_img, ((2, 1), (3, 2), (2, 1), (0, 0)), "constant")
    b0_img = np.pad(b0_img, ((2, 1), (3, 2), (2, 1), (0, 0)), "constant")

    # Convert to torch format
    t1_torch = nii2torch(t1_img)
    b0_torch = nii2torch(b0_img)

    # Normalize data
    # T1 normalization: fixed range based on training data
    t1_torch = normalize_img(t1_torch, 150, 0, 1, -1)

    # b0 normalization: use 99th percentile of image
    max_b0 = float(np.percentile(b0_torch, 99))
    min_b0 = 0.0
    b0_torch = normalize_img(b0_torch, max_b0, min_b0, 1, -1)

    # Concatenate inputs (b0 first, then T1)
    img_data = np.concatenate((b0_torch, t1_torch), axis=1)

    # Convert to torch tensor
    img_tensor = torch.from_numpy(img_data).float().to(device_obj)

    # Load models and run inference
    model_paths = get_model_paths()
    predictions = []

    for i, model_path in enumerate(model_paths, 1):
        if not model_path.exists():
            return False, f"Model weights not found: {model_path}"

        log(f"  Running fold {i}/5...")

        # Load model
        model = UNet3D(n_in=2, n_out=1).to(device_obj)
        try:
            state_dict = torch.load(model_path, map_location=device_obj, weights_only=True)
            model.load_state_dict(state_dict)
        except Exception as e:
            return False, f"Failed to load model fold {i}: {e}"

        model.eval()

        # Run inference
        with torch.no_grad():
            output = model(img_tensor)
            predictions.append(output.cpu().numpy())

        # Clean up
        del model
        if device_str == "cuda":
            torch.cuda.empty_cache()

    # Average predictions
    log("  Averaging fold predictions...")
    avg_prediction = np.mean(predictions, axis=0)

    # Unnormalize output (use b0 normalization parameters)
    avg_prediction = unnormalize_img(avg_prediction, max_b0, min_b0, 1, -1)

    # Remove padding: (80, 96, 80) -> (77, 91, 77)
    avg_prediction = avg_prediction[:, :, 2:-1, 2:-1, 3:-2]

    # Convert to NIfTI format
    output_img = torch2nii(avg_prediction)

    # Save output using b0 atlas as template (for affine and header)
    log(f"  Saving synthetic b0: {output_path}")
    try:
        output_nii = nib.Nifti1Image(output_img.astype(np.float32), b0_nii.affine, b0_nii.header)
        nib.save(output_nii, output_path)
    except Exception as e:
        return False, f"Failed to save output: {e}"

    return True, "Inference completed successfully"
