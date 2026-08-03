"""
The protocol file: a portable, shareable serialization of *what the analysis is*.

``BatchConfig`` currently mixes two unlike things. **Protocol** is what the
analysis is -- which stages run, the acquisition parameters, the eight per-stage
tool-option dicts, the ROI geometry, the ALPS method, which outputs to keep. It
is portable, shareable, citable. **Run placement** is where this particular
invocation lands -- ``output_dir``, staging -- and is machine-specific.

This module serializes the first set only. That is what lets a
``study-protocol.json`` be attached to a methods section or handed to a
collaborator without carrying somebody's home directory into their cluster run,
and it is why ``dti-alps run`` *requires* ``--output``: with placement keys
absent from the file there is no "does an absent ``--output`` mean use the
file's path, or error?" ambiguity to resolve.

Design notes:

* **The serialized type is ``BatchConfig``, not ``FormState``.** ``FormState`` is
  shaped by how the GUI *edits*: raw strings, a ``readout_auto`` editing flag,
  ``OptionState(enabled, value, type)`` triples where ``type`` is a widget type.
  A CLI consuming that would have to fabricate widget state purely to run it back
  through the form model and recover ``{"-nthreads": 8}``. ``BatchConfig`` is
  already the engine's own vocabulary.
* **Direction is one-way through the existing seam.** GUI export is
  ``build_batch_state(form_state, []).config`` -> :func:`write_protocol`; the CLI
  is :func:`read_protocol` -> ``BatchState(config, subjects)``. There is no
  import-a-protocol-into-the-GUI: ``BatchConfig`` cannot faithfully restore a
  ``FormState``, and a half-working import is worse than none.
* **JSON, not YAML.** No new runtime dependency, already the house format
  (``~/.dti-alps/user_config.json``, BIDS sidecars), and no PyInstaller hook
  needed in the AppImage bundle. The file is generated in the common case, so
  hand-commentability did not justify a dependency.

Lives in ``processing/`` because both front ends need it and the engine is the
shared floor. Imports stdlib and :mod:`~dti_alps.processing.state` only, so the
Qt-free guarantee holds.
"""

import hashlib
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from .constants import AdaptiveSearchConfig
from .state import BatchConfig, OutputConfig

# The schema version written into every protocol file. Bumped only for a change
# a reader must behave differently about; readers accept any version they know.
PROTOCOL_VERSION = 1

# The key carrying the version. Not a protocol field -- it describes the file.
VERSION_KEY = "protocol_version"

# --- The domain split -------------------------------------------------------
# Every BatchConfig field is classified as exactly one of these. The partition is
# pinned by an exhaustiveness test, so a newly added BatchConfig field fails the
# suite until somebody classifies it -- the field cannot quietly default to
# "not serialized" and go missing from every exported protocol.

#: What the analysis *is*. Serialized; portable across machines.
PROTOCOL_FIELDS = frozenset(
    {
        "run_denoising",
        "dwidenoise_options",
        "run_degibbs",
        "mrdegibbs_options",
        "pe_direction",
        "auto_pe_direction",
        "readout_time",
        "rpe_scheme",
        "dwifslpreproc_options",
        "dwi2tensor_options",
        "tensor2metric_options",
        # synB0 is deliberately protocol, not placement: it names an input
        # dataset the analysis depends on, in the same way the subject folders
        # do, and a protocol that silently dropped it would produce a different
        # pipeline (10 stages vs 9). `run` overrides it per-run with --synb0-dir.
        "use_synb0",
        "synb0_output_dir",
        "synb0_eddy_options",
        "flirt_options",
        "fnirt_options",
        "roi_shapes",
        "fa_threshold",
        "alps_method",
        "adaptive_roi_placement",
        "adaptive_search",
        "output_config",
    }
)

#: Where *this invocation* lands. Omitted on write, ignored on read.
RUN_FIELDS = frozenset({"output_dir", "staging_enabled", "staging_dir"})

# The nested dataclasses a reader must rebuild explicitly: `asdict` flattens them
# to plain dicts on the way out, and `BatchConfig(**data)` would otherwise leave
# them as dicts, which every downstream attribute access would fail on.
_NESTED_TYPES = {
    "adaptive_search": AdaptiveSearchConfig,
    "output_config": OutputConfig,
}


class ProtocolError(Exception):
    """A protocol file could not be read: malformed, or carrying unknown keys."""


