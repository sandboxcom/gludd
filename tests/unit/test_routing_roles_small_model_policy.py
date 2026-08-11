"""Deep behavioral tests for small_model_policy.py — the shallow-tested module.

Covers: TaskContract, PolicyConfig, SmallModelTaskSpec, ModelIdentity,
CapabilityEvidence, CompletionEvidence, SmallModelTaskPolicy.authorize(),
record_completion(), DispatchDecision, CompletionDecision, enums, and validation.
"""

from __future__ import annotations

import pytest

from general_ludd.routing_roles.small_model_policy import (
    _BOUNDED_IMPACTS,
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
    _stable_digest,
)
from general_ludd.schemas.benchmark import TaskRole

# ── helpers ────────────────────────────────────────────────────────────────


def _sha256(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode()).hexdigest()


_MODEL_PROFILE = "test.model_v1"
_ARTIFACT = _sha256("artifact")
_RUNTIME = _sha256("runtime")
_PROMPT = _sha256("prompt")

_MODEL = ModelIdentity(
    model_profile_id=_MODEL_PROFILE,
    model_artifact_digest=_ARTIFACT,
    runtime_config_digest=_RUNTIME,
    prompt_contract_digest=_PROMPT,
)

_DIGEST_FOR = _sha256("input")


def _spec(
    task_id: str = "test.task_v1.001",
    task_kind: str = "coding",
    role: TaskRole = TaskRole.CODER,
    collection: str = "test.collection",
    input_digest: str = "",
    impacts: frozenset[TaskImpact] | None = None,
    checks: tuple[str, ...] | None = None,
) -> SmallModelTaskSpec:
    return SmallModelTaskSpec(
        task_id=task_id,
        task_kind=task_kind,
        role=role,
        collection=collection,
        input_digest=input_digest or _DIGEST_FOR,
        impacts=impacts or frozenset({TaskImpact.WRITE_ARTIFACT}),
        acceptance_checks=checks or ("syntax_valid", "import_ok", "run_without_crash"),
    )


def _evidence(
    model_profile_id: str = _MODEL_PROFILE,
    model_identity_digest: str = "",
    task_kind: str = "coding",
    role: TaskRole = TaskRole.CODER,
    collection: str = "test.collection",
    acceptance_contract_digest: str = "",
    passed: int = 50,
    total: int = 50,
    collection_ok: bool = True,
    local_only: bool = True,
) -> CapabilityEvidence:
    if not model_identity_digest:
        model_identity_digest = _MODEL.fingerprint
    if not acceptance_contract_digest:
        task = _spec()
        acceptance_contract_digest = task.acceptance_contract_digest
    return CapabilityEvidence(
        model_profile_id=model_profile_id,
        model_identity_digest=model_identity_digest,
        task_kind=task_kind,
        role=role,
        collection=collection,
        suite_id="suite.test_v1",
        suite_revision="rev.001",
        acceptance_contract_digest=acceptance_contract_digest,
        passed_cases=passed,
        total_cases=total,
        collection_ok=collection_ok,
        local_only=local_only,
        evidence_digest=_sha256("evidence"),
    )


# ── enums ──────────────────────────────────────────────────────────────────


class TestTaskImpact:
    def test_members(self):
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
        assert {m.name for m in TaskImpact} == expected

    def test_forbidden_impacts_are_subset(self):
        assert frozenset(TaskImpact) >= FORBIDDEN_IMPACTS

    def test_bound_impacts_are_safe_only(self):
        assert frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}) == _BOUNDED_IMPACTS

    def test_forbidden_and_bounded_disjoint(self):
        assert frozenset() == FORBIDDEN_IMPACTS & _BOUNDED_IMPACTS


class TestDispatchAction:
    def test_values(self):
        assert DispatchAction.LOCAL == "local"
        assert DispatchAction.ESCALATE == "escalate"


class TestCompletionAction:
    def test_values(self):
        assert CompletionAction.ACCEPT == "accept"
        assert CompletionAction.RETRY == "retry"
        assert CompletionAction.ESCALATE == "escalate"


