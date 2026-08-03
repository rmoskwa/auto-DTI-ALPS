"""
Unit tests for the protocol file (``processing/config_io.py``).

The whole risk of a second front end is that it quietly stops agreeing with the
first, so several of these are *drift guards* rather than behaviour tests: they
fail when somebody adds a field, not when somebody breaks a feature.

Nothing here touches Qt or the filesystem beyond ``tmp_path``.
"""

import json
from dataclasses import fields

import pytest

from dti_alps.processing.config_io import (
    PROTOCOL_FIELDS,
    PROTOCOL_VERSION,
    RUN_FIELDS,
    VERSION_KEY,
    ProtocolError,
    protocol_dict,
    protocol_hash,
    read_protocol,
    write_protocol,
)
from dti_alps.processing.constants import AdaptiveSearchConfig
from dti_alps.processing.state import BatchConfig, OutputConfig


def _all_non_default_config() -> BatchConfig:
    """A config with every protocol field set away from its default."""
    return BatchConfig(
        run_denoising=False,
        dwidenoise_options={"-nthreads": 8, "-extent": "5,5,5"},
        run_degibbs=False,
        mrdegibbs_options={"-nshifts": 32},
        pe_direction="PA",
        auto_pe_direction=False,
        readout_time=0.037,
        rpe_scheme="pair",
        dwifslpreproc_options={"-eddy_options": "--repol --slm=linear", "-nocleanup": True},
        dwi2tensor_options={"-ols": True},
        tensor2metric_options={"-modulate": "FA"},
        use_synb0=True,
        synb0_output_dir="/data/synb0/OUTPUTS",
        synb0_eddy_options={"repol": True, "niter": 7},
        flirt_options={"-dof": "9"},
        fnirt_options={"--warpres": "8,8,8"},
        roi_shapes=[{"type": "sphere", "radius": 2.5}, {"type": "squarev9"}],
        fa_threshold=0.35,
        alps_method="ALPS-PAS",
        adaptive_roi_placement="Standard",
        adaptive_search=AdaptiveSearchConfig(
            search_x=4, search_y=2, search_z=3, max_y_drift=2, max_z_drift=4
        ),
        output_config=OutputConfig(denoised_dwi=False, tensor=False, log_file=False),
        # Run placement -- must NOT survive a round trip through the file.
        output_dir="/home/someone/scratch/out",
        staging_enabled=True,
        staging_dir="/fast/local",
    )


class TestExhaustiveness:
    """
    Guard 1: every ``BatchConfig`` field is classified as protocol or placement.

    A newly added field fails here until somebody decides which it is -- it
    cannot quietly default to "not serialized" and go missing from every
    exported protocol.
    """

    def test_partition_covers_batch_config_exactly(self):
        declared = {f.name for f in fields(BatchConfig)}
        assert PROTOCOL_FIELDS | RUN_FIELDS == declared

    def test_partition_is_disjoint(self):
        assert not (PROTOCOL_FIELDS & RUN_FIELDS)


class TestRoundTrip:
    """Guard 2: ``read_protocol(write_protocol(cfg))`` recovers the protocol."""

    def test_every_protocol_field_survives(self, tmp_path):
        original = _all_non_default_config()
        path = tmp_path / "protocol.json"

        write_protocol(path, original)
        restored = read_protocol(path)

        for name in sorted(PROTOCOL_FIELDS):
            assert getattr(restored, name) == getattr(original, name), name

    def test_nested_dataclasses_come_back_as_dataclasses(self, tmp_path):
        """Left as dicts, every downstream attribute access would fail."""
        path = tmp_path / "protocol.json"
        write_protocol(path, _all_non_default_config())

        restored = read_protocol(path)

        assert isinstance(restored.adaptive_search, AdaptiveSearchConfig)
        assert isinstance(restored.output_config, OutputConfig)
        assert restored.adaptive_search.search_x == 4
        assert restored.output_config.log_file is False


class TestPlacementIsNotSerialized:
    """
    The point of the split: a protocol carries no trace of the machine that
    produced it, so it can be committed beside the analysis code.
    """

    def test_placement_keys_are_absent_from_the_file(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(path, _all_non_default_config())

        document = json.loads(path.read_text())

        for name in RUN_FIELDS:
            assert name not in document
        assert "/home/someone" not in path.read_text()

    def test_reading_leaves_placement_at_the_base_values(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(path, _all_non_default_config())

        restored = read_protocol(path)

        assert restored.output_dir == ""
        assert restored.staging_enabled is False
        assert restored.staging_dir is None

    def test_base_supplies_placement(self, tmp_path):
        """The CLI reads a protocol onto a config already carrying --output."""
        path = tmp_path / "protocol.json"
        write_protocol(path, _all_non_default_config())

        restored = read_protocol(path, base=BatchConfig(output_dir="/scratch/mine"))

        assert restored.output_dir == "/scratch/mine"
        assert restored.fa_threshold == 0.35  # file still wins for protocol keys

    def test_a_file_carrying_a_placement_key_is_rejected_by_name(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"fa_threshold": 0.3, "output_dir": "/somebody/else"}))

        with pytest.raises(ProtocolError) as exc:
            read_protocol(path)

        assert "output_dir" in str(exc.value)
        assert "--output" in str(exc.value)


