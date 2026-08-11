"""Unit tests for src/general_ludd/routing_roles/small_model_policy.py."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from general_ludd.routing_roles.small_model_policy import (
    _BOUNDED_IMPACTS,
    _CHECK_RE,
    _SHA256_RE,
    DEFAULT_TASK_CONTRACTS,
    FORBIDDEN_IMPACTS,
    CapabilityEvidence,
    CompletionAction,
    CompletionEvidence,
    DispatchAction,
    DispatchDecision,
    ModelIdentity,
    PolicyConfig,
    SmallModelTaskPolicy,
    SmallModelTaskSpec,
    TaskContract,
    TaskImpact,
    _contract,
    _require_digest,
    _require_pattern,
    _stable_digest,
    _validate_checks,
)
from general_ludd.schemas.benchmark import TaskRole

_D = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _digest_of(payload: dict) -> str:

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _eval_digest(suite_id: str = "suite/abc") -> str:
    return _digest_of({"suite_id": suite_id, "dummy": "eval"})


def _model_id(**overrides) -> ModelIdentity:
    kwargs = {
        "model_profile_id": "p/model-1",
        "model_artifact_digest": _digest_of({"weights": "v1"}),
        "runtime_config_digest": _digest_of({"cuda": 12}),
        "prompt_contract_digest": _digest_of({"prompt": "v1"}),
    }
    kwargs.update(overrides)
    return ModelIdentity(**kwargs)


def _cap_evidence(
    model: ModelIdentity | None = None,
    task_kind: str = "coding",
    role: TaskRole = TaskRole.CODER,
    collection: str = "general_ludd.agent",
    acceptance_contract_digest: str | None = None,
    passed_cases: int = 25,
    total_cases: int = 25,
    collection_ok: bool = True,
    local_only: bool = True,
    suite_id: str = "suite/code-v1",
    suite_revision: str = "suite/rev-1",
    **overrides,
) -> CapabilityEvidence:
    if model is None:
        model = _model_id()
    if acceptance_contract_digest is None:
        acceptance_contract_digest = _digest_of({"dummy": "acc"})
    kwargs = {
        "model_profile_id": model.model_profile_id,
        "model_identity_digest": model.fingerprint,
        "task_kind": task_kind,
        "role": role,
        "collection": collection,
        "suite_id": suite_id,
        "suite_revision": suite_revision,
        "acceptance_contract_digest": acceptance_contract_digest,
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "collection_ok": collection_ok,
        "local_only": local_only,
        "evidence_digest": _eval_digest(suite_id),
    }
    kwargs.update(overrides)
    return CapabilityEvidence(**kwargs)


def _task_spec(
    task_id: str = "task-001",
    task_kind: str = "coding",
    role: TaskRole = TaskRole.CODER,
    collection: str = "general_ludd.agent",
    acceptance_checks: tuple[str, ...] = ("syntax_valid", "import_ok", "run_without_crash"),
    **overrides,
) -> SmallModelTaskSpec:
    kwargs = {
        "task_id": task_id,
        "task_kind": task_kind,
        "role": role,
        "collection": collection,
        "input_digest": _digest_of({"input": "hello"}),
        "impacts": frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
        "acceptance_checks": acceptance_checks,
    }
    kwargs.update(overrides)
    return SmallModelTaskSpec(**kwargs)


def _completion_evidence(
    task: SmallModelTaskSpec | None = None,
    attempt: int = 1,
    artifact_digest: str | None = None,
    acceptance_results: dict[str, bool] | None = None,
    collection_ok: bool = True,
    **overrides,
) -> CompletionEvidence:
    if task is None:
        task = _task_spec()
    if artifact_digest is None:
        artifact_digest = _digest_of({"artifact": attempt})
    if acceptance_results is None:
        acceptance_results = {c: True for c in task.acceptance_checks}
    evidence_digest = _digest_of({"comp": attempt})
    kwargs = {
        "task_fingerprint": task.fingerprint,
        "attempt": attempt,
        "artifact_digest": artifact_digest,
        "acceptance_results": acceptance_results,
        "collection_ok": collection_ok,
        "evidence_digest": evidence_digest,
    }
    kwargs.update(overrides)
    return CompletionEvidence(**kwargs)


# ── TaskImpact ──────────────────────────────────────────────────────────────


class TestTaskImpact:
    def test_enum_members(self):
        expected = {
            "READ_SOURCE",
            "WRITE_ARTIFACT",
            "MUTATE_REPOSITORY",
            "EXECUTE_COMMAND",
            "NETWORK_WRITE",
            "CREDENTIAL_ACCESS",
            "DEPLOYMENT",
            "RELEASE",
            "SECURITY_DECISION",
        }
        assert set(TaskImpact.__members__) == expected

    def test_forbidden_impacts_are_mutating(self):
        assert (
            frozenset(
                {
                    TaskImpact.MUTATE_REPOSITORY,
                    TaskImpact.EXECUTE_COMMAND,
                    TaskImpact.NETWORK_WRITE,
                    TaskImpact.CREDENTIAL_ACCESS,
                    TaskImpact.DEPLOYMENT,
                    TaskImpact.RELEASE,
                    TaskImpact.SECURITY_DECISION,
                }
            )
            == FORBIDDEN_IMPACTS
        )

    def test_bounded_impacts_are_read_write(self):
        assert frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}) == _BOUNDED_IMPACTS

    def test_forbidden_and_bounded_are_disjoint(self):
        assert frozenset() == FORBIDDEN_IMPACTS & _BOUNDED_IMPACTS


# ── TaskContract ────────────────────────────────────────────────────────────


class TestTaskContract:
    def test_valid_contract(self):
        c = TaskContract(
            task_kind="coding",
            allowed_roles=frozenset({TaskRole.CODER}),
            allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
            required_acceptance_checks=frozenset({"syntax_valid"}),
        )
        assert c.task_kind == "coding"
        assert c.allowed_roles == frozenset({TaskRole.CODER})

    def test_invalid_task_kind_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            TaskContract(
                "9invalid", frozenset({TaskRole.CODER}), frozenset({TaskImpact.WRITE_ARTIFACT}), frozenset({"chk"})
            )

    def test_empty_roles_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            TaskContract("coding", frozenset(), frozenset({TaskImpact.WRITE_ARTIFACT}), frozenset({"chk"}))

    def test_non_frozenset_roles_raises(self):
        with pytest.raises(ValueError):
            TaskContract("coding", set(), frozenset({TaskImpact.WRITE_ARTIFACT}), frozenset({"chk"}))

    def test_empty_impacts_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            TaskContract("coding", frozenset({TaskRole.CODER}), frozenset(), frozenset({"chk"}))

    def test_forbidden_impacts_raises(self):
        with pytest.raises(ValueError, match="cannot grant high-impact"):
            TaskContract(
                "coding", frozenset({TaskRole.CODER}), frozenset({TaskImpact.MUTATE_REPOSITORY}), frozenset({"chk"})
            )

    def test_mixed_safe_and_forbidden_impacts_raises(self):
        with pytest.raises(ValueError, match="cannot grant high-impact"):
            TaskContract(
                "coding",
                frozenset({TaskRole.CODER}),
                frozenset({TaskImpact.WRITE_ARTIFACT, TaskImpact.DEPLOYMENT}),
                frozenset({"chk"}),
            )

    def test_empty_checks_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            TaskContract("coding", frozenset({TaskRole.CODER}), frozenset({TaskImpact.WRITE_ARTIFACT}), frozenset())

    def test_non_frozenset_impacts_raises(self):
        with pytest.raises(ValueError):
            TaskContract("coding", frozenset({TaskRole.CODER}), set(), frozenset({"chk"}))

    def test_non_taskrole_in_roles_raises(self):
        with pytest.raises(ValueError):
            TaskContract(
                "coding", frozenset({"not_a_role"}), frozenset({TaskImpact.WRITE_ARTIFACT}), frozenset({"chk"})
            )


# ── PolicyConfig ────────────────────────────────────────────────────────────


class TestPolicyConfig:
    def test_defaults(self):
        c = PolicyConfig()
        assert c.max_attempts == 2
        assert c.min_evaluation_cases == 20

    def test_custom_values(self):
        c = PolicyConfig(max_attempts=3, min_evaluation_cases=500)
        assert c.max_attempts == 3
        assert c.min_evaluation_cases == 500

    def test_max_attempts_too_low(self):
        with pytest.raises(ValueError):
            PolicyConfig(max_attempts=0)

    def test_max_attempts_too_high(self):
        with pytest.raises(ValueError):
            PolicyConfig(max_attempts=4)

    def test_max_attempts_bool_raises(self):
        with pytest.raises(ValueError):
            PolicyConfig(max_attempts=True)

    def test_min_evaluation_cases_zero_raises(self):
        with pytest.raises(ValueError):
            PolicyConfig(min_evaluation_cases=0)

    def test_min_evaluation_cases_too_high(self):
        with pytest.raises(ValueError):
            PolicyConfig(min_evaluation_cases=10_001)

    def test_min_evaluation_cases_bool_raises(self):
        with pytest.raises(ValueError):
            PolicyConfig(min_evaluation_cases=False)


# ── SmallModelTaskSpec ──────────────────────────────────────────────────────


class TestSmallModelTaskSpec:
    def test_valid_spec(self):
        s = _task_spec()
        assert s.task_id == "task-001"
        assert s.task_kind == "coding"
        assert s.role == TaskRole.CODER

    def test_invalid_task_id_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _task_spec(task_id="")

    def test_invalid_task_id_special_chars(self):
        with pytest.raises(ValueError, match="invalid format"):
            _task_spec(task_id="task@bad")

    def test_invalid_task_kind_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _task_spec(task_kind="9invalid")

    def test_non_taskrole_role_raises(self):
        with pytest.raises(ValueError, match="role must be a TaskRole"):
            _task_spec(role="planner")

    def test_invalid_collection_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _task_spec(collection="no_dots")

    def test_invalid_input_digest_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _task_spec(input_digest="short")

    def test_non_frozenset_impacts_raises(self):
        with pytest.raises(ValueError):
            _task_spec(impacts=set())

    def test_empty_impacts_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _task_spec(impacts=frozenset())

    def test_non_tuple_acceptance_checks_raises(self):
        with pytest.raises(ValueError):
            _task_spec(acceptance_checks=["syntax_valid"])

    def test_empty_acceptance_checks_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _task_spec(acceptance_checks=())

    def test_duplicate_acceptance_checks_raises(self):
        with pytest.raises(ValueError, match="must not contain duplicates"):
            _task_spec(acceptance_checks=("syntax_valid", "syntax_valid"))

    def test_acceptance_contract_digest_is_deterministic(self):
        s1 = _task_spec(task_id="task-a", acceptance_checks=("chk_b", "chk_a"))
        s2 = _task_spec(task_id="task-b", acceptance_checks=("chk_a", "chk_b"))
        assert s1.acceptance_contract_digest == s2.acceptance_contract_digest

    def test_acceptance_contract_digest_differs_on_role(self):
        s1 = _task_spec(role=TaskRole.CODER)
        s2 = _task_spec(role=TaskRole.EDITOR)
        assert s1.acceptance_contract_digest != s2.acceptance_contract_digest

    def test_fingerprint_is_deterministic(self):
        s1 = _task_spec(task_id="task-a")
        s2 = _task_spec(task_id="task-a")
        assert s1.fingerprint == s2.fingerprint

    def test_fingerprint_differs_on_input(self):
        s1 = _task_spec(task_id="task-a", input_digest=_digest_of({"x": 1}))
        s2 = _task_spec(task_id="task-a", input_digest=_digest_of({"x": 2}))
        assert s1.fingerprint != s2.fingerprint


# ── ModelIdentity ───────────────────────────────────────────────────────────


class TestModelIdentity:
    def test_valid_identity(self):
        m = _model_id()
        assert m.model_profile_id == "p/model-1"

    def test_invalid_profile_id_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _model_id(model_profile_id="")

    def test_invalid_artifact_digest_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _model_id(model_artifact_digest="short")

    def test_fingerprint_is_deterministic(self):
        m1 = _model_id()
        m2 = _model_id()
        assert m1.fingerprint == m2.fingerprint

    def test_fingerprint_differs_on_different_artifact(self):
        m1 = _model_id()
        m2 = _model_id(model_artifact_digest=_digest_of({"weights": "v2"}))
        assert m1.fingerprint != m2.fingerprint


# ── CapabilityEvidence ──────────────────────────────────────────────────────


class TestCapabilityEvidence:
    def test_valid_evidence(self):
        ev = _cap_evidence()
        assert ev.passed_cases == 25
        assert ev.collection_ok is True

    def test_invalid_profile_id_raises(self):
        with pytest.raises(ValueError):
            _cap_evidence(model_profile_id="")

    def test_passed_gt_total_raises(self):
        with pytest.raises(ValueError, match="passed_cases must be between"):
            _cap_evidence(passed_cases=30, total_cases=25)

    def test_passed_negative_raises(self):
        with pytest.raises(ValueError):
            _cap_evidence(passed_cases=-1)

    def test_total_zero_raises(self):
        with pytest.raises(ValueError):
            _cap_evidence(total_cases=0)

    def test_collection_ok_not_bool_raises(self):
        with pytest.raises(ValueError, match="collection_ok must be a boolean"):
            _cap_evidence(collection_ok="yes")

    def test_local_only_not_bool_raises(self):
        with pytest.raises(ValueError, match="local_only must be a boolean"):
            _cap_evidence(local_only="yes")


# ── DispatchDecision ────────────────────────────────────────────────────────


class TestDispatchDecision:
    def test_local_is_approved(self):
        d = DispatchDecision(DispatchAction.LOCAL, _D, "ok", 2)
        assert d.approved is True

    def test_escalate_is_not_approved(self):
        d = DispatchDecision(DispatchAction.ESCALATE, _D, "nope", 0)
        assert d.approved is False


# ── CompletionEvidence ──────────────────────────────────────────────────────


class TestCompletionEvidence:
    def test_valid_evidence(self):
        t = _task_spec()
        ev = _completion_evidence(t)
        assert ev.attempt == 1
        assert ev.collection_ok is True

    def test_invalid_fingerprint_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _completion_evidence(task_fingerprint="bad")

    def test_attempt_zero_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            _completion_evidence(attempt=0)

    def test_empty_acceptance_results_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _completion_evidence(acceptance_results={})

    def test_non_bool_acceptance_values_raises(self):
        with pytest.raises(ValueError, match="values must be booleans"):
            _completion_evidence(acceptance_results={"chk": 1})

    def test_acceptance_results_immutable(self):
        ev = _completion_evidence(acceptance_results={"chk_a": True})
        with pytest.raises(TypeError):
            ev.acceptance_results["new"] = True


# ── SmallModelTaskPolicy.authorize ──────────────────────────────────────────


class TestPolicyAuthorizeEscalations:
    def test_unknown_task_kind_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="unknown_kind")
        d = p.authorize(t, _model_id(), [_cap_evidence(task_kind="unknown_kind")])
        assert d.action == DispatchAction.ESCALATE
        assert "task_kind_not_proven_safe" in d.reason

    def test_forbidden_impacts_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(impacts=frozenset({TaskImpact.MUTATE_REPOSITORY}))
        d = p.authorize(t, _model_id(), [])
        assert d.reason == "impact_requires_stronger_model"
        assert d.action == DispatchAction.ESCALATE

    def test_role_not_allowed_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding", role=TaskRole.ENUMERATOR)
        d = p.authorize(t, _model_id(), [_cap_evidence(role=TaskRole.ENUMERATOR, task_kind="coding")])
        assert d.reason == "role_not_allowed_for_task"

    def test_impact_not_allowed_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding", impacts=frozenset({TaskImpact.MUTATE_REPOSITORY, TaskImpact.WRITE_ARTIFACT}))
        d = p.authorize(t, _model_id(), [])
        assert d.reason == "impact_requires_stronger_model"

    def test_incomplete_acceptance_checks_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding", acceptance_checks=("syntax_valid",))
        d = p.authorize(t, _model_id(), [_cap_evidence(task_kind="coding")])
        assert d.reason == "acceptance_contract_incomplete"

    def test_capability_evidence_missing(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        d = p.authorize(t, _model_id(), [])
        assert d.reason == "capability_evidence_missing"
        assert d.action == DispatchAction.ESCALATE

    def test_collection_ok_false_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(
            task_kind="coding", collection_ok=False, acceptance_contract_digest=t.acceptance_contract_digest
        )
        d = p.authorize(t, _model_id(), [ev])
        assert d.reason == "evaluation_collection_failed"

    def test_not_local_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(
            task_kind="coding", local_only=False, acceptance_contract_digest=t.acceptance_contract_digest
        )
        d = p.authorize(t, _model_id(), [ev])
        assert d.reason == "evaluation_not_local"

    def test_suite_too_small_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(
            task_kind="coding", total_cases=15, passed_cases=15, acceptance_contract_digest=t.acceptance_contract_digest
        )
        d = p.authorize(t, _model_id(), [ev])
        assert d.reason == "evaluation_suite_too_small"

    def test_suite_failed_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(
            task_kind="coding", total_cases=25, passed_cases=23, acceptance_contract_digest=t.acceptance_contract_digest
        )
        d = p.authorize(t, _model_id(), [ev])
        assert d.reason == "evaluation_suite_failed"


class TestPolicyAuthorizeSuccess:
    def test_capability_proven_local(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(model=model, task_kind="coding", acceptance_contract_digest=t.acceptance_contract_digest)
        d = p.authorize(t, model, [ev])
        assert d.action == DispatchAction.LOCAL
        assert d.reason == "capability_proven"
        assert d.max_attempts == 2
        assert d.task_fingerprint == t.fingerprint

    def test_duplicate_task_id_escalates(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(model=model, task_kind="coding", acceptance_contract_digest=t.acceptance_contract_digest)
        p.authorize(t, model, [ev])
        d2 = p.authorize(t, model, [ev])
        assert d2.reason == "duplicate_task_claim"
        assert d2.action == DispatchAction.ESCALATE

    def test_multiple_evidence_picks_first_passing(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev_bad = _cap_evidence(
            model=model,
            task_kind="coding",
            collection_ok=False,
            acceptance_contract_digest=t.acceptance_contract_digest,
        )
        ev_good = _cap_evidence(
            model=model, task_kind="coding", acceptance_contract_digest=t.acceptance_contract_digest
        )
        d = p.authorize(t, model, [ev_bad, ev_good])
        assert d.action == DispatchAction.LOCAL
        assert d.reason == "capability_proven"

    def test_mismatched_model_profile_evidence_ignored(self):
        p = SmallModelTaskPolicy()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(model_profile_id="p/other", task_kind="coding")
        d = p.authorize(t, _model_id(), [ev])
        assert d.reason == "capability_evidence_missing"

    def test_mismatched_model_identity_digest_ignored(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(model=model, model_identity_digest="c" * 64, task_kind="coding")
        d = p.authorize(t, model, [ev])
        assert d.reason == "capability_evidence_missing"

    def test_acceptance_checks_superset_ok(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(
            task_kind="coding", acceptance_checks=("syntax_valid", "import_ok", "run_without_crash", "extra_check")
        )
        ev = _cap_evidence(model=model, task_kind="coding", acceptance_contract_digest=t.acceptance_contract_digest)
        d = p.authorize(t, model, [ev])
        assert d.action == DispatchAction.LOCAL

    def test_custom_config_max_attempts(self):
        config = PolicyConfig(max_attempts=3)
        p = SmallModelTaskPolicy(config=config)
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(
            model=model,
            task_kind="coding",
            acceptance_contract_digest=t.acceptance_contract_digest,
            total_cases=30,
            passed_cases=30,
        )
        d = p.authorize(t, model, [ev])
        assert d.max_attempts == 3

    def test_default_task_contracts_all_valid(self):
        for key, contract in DEFAULT_TASK_CONTRACTS.items():
            assert key == contract.task_kind
            assert contract.allowed_impacts == _BOUNDED_IMPACTS


# ── SmallModelTaskPolicy.record_completion ──────────────────────────────────


class TestPolicyRecordCompletion:
    @pytest.fixture
    def authorized_policy(self):
        p = SmallModelTaskPolicy()
        model = _model_id()
        t = _task_spec(task_kind="coding")
        ev = _cap_evidence(model=model, task_kind="coding", acceptance_contract_digest=t.acceptance_contract_digest)
        p.authorize(t, model, [ev])
        return p, t

    def test_not_authorized_escalates(self):
        p = SmallModelTaskPolicy()
        t = _task_spec()
        d = p.record_completion(_completion_evidence(t))
        assert d.action == CompletionAction.ESCALATE
        assert d.reason == "task_not_authorized"
        assert d.attempts_used == 0

    def test_accept_on_complete_evidence(self, authorized_policy):
        p, t = authorized_policy
        d = p.record_completion(_completion_evidence(t, attempt=1))
        assert d.action == CompletionAction.ACCEPT
        assert d.reason == "acceptance_evidence_complete"
        assert d.attempts_used == 1

    def test_retry_on_failed_evidence(self, authorized_policy):
        p, t = authorized_policy
        ev = _completion_evidence(
            t, attempt=1, acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True}
        )
        d = p.record_completion(ev)
        assert d.action == CompletionAction.RETRY
        assert d.reason == "acceptance_evidence_failed"

    def test_retry_budget_exhausted(self, authorized_policy):
        p, t = authorized_policy
        p.record_completion(
            _completion_evidence(
                t, attempt=1, acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True}
            )
        )
        d = p.record_completion(
            _completion_evidence(
                t, attempt=2, acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True}
            )
        )
        assert d.action == CompletionAction.ESCALATE
        assert d.reason == "retry_budget_exhausted"

    def test_duplicate_completion_evidence(self, authorized_policy):
        p, t = authorized_policy
        ev = _completion_evidence(
            t, attempt=1, acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True}
        )
        p.record_completion(ev)
        d2 = p.record_completion(ev)
        assert d2.reason == "duplicate_completion_evidence"

    def test_already_completed_escalates(self, authorized_policy):
        p, t = authorized_policy
        p.record_completion(_completion_evidence(t, attempt=1))
        d = p.record_completion(_completion_evidence(t, attempt=2))
        assert d.reason == "task_already_completed"
        assert d.action == CompletionAction.ESCALATE

    def test_out_of_sequence_escalates(self, authorized_policy):
        p, t = authorized_policy
        d = p.record_completion(_completion_evidence(t, attempt=5))
        assert d.reason == "attempt_out_of_sequence"
        assert d.action == CompletionAction.ESCALATE

    def test_missing_acceptance_check_rejected(self, authorized_policy):
        p, t = authorized_policy
        ev = _completion_evidence(t, attempt=1, acceptance_results={"syntax_valid": True})
        d = p.record_completion(ev)
        assert d.action == CompletionAction.RETRY

    def test_collection_ok_false_fails(self, authorized_policy):
        p, t = authorized_policy
        ev = _completion_evidence(t, attempt=1, collection_ok=False)
        d = p.record_completion(ev)
        assert d.action == CompletionAction.RETRY

    def test_retry_then_success(self, authorized_policy):
        p, t = authorized_policy
        p.record_completion(
            _completion_evidence(
                t, attempt=1, acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True}
            )
        )
        d2 = p.record_completion(_completion_evidence(t, attempt=2))
        assert d2.action == CompletionAction.ACCEPT

    def test_accept_writes_evidence_digest(self, authorized_policy):
        p, t = authorized_policy
        ev = _completion_evidence(t, attempt=1)
        p.record_completion(ev)
        assert p._claims[t.fingerprint].accepted_evidence_digest == ev.evidence_digest


# ── ModelIdentity.invalid_runtime_config ────────────────────────────────────


class TestModelIdentityEdgeCases:
    def test_invalid_runtime_config_digest_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _model_id(runtime_config_digest="short")

    def test_invalid_prompt_contract_digest_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _model_id(prompt_contract_digest="short")


# ── CompletionAction / DispatchAction enums ─────────────────────────────────


class TestEnums:
    def test_completion_action_values(self):
        assert CompletionAction.ACCEPT == "accept"
        assert CompletionAction.RETRY == "retry"
        assert CompletionAction.ESCALATE == "escalate"

    def test_dispatch_action_values(self):
        assert DispatchAction.LOCAL == "local"
        assert DispatchAction.ESCALATE == "escalate"


# ── PolicyConfig with custom contracts ──────────────────────────────────────


class TestPolicyCustomContracts:
    def test_contract_keys_must_match_task_kind(self):
        bad_contract = TaskContract(
            task_kind="coding",
            allowed_roles=frozenset({TaskRole.CODER}),
            allowed_impacts=_BOUNDED_IMPACTS,
            required_acceptance_checks=frozenset({"chk"}),
        )
        with pytest.raises(ValueError, match="contract keys must match"):
            SmallModelTaskPolicy(contracts={"mismatched_key": bad_contract})

    def test_custom_contract_allows_authorization(self):
        custom = TaskContract(
            task_kind="custom_task",
            allowed_roles=frozenset({TaskRole.REVIEWER}),
            allowed_impacts=_BOUNDED_IMPACTS,
            required_acceptance_checks=frozenset({"custom_check"}),
        )
        p = SmallModelTaskPolicy(contracts={"custom_task": custom})
        model = _model_id()
        t = _task_spec(task_kind="custom_task", role=TaskRole.REVIEWER, acceptance_checks=("custom_check",))
        ev = _cap_evidence(
            model=model,
            task_kind="custom_task",
            role=TaskRole.REVIEWER,
            acceptance_contract_digest=t.acceptance_contract_digest,
        )
        d = p.authorize(t, model, [ev])
        assert d.action == DispatchAction.LOCAL


# ── CapabilityEvidence edge cases ───────────────────────────────────────────


class TestCapabilityEvidenceEdgeCases:
    def test_passed_equals_total_at_boundary(self):
        ev = _cap_evidence(total_cases=1, passed_cases=1)
        assert ev.passed_cases == 1
        assert ev.total_cases == 1

    def test_invalid_suite_id_raises(self):
        with pytest.raises(ValueError):
            _cap_evidence(suite_id="")

    def test_invalid_evidence_digest_raises(self):
        with pytest.raises(ValueError):
            _cap_evidence(evidence_digest="bad")


# ── SmallModelTaskSpec edge cases ───────────────────────────────────────────


class TestSmallModelTaskSpecEdgeCases:
    def test_acceptance_contract_digest_stable_across_instances(self):
        s1 = _task_spec(task_id="x", acceptance_checks=("a", "b", "c"))
        s2 = _task_spec(task_id="y", acceptance_checks=("b", "c", "a"))
        assert s1.acceptance_contract_digest == s2.acceptance_contract_digest

    def test_fingerprint_changes_when_impacts_differ(self):
        s1 = _task_spec(impacts=frozenset({TaskImpact.READ_SOURCE}))
        s2 = _task_spec(impacts=frozenset({TaskImpact.WRITE_ARTIFACT}))
        assert s1.fingerprint != s2.fingerprint


# ── _stable_digest (private helper) ─────────────────────────────────────────


class TestStableDigest:
    def test_same_payload_same_digest(self):
        a = _stable_digest({"x": 1, "y": 2})
        b = _stable_digest({"x": 1, "y": 2})
        assert a == b

    def test_different_payload_different_digest(self):
        a = _stable_digest({"x": 1})
        b = _stable_digest({"x": 2})
        assert a != b

    def test_key_order_independent(self):
        a = _stable_digest({"a": 1, "b": 2})
        b = _stable_digest({"b": 2, "a": 1})
        assert a == b

    def test_nested_structures(self):
        a = _stable_digest({"parent": {"child": [1, 2, 3]}})
        b = _stable_digest({"parent": {"child": [1, 2, 3]}})
        assert a == b

    def test_returns_64_char_hex(self):
        d = _stable_digest({"key": "val"})
        assert len(d) == 64
        assert re.fullmatch(r"^[0-9a-f]{64}$", d)

    def test_empty_payload(self):
        d = _stable_digest({})
        assert len(d) == 64

    def test_bool_true_value(self):
        a = _stable_digest({"flag": True})
        b = _stable_digest({"flag": True})
        assert a == b

    def test_null_value(self):
        a = _stable_digest({"key": None})
        b = _stable_digest({"key": None})
        assert a == b

    def test_int_and_string_distinct(self):
        a = _stable_digest({"v": "1"})
        b = _stable_digest({"v": 1})
        assert a != b

    def test_list_vs_tuple_identical_json(self):
        a = _stable_digest({"items": [1, 2, 3]})
        b = _stable_digest({"items": (1, 2, 3)})
        assert a == b


# ── _require_pattern / _require_digest / _validate_checks (private) ─────────


class TestRequirePattern:
    def test_matching_value_passes(self):
        _require_pattern("tag", "abc123", _CHECK_RE)

    def test_non_matching_value_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("tag", "HAS_UPPER", _CHECK_RE)

    def test_empty_value_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("tag", "", _CHECK_RE)

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("tag", 42, _CHECK_RE)

    def test_none_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("tag", None, _CHECK_RE)

    def test_sha256_pattern_accepts_valid_digest(self):
        valid = "a" * 64
        _require_pattern("digest", valid, _SHA256_RE)

    def test_sha256_pattern_rejects_uppercase(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("digest", "A" * 64, _SHA256_RE)

    def test_sha256_pattern_rejects_short(self):
        with pytest.raises(ValueError, match="invalid format"):
            _require_pattern("digest", "a" * 63, _SHA256_RE)


class TestRequireDigest:
    def test_valid_sha256_passes(self):
        _require_digest("digest", "a" * 64)

    def test_invalid_digest_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _require_digest("digest", "short")

    def test_uppercase_sha256_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _require_digest("digest", "A" * 64)

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _require_digest("digest", "a" * 65)

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _require_digest("digest", 123)

    def test_none_raises(self):
        with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
            _require_digest("digest", None)


class TestValidateChecks:
    def test_single_check_passes(self):
        _validate_checks(["syntax_valid"])

    def test_multiple_checks_pass(self):
        _validate_checks(["syntax_valid", "import_ok", "run_without_crash"])

    def test_empty_sequence_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_checks([])

    def test_empty_tuple_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_checks(())

    def test_invalid_check_name_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _validate_checks(["ValidCheck"])

    def test_check_with_special_chars_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _validate_checks(["check@bad"])

    def test_numeric_start_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _validate_checks(["9invalid"])

    def test_empty_check_name_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _validate_checks([""])

    def test_frozenset_works(self):
        _validate_checks(frozenset(["chk_a", "chk_b"]))


# ── _contract (private helper) ──────────────────────────────────────────────


class TestContractHelper:
    def test_creates_valid_contract(self):
        c = _contract("coding", TaskRole.CODER, "syntax_valid", "import_ok")
        assert c.task_kind == "coding"
        assert c.allowed_roles == frozenset({TaskRole.CODER})
        assert c.allowed_impacts == _BOUNDED_IMPACTS
        assert c.required_acceptance_checks == frozenset({"syntax_valid", "import_ok"})

    def test_always_uses_bounded_impacts(self):
        c = _contract("documentation_draft", TaskRole.EDITOR, "facts_traceable")
        assert c.allowed_impacts & FORBIDDEN_IMPACTS == frozenset()

    def test_single_role(self):
        c = _contract("format_normalization", TaskRole.EDITOR, "idempotent")
        assert c.allowed_roles == frozenset({TaskRole.EDITOR})

    def test_no_checks(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _contract("coding", TaskRole.CODER)

    def test_invalid_task_kind_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            _contract("99bad", TaskRole.CODER, "chk")

    def test_bounded_impacts_invariant(self):
        for kind in DEFAULT_TASK_CONTRACTS:
            contract = DEFAULT_TASK_CONTRACTS[kind]
            assert contract.allowed_impacts == _BOUNDED_IMPACTS
            assert contract.allowed_impacts & FORBIDDEN_IMPACTS == frozenset()