# ── PolicyConfig ────────────────────────────────────────────────────────────


class TestPolicyConfig:
    def test_defaults(self):
        c = PolicyConfig()
        assert c.max_attempts == 2
        assert c.min_evaluation_cases == 20

    def test_valid_custom(self):
        c = PolicyConfig(max_attempts=3, min_evaluation_cases=1)
        assert c.max_attempts == 3
        assert c.min_evaluation_cases == 1

    def test_max_attempts_out_of_range(self):
        for bad in (0, 4):
            with pytest.raises(ValueError, match="max_attempts"):
                PolicyConfig(max_attempts=bad)

    def test_min_evaluation_cases_out_of_range(self):
        with pytest.raises(ValueError, match="min_evaluation_cases"):
            PolicyConfig(min_evaluation_cases=0)
        with pytest.raises(ValueError, match="min_evaluation_cases"):
            PolicyConfig(min_evaluation_cases=10001)

    def test_bool_rejected_for_max_attempts(self):
        with pytest.raises(ValueError):
            PolicyConfig(max_attempts=True)

    def test_bool_rejected_for_min_cases(self):
        with pytest.raises(ValueError):
            PolicyConfig(min_evaluation_cases=True)


# ── TaskContract ───────────────────────────────────────────────────────────


class TestTaskContract:
    def test_valid_contract(self):
        tc = TaskContract(
            task_kind="coding",
            allowed_roles=frozenset({TaskRole.CODER}),
            allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
            required_acceptance_checks=frozenset({"syntax_valid"}),
        )
        assert tc.task_kind == "coding"

    def test_invalid_task_kind_format(self):
        with pytest.raises(ValueError, match="task_kind"):
            TaskContract(
                task_kind="Bad_Caps",
                allowed_roles=frozenset({TaskRole.CODER}),
                allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                required_acceptance_checks=frozenset({"a"}),
            )

    def test_empty_allowed_roles_raises(self):
        with pytest.raises(ValueError, match="allowed_roles"):
            TaskContract(
                task_kind="coding",
                allowed_roles=frozenset(),
                allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                required_acceptance_checks=frozenset({"a"}),
            )

    def test_empty_allowed_impacts_raises(self):
        with pytest.raises(ValueError, match="allowed_impacts"):
            TaskContract(
                task_kind="coding",
                allowed_roles=frozenset({TaskRole.CODER}),
                allowed_impacts=frozenset(),
                required_acceptance_checks=frozenset({"a"}),
            )

    def test_forbidden_impact_rejected(self):
        with pytest.raises(ValueError, match="high-impact"):
            TaskContract(
                task_kind="coding",
                allowed_roles=frozenset({TaskRole.CODER}),
                allowed_impacts=frozenset({TaskImpact.RELEASE}),
                required_acceptance_checks=frozenset({"a"}),
            )

    def test_empty_checks_raises(self):
        with pytest.raises(ValueError, match="not be empty"):
            TaskContract(
                task_kind="coding",
                allowed_roles=frozenset({TaskRole.CODER}),
                allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                required_acceptance_checks=frozenset(),
            )

    def test_default_contracts_coverage(self):
        assert "coding" in DEFAULT_TASK_CONTRACTS
        assert "bounded_enumeration" in DEFAULT_TASK_CONTRACTS
        assert "context_compaction" in DEFAULT_TASK_CONTRACTS

    def test_default_contracts_keys_match_task_kind(self):
        for key, contract in DEFAULT_TASK_CONTRACTS.items():
            assert key == contract.task_kind

    def test_default_contracts_all_roles_in_taskrole(self):
        for contract in DEFAULT_TASK_CONTRACTS.values():
            for role in contract.allowed_roles:
                assert role in TaskRole


# ── SmallModelTaskSpec ─────────────────────────────────────────────────────


