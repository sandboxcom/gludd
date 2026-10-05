"""Contract tests for the radio collection's managed-host runtime boundary."""

from __future__ import annotations

import builtins
import json
import math
import runpy
import struct
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    exam_quiz_runtime,
    link_budget_runtime,
    propagation_runtime,
    regulation_lookup_runtime,
    signal_identify_runtime,
)
from ansible_collections.general_ludd.radio.plugins.modules import radio_runtime

_COLLECTION_ROOT = Path(__file__).resolve().parents[2]

_ROLE_FILES = {
    "propagation_model": "propagation_model.py",
    "link_budget": "link_budget.py",
    "exam_quiz": "exam_quiz.py",
    "signal_identify": "signal_identify.py",
    "regulation_lookup": "regulation_lookup.py",
}


def test_runtime_module_has_typed_operation_contract() -> None:
    operation = radio_runtime.ARGUMENT_SPEC["operation"]
    assert operation["type"] == "str"
    assert operation["required"] is True
    assert set(operation["choices"]) == set(_ROLE_FILES)
    assert radio_runtime.ARGUMENT_SPEC["params"]["type"] == "dict"
    assert all(
        "type" in option
        for option in radio_runtime.ARGUMENT_SPEC["params"]["options"].values()
    )


@pytest.mark.parametrize("role", _ROLE_FILES)
def test_roles_call_packaged_fqcn_without_ambient_python(role: str) -> None:
    tasks = (_COLLECTION_ROOT / "roles" / role / "tasks" / "main.yml").read_text()
    assert "general_ludd.radio.radio_runtime:" in tasks
    for forbidden in (
        "ansible.builtin.command:",
        "ansible_python_interpreter",
        "role_path",
        "PYTHONPATH",
    ):
        assert forbidden not in tasks


@pytest.mark.parametrize(("role", "filename"), _ROLE_FILES.items())
def test_legacy_cli_shims_use_packaged_module_utils(role: str, filename: str) -> None:
    script = (_COLLECTION_ROOT / "roles" / role / "files" / filename).read_text()
    assert "ansible_collections.general_ludd.radio.plugins.module_utils" in script
    assert "sys.path" not in script


def test_dispatch_preserves_radio_result_shapes() -> None:
    propagation = radio_runtime.dispatch(
        "propagation_model",
        {"model": "free_space", "freq_hz": 144_000_000, "distance_m": 1_000.0},
    )
    assert propagation["result"]["loss_db"] > 0
    assert propagation["artifact_filename"] == "propagation_model.json"

    link = radio_runtime.dispatch(
        "link_budget",
        {"tx_power_dbm": 30.0, "freq_hz": 144_000_000, "distance_m": 1_000.0},
    )
    assert isinstance(link["result"]["viable"], bool)

    exam = radio_runtime.dispatch(
        "exam_quiz",
        {"exam": "fcc_tech", "count": 2, "seed": 42, "format": "json"},
    )
    assert exam["result"]["count_returned"] == 2

    signal = radio_runtime.dispatch(
        "signal_identify",
        {"bandwidth_hz": 6_250.0, "symbol_rate_baud": 4_800.0},
    )
    assert signal["result"]["role"] == "signal_identify"

    regulation = radio_runtime.dispatch(
        "regulation_lookup",
        {"country": "US", "freq_mhz": 146.52},
    )
    assert regulation["result"]["country"] == "US"


