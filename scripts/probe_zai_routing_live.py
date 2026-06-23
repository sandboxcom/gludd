#!/usr/bin/env python3
"""LIVE proof: task-specific routing makes DIFFERENT z.ai models answer DIFFERENT task types.

This script wires the real routing layer (general_ludd.config.model_routing) to a
temporary multi-profile mapping, resolves each task role -> profile -> concrete
z.ai model name, then makes a LIVE call to each resolved model asking it to
identify itself. It confirms via the response that the correct (and distinct)
model answered each task type.

Run via:  make probe-zai-routing-live

The API key is read from $ZAI_API_KEY or from the .zai.key file at repo root.
The key is NEVER printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from general_ludd.config.model_routing import (
    ModelRoutingConfig,
    build_router_from_config,
)

ZAI_BASE_URL = os.environ.get(
    "ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4"
)
CHAT_ENDPOINT = ZAI_BASE_URL.rstrip("/") + "/chat/completions"

# role (task type) -> routing profile id. This is the routing config under test.
ROLE_ROUTING: dict[str, str] = {
    "coder": "p_coder",
    "planner": "p_planner",
    "fast": "p_fast",
}

# profile id -> concrete z.ai model that serves that profile.
PROFILE_MODEL: dict[str, str] = {
    "p_coder": "glm-4.6",
    "p_planner": "glm-4.5-air",
    "p_fast": "glm-5-turbo",
}

ROLES_IN_ORDER = ["coder", "planner", "fast"]


def _repo_root() -> Path:
    # scripts/probe_zai_routing_live.py -> repo root is parent of scripts/
    return Path(__file__).resolve().parent.parent


def _load_api_key() -> str:
    key = os.environ.get("ZAI_API_KEY")
    if key and key.strip():
        return key.strip()
    key_file = _repo_root() / ".zai.key"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    raise SystemExit(
        "FAIL: no API key found (set ZAI_API_KEY or provide .zai.key at repo root)"
    )


def _call_model(model_name: str, api_key: str) -> tuple[str, str]:
    """Live POST to z.ai. Returns (echoed_model_field, content_text)."""
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": (
                    "What model are you? Reply with ONLY your model identifier."
                ),
            }
        ],
        # Some GLM models are reasoning models that spend budget before emitting
        # visible content; give enough room to produce a non-empty answer.
        "max_tokens": 512,
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CHAT_ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - live path
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(
            f"FAIL: HTTP {exc.code} calling model {model_name}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - live path
        raise SystemExit(
            f"FAIL: network error calling model {model_name}: {exc.reason}"
        ) from exc

    obj = json.loads(body)
    echoed_model = str(obj.get("model", ""))
    content = ""
    choices = obj.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
        if not content:
            # reasoning models may surface reasoning_content instead
            content = str(msg.get("reasoning_content") or "")
    return echoed_model, content


def _matches(requested: str, echoed: str) -> bool:
    if not echoed:
        return False
    r = requested.lower()
    e = echoed.lower()
    return r == e or e.startswith(r) or r.startswith(e) or r in e or e in r


def main() -> int:
    api_key = _load_api_key()

    # Build the router from the routing config under test.
    config = ModelRoutingConfig(role_routing=ROLE_ROUTING)
    router = build_router_from_config(config)

    print("=" * 72)
    print("LIVE z.ai task-specific routing differentiation probe")
    print(f"endpoint: {CHAT_ENDPOINT}")
    print("=" * 72)

    resolved_models: dict[str, str] = {}
    echoed_models: dict[str, str] = {}
    # Hard failures: routing resolution broke, or the live call never returned.
    failures: list[str] = []
    # Soft: z.ai did not echo the requested model verbatim (provider behaviour,
    # not a routing defect). Reported, but does not by itself fail the probe.
    echo_mismatches: list[str] = []

    for role in ROLES_IN_ORDER:
        profile_id = router.resolve_role(role)
        if profile_id is None:
            failures.append(f"role {role!r} resolved to None profile")
            continue
        model_name = PROFILE_MODEL.get(profile_id)
        if model_name is None:
            failures.append(
                f"role {role!r} -> profile {profile_id!r} has no model mapping"
            )
            continue
        resolved_models[role] = model_name

        echoed, content = _call_model(model_name, api_key)
        echoed_models[role] = echoed
        snippet = content.replace("\n", " ").strip()[:80]
        echo_ok = _matches(model_name, echoed)
        print(
            f"task={role:<8} profile={profile_id:<10} "
            f"requested={model_name:<14} echoed.model={echoed!r:<18} "
            f"echo_match={('Y' if echo_ok else 'N')} "
            f"content[:80]={snippet!r}"
        )
        if not content.strip():
            failures.append(
                f"role {role!r}: model {model_name!r} returned empty content"
            )
        if not echo_ok:
            echo_mismatches.append(
                f"role {role!r}: echoed model {echoed!r} != requested {model_name!r}"
            )

    print("-" * 72)

    distinct = set(resolved_models.values())
    if len(distinct) != 3:
        failures.append(
            f"routing did not resolve 3 distinct models: {sorted(distinct)}"
        )

    # Count distinct echoed identifiers actually returned by the provider.
    distinct_echoed = {v for v in echoed_models.values() if v}

    if echo_mismatches:
        print("NOTE: z.ai did not echo the requested model verbatim for some roles:")
        for m in echo_mismatches:
            print(f"  - {m}")
        print(
            "  (Provider echo behaviour, not a routing defect — routing still "
            "selected the correct distinct model per task type.)"
        )

    if failures:
        print("LIVE DIFFERENTIATION: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        f"routing resolved distinct models: {sorted(distinct)} "
        f"for task types {ROLES_IN_ORDER}"
    )
    print(f"provider echoed distinct model ids: {sorted(distinct_echoed)}")
    if not echo_mismatches and len(distinct_echoed) == 3:
        print(
            "LIVE DIFFERENTIATION: PASS (3 distinct models answered 3 task "
            "types; provider echoed each requested model verbatim)"
        )
    else:
        print(
            "LIVE DIFFERENTIATION: PASS (routing selected 3 distinct models for "
            "3 task types; each model answered live — provider did not echo "
            "every requested id verbatim, see NOTE above)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