class TestSmallModelTaskSpec:
    def test_valid_spec(self):
        spec = _spec()
        assert spec.task_id == "test.task_v1.001"
        assert spec.acceptance_checks == ("syntax_valid", "import_ok", "run_without_crash")

    def test_invalid_task_id(self):
        with pytest.raises(ValueError, match="task_id"):
            _spec(task_id="")

    def test_duplicate_checks_raise(self):
        with pytest.raises(ValueError, match="duplicate"):
            _spec(checks=("a", "a"))

    def test_empty_impacts_raises(self):
        with pytest.raises(ValueError, match="impacts must not be empty"):
            SmallModelTaskSpec(
                task_id="test.one",
                task_kind="coding",
                role=TaskRole.CODER,
                collection="test.col",
                input_digest=_DIGEST_FOR,
                impacts=frozenset(),
                acceptance_checks=("syntax_valid",),
            )
        assert True

    def test_acceptance_contract_digest_is_stable(self):
        s1 = _spec()
        s2 = _spec()
        assert s1.acceptance_contract_digest == s2.acceptance_contract_digest

    def test_acceptance_contract_digest_changes_with_role(self):
        s1 = _spec(role=TaskRole.CODER)
        s2 = _spec(role=TaskRole.EDITOR)
        assert s1.acceptance_contract_digest != s2.acceptance_contract_digest

    def test_fingerprint_is_stable(self):
        s1 = _spec()
        s2 = _spec()
        assert s1.fingerprint == s2.fingerprint

    def test_fingerprint_changes_with_input_digest(self):
        s1 = _spec(input_digest=_sha256("a"))
        s2 = _spec(input_digest=_sha256("b"))
        assert s1.fingerprint != s2.fingerprint

    def test_fingerprint_changes_with_impacts(self):
        s1 = _spec(impacts=frozenset({TaskImpact.READ_SOURCE}))
        s2 = _spec(impacts=frozenset({TaskImpact.WRITE_ARTIFACT}))
        assert s1.fingerprint != s2.fingerprint

    def test_checks_not_a_tuple_raises(self):
        with pytest.raises(ValueError, match="immutable tuple"):
            SmallModelTaskSpec(
                task_id="a.b_c-1",
                task_kind="coding",
                role=TaskRole.CODER,
                collection="a.b",
                input_digest=_DIGEST_FOR,
                impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                acceptance_checks=["a"],  # list, not tuple
            )

    def test_invalid_check_name_raises(self):
        with pytest.raises(ValueError, match="acceptance check"):
            _spec(checks=("INVALID!",))

    def test_invalid_collection_raises(self):
        with pytest.raises(ValueError, match="collection"):
            _spec(collection="no_dot")

    def test_invalid_task_kind_raises(self):
        with pytest.raises(ValueError, match="task_kind"):
            _spec(task_kind="Bad_Case")


# ── ModelIdentity ──────────────────────────────────────────────────────────


class TestModelIdentity:
    def test_valid_identity(self):
        assert _MODEL.model_profile_id == _MODEL_PROFILE
        assert _MODEL.fingerprint is not None

    def test_invalid_digest_raises(self):
        with pytest.raises(ValueError, match="model_artifact_digest"):
            ModelIdentity(
                model_profile_id=_MODEL_PROFILE,
                model_artifact_digest="not-a-sha256",
                runtime_config_digest=_RUNTIME,
                prompt_contract_digest=_PROMPT,
            )

    def test_fingerprint_is_stable(self):
        m1 = ModelIdentity(
            model_profile_id="a.b-1",
            model_artifact_digest=_sha256("a"),
            runtime_config_digest=_sha256("b"),
            prompt_contract_digest=_sha256("c"),
        )
        m2 = ModelIdentity(
            model_profile_id="a.b-1",
            model_artifact_digest=_sha256("a"),
            runtime_config_digest=_sha256("b"),
            prompt_contract_digest=_sha256("c"),
        )
        assert m1.fingerprint == m2.fingerprint

    def test_fingerprint_changes_with_artifact(self):
        m1 = ModelIdentity(
            model_profile_id="a.b-1",
            model_artifact_digest=_sha256("a"),
            runtime_config_digest=_sha256("b"),
            prompt_contract_digest=_sha256("c"),
        )
        m2 = ModelIdentity(
            model_profile_id="a.b-1",
            model_artifact_digest=_sha256("x"),
            runtime_config_digest=_sha256("b"),
            prompt_contract_digest=_sha256("c"),
        )
        assert m1.fingerprint != m2.fingerprint