def test_dispatch_covers_optional_radio_contracts() -> None:
    rain = radio_runtime.dispatch(
        "propagation_model",
        {
            "model": "rain",
            "freq_hz": 10_000_000_000,
            "distance_m": 5_000.0,
            "rain_rate_mmh": 25.0,
            "polarization": "vertical",
        },
    )
    assert rain["result"]["extra"]["total_attenuation_db"] >= 0

    rainy_link = radio_runtime.dispatch(
        "link_budget",
        {
            "tx_power_dbm": 30.0,
            "freq_hz": 10_000_000_000,
            "distance_m": 2_000.0,
            "model": "rain",
            "rain_enabled": True,
            "rain_rate_mmh": 20.0,
        },
    )
    assert "rain_attenuation_db" in rainy_link["result"]

    terrestrial_link = radio_runtime.dispatch(
        "link_budget",
        {
            "tx_power_dbm": 30.0,
            "freq_hz": 144_000_000,
            "distance_m": 10_000.0,
            "model": "hata_urban",
            "tx_antenna_type": "not-a-known-antenna",
            "tx_antenna_gain_dbi": 3.0,
        },
    )
    assert terrestrial_link["result"]["path_loss_model"] == "Hata-Okumura Urban"

    loaded = radio_runtime.dispatch(
        "exam_quiz",
        {"exam": "fcc_tech", "count": 1, "seed": 3, "format": "text"},
    )
    question_id = loaded["result"]["questions"][0]["id"]
    graded = radio_runtime.dispatch(
        "exam_quiz",
        {
            "exam": "fcc_tech",
            "count": 1,
            "seed": 3,
            "answers": {question_id: 0},
            "format": "md",
        },
    )
    assert graded["artifact_filename"] == "exam_quiz.md"
    assert "grade" in graded["result"]
    assert "Score:" in exam_quiz_runtime._render_text(graded["result"])
    assert "No answers submitted" in exam_quiz_runtime._render_md(loaded["result"])

    invalid_exam = exam_quiz_runtime.load_questions("not-an-exam", 1)
    assert invalid_exam.questions == []

    regulation = radio_runtime.dispatch(
        "regulation_lookup",
        {
            "country": "us",
            "band_name": "2m",
            "license_class": "technician",
            "marine_channel": 16,
        },
    )
    assert regulation["result"]["band_plan"]
    assert regulation["result"]["license_privileges"]
    assert regulation["result"]["marine_channel"]["channel"] == 16

    unknown_model = radio_runtime.dispatch(
        "propagation_model",
        {"model": "unknown", "freq_hz": 1, "distance_m": 1.0},
    )
    assert unknown_model["result"]["verdict"] == "skipped"

    empty_verdict = propagation_runtime.PropagationVerdict(
        model="free_space",
        freq_hz=144_000_000,
        distance_m=1_000.0,
        tx_height_m=30.0,
        rx_height_m=1.5,
    ).to_dict()
    assert "extra" not in empty_verdict


def test_propagation_runtime_preserves_model_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propagation_runtime,
        "predict_path_loss",
        lambda **_kwargs: {"error": "model unavailable"},
    )
    verdict = propagation_runtime.compute_path_loss(
        "itm",
        144_000_000,
        1_000.0,
    ).to_dict()
    assert verdict["verdict"] == "skipped"
    assert verdict["extra"] == {"error": "model unavailable"}


