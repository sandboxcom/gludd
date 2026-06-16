"""Cross-source normalization: join keys, correlation, and auth families.

Concrete connectors each emit *normalized records* (see
:mod:`general_ludd.connectors.base`) shaped ``{ts, source, kind,
level_or_status, message, value, labels, raw}``. Their ``labels`` sub-dicts,
however, are *heterogeneous*: AWS CloudWatch calls a host ``instance``, a Loki
stream calls it ``host``, a Kubernetes-aware shipper calls it ``node``. To
correlate an error across these backends you need a *canonical* set of join
keys regardless of which vocabulary the source happened to use.

This module is the cross-source normalization layer:

- :func:`normalize_join_keys` folds a record's heterogeneous labels into a
  canonical ``join`` sub-dict (``trace_id`` / ``host`` / ``service`` / ``k8s`` /
  ``cloud`` / ``severity``). It is **idempotent** (re-normalizing a normalized
  record is a no-op) and **total** (a malformed record yields ``{}`` join keys
  rather than raising).
- :func:`correlate` groups records by one canonical join key.
- :func:`auth_family` classifies a source/connector name into an auth *family*
  (AWS / Azure / GCP / Grafana / ...), and :func:`bundle_credentials` collects
  the credential *environment-variable NAMES* (never values) per family so one
  auth bundle can fan out to every connector in that family.

Hard invariants
---------------
- **No secret values, ever.** :func:`bundle_credentials` reads only the *names*
  of ``*_env`` config entries (the env vars that *hold* credentials); it never
  dereferences them or touches a secret value.
- **Never raises.** Every public function is best-effort: a malformed input is
  skipped / yields an empty result, never an exception.
- Pure stdlib; does **not** import sibling connector modules.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AUTH_FAMILY_PREFIXES",
    "CANONICAL_SEVERITIES",
    "auth_family",
    "bundle_credentials",
    "correlate",
    "normalize_join_keys",
]

# --------------------------------------------------------------------------- #
# Severity canonicalization
# --------------------------------------------------------------------------- #
# The five canonical severities every backend's level/status collapses into.
CANONICAL_SEVERITIES = ("debug", "info", "warn", "error", "critical")

# Heterogeneous level/status/priority tokens -> canonical severity. Keys are
# lower-cased; lookup is case-insensitive. syslog numeric priorities (0-7) and
# common backend spellings are all folded here.
_SEVERITY_MAP: dict[str, str] = {
    # debug / trace
    "trace": "debug",
    "debug": "debug",
    "fine": "debug",
    "finer": "debug",
    "finest": "debug",
    "verbose": "debug",
    "7": "debug",  # syslog DEBUG
    # info / notice / success
    "info": "info",
    "information": "info",
    "informational": "info",
    "notice": "info",
    "log": "info",
    "default": "info",
    "ok": "info",
    "success": "info",
    "succeeded": "info",
    "successful": "info",
    "pass": "info",
    "passed": "info",
    "healthy": "info",
    "up": "info",
    "5": "info",  # syslog NOTICE
    "6": "info",  # syslog INFO
    # warn
    "warn": "warn",
    "warning": "warn",
    "degraded": "warn",
    "unstable": "warn",
    "4": "warn",  # syslog WARNING
    # error / failure
    "err": "error",
    "error": "error",
    "fail": "error",
    "failed": "error",
    "failure": "error",
    "down": "error",
    "unhealthy": "error",
    "3": "error",  # syslog ERR
    # critical / fatal / emergency
    "crit": "critical",
    "critical": "critical",
    "fatal": "critical",
    "alert": "critical",
    "emerg": "critical",
    "emergency": "critical",
    "panic": "critical",
    "0": "critical",  # syslog EMERG
    "1": "critical",  # syslog ALERT
    "2": "critical",  # syslog CRIT
}

# Label aliases per canonical join key. First non-empty alias wins. All lookups
# are case-insensitive on the *label name* (aliases are stored lower-cased).
_TRACE_ALIASES = ("trace_id", "traceid", "x-b3-traceid", "x_b3_traceid")
_HOST_ALIASES = ("host", "hostname", "instance", "computer", "node")
_SERVICE_ALIASES = ("service", "app", "application", "job", "unit", "container_name")
_NAMESPACE_ALIASES = ("namespace", "k8s_namespace", "kubernetes_namespace")
_POD_ALIASES = ("pod", "k8s_pod", "pod_name")
_CONTAINER_ALIASES = ("container", "k8s_container", "container_name")
_ACCOUNT_ALIASES = ("account", "account_id", "aws_account", "aws_account_id")
_PROJECT_ALIASES = ("project", "project_id", "gcp_project")
_SUBSCRIPTION_ALIASES = ("subscription", "subscription_id", "azure_subscription")
_REGION_ALIASES = ("region", "location", "availability_zone", "zone")


def _coerce_label_map(labels: Any) -> dict[str, Any]:
    """Return a case-folded ``{lower(name): value}`` view of ``labels``.

    Non-dict or unusable ``labels`` collapse to ``{}`` (best-effort, no raise).
    Keys are lower-cased so callers can look aliases up case-insensitively. On a
    case collision the last writer wins — acceptable for correlation keys.
    """
    if not isinstance(labels, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in labels.items():
        try:
            out[str(key).lower()] = value
        except Exception:  # pragma: no cover - str() on a pathological key
            continue
    return out


def _first_present(label_map: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    """Return the first non-empty value among ``aliases`` (case-folded), else None."""
    for alias in aliases:
        if alias in label_map:
            value = label_map[alias]
            if value is not None and value != "":
                return value
    return None


def _as_str(value: Any) -> str | None:
    """Coerce ``value`` to a non-empty stripped string, else ``None``."""
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:  # pragma: no cover - str() on a pathological value
        return None
    return text or None


def _normalize_host(value: Any) -> str | None:
    """Lower-case a host and strip a trailing ``:port`` (IPv6-aware).

    ``Web-01:8080`` -> ``web-01``. A bracketed IPv6 literal keeps its address:
    ``[::1]:9090`` -> ``[::1]``. Bare IPv6 (multiple colons, no brackets) is left
    intact rather than mangled at the first colon.
    """
    text = _as_str(value)
    if text is None:
        return None
    text = text.lower()
    if text.startswith("["):
        # Bracketed IPv6 — keep through the closing bracket, drop any :port.
        end = text.find("]")
        if end != -1:
            return text[: end + 1]
        return text
    # Bare IPv6 (2+ colons, no brackets): no host:port to split — keep as-is.
    if text.count(":") >= 2:
        return text
    # host:port -> host
    return text.split(":", 1)[0] if ":" in text else text


def _canonical_severity(*candidates: Any) -> str | None:
    """Map the first recognizable level/status/priority token to a canonical severity."""
    for candidate in candidates:
        text = _as_str(candidate)
        if text is None:
            continue
        mapped = _SEVERITY_MAP.get(text.lower())
        if mapped is not None:
            return mapped
    return None


def _build_k8s(label_map: dict[str, Any]) -> dict[str, str]:
    """Collect the present Kubernetes coordinates (namespace/pod/container)."""
    k8s: dict[str, str] = {}
    for key, aliases in (
        ("namespace", _NAMESPACE_ALIASES),
        ("pod", _POD_ALIASES),
        ("container", _CONTAINER_ALIASES),
    ):
        text = _as_str(_first_present(label_map, aliases))
        if text is not None:
            k8s[key] = text
    return k8s


def _build_cloud(label_map: dict[str, Any]) -> dict[str, str]:
    """Collect cloud-account coordinates (account/project/subscription + region)."""
    cloud: dict[str, str] = {}
    for key, aliases in (
        ("account", _ACCOUNT_ALIASES),
        ("project", _PROJECT_ALIASES),
        ("subscription", _SUBSCRIPTION_ALIASES),
        ("region", _REGION_ALIASES),
    ):
        text = _as_str(_first_present(label_map, aliases))
        if text is not None:
            cloud[key] = text
    return cloud


def _derive_join(record: dict[str, Any]) -> dict[str, Any]:
    """Compute the canonical ``join`` sub-dict for one record (missing -> omit)."""
    label_map = _coerce_label_map(record.get("labels"))

    join: dict[str, Any] = {}

    trace_id = _as_str(_first_present(label_map, _TRACE_ALIASES))
    if trace_id is not None:
        join["trace_id"] = trace_id

    host = _normalize_host(_first_present(label_map, _HOST_ALIASES))
    if host is not None:
        join["host"] = host

    service = _as_str(_first_present(label_map, _SERVICE_ALIASES))
    if service is not None:
        join["service"] = service

    k8s = _build_k8s(label_map)
    if k8s:
        join["k8s"] = k8s

    cloud = _build_cloud(label_map)
    if cloud:
        join["cloud"] = cloud

    # Severity draws from labels first (a backend may put status in labels), then
    # the record's own level_or_status field, then a labelled priority.
    severity = _canonical_severity(
        label_map.get("level"),
        label_map.get("level_or_status"),
        label_map.get("status"),
        label_map.get("severity"),
        label_map.get("priority"),
        record.get("level_or_status"),
    )
    if severity is not None:
        join["severity"] = severity

    return join


def normalize_join_keys(record: dict[str, Any]) -> dict[str, Any]:
    """Return ``record`` with a canonical ``join`` sub-dict added.

    Folds the record's heterogeneous ``labels`` into canonical correlation keys
    so records from different backends can be joined on the same vocabulary:

    - ``trace_id`` — from ``trace_id`` / ``traceId`` / ``x-b3-traceid``.
    - ``host`` — from ``host`` / ``hostname`` / ``instance`` / ``computer`` /
      ``node``; lower-cased with any ``:port`` stripped.
    - ``service`` — from ``service`` / ``app`` / ``job`` / ``unit`` /
      ``container``.
    - ``k8s`` — ``{namespace, pod, container}`` (only present keys).
    - ``cloud`` — ``{account, project, subscription, region}`` (only present
      keys).
    - ``severity`` — ``level`` / ``level_or_status`` / ``status`` / ``priority``
      mapped to one of ``{debug, info, warn, error, critical}``.

    A key with no source label is **omitted** (never set to ``None``).

    **Idempotent**: re-normalizing a record whose ``join`` is already present
    recomputes it from the same labels — calling this twice yields an equal
    result. **Total**: a non-dict argument, or a record with a malformed
    ``labels``, returns a best-effort dict rather than raising.
    """
    if not isinstance(record, dict):
        return {}

    # Shallow copy so the caller's record is not mutated; ``join`` is rebuilt
    # from scratch each call, which is what makes the function idempotent.
    out = dict(record)
    out["join"] = _derive_join(record)
    return out


def correlate(records: list[dict[str, Any]], by: str) -> dict[str, list[dict[str, Any]]]:
    """Group ``records`` by a single canonical join key.

    ``by`` names a key inside each record's ``join`` sub-dict (e.g. ``trace_id``,
    ``host``, ``service``, ``severity``). Records are normalized on the fly, so
    callers may pass raw connector records without calling
    :func:`normalize_join_keys` first.

    For scalar join keys the group key is the value's string form. For the
    structured keys ``k8s`` / ``cloud`` the group key is a stable
    ``"a=1,b=2"`` rendering of the sub-dict (sorted by field name).

    Records whose ``join`` lacks ``by`` are dropped (they cannot be correlated on
    that key). Group insertion order follows first appearance. Never raises:
    a malformed record is simply skipped.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(records, list):
        return groups

    for record in records:
        if not isinstance(record, dict):
            continue
        join = record.get("join")
        if not isinstance(join, dict):
            join = _derive_join(record)
        value = join.get(by)
        key = _group_key(value)
        if key is None:
            continue
        groups.setdefault(key, []).append(record)
    return groups