# ── CapabilityEvidence ─────────────────────────────────────────────────────


class TestCapabilityEvidence:
    def test_valid_evidence(self):
        ev = _evidence()
        assert ev.passed_cases == 50
        assert ev.total_cases == 50

    def test_passed_cases_above_total_raises(self):
        with pytest.raises(ValueError, match="passed_cases"):
            _evidence(passed=51, total=50)

    def test_total_cases_zero_raises(self):
        with pytest.raises(ValueError, match="total_cases"):
            _evidence(total=0)

    def test_collection_ok_not_bool_raises(self):
        with pytest.raises(ValueError, match="collection_ok"):
            CapabilityEvidence(
                model_profile_id=_MODEL_PROFILE,
                model_identity_digest=_MODEL.fingerprint,
                task_kind="coding",
                role=TaskRole.CODER,
                collection="test.collection",
                suite_id="suite.test_v1",
                suite_revision="rev.001",
                acceptance_contract_digest=_spec().acceptance_contract_digest,
                passed_cases=1,
                total_cases=1,
                collection_ok=1,  # not bool
                local_only=True,
                evidence_digest=_sha256("e"),
            )

    def test_local_only_not_bool_raises(self):
        with pytest.raises(ValueError, match="local_only"):
            CapabilityEvidence(
                model_profile_id=_MODEL_PROFILE,
                model_identity_digest=_MODEL.fingerprint,
                task_kind="coding",
                role=TaskRole.CODER,
                collection="test.collection",
                suite_id="suite.test_v1",
                suite_revision="rev.001",
                acceptance_contract_digest=_spec().acceptance_contract_digest,
                passed_cases=1,
                total_cases=1,
                collection_ok=True,
                local_only=0,  # not bool
                evidence_digest=_sha256("e"),
            )


# ── CompletionEvidence ─────────────────────────────────────────────────────


class TestCompletionEvidence:
    def test_valid_completion_evidence(self):
        ce = CompletionEvidence(
            task_fingerprint=_spec().fingerprint,
            attempt=1,
            artifact_digest=_sha256("artifact"),
            acceptance_results={"syntax_valid": True, "import_ok": True, "run_without_crash": True},
            collection_ok=True,
            evidence_digest=_sha256("evidence"),
        )
        assert ce.attempt == 1
        assert ce.collection_ok is True

    def test_attempt_zero_raises(self):
        with pytest.raises(ValueError, match="attempt"):
            CompletionEvidence(
                task_fingerprint=_spec().fingerprint,
                attempt=0,
                artifact_digest=_sha256("a"),
                acceptance_results={"syntax_valid": True},
                collection_ok=True,
                evidence_digest=_sha256("e"),
            )

    def test_empty_acceptance_results_raises(self):
        with pytest.raises(ValueError, match="acceptance_results"):
            CompletionEvidence(
                task_fingerprint=_spec().fingerprint,
                attempt=1,
                artifact_digest=_sha256("a"),
                acceptance_results={},
                collection_ok=True,
                evidence_digest=_sha256("e"),
            )

    def test_non_bool_values_raises(self):
        with pytest.raises(ValueError, match="booleans"):
            CompletionEvidence(
                task_fingerprint=_spec().fingerprint,
                attempt=1,
                artifact_digest=_sha256("a"),
                acceptance_results={"syntax_valid": "yes"},  # not bool
                collection_ok=True,
                evidence_digest=_sha256("e"),
            )

    def test_acceptance_results_becomes_mappingproxy(self):
        ce = CompletionEvidence(
            task_fingerprint=_spec().fingerprint,
            attempt=1,
            artifact_digest=_sha256("a"),
            acceptance_results={"syntax_valid": True},
            collection_ok=True,
            evidence_digest=_sha256("e"),
        )
        from types import MappingProxyType

        assert isinstance(ce.acceptance_results, MappingProxyType)

    def test_collection_ok_not_bool_raises(self):
        with pytest.raises(ValueError, match="collection_ok"):
            CompletionEvidence(
                task_fingerprint=_spec().fingerprint,
                attempt=1,
                artifact_digest=_sha256("a"),
                acceptance_results={"syntax_valid": True},
                collection_ok="yes",  # not bool
                evidence_digest=_sha256("e"),
            )


