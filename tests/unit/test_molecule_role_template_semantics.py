"""Render repaired report, diagnostic and log-audit expressions with real Ansible."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROOT = Path(__file__).resolve().parents[2] / "collections/ansible_collections/general_ludd"
SCANS = (
    ("_sql_log_union", "UNION SELECT", "critical", "web"),
    ("_sql_log_boolean", "Boolean-based blind", "high", "web"),
    ("_sql_log_time", "Time-based blind", "critical", "web"),
    ("_sql_log_info_schema", "Information schema probe", "medium", "web"),
    ("_sql_log_stacked", "Stacked queries", "critical", "web"),
    ("_sql_log_encoded", "Encoded payload", "medium", "web"),
    ("_sql_log_advanced", "Advanced exploitation", "critical", "web"),
    ("_sql_db_select_star", "Suspicious full scan", "medium", "database"),
    ("_sql_db_union", "UNION SELECT (DB log)", "critical", "database"),
    ("_sql_db_schema", "Schema exploration (DB log)", "medium", "database"),
    ("_sql_db_fileops", "File/OS operations (DB log)", "critical", "database"),
)


@pytest.fixture(scope="module", autouse=True)
def isolated_ansible_state() -> Iterator[None]:
    from general_ludd.ansible.core_runner import _isolated_ansible_process_state

    with _isolated_ansible_process_state(None):
        yield


def _task(relative: str, name: str) -> dict[str, Any]:
    loaded: Any = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return next(task for task in loaded if task.get("name") == name)


def _render(expression: str, variables: dict[str, object]) -> object:
    result: object = Templar(loader=DataLoader(), variables=variables).template(trust_as_template(expression))
    return result


@pytest.mark.parametrize("report", ("Markdown", "risk matrix CSV"))
def test_entity_risk_reports_render_all_category_rows(report: str) -> None:
    task = _task(
        "business/roles/entity_research/tasks/output.yml",
        f"Build {report} report" if report == "Markdown" else "Build risk matrix CSV",
    )
    key = "entity_report_md" if report == "Markdown" else "entity_report_csv"
    result = _render(task["ansible.builtin.set_fact"][key], {
        "entity_name": "Example entity", "ansible_date_time": {"iso8601": "2026-10-13T00:00:00Z"},
        "entity_discovered": {}, "entity_associations": {}, "entity_assets": {},
        "entity_exposure": {}, "entity_demographics": {}, "entity_monitoring": {},
        "entity_risks": {"categories": {
            "financial": {"rating": "high", "score": 90, "signals_count": 7,
                          "template": {"thresholds": {"low_risk": 2, "high_risk": 6}}},
            "security": {"rating": "low", "signals_count": 1},
        }},
    })
    assert isinstance(result, str)
    if report == "Markdown":
        assert "| financial | high | 7 |" in result
        assert "| security | low | 1 |" in result
    else:
        assert "financial,high,90,7,2,6" in result
        assert "security,low,N/A,1,N/A,N/A" in result


def test_opencorporates_rewrite_preserves_native_dictionary_and_cached_results() -> None:
    task = _task("business/roles/entity_research/tasks/discover.yml", "Parse OpenCorporates result")
    previous = {"source": "searx", "data": {"results": ["retained"]}}
    payload = {"results": {"companies": [{"company": {
        "name": "Registered entity", "jurisdiction_code": "us_de", "company_type": "llc",
    }}]}}
    result = _render(task["ansible.builtin.set_fact"]["entity_discovered"], {
        "entity_discovered": {"raw_results": [previous], "unchanged": "preserved"},
        "oc_search_result": {"json": payload}, "entity_name": "Fallback", "entity_jurisdiction": "us",
    })
    assert isinstance(result, dict)
    assert result == {
        "raw_results": [previous, {"source": "opencorporates", "data": payload}],
        "unchanged": "preserved", "legal_name": "Registered entity",
        "jurisdiction": "us_de", "entity_type": "llc",
    }


@pytest.mark.parametrize("case", ("sign", "card-type", "card-diagnostic", "apdu-diagnostic"))
def test_hsm_diagnostics_apply_bounded_slices_after_default(case: str) -> None:
    if case == "sign":
        task = _task(
            "security/roles/hsm_operations/tasks/sign_ops.yml", "Request signing diagnostic analysis from model",
        )
        expression = task["general_ludd.agent.gludd_model_call"]["prompt"]
        variables: dict[str, object] = {
            "hsm_operations__mode": "sign_csr", "hsm_operations__key_label": "example",
            "_hsm_sign_csr": {"rc": 1, "stderr": "x" * 2500 + "BOUNDARY_EXCLUDED"},
        }
        limit = 2000
    elif case == "card-type":
        task = _task("security/roles/hsm_operations/tasks/smartcard.yml", "Parse ATR hex value")
        expression = task["ansible.builtin.set_fact"]["_hsm_card_type"]
        variables = {"_hsm_card_atr": {"stdout": "x" * 700 + "BOUNDARY_EXCLUDED"}}
        limit = 500
    else:
        task = _task(
            "security/roles/hsm_operations/tasks/smartcard.yml", "Request smartcard diagnostic analysis from model",
        )
        expression = task["general_ludd.agent.gludd_model_call"]["prompt"]
        variables = {"hsm_operations__mode": "card_atr", "_hsm_card_type": "x" * 1500 + "BOUNDARY_EXCLUDED"}
        limit = 1000
        if case == "apdu-diagnostic":
            variables = {
                "hsm_operations__mode": "card_apdu", "_hsm_card_apdu": {"stdout": "x" * 700 + "BOUNDARY_EXCLUDED"},
            }
            limit = 500
    result = _render(expression, variables)
    assert isinstance(result, str)
    assert "x" * limit in result
    assert "x" * (limit + 1) not in result
    assert "BOUNDARY_EXCLUDED" not in result
    for name in ("_hsm_sign_csr", "_hsm_card_atr", "_hsm_card_type", "_hsm_card_apdu"):
        variables.pop(name, None)
    fallback = _render(expression, variables)
    assert isinstance(fallback, str)
    assert "BOUNDARY_EXCLUDED" not in fallback


@pytest.mark.parametrize("case", ("all", "mixed", "missing"))
def test_risk_assessment_preserves_every_category_and_native_null(case: str) -> None:
    task = _task("business/roles/entity_research/tasks/risks.yml", "Build risk category assessments")
    names = ("financial", "security", "personnel", "market", "legal")
    templates = {name: {"category": name, "threshold": index} for index, name in enumerate(names)}
    previous = {"entity_name": "Example", "signals": ["preserved"]}
    variables: dict[str, object] = {
        "entity_risks": previous, "risk_categories": templates,
        "entity_exposure": {"breach_history": ["first", "second"]},
    }
    expected: dict[str, object] = {}
    for index, name in enumerate(names):
        failed = case == "mixed" and index % 2 == 0
        if case != "missing":
            variables[f"searx_{name}_risk"] = {"failed": failed, "json": {"number_of_results": index + 3}}
        entry = {"score": None, "rating": "unknown", "template": templates[name],
                 "signals_count": 0 if case == "missing" or failed else index + 3}
        if name == "security":
            entry["breaches"] = 2
        expected[name] = entry
    result = _render(task["ansible.builtin.set_fact"]["entity_risks"], variables)
    assert result == {**previous, "categories": expected}


def test_sql_advanced_scan_retains_original_classification() -> None:
    task = _task(
        "security/roles/sql_injection/tasks/log_audit.yml",
        "Build log findings with severity and attack type classification",
    )
    result = _render(task["ansible.builtin.set_fact"]["_sql_log_findings"], {
        "_sql_log_advanced": {"rc": 0, "stdout_lines": ["no source metadata"]},
    })
    assert result == [{
        "attack_type": "Advanced exploitation", "severity": "critical", "log_source": "web",
        "entry": "no source metadata", "source_ip": "unknown", "timestamp": "unknown",
    }]


@pytest.mark.parametrize("case", ("all", "mixed", "missing", "unmatched"))
def test_sql_log_findings_preserve_classification_and_safe_empty_results(case: str) -> None:
    task = _task(
        "security/roles/sql_injection/tasks/log_audit.yml",
        "Build log findings with severity and attack type classification",
    )
    variables: dict[str, object] = {}
    expected = []
    for index, (name, attack, severity, source) in enumerate(SCANS):
        if case == "missing" or (case == "mixed" and index % 3 == 0):
            continue
        lines = [f"192.0.2.{index + 1} [13/Oct/2026:00:00:00 +0000] first", "no source metadata"]
        if case == "unmatched":
            lines = ["no source metadata"]
        rc = 1 if case == "mixed" and index % 3 == 1 else 0
        variables[name] = {"rc": rc, "stdout_lines": lines}
        if rc:
            continue
        for line in lines:
            address = "unknown" if source == "web" else "N/A (database log)"
            timestamp = "unknown"
            if source == "web" and line.startswith("192."):
                address = line.split()[0]
                timestamp = "[13/Oct/2026:00:00:00 +0000]"
            expected.append({
                "attack_type": attack, "severity": severity, "log_source": source, "entry": line,
                "source_ip": address, "timestamp": timestamp,
            })
    result = _render(task["ansible.builtin.set_fact"]["_sql_log_findings"], variables)
    assert isinstance(result, list)
    assert result == expected