def _group_key(value: Any) -> str | None:
    """Render a join-key value as a stable group key, or ``None`` to skip."""
    if value is None:
        return None
    if isinstance(value, dict):
        if not value:
            return None
        parts = [f"{field}={value[field]}" for field in sorted(value)]
        return ",".join(parts)
    return _as_str(value)


# --------------------------------------------------------------------------- #
# Auth families
# --------------------------------------------------------------------------- #
# Each family lists the substrings/prefixes that classify a connector name into
# it. A source/connector name matches a family if it *starts with* a token OR
# *contains* it. Families are checked in declaration order; tokens are chosen so
# matches are effectively disjoint.
AUTH_FAMILY_PREFIXES: dict[str, tuple[str, ...]] = {
    "aws": ("aws", "cloudwatch", "x-ray", "xray", "x_ray", "dynamodb", "eventbridge"),
    "azure": ("azure", "entra", "graph", "appinsights", "app_insights", "loganalytics", "log_analytics"),
    "gcp": ("gcp", "google", "stackdriver", "bigquery", "pubsub", "gke"),
    "grafana": ("grafana", "loki", "tempo", "mimir", "oncall", "prometheus"),
    "datadog": ("datadog", "ddog", "dd_"),
    "elastic": ("elastic", "elasticsearch", "kibana", "logstash", "opensearch"),
    "github": ("github", "gh_", "actions"),
    "gitlab": ("gitlab", "glab"),
    "splunk": ("splunk", "hec"),
    "newrelic": ("newrelic", "new_relic", "nr_"),
    "pagerduty": ("pagerduty", "pd_"),
}


