"""Unit tests for sandbox security policy."""

from __future__ import annotations

from general_ludd.sandbox.security_policy import SecurityPolicy


class TestSecurityPolicy:
    def test_default_policy_is_restrictive(self) -> None:
        policy = SecurityPolicy()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.no_new_privileges is True
        assert policy.allow_privilege_escalation is False
        assert policy.capabilities == []
        assert policy.is_restrictive()

    def test_minimal_classmethod(self) -> None:
        policy = SecurityPolicy.minimal()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.no_new_privileges is True

    def test_default_docker_classmethod(self) -> None:
        policy = SecurityPolicy.default_docker()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.no_new_privileges is True
        assert policy.allow_privilege_escalation is False

    def test_to_docker_args_read_only(self) -> None:
        policy = SecurityPolicy(read_only_root=True)
        args = policy.to_docker_args()
        assert "--read-only" in args

    def test_to_docker_args_no_read_only(self) -> None:
        policy = SecurityPolicy(read_only_root=False)
        args = policy.to_docker_args()
        assert "--read-only" not in args

    def test_to_docker_args_privileged(self) -> None:
        policy = SecurityPolicy(privileged=True)
        args = policy.to_docker_args()
        assert "--privileged" in args

    def test_to_docker_args_seccomp(self) -> None:
        policy = SecurityPolicy(seccomp_profile="default.json")
        args = policy.to_docker_args()
        assert "--security-opt" in args
        assert "seccomp=default.json" in args

    def test_to_docker_args_apparmor(self) -> None:
        policy = SecurityPolicy(apparmor_profile="my-profile")
        args = policy.to_docker_args()
        args_str = " ".join(args)
        assert "apparmor=my-profile" in args_str

    def test_to_docker_args_capabilities(self) -> None:
        policy = SecurityPolicy(capabilities=["NET_ADMIN", "SYS_PTRACE"])
        args = policy.to_docker_args()
        assert "--cap-add" in args
        assert "NET_ADMIN" in args
        assert "SYS_PTRACE" in args

    def test_to_docker_args_volumes(self) -> None:
        policy = SecurityPolicy(read_only_paths=["/data/ro"], writable_paths=["/tmp/rw"])
        args = policy.to_docker_args()
        assert "/data/ro:/data/ro:ro" in args
        assert "/tmp/rw:/tmp/rw:rw" in args

    def test_to_kubernetes_context_minimal(self) -> None:
        policy = SecurityPolicy()
        ctx = policy.to_kubernetes_context()
        assert ctx["readOnlyRootFilesystem"] is True
        assert ctx["privileged"] is False
        assert ctx["allowPrivilegeEscalation"] is False
        assert "capabilities" not in ctx

    def test_to_kubernetes_context_with_caps(self) -> None:
        policy = SecurityPolicy(capabilities=["NET_RAW"])
        ctx = policy.to_kubernetes_context()
        assert ctx["capabilities"]["add"] == ["NET_RAW"]

    def test_to_kubernetes_context_with_seccomp(self) -> None:
        policy = SecurityPolicy(seccomp_profile="my-seccomp.json")
        ctx = policy.to_kubernetes_context()
        assert ctx["seccompProfile"]["type"] == "Localhost"
        assert ctx["seccompProfile"]["localhostProfile"] == "my-seccomp.json"

    def test_is_restrictive_privileged_false(self) -> None:
        policy = SecurityPolicy(privileged=True)
        assert policy.is_restrictive() is False

    def test_is_restrictive_capabilities_added_false(self) -> None:
        policy = SecurityPolicy(capabilities=["SYS_ADMIN"])
        assert policy.is_restrictive() is False

    def test_is_restrictive_allow_escalation_false(self) -> None:
        policy = SecurityPolicy(allow_privilege_escalation=True)
        assert policy.is_restrictive() is False

    def test_custom_policy_fields(self) -> None:
        policy = SecurityPolicy(capabilities=["NET_BIND_SERVICE"], read_only_root=False, privileged=False, seccomp_profile="custom.json", apparmor_profile="custom-armor", no_new_privileges=True, allow_privilege_escalation=False, hidden_paths=["/secret"])
        assert policy.hidden_paths == ["/secret"]
        assert policy.read_only_root is False
        assert policy.seccomp_profile == "custom.json"

    def test_empty_policy_no_docker_args(self) -> None:
        policy = SecurityPolicy(read_only_root=False, privileged=False, capabilities=[], read_only_paths=[], writable_paths=[], seccomp_profile="", apparmor_profile="")
        args = policy.to_docker_args()
        assert "--read-only" not in args
        assert "--privileged" not in args
        assert "--cap-add" not in args
