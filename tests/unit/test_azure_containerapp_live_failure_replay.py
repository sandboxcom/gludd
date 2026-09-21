"""Replay every bounded incident learned from live Azure model work."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import cast

from ansible_collections.general_ludd.azure.plugins.filter import (
    containerapp_lifecycle,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
ROLE = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "azure"
    / "roles"
    / "container_app_deploy"
)
CORPUS = ROLE / "files" / "live_failure_replay_v1.json"
SCHEMA = ROLE / "files" / "live_failure_replay_v1.schema.json"
README = ROLE / "README.md"
REVISION = "fixture-app--0000001"

EXPECTED_CASES = (
    "canonical-envelope-proposal-scope-2026-09-10",
    "preflight-api-version-pin-2026-09-11",
    "a100-zero-running-replica-timeout-2026-09-12",
    "healthy-revision-empty-supplementary-inventory-2026-09-13",
    "typed-azure-timeout-local-continuation-2026-09-13",
    "retained-environment-state-adoption-2026-09-13",
    "local-candidate-deadline-drift-2026-09-13",
    "gpu-empty-metric-collection-2026-09-13",
    "gpu-concrete-revision-filter-http-400-2026-09-13",
    "gpu-two-wildcard-filter-http-400-2026-09-13",
    "gpu-one-wildcard-filter-http-400-2026-09-13",
    "gpu-validation-disabled-http-400-2026-09-13",
    "local-commit-guard-category-none-2026-09-13",
    "gpu-metric-catalog-empty-2026-09-13",
    "gpu-direct-query-http-400-five-minute-2026-09-14",
    "gpu-direct-query-http-400-fifteen-minute-2026-09-14",
    "local-canonical-batch-context-overflow-2026-09-14",
    "local-native-decode-misclassified-2026-09-14",
    "a100-terminal-zero-replica-2026-09-15",
    "t4-terminal-zero-replica-2026-09-21",
    "t4-environment-destroy-poll-timeout-2026-09-21",
    "versioned-operational-failover-2026-09-21",
)

FORBIDDEN_KEY_FRAGMENTS = (
    "clientsecret",
    "credential",
    "endpoint",
    "modelname",
    "prompt",
    "providertext",
    "repository",
    "resourceid",
    "subscription",
    "tenant",
    "token",
)


def _json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _cases() -> list[dict[str, object]]:
    return cast(list[dict[str, object]], _json(CORPUS)["cases"])


def _walk(value: object) -> Iterator[tuple[str | None, object]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield None, item
            yield from _walk(item)


def _startup_raw(values: Mapping[str, object]) -> dict[str, object]:
    listed = cast(int, values["listed_replicas"])
    return {
        "revisions": {
            "response": {
                "value": [
                    {
                        "name": REVISION,
                        "properties": {
                            "provisioningState": values["provisioning_state"],
                            "healthState": values["health_state"],
                            "runningState": values["running_state"],
                            "replicas": values["summary_replicas"],
                        },
                    }
                ]
            }
        },
        "replicas": {
            "response": {
                "value": [
                    {"name": f"fixture-replica-{index}", "properties": {}}
                    for index in range(listed)
                ]
            }
        },
    }


def test_corpus_and_schema_are_canonical_draft_2020_12_documents() -> None:
    corpus = _json(CORPUS)
    schema = _json(SCHEMA)

    assert CORPUS.read_text(encoding="utf-8") == _canonical(corpus)
    assert SCHEMA.read_text(encoding="utf-8") == _canonical(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(corpus)


def test_corpus_replays_every_documented_live_incident_in_order() -> None:
    corpus = _json(CORPUS)
    cases = _cases()
    sources = tuple(cast(dict[str, object], case["source"]) for case in cases)

    assert corpus["protocol"] == "gludd-azure-live-failure-replay-v1"
    assert corpus["source_document"] == (
        "docs/features/SELF_IMPROVEMENT_MIXED_MODELS.md"
    )
    assert tuple(case["id"] for case in cases) == EXPECTED_CASES
    assert all(cast(int, source["start_line"]) > 0 for source in sources)
    assert all(
        cast(int, source["end_line"]) >= cast(int, source["start_line"])
        for source in sources
    )


def test_corpus_is_bounded_and_excludes_sensitive_provider_content() -> None:
    payload = _json(CORPUS)
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)

    assert len(serialized.encode("ascii")) <= 65_536
    for key, value in _walk(payload):
        if key is not None:
            normalized = re.sub(r"[^a-z]", "", key.casefold())
            assert not any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS)
        if isinstance(value, str):
            assert len(value.encode("utf-8")) <= 512
    assert "client_secret" not in serialized.casefold()
    assert "provider_error" not in serialized.casefold()


def test_every_fixture_binds_an_existing_exact_regression_node() -> None:
    for case in _cases():
        nodeid = cast(str, case["regression_node"])
        relative, separator, function_name = nodeid.partition("::")
        assert separator == "::"
        path = ROOT / relative
        assert path.is_file(), nodeid
        source = path.read_text(encoding="utf-8")
        assert re.search(
            rf"^\s*def {re.escape(function_name)}\(",
            source,
            flags=re.MULTILINE,
        ), nodeid


def test_startup_fixtures_replay_through_the_collection_classifier() -> None:
    startup_cases = [case for case in _cases() if case["kind"] == "startup_diagnosis"]

    assert tuple(case["id"] for case in startup_cases) == (
        "a100-zero-running-replica-timeout-2026-09-12",
        "healthy-revision-empty-supplementary-inventory-2026-09-13",
        "a100-terminal-zero-replica-2026-09-15",
        "t4-terminal-zero-replica-2026-09-21",
    )
    for case in startup_cases:
        values = cast(dict[str, object], case["input"])
        expected = cast(dict[str, object], case["expected"])["classifier"]
        actual = containerapp_lifecycle.diagnose_containerapp_startup(
            _startup_raw(values),
            {
                "revision_name": REVISION,
                "requested_replicas": values["requested_replicas"],
            },
        )
        assert actual == expected
        assert REVISION not in json.dumps(actual, sort_keys=True)


def test_role_documents_replay_and_universal_model_work_scope() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "live_failure_replay_v1.json" in readme
    assert "model-work capacity, including self-improvement" in readme
    assert "answers/questions/5572527" in readme
    assert "azure-container-apps/issues/1705" in readme
    assert "opentofu/opentofu/issues/1571" in readme
    assert "answers/questions/460863" in readme
    assert "answers/questions/5811384" in readme
    assert "azure-container-apps/issues/1511" in readme
    assert "azure-container-apps/issues/1797" in readme
