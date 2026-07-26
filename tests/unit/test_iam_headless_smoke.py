"""Regression coverage for the credential-free IAM smoke runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "iam_headless_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("iam_headless_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_headless_smoke_validates_all_provider_personas() -> None:
    module = _load_module()

    report = module.run_smoke(ROOT / "config" / "infra")

    assert report["ok"] is True
    assert report["mode"] == "headless"
    assert report["providers"] == {"aws": 4, "azure": 4, "gcp": 4}
    assert report["violations"] == []


def test_headless_smoke_reports_missing_persona(tmp_path: Path) -> None:
    module = _load_module()
    infra = tmp_path / "infra"
    infra.mkdir()
    for source in (ROOT / "config" / "infra").glob("*-iam-roles.yml"):
        (infra / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (infra / "aws-iam-roles.yml").write_text(
        (infra / "aws-iam-roles.yml")
        .read_text(encoding="utf-8")
        .replace("  monitor:\n", "  removed_monitor:\n", 1),
        encoding="utf-8",
    )

    report = module.run_smoke(infra)

    assert report["ok"] is False
    assert "aws: missing personas: monitor" in report["violations"]


def test_headless_smoke_reports_missing_opa_rule(tmp_path: Path) -> None:
    module = _load_module()
    infra = tmp_path / "infra"
    infra.mkdir()
    for source in (ROOT / "config" / "infra").glob("*-iam-roles.yml"):
        (infra / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    opa = tmp_path / "iam_policy.rego"
    opa.write_text("package iam\n", encoding="utf-8")

    report = module.run_smoke(infra, opa_path=opa)

    assert report["ok"] is False
    assert "OPA missing rule: aws_least_privilege_valid" in report["violations"]