# ── SmallModelTaskPolicy.authorize() — success path ────────────────────────


class TestAuthorizeSuccess:
    def test_local_approval_with_valid_evidence(self):
        policy = SmallModelTaskPolicy()
        task = _spec()
        evidence = _evidence()
        decision = policy.authorize(task, _MODEL, [evidence])
        assert decision.action is DispatchAction.LOCAL
        assert decision.approved
        assert decision.reason == "capability_proven"
        assert decision.max_attempts == 2
        assert decision.task_fingerprint == task.fingerprint


# ── authorize() — escalation paths ────────────────────────────────────────


class TestAuthorizeEscalation:
    @pytest.fixture(autouse=True)
    def _fresh_policy(self):
        self.policy = SmallModelTaskPolicy()

    def _escalate(self, task, evidence_list, reason_fragment):
        decision = self.policy.authorize(task, _MODEL, evidence_list)
        assert decision.action is DispatchAction.ESCALATE
        assert decision.reason == reason_fragment
        assert not decision.approved
        assert decision.max_attempts == 0

    def test_unknown_task_kind(self):
        self._escalate(
            _spec(task_kind="nonexistent"),
            [_evidence()],
            "task_kind_not_proven_safe",
        )

    def test_forbidden_impact(self):
        self._escalate(
            _spec(impacts=frozenset({TaskImpact.RELEASE})),
            [_evidence()],
            "impact_requires_stronger_model",
        )

    def test_role_not_allowed(self):
        self._escalate(
            _spec(task_kind="coding", role=TaskRole.REVIEWER),
            [_evidence(task_kind="coding", role=TaskRole.REVIEWER)],
            "role_not_allowed_for_task",
        )

    def test_impact_not_allowed(self):
        narrow = TaskContract(
            task_kind="narrow",
            allowed_roles=frozenset({TaskRole.CODER}),
            allowed_impacts=frozenset({TaskImpact.READ_SOURCE}),
            required_acceptance_checks=frozenset({"syntax_valid"}),
        )
        p = SmallModelTaskPolicy(contracts={"narrow": narrow})
        t = SmallModelTaskSpec(
            task_id="test.narrow.1",
            task_kind="narrow",
            role=TaskRole.CODER,
            collection="test.col",
            input_digest=_DIGEST_FOR,
            impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
            acceptance_checks=("syntax_valid",),
        )
        ev = _evidence(task_kind="narrow", role=TaskRole.CODER, acceptance_contract_digest=t.acceptance_contract_digest)
        d = p.authorize(t, _MODEL, [ev])
        assert d.action is DispatchAction.ESCALATE
        assert d.reason == "impact_not_allowed_for_task"
        assert not d.approved

    def test_acceptance_contract_incomplete(self):
        self._escalate(
            _spec(task_kind="coding", checks=("syntax_valid",)),
            [_evidence()],
            "acceptance_contract_incomplete",
        )

    def test_duplicate_task_claim(self):
        policy = self.policy
        task = _spec()
        evidence = _evidence()
        d1 = policy.authorize(task, _MODEL, [evidence])
        assert d1.approved
        d2 = policy.authorize(task, _MODEL, [evidence])
        assert not d2.approved
        assert d2.reason == "duplicate_task_claim"

    def test_no_evidence_at_all(self):
        self._escalate(_spec(), [], "capability_evidence_missing")

    def test_evidence_wrong_model_profile(self):
        self._escalate(
            _spec(),
            [_evidence(model_profile_id="other.model_v2")],
            "capability_evidence_missing",
        )

    def test_evidence_wrong_identity_digest(self):
        self._escalate(
            _spec(),
            [_evidence(model_identity_digest=_sha256("wrong"))],
            "capability_evidence_missing",
        )

    def test_evidence_wrong_task_kind(self):
        self._escalate(
            _spec(task_kind="coding"),
            [_evidence(task_kind="format_normalization", role=TaskRole.EDITOR)],
            "capability_evidence_missing",
        )

    def test_evidence_wrong_collection(self):
        self._escalate(
            _spec(collection="test.collection"),
            [_evidence(collection="other.namespace")],
            "capability_evidence_missing",
        )

    def test_evidence_collection_not_ok(self):
        self._escalate(
            _spec(),
            [_evidence(collection_ok=False)],
            "evaluation_collection_failed",
        )

    def test_evidence_not_local(self):
        self._escalate(
            _spec(),
            [_evidence(local_only=False)],
            "evaluation_not_local",
        )

    def test_evidence_suite_too_small(self):
        self._escalate(
            _spec(),
            [_evidence(passed=10, total=10)],
            "evaluation_suite_too_small",
        )

    def test_evidence_suite_failed(self):
        self._escalate(
            _spec(),
            [_evidence(passed=49, total=50)],
            "evaluation_suite_failed",
        )

    def test_multiple_evidence_picks_first_valid(self):
        policy = SmallModelTaskPolicy()
        task = _spec()
        good = _evidence()
        bad_fail = _evidence(passed=40, total=50)
        decision = policy.authorize(task, _MODEL, [bad_fail, good])
        assert decision.approved

    def test_contracts_param_is_used(self):
        custom = {
            "custom_kind": TaskContract(
                task_kind="custom_kind",
                allowed_roles=frozenset({TaskRole.CODER}),
                allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                required_acceptance_checks=frozenset({"a"}),
            )
        }
        policy = SmallModelTaskPolicy(contracts=custom)
        task = SmallModelTaskSpec(
            task_id="test.custom.1",
            task_kind="custom_kind",
            role=TaskRole.CODER,
            collection="test.thing",
            input_digest=_DIGEST_FOR,
            impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
            acceptance_checks=("a",),
        )
        ev = CapabilityEvidence(
            model_profile_id=_MODEL_PROFILE,
            model_identity_digest=_MODEL.fingerprint,
            task_kind="custom_kind",
            role=TaskRole.CODER,
            collection="test.thing",
            suite_id="suite.test_v1",
            suite_revision="rev.001",
            acceptance_contract_digest=task.acceptance_contract_digest,
            passed_cases=50,
            total_cases=50,
            collection_ok=True,
            local_only=True,
            evidence_digest=_sha256("custom-ev"),
        )
        decision = policy.authorize(task, _MODEL, [ev])
        assert decision.approved

    def test_contracts_key_mismatch_raises(self):
        with pytest.raises(ValueError, match="contract keys"):
            SmallModelTaskPolicy(
                contracts={
                    "wrong_key": TaskContract(
                        task_kind="right_key",
                        allowed_roles=frozenset({TaskRole.CODER}),
                        allowed_impacts=frozenset({TaskImpact.WRITE_ARTIFACT}),
                        required_acceptance_checks=frozenset({"a"}),
                    )
                }
            )