def auth_family(name: str) -> str:
    """Classify a source/connector name into its auth family.

    ``name`` may be a registered source name (``"prod-cloudwatch"``) or a
    connector kind (``"aws_s3"``). Matching is case-insensitive and
    token-based: the name is matched against each family's recognized
    prefixes/tokens (see :data:`AUTH_FAMILY_PREFIXES`).

    Returns the family slug (``"aws"`` / ``"azure"`` / ``"gcp"`` / ``"grafana"``
    / ...). An unrecognized or empty name yields ``"unknown"``. Never raises.
    """
    text = _as_str(name)
    if text is None:
        return "unknown"
    lowered = text.lower()
    for family, tokens in AUTH_FAMILY_PREFIXES.items():
        for token in tokens:
            if lowered.startswith(token) or token in lowered:
                return family
    return "unknown"


def _iter_env_names(config: dict[str, Any]) -> list[str]:
    """Collect the credential env-var NAMES declared in one connector config.

    A credential is declared via any config key ending in ``_env`` whose value is
    the *name* of an environment variable that holds the secret (e.g.
    ``{"token_env": "DATADOG_API_KEY"}`` -> ``["DATADOG_API_KEY"]``). Only the
    env-var *names* are collected — the secret values are NEVER read.

    A list value under an ``*_env`` key contributes each of its string names.
    Empty / non-string names are skipped. Order of first appearance is kept.
    """
    names: list[str] = []
    for key, value in config.items():
        try:
            key_str = str(key)
        except Exception:  # pragma: no cover - pathological key
            continue
        if not key_str.lower().endswith("_env"):
            continue
        candidates = value if isinstance(value, list | tuple) else [value]
        for candidate in candidates:
            text = _as_str(candidate)
            if text is not None and text not in names:
                names.append(text)
    return names