class TestDefaultsForOmittedKeys:
    """A hand-trimmed file carrying three settings must work."""

    def test_omitted_keys_fall_back_to_defaults(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"fa_threshold": 0.31, "alps_method": "ALPS-LAB"}))

        restored = read_protocol(path)

        assert restored.fa_threshold == 0.31
        assert restored.alps_method == "ALPS-LAB"
        assert restored.run_denoising is BatchConfig().run_denoising
        assert restored.adaptive_search == AdaptiveSearchConfig()

    def test_empty_object_is_a_valid_protocol(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text("{}")

        assert read_protocol(path) == BatchConfig()


class TestUnknownKeysAreRejected:
    """
    A typo'd option that silently did nothing would be indistinguishable from
    one the tool does not support, so the message names the key.
    """

    def test_unknown_top_level_key(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"fa_treshold": 0.3}))

        with pytest.raises(ProtocolError, match="fa_treshold"):
            read_protocol(path)

    def test_unknown_nested_key(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"output_config": {"tensr": False}}))

        with pytest.raises(ProtocolError, match="tensr"):
            read_protocol(path)

    def test_out_of_range_search_envelope_fails_at_read_time(self, tmp_path):
        """The engine's own 1-4 guard fires here, not hours into placement."""
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"adaptive_search": {"search_x": 99}}))

        with pytest.raises(ProtocolError, match="adaptive_search"):
            read_protocol(path)

    def test_nested_value_of_the_wrong_shape(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text(json.dumps({"output_config": "all of them"}))

        with pytest.raises(ProtocolError, match="output_config"):
            read_protocol(path)


class TestMalformedFiles:
    """Failures are reported as ProtocolError, never as a raw traceback."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(ProtocolError, match="Could not read"):
            read_protocol(tmp_path / "absent.json")

    def test_not_json(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text("this is not json")

        with pytest.raises(ProtocolError, match="not valid JSON"):
            read_protocol(path)

    def test_json_that_is_not_an_object(self, tmp_path):
        path = tmp_path / "protocol.json"
        path.write_text("[1, 2, 3]")

        with pytest.raises(ProtocolError, match="JSON object"):
            read_protocol(path)


class TestVersioning:
    """The file names its schema version, and the reader does not choke on it."""

    def test_version_is_written(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(path, BatchConfig())

        assert json.loads(path.read_text())[VERSION_KEY] == PROTOCOL_VERSION

    def test_version_is_not_mistaken_for_an_unknown_key(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(path, BatchConfig())

        read_protocol(path)  # does not raise

    def test_version_is_not_a_batch_config_field(self):
        assert VERSION_KEY not in {f.name for f in fields(BatchConfig)}


class TestProtocolHash:
    """The resume marker's identity: same analysis, same digest."""

    def test_equal_protocols_hash_equal(self):
        assert protocol_hash(_all_non_default_config()) == protocol_hash(_all_non_default_config())

    def test_placement_does_not_affect_the_hash(self):
        """A protocol run on two machines is the same protocol."""
        here = _all_non_default_config()
        there = _all_non_default_config()
        there.output_dir = "/mnt/cluster/scratch"
        there.staging_enabled = False
        there.staging_dir = None

        assert protocol_hash(here) == protocol_hash(there)

    def test_a_changed_protocol_field_changes_the_hash(self):
        before = _all_non_default_config()
        after = _all_non_default_config()
        after.fa_threshold = 0.36

        assert protocol_hash(before) != protocol_hash(after)

    def test_hash_survives_the_file_round_trip(self, tmp_path):
        path = tmp_path / "protocol.json"
        original = _all_non_default_config()
        write_protocol(path, original)

        assert protocol_hash(read_protocol(path)) == protocol_hash(original)

    def test_dict_key_order_does_not_affect_the_hash(self):
        a = BatchConfig(dwidenoise_options={"-nthreads": 4, "-extent": "5,5,5"})
        b = BatchConfig(dwidenoise_options={"-extent": "5,5,5", "-nthreads": 4})

        assert protocol_hash(a) == protocol_hash(b)


class TestProtocolDict:
    """The in-memory form the writer and the hash share."""

    def test_contains_exactly_the_protocol_fields(self):
        assert set(protocol_dict(BatchConfig())) == set(PROTOCOL_FIELDS)

    def test_is_json_serializable(self):
        json.dumps(protocol_dict(_all_non_default_config()))

    def test_keys_are_sorted(self):
        keys = list(protocol_dict(_all_non_default_config()))
        assert keys == sorted(keys)