# ── SmallModelTaskPolicy.record_completion() ───────────────────────────────


class TestRecordCompletion:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.policy = SmallModelTaskPolicy()
        self.task = _spec()
        self.ev = _evidence()
        self.decision = self.policy.authorize(self.task, _MODEL, [self.ev])
        assert self.decision.approved

    def _complete(self, results, attempt=1, collection_ok=True) -> CompletionEvidence:
        return CompletionEvidence(
            task_fingerprint=self.task.fingerprint,
            attempt=attempt,
            artifact_digest=_sha256(f"artifact{attempt}"),
            acceptance_results=results,
            collection_ok=collection_ok,
            evidence_digest=_sha256(f"evidence{attempt}"),
        )

    def test_accept_on_first_attempt_all_checks_pass(self):
        result = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
                "run_without_crash": True,
            }
        )
        decision = self.policy.record_completion(result)
        assert decision.action is CompletionAction.ACCEPT
        assert decision.reason == "acceptance_evidence_complete"
        assert decision.attempts_used == 1

    def test_retry_on_failure_within_budget(self):
        result = self._complete(
            {
                "syntax_valid": False,
                "import_ok": True,
                "run_without_crash": True,
            }
        )
        decision = self.policy.record_completion(result)
        assert decision.action is CompletionAction.RETRY
        assert decision.attempts_used == 1

    def test_escalate_after_retry_budget_exhausted(self):
        bad = self._complete(
            {
                "syntax_valid": False,
                "import_ok": True,
                "run_without_crash": True,
            },
            attempt=1,
        )
        d1 = self.policy.record_completion(bad)
        assert d1.action is CompletionAction.RETRY

        bad2 = self._complete(
            {
                "syntax_valid": False,
                "import_ok": True,
                "run_without_crash": True,
            },
            attempt=2,
        )
        d2 = self.policy.record_completion(bad2)
        assert d2.action is CompletionAction.ESCALATE
        assert d2.reason == "retry_budget_exhausted"

    def test_task_not_authorized_escalates(self):
        unknown_fingerprint = _sha256("unknown")
        ce = CompletionEvidence(
            task_fingerprint=unknown_fingerprint,
            attempt=1,
            artifact_digest=_sha256("a"),
            acceptance_results={"syntax_valid": True},
            collection_ok=True,
            evidence_digest=_sha256("e"),
        )
        decision = self.policy.record_completion(ce)
        assert decision.action is CompletionAction.ESCALATE
        assert decision.reason == "task_not_authorized"

    def test_duplicate_evidence_replayed(self):
        result1 = self._complete(
            {
                "syntax_valid": False,
                "import_ok": True,
                "run_without_crash": True,
            }
        )
        self.policy.record_completion(result1)
        result2 = CompletionEvidence(
            task_fingerprint=self.task.fingerprint,
            attempt=2,
            artifact_digest=_sha256("a2"),
            acceptance_results={"syntax_valid": False, "import_ok": True, "run_without_crash": True},
            collection_ok=True,
            evidence_digest=result1.evidence_digest,  # same digest
        )
        decision = self.policy.record_completion(result2)
        assert decision.reason == "duplicate_completion_evidence"

    def test_attempt_out_of_sequence(self):
        wrong_attempt = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
                "run_without_crash": True,
            },
            attempt=3,
        )  # should be 1
        decision = self.policy.record_completion(wrong_attempt)
        assert decision.action is CompletionAction.ESCALATE
        assert decision.reason == "attempt_out_of_sequence"

    def test_task_already_completed_escalates(self):
        result = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
                "run_without_crash": True,
            }
        )
        self.policy.record_completion(result)
        second = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
                "run_without_crash": True,
            },
            attempt=2,
        )
        decision = self.policy.record_completion(second)
        assert decision.action is CompletionAction.ESCALATE
        assert decision.reason == "task_already_completed"

    def test_acceptance_results_missing_check_retries(self):
        incomplete = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
            }
        )  # missing run_without_crash
        decision = self.policy.record_completion(incomplete)
        assert decision.action is CompletionAction.RETRY
        assert decision.reason == "acceptance_evidence_failed"
        assert decision.attempts_used == 1

    def test_collection_not_ok_retries(self):
        result = self._complete(
            {
                "syntax_valid": True,
                "import_ok": True,
                "run_without_crash": True,
            },
            collection_ok=False,
        )
        decision = self.policy.record_completion(result)
        assert decision.action is CompletionAction.RETRY
        assert decision.reason == "acceptance_evidence_failed"
        assert decision.attempts_used == 1