def bundle_credentials(configs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Collect credential env-var NAMES per auth family across connector configs.

    Each config is a connector's declared settings. Its family is read from a
    ``family`` / ``auth_family`` key if present, else inferred via
    :func:`auth_family` from its ``source`` / ``name`` / ``kind`` / ``connector``
    field. Its credential env-var *names* are the values of any ``*_env`` keys
    (see :func:`_iter_env_names`).

    Returns ``{family: [ENV_VAR_NAME, ...]}`` — de-duplicated, first-appearance
    order — so a single auth bundle (one resolved family credential) can fan out
    to every connector in that family.

    **Secret-safe**: only env-var *names* ever appear in the result; no secret
    value is read. Never raises — a malformed config entry is skipped.
    """
    bundle: dict[str, list[str]] = {}
    if not isinstance(configs, list):
        return bundle

    for config in configs:
        if not isinstance(config, dict):
            continue
        family = _config_family(config)
        names = _iter_env_names(config)
        existing = bundle.setdefault(family, [])
        for name in names:
            if name not in existing:
                existing.append(name)
    return bundle


def _config_family(config: dict[str, Any]) -> str:
    """Resolve a config's auth family: explicit key wins, else infer from its name."""
    explicit = _as_str(config.get("family") or config.get("auth_family"))
    if explicit is not None:
        return explicit.lower()
    for field in ("source", "name", "kind", "connector"):
        inferred = auth_family(config.get(field, ""))
        if inferred != "unknown":
            return inferred
    return "unknown"
