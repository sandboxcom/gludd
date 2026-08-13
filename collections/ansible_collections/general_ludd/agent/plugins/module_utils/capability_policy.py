"""
Unified per-role capability policy for general_ludd.agent modules.

This is the authorisation layer that sits *above* the filesystem-write guard
(:mod:`fs_write_policy`).  Where ``fs_write_policy`` answers "may this path be
written?", this module answers "may *this role* do *this kind of thing* at
all?" across every capability dimension a module might exercise:

* **filesystem write** — delegated to / composed with the existing
  :class:`WritePolicy` (this module gates *whether* a role may write at all and
  with which roots; the WritePolicy still enforces the path allowlist itself).
* **collections self-modify** — writing under ``collections/`` (the agent's own
  Ansible source).  Allowed ONLY for explicitly self-improvement / self-research
  roles; everybody else is denied even if their fs-write roots would contain it.
* **facts / vars path access** — which fact namespaces (dot-prefixes) a role may
  read.
* **network** — url / socket / host allowlist (which hosts a role may reach).
* **secrets access** — which secret aliases/prefixes a role may resolve.
* **database access** — which ``gludd_db`` ops a role may perform.

The policy is **default-DENY** in every dimension: a capability is refused
unless the role's entry provably grants it.  An *unknown* role gets the empty
baseline (deny everything).  Every ``check_*`` method raises
:class:`CapabilityError` (fail-closed) on denial so a caller that forgets to
handle the boolean can never silently proceed.

This util has **no third-party dependencies** (stdlib only) so it runs inside
Ansible module execution on a managed node without extra installs, matching the
constraint documented in ``gludd.py`` and ``fs_write_policy.py``.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.capability_policy import (
        CapabilityError,
        for_role,
    )

    cap = for_role(module.params["role"])
    try:
        cap.check_db_op(module.params["op"])
    except CapabilityError as exc:
        module.fail_json(**error_result(f"capability denied: {exc}"))
        return
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# fs_write_policy is the sibling module_util; import the WritePolicy so the
# filesystem dimension is *composed* with the existing guard rather than
# re-implemented.  Both relative (collection-installed) and flat (importlib /
# sys.path) layouts are supported, matching the gludd_* modules.
try:  # pragma: no cover - exercised by whichever layout the runtime uses
    from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_policy import (
        WritePolicy,
        WritePolicyError,
    )
except ImportError:  # pragma: no cover
    from fs_write_policy import WritePolicy, WritePolicyError  # type: ignore[no-redef]


class CapabilityError(Exception):
    """Raised (fail-closed) when a role lacks the requested capability."""


# ---------------------------------------------------------------------------
# Collections-self-modify guard
# ---------------------------------------------------------------------------

#: Path component that marks the agent's own Ansible source tree.  A write whose
#: resolved path contains this component is a *self-modification* and is gated by
#: the dedicated ``collections_self_modify`` capability, never by ordinary
#: fs-write roots.
COLLECTIONS_COMPONENT = "collections"


def _norm_prefix(value: str) -> str:
    """Normalise an allowlist prefix for stable, case-sensitive matching."""
    return value.strip()


@dataclass
class CapabilityPolicy:
    """A per-role capability grant set.  Default-DENY in every dimension.

    Every collection field defaults to *empty* so a bare ``CapabilityPolicy()``
    (the baseline an unknown role receives) denies everything.  A grant is only
    present if the built-in table or caller config explicitly adds it.

    Parameters
    ----------
    role:
        The role name this grant set is for (informational; used in errors).
    fs_write:
        Whether the role may write to the filesystem at all.  When True a
        :class:`WritePolicy` (built from ``fs_write_roots``) still enforces the
        path allowlist — this flag only gates *whether* writes are attempted.
    fs_write_roots:
        Path roots handed to the composed :class:`WritePolicy`.  Empty => the
        WritePolicy itself denies everything (strict default-DENY).
    collections_self_modify:
        Whether the role may write under ``collections/`` (its own source).
        Only self-improvement / self-research roles get this.
    facts_prefixes:
        Fact/var dot-prefixes the role may read (``"ludd.todos"`` matches
        ``ludd.todos`` and ``ludd.todos.count``).  Empty => no facts.
    network_hosts:
        Hostnames the role may reach over url/socket.  Empty => no network.
    secret_prefixes:
        Secret alias prefixes the role may resolve (``"deploy/"`` matches
        ``deploy/token``).  Empty => no secrets.
    db_ops:
        ``gludd_db`` op names the role may perform.  Empty => no DB.
    """

    role: str = "<unknown>"
    fs_write: bool = False
    fs_write_roots: list[str] = field(default_factory=list)
    collections_self_modify: bool = False
    facts_prefixes: list[str] = field(default_factory=list)
    network_hosts: list[str] = field(default_factory=list)
    secret_prefixes: list[str] = field(default_factory=list)
    db_ops: list[str] = field(default_factory=list)

    # -- filesystem ---------------------------------------------------------

    def write_policy(self) -> WritePolicy:
        """Return a composed :class:`WritePolicy` over this role's fs roots."""
        return WritePolicy(allowed_roots=list(self.fs_write_roots))

    def check_fs_write(self, path: str) -> str:
        """Authorise a filesystem write of ``path`` for this role.

        Two gates, both fail-closed:

        1. The role must hold the ``fs_write`` capability at all.
        2. The resolved path must pass the composed :class:`WritePolicy`
           (allowlist + sensitive-name guard).
        3. If the path lands inside a ``collections/`` tree it is a
           self-modification and additionally requires
           ``collections_self_modify``.

        Returns the resolved path on success; raises :class:`CapabilityError`
        otherwise.
        """
        if not self.fs_write:
            raise CapabilityError(
                f"role {self.role!r} has no filesystem-write capability "
                f"(default-deny): {path!r}"
            )
        try:
            resolved = self.write_policy().check(path)
        except WritePolicyError as exc:
            # Re-raise as a CapabilityError so callers handle one exception type.
            raise CapabilityError(
                f"role {self.role!r} write denied by path policy: {exc}"
            ) from exc

        if self._is_collections_path(resolved):
            self.check_collections_self_modify(resolved)
        return resolved

    @staticmethod
    def _is_collections_path(resolved: str) -> bool:
        parts = resolved.replace("\\", "/").split("/")
        return COLLECTIONS_COMPONENT in parts

    def check_collections_self_modify(self, path: str) -> str:
        """Authorise a write under ``collections/`` (agent self-modification).

        Allowed ONLY for roles holding ``collections_self_modify`` (the
        self-improvement / self-research roles).  Fail-closed for everyone else.
        """
        if not self.collections_self_modify:
            raise CapabilityError(
                f"role {self.role!r} may not self-modify collections/ "
                f"(default-deny; only self-improvement roles may): {path!r}"
            )
        return path

    # -- facts / vars -------------------------------------------------------

    def check_facts_access(self, fact_path: str) -> str:
        """Authorise reading a fact/var path for this role.

        ``fact_path`` is matched against the role's ``facts_prefixes``: a prefix
        ``ludd.todos`` grants ``ludd.todos`` itself and any dotted descendant
        (``ludd.todos.count``), but NOT a sibling (``ludd.todos_evil``).
        """
        if not isinstance(fact_path, str) or not fact_path:
            raise CapabilityError(
                f"role {self.role!r}: empty/invalid fact path"
            )
        for prefix in self.facts_prefixes:
            p = _norm_prefix(prefix)
            if fact_path == p or fact_path.startswith(p + "."):
                return fact_path
        raise CapabilityError(
            f"role {self.role!r} may not read fact path {fact_path!r} "
            f"(default-deny; granted prefixes: {self.facts_prefixes!r})"
        )

    # -- network ------------------------------------------------------------

    def check_network_host(self, host: str) -> str:
        """Authorise reaching ``host`` (url/socket target) for this role.

        Matches the bare host against ``network_hosts`` exactly, or as a
        subdomain suffix (``api.example.com`` is allowed by ``example.com``).
        ``host`` must be a bare hostname — pass it through :func:`extract_host`
        first if you hold a full URL.
        """
        if not isinstance(host, str) or not host:
            raise CapabilityError(f"role {self.role!r}: empty/invalid host")
        host = host.strip().lower()
        for allowed in self.network_hosts:
            a = _norm_prefix(allowed).lower()
            if host == a or host.endswith("." + a):
                return host
        raise CapabilityError(
            f"role {self.role!r} may not reach host {host!r} "
            f"(default-deny; allowlist: {self.network_hosts!r})"
        )

    # -- secrets ------------------------------------------------------------

    def check_secret_access(self, alias: str) -> str:
        """Authorise resolving a secret ``alias`` for this role.

        ``alias`` is matched against ``secret_prefixes``: a prefix ``deploy/``
        grants ``deploy/token`` and ``deploy/anything``; an exact prefix
        without a trailing separator matches that alias exactly or as a
        ``prefix/...`` child.
        """
        if not isinstance(alias, str) or not alias:
            raise CapabilityError(f"role {self.role!r}: empty/invalid secret alias")
        for prefix in self.secret_prefixes:
            p = _norm_prefix(prefix)
            if alias == p or alias.startswith(p if p.endswith("/") else p + "/"):
                return alias
        raise CapabilityError(
            f"role {self.role!r} may not resolve secret {alias!r} "
            f"(default-deny; granted prefixes: {self.secret_prefixes!r})"
        )

    # -- database -----------------------------------------------------------

    def check_db_op(self, op: str) -> str:
        """Authorise a ``gludd_db`` ``op`` for this role.

        The op must be explicitly present in the role's ``db_ops`` allowlist.
        Fail-closed for any unlisted op (and for unknown roles, whose list is
        empty).
        """
        if not isinstance(op, str) or not op:
            raise CapabilityError(f"role {self.role!r}: empty/invalid db op")
        if op in self.db_ops:
            return op
        raise CapabilityError(
            f"role {self.role!r} may not perform db op {op!r} "
            f"(default-deny; allowed ops: {self.db_ops!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_host(url_or_host: str) -> str:
    """Return the bare hostname from a URL or pass a bare host through.

    Stdlib ``urllib.parse`` is used so this works inside module exec.  A value
    with no scheme (``example.com:8000`` or ``example.com``) is treated as a
    host; the port is stripped.
    """
    from urllib.parse import urlsplit

    value = (url_or_host or "").strip()
    netloc = urlsplit(value).netloc if "://" in value else value
    # Strip any userinfo and port.
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    # The bracket-aware branch preserves IPv6 literals such as ``[::1]:8000``.
    host = netloc[1:].split("]", 1)[0] if netloc.startswith("[") else netloc.split(":", 1)[0]
    return host


# ---------------------------------------------------------------------------
# Built-in role table — the minimal grants specific roles need.
# Default-DENY: a role NOT in this table receives the empty baseline.
# ---------------------------------------------------------------------------

#: The empty, deny-everything baseline.  Used for unknown roles.
_BASELINE = CapabilityPolicy()


def _builtin_table() -> dict[str, CapabilityPolicy]:
    """Construct the built-in per-role grants (fresh objects each call).

    Grants are intentionally minimal — each role gets ONLY what its job needs:

    * ``self_improve_*`` / ``self_research_*`` — may self-modify ``collections/``
      and read their own facts; that is the whole point of the role.
    * ``report_*`` — may READ facts but may NOT write the filesystem, touch the
      DB, reach the network, or read secrets.
    * ``coder`` — ordinary fs write (no self-modify), and the DB ops needed to
      manage its own todos.
    * ``security_*`` — may read secrets (audit/rotation) and read facts; no fs
      write, no DB writes.
    * ``operator`` — broad DB ops + network to the local daemon; no collections
      self-modify.
    """
    return {
        # --- self-improvement / self-research: the only roles allowed to
        #     write under collections/ (the agent's own source). ---
        "self_improve_agent": CapabilityPolicy(
            role="self_improve_agent",
            fs_write=True,
            fs_write_roots=["/workspace", "/tmp"],
            collections_self_modify=True,
            facts_prefixes=["ludd"],
            db_ops=["todo_get", "todo_create", "todo_update_status"],
        ),
        "self_research_agent": CapabilityPolicy(
            role="self_research_agent",
            fs_write=True,
            fs_write_roots=["/workspace", "/tmp"],
            collections_self_modify=True,
            facts_prefixes=["ludd"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # --- reporting: read facts only, no writes/secrets/network/db. ---
        "report_status": CapabilityPolicy(
            role="report_status",
            facts_prefixes=["ludd", "ansible"],
        ),
        "report_metrics": CapabilityPolicy(
            role="report_metrics",
            facts_prefixes=["ludd.metrics", "ludd.traces"],
        ),
        # --- ordinary coding agent: fs write (NOT collections), own todos. ---
        "coder": CapabilityPolicy(
            role="coder",
            fs_write=True,
            fs_write_roots=["/workspace", "/tmp"],
            facts_prefixes=["ludd.todos"],
            db_ops=["todo_get", "todo_create", "todo_update_status"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # --- security: may resolve secrets (audit/rotation) + read facts. ---
        "security_auditor": CapabilityPolicy(
            role="security_auditor",
            facts_prefixes=["ludd"],
            secret_prefixes=["audit/", "deploy/"],
        ),
        # --- operator: broad DB + local-daemon network, no self-modify. ---
        "operator": CapabilityPolicy(
            role="operator",
            db_ops=[
                "todo_get",
                "todo_create",
                "todo_update_status",
                "resource_preference",
            ],
            facts_prefixes=["ludd"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # --- observability workflows: read-only access to the local daemon.
        #     The daemon owns connector construction and endpoint validation;
        #     these roles may not steer the module to a remote host. ---
        "observe_incident_triage": CapabilityPolicy(
            role="observe_incident_triage",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "observe_latency_regression": CapabilityPolicy(
            role="observe_latency_regression",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "observe_error_spike_rca": CapabilityPolicy(
            role="observe_error_spike_rca",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "observe_deploy_correlator": CapabilityPolicy(
            role="observe_deploy_correlator",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "observe_saturation_capacity": CapabilityPolicy(
            role="observe_saturation_capacity",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "observe_security_signal": CapabilityPolicy(
            role="observe_security_signal",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        "molecule_observe_probe": CapabilityPolicy(
            role="molecule_observe_probe",
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # --- per-collection-role db grants (principle of least privilege): each
        #     general_ludd.agent role that calls gludd_db gets a capability
        #     identity granting ONLY the db ops it actually invokes.  A role's
        #     tasks pass `role: "{{ capability_role }}"` (defaulted to its own
        #     name); the default-DENY policy then permits exactly those ops and
        #     nothing more.  Adding a new db op to a role REQUIRES extending its
        #     grant here — the wiring unit test enforces this. ---
        # agent_task: fetch its todo, then mark it done/failed.
        "agent_task": CapabilityPolicy(
            role="agent_task",
            db_ops=["todo_get", "todo_update_status"],
            facts_prefixes=["ludd.todos"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # estimate_story: read-only — fetch the story to estimate.
        "estimate_story": CapabilityPolicy(
            role="estimate_story",
            db_ops=["todo_get"],
            facts_prefixes=["ludd"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # security_requirements: fetch the story; optional write_back updates status.
        "security_requirements": CapabilityPolicy(
            role="security_requirements",
            db_ops=["todo_get", "todo_update_status"],
            facts_prefixes=["ludd"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # story_create: optional write_back creates a todo (create only).
        "story_create": CapabilityPolicy(
            role="story_create",
            db_ops=["todo_create"],
            facts_prefixes=["ludd"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
        # molecule_db_probe: the direct gludd_db module molecule test exercises
        # every read/update/resource op against the mock daemon.
        "molecule_db_probe": CapabilityPolicy(
            role="molecule_db_probe",
            db_ops=["todo_get", "todo_update_status", "resource_preference"],
            network_hosts=["localhost", "127.0.0.1"],
        ),
    }


def for_role(
    role_name: str | None,
    config: dict[str, object] | None = None,
) -> CapabilityPolicy:
    """Load the :class:`CapabilityPolicy` for ``role_name``.

    Resolution order, all default-DENY:

    1. If ``config`` carries an explicit grant set for ``role_name`` (under a
       ``roles`` mapping), that wins — operators can extend the policy without
       editing this file.
    2. Otherwise the built-in table is consulted.
    3. An unknown / missing / empty role gets the empty baseline (deny all).

    ``config`` shape (all keys optional)::

        {"roles": {"my_role": {"fs_write": true,
                               "fs_write_roots": ["/workspace"],
                               "db_ops": ["todo_get"], ...}}}
    """
    if not role_name or not isinstance(role_name, str):
        return replace(_BASELINE)

    role_name = role_name.strip()

    # 1. Caller-supplied config override.
    if config:
        roles_cfg = config.get("roles")
        if isinstance(roles_cfg, dict) and role_name in roles_cfg:
            entry = roles_cfg[role_name]
            if isinstance(entry, dict):
                return _from_config_entry(role_name, entry)

    # 2. Built-in table.
    table = _builtin_table()
    if role_name in table:
        return table[role_name]

    # 3. Default-DENY baseline for unknown roles.
    return replace(_BASELINE, role=role_name)


def _from_config_entry(role_name: str, entry: dict[str, object]) -> CapabilityPolicy:
    """Build a policy from a raw config dict, ignoring unknown keys.

    Every dimension defaults to its empty/deny value, so a partial entry still
    denies everything it does not explicitly grant.
    """

    def _list(key: str) -> list[str]:
        v = entry.get(key)
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

    return CapabilityPolicy(
        role=role_name,
        fs_write=bool(entry.get("fs_write", False)),
        fs_write_roots=_list("fs_write_roots"),
        collections_self_modify=bool(entry.get("collections_self_modify", False)),
        facts_prefixes=_list("facts_prefixes"),
        network_hosts=_list("network_hosts"),
        secret_prefixes=_list("secret_prefixes"),
        db_ops=_list("db_ops"),
    )