# ── DispatchDecision ───────────────────────────────────────────────────────


class TestDispatchDecision:
    def test_local_is_approved(self):
        d = DispatchDecision(DispatchAction.LOCAL, "fp", "ok", 2)
        assert d.approved

    def test_escalate_is_not_approved(self):
        d = DispatchDecision(DispatchAction.ESCALATE, "fp", "blocked", 0)
        assert not d.approved


# ── DEFAULT_TASK_CONTRACTS integrity ───────────────────────────────────────


class TestDefaultContracts:
    def test_all_known_task_kinds_present(self):
        kinds = {
            "bounded_enumeration",
            "coding",
            "context_compaction",
            "documentation_draft",
            "failure_classification",
            "format_normalization",
            "game_logic",
            "schema_extraction",
        }
        assert set(DEFAULT_TASK_CONTRACTS) == kinds

    def test_all_impacts_are_bounded(self):
        for contract in DEFAULT_TASK_CONTRACTS.values():
            assert contract.allowed_impacts == _BOUNDED_IMPACTS

    def test_each_contract_has_checks(self):
        for contract in DEFAULT_TASK_CONTRACTS.values():
            assert len(contract.required_acceptance_checks) > 0

    def test_game_logic_has_lifecycle_checks(self):
        game = DEFAULT_TASK_CONTRACTS["game_logic"]
        required = {c for c in game.required_acceptance_checks}
        assert "lifecycle_initial_state" in required
        assert "lifecycle_start" in required