def protocol_dict(config: BatchConfig) -> dict[str, Any]:
    """
    The protocol half of ``config`` as plain JSON-able data.

    ``dataclasses.asdict`` restricted to :data:`PROTOCOL_FIELDS`; it recurses
    into ``OutputConfig`` and ``AdaptiveSearchConfig`` for free. Keys are emitted
    in sorted order so two equal protocols serialize identically -- which is what
    makes :func:`protocol_hash` stable.
    """
    data: dict[str, Any] = {}
    for field in fields(config):
        if field.name not in PROTOCOL_FIELDS:
            continue
        data[field.name] = _plain(getattr(config, field.name))
    return dict(sorted(data.items()))


def _plain(value: Any) -> Any:
    """Recursively reduce dataclasses to dicts, leaving other values alone."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def protocol_hash(config: BatchConfig) -> str:
    """
    A stable digest over ``config``'s protocol half.

    Two configs that describe the same analysis hash the same regardless of
    output directory, staging, or field ordering. ``--resume`` compares this
    against the hash recorded in each subject's completion marker: a changed
    protocol means nothing is skipped, so a cohort can never end up half
    processed one way and half the other.
    """
    payload = json.dumps(protocol_dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_protocol(path: str | Path, config: BatchConfig) -> None:
    """
    Write ``config``'s protocol half to ``path`` as JSON.

    Run-placement keys are omitted entirely -- not blanked -- so the file carries
    no trace of the machine that produced it.
    """
    document = {VERSION_KEY: PROTOCOL_VERSION, **protocol_dict(config)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=False)
        f.write("\n")


def read_protocol(path: str | Path, base: BatchConfig | None = None) -> BatchConfig:
    """
    Read a protocol file into a ``BatchConfig``.

    ``base`` supplies the run-placement values (and any protocol field the file
    omits); it defaults to a fresh ``BatchConfig``, i.e. engine defaults with an
    empty ``output_dir``. Omitting a protocol key is legal and means "use the
    default" -- that is what makes a hand-trimmed file carrying three settings
    work.

    Unknown keys are rejected rather than ignored, and the message names the
    key: a typo'd option that silently did nothing would be indistinguishable
    from one the tool does not support.

    Raises
    ------
    ProtocolError
        The file is not readable JSON, is not an object, or carries a key that
        is neither a protocol field nor the version marker. Run-placement keys
        get their own message, because finding one means the file was
        hand-edited or produced by something other than this writer.
    """
    try:
        with open(path, encoding="utf-8") as f:
            document = json.load(f)
    except OSError as err:
        raise ProtocolError(f"Could not read protocol file {path}: {err}") from err
    except json.JSONDecodeError as err:
        raise ProtocolError(f"Protocol file {path} is not valid JSON: {err}") from err

    if not isinstance(document, dict):
        raise ProtocolError(f"Protocol file {path} must contain a JSON object")

    document.pop(VERSION_KEY, None)

    unknown = set(document) - PROTOCOL_FIELDS
    if unknown:
        placement = sorted(unknown & RUN_FIELDS)
        if placement:
            raise ProtocolError(
                f"Protocol file {path} carries run-placement key(s) "
                f"{', '.join(placement)}. A protocol describes the analysis, not "
                "where it runs -- pass --output (and --staging-dir) on the "
                "command line instead."
            )
        raise ProtocolError(
            f"Protocol file {path} carries unknown key(s): {', '.join(sorted(unknown))}"
        )

    base = base or BatchConfig()
    values = {f.name: getattr(base, f.name) for f in fields(BatchConfig)}

    for key, value in document.items():
        nested = _NESTED_TYPES.get(key)
        if nested is not None:
            value = _rebuild(nested, value, key, path)
        values[key] = value

    return BatchConfig(**values)


def _rebuild(cls, value, key: str, path: str | Path):
    """Rebuild one nested dataclass from its serialized dict."""
    if not isinstance(value, dict):
        raise ProtocolError(f"Protocol file {path}: '{key}' must be a JSON object")

    known = {f.name for f in fields(cls)}
    unknown = set(value) - known
    if unknown:
        raise ProtocolError(
            f"Protocol file {path}: '{key}' carries unknown key(s): {', '.join(sorted(unknown))}"
        )
    try:
        return cls(**value)
    except (TypeError, ValueError) as err:
        # AdaptiveSearchConfig enforces its 1-4 range in __post_init__, so an
        # out-of-range protocol fails here with the engine's own message rather
        # than surfacing hours later as a strange placement.
        raise ProtocolError(f"Protocol file {path}: '{key}' is invalid -- {err}") from err