def test_signal_runtime_analyzes_packaged_iq_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iq_file = tmp_path / "samples.iq"
    samples: list[int] = []
    for index in range(4_096):
        samples.extend((int(1_000 * math.sin(index / 10.0)), 0))
    samples.append(0)
    iq_file.write_bytes(struct.pack(f"<{len(samples)}h", *samples))
    payload = radio_runtime.dispatch(
        "signal_identify",
        {
            "input_file": str(iq_file),
            "sample_rate": 48_000,
            "center_freq_hz": 14_200_000,
        },
    )
    assert payload["result"]["signal_analysis"]["iq_samples"] == 8_192

    original_import = builtins.__import__

    def _without_numpy(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "numpy":
            raise ImportError("numpy unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _without_numpy)
    fallback = signal_identify_runtime.signal_identify(input_file=str(iq_file))
    assert "numpy/scipy not available" in fallback["warning"]


@pytest.mark.parametrize(
    ("active_rows", "expected_shape"),
    (
        ((True, True, True), "single_carrier"),
        ((False, True, False, True, False), "two_tone_fsk"),
        ((False, True, False, True, False, True, False), "multi_tone_fsk"),
        ((False, True) * 9 + (False,), "many_tone_fsk"),
        ((False, True, False), "unknown"),
    ),
)
def test_signal_runtime_classifies_deterministic_spectrum_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_rows: tuple[bool, ...],
    expected_shape: str,
) -> None:
    numpy = pytest.importorskip("numpy")
    scipy_signal = pytest.importorskip("scipy.signal")
    iq_file = tmp_path / "shape.iq"
    iq_file.write_bytes(struct.pack("<128h", *([1, 0] * 64)))

    def _spectrogram(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        spectrum = numpy.full((len(active_rows), 2), 0.0001)
        spectrum[list(active_rows), :] = 100.0
        return numpy.arange(len(active_rows)), numpy.arange(2), spectrum

    monkeypatch.setattr(scipy_signal, "spectrogram", _spectrogram)
    result = signal_identify_runtime.signal_identify(
        input_file=str(iq_file),
        sample_rate=48_000,
    )
    assert result["signal_analysis"]["estimated_shape"] == expected_shape


@pytest.mark.parametrize(
    ("operation", "params", "missing"),
    (
        ("propagation_model", {"model": "free_space"}, "freq_hz"),
        ("link_budget", {"freq_hz": 1, "distance_m": 1.0}, "tx_power_dbm"),
        ("exam_quiz", {"count": 1}, "exam"),
        ("regulation_lookup", {"freq_mhz": 1.0}, "country"),
    ),
)
def test_dispatch_rejects_missing_required_params(
    operation: str,
    params: dict[str, object],
    missing: str,
) -> None:
    with pytest.raises(ValueError, match=missing):
        radio_runtime.dispatch(operation, params)


def test_packaged_cli_entrypoints_preserve_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = (
        (
            propagation_runtime.main,
            [
                "propagation_model",
                "--model",
                "free_space",
                "--freq-hz",
                "144000000",
                "--distance-m",
                "1000",
                "--output-dir",
                str(tmp_path),
            ],
            "propagation_model.json",
        ),
        (
            link_budget_runtime.main,
            [
                "link_budget",
                "--freq-hz",
                "144000000",
                "--distance-m",
                "1000",
                "--output-dir",
                str(tmp_path),
            ],
            "link_budget.json",
        ),
        (
            exam_quiz_runtime.main,
            [
                "exam_quiz",
                "--exam",
                "fcc_tech",
                "--count",
                "1",
                "--format",
                "json",
                "--output-dir",
                str(tmp_path),
            ],
            "exam_quiz.json",
        ),
        (
            signal_identify_runtime.main,
            ["signal_identify", "--bandwidth", "6250", "--symbol-rate", "4800"],
            None,
        ),
        (
            regulation_lookup_runtime.main,
            [
                "regulation_lookup",
                "--country",
                "US",
                "--freq-mhz",
                "146.52",
                "--output-dir",
                str(tmp_path),
            ],
            "regulation_lookup.json",
        ),
    )
    for entrypoint, argv, artifact in cases:
        monkeypatch.setattr(sys, "argv", argv)
        runpy.run_path(entrypoint.__code__.co_filename, run_name="__main__")
        output = capsys.readouterr().out
        assert json.loads(output)
        if artifact is not None:
            assert (tmp_path / artifact).is_file()


class _ModuleResult(Exception):
    """Capture an Ansible module terminal payload."""


class _FakeModule:
    def __init__(self, operation: str) -> None:
        self.params = {
            "operation": operation,
            "params": {
                "model": "free_space",
                "freq_hz": 144_000_000,
                "distance_m": 1_000.0,
            },
        }

    def exit_json(self, **payload: object) -> None:
        raise _ModuleResult(payload)

    def fail_json(self, **payload: object) -> None:
        raise _ModuleResult(payload)


def test_ansible_entrypoint_returns_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModule("propagation_model")
    monkeypatch.setattr(radio_runtime, "AnsibleModule", lambda **_kwargs: fake)
    with pytest.raises(_ModuleResult) as success:
        radio_runtime.main()
    assert success.value.args[0]["changed"] is False

    fake.params["operation"] = "unknown"
    with pytest.raises(_ModuleResult) as failure:
        radio_runtime.main()
    assert "Unsupported radio operation" in failure.value.args[0]["msg"]


def test_dispatch_fails_closed_before_artifact_mutation(tmp_path: Path) -> None:
    sentinel = tmp_path / "existing.json"
    sentinel.write_text("stable", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported radio operation"):
        radio_runtime.dispatch("unknown", {})
    assert sentinel.read_text(encoding="utf-8") == "stable"