# ── _stable_digest ─────────────────────────────────────────────────────────


class TestStableDigest:
    def test_same_payload_produces_same_digest(self):
        d1 = _stable_digest({"a": "b", "c": "d"})
        d2 = _stable_digest({"a": "b", "c": "d"})
        assert d1 == d2

    def test_different_payload_produces_different_digest(self):
        d1 = _stable_digest({"a": "b"})
        d2 = _stable_digest({"a": "c"})
        assert d1 != d2

    def test_sorting_is_deterministic(self):
        d1 = _stable_digest({"b": "1", "a": "2"})
        d2 = _stable_digest({"a": "2", "b": "1"})
        assert d1 == d2

    def test_length_is_64_chars(self):
        d = _stable_digest({"key": "value"})
        assert len(d) == 64

    def test_only_hex_chars(self):
        d = _stable_digest({"key": "value"})
        assert all(c in "0123456789abcdef" for c in d)


# ── Deep: two tasks in same policy instance ────────────────────────────────


class TestTwoTasksInOnePolicy:
    def test_two_different_tasks_both_authorized(self):
        policy = SmallModelTaskPolicy()
        task1 = _spec(task_id="test.one", task_kind="coding", role=TaskRole.CODER)
        ev1 = _evidence(
            task_kind="coding", role=TaskRole.CODER, acceptance_contract_digest=task1.acceptance_contract_digest
        )
        d1 = policy.authorize(task1, _MODEL, [ev1])
        assert d1.approved

        task2 = _spec(
            task_id="test.two",
            task_kind="documentation_draft",
            role=TaskRole.EDITOR,
            checks=("facts_traceable", "links_valid", "schema_valid"),
        )
        ev2 = _evidence(
            task_kind="documentation_draft",
            role=TaskRole.EDITOR,
            acceptance_contract_digest=task2.acceptance_contract_digest,
        )
        d2 = policy.authorize(task2, _MODEL, [ev2])
        assert d2.approved

    def test_same_task_id_different_policy_instances_independent(self):
        p1 = SmallModelTaskPolicy()
        p2 = SmallModelTaskPolicy()
        task = _spec()
        ev = _evidence()
        assert p1.authorize(task, _MODEL, [ev]).approved
        assert p2.authorize(task, _MODEL, [ev]).approved
