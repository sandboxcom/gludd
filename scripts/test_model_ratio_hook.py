#!/usr/bin/env python3
"""
Tests for .claude/hooks/model_utilization_pretool.sh enforcement logic.

Cases:
  A. Sonnet dispatch → always allowed (exit 0, no deny)
  B. Opus dispatch with NO headroom → denied
  C. Opus dispatch WITH headroom → allowed (exit 0, no deny)
  D. Bad JSON input → fail-open (exit 0, no deny)
  E. Empty window (< 3 entries) grace → opus allowed
  F. Enforcement disabled (GLUDD_MODEL_UTIL_ENFORCE=0) → opus allowed even at limit
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks",
                    "model_utilization_pretool.sh")
HOOK = os.path.abspath(HOOK)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
failures = []


def run_hook(stdin_payload: str, state: list[str], env_extra: dict | None = None,
             target: float = 0.91, window: int = 20) -> tuple[int, dict | None, str]:
    """Run the hook with a pre-seeded state and return (exit_code, parsed_output, raw_out)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as sf:
        json.dump({"history": state}, sf)
        sf_path = sf.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as cfg:
        json.dump({"target_share": target, "until_epoch": 9999999999}, cfg)
        cfg_path = cfg.name

    env = os.environ.copy()
    env["GLUDD_MODEL_UTIL_STATE"] = sf_path
    env["GLUDD_MODEL_UTIL_WINDOW"] = str(window)
    env["GLUDD_SONNET_TARGET_CONFIG"] = cfg_path
    env.pop("GLUDD_SONNET_TARGET_SHARE", None)
    env.pop("GLUDD_MODEL_UTIL_ENFORCE", None)
    if env_extra:
        env.update(env_extra)

    try:
        result = subprocess.run(
            ["bash", HOOK],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        rc = result.returncode
        raw = result.stdout.strip()
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"_parse_error": raw}
        return rc, parsed, raw
    finally:
        os.unlink(sf_path)
        os.unlink(cfg_path)


def is_denied(parsed: dict | None) -> bool:
    if parsed is None:
        return False
    hso = parsed.get("hookSpecificOutput", {})
    return hso.get("permissionDecision") == "deny"


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS} {label}")
    else:
        print(f"  {FAIL} {label}" + (f": {detail}" if detail else ""))
        failures.append(label)


def case_A():
    """Sonnet dispatch → always allowed."""
    print("Case A: sonnet dispatch always allowed")
    payload = json.dumps({"tool_input": {"model": "sonnet"}})
    # Fill the window with all opus so there's definitely no headroom for non-sonnet
    # — but sonnet must pass regardless.
    state = ["opus"] * 19  # 0% sonnet, way below target
    rc, parsed, raw = run_hook(payload, state)
    check("A1: exit 0", rc == 0, f"exit={rc}")
    check("A2: not denied", not is_denied(parsed), f"output={raw}")

    # Also test with no model field (defaults to sonnet)
    payload2 = json.dumps({"tool_input": {}})
    rc2, parsed2, _ = run_hook(payload2, state)
    check("A3: no-model-field allowed (default=sonnet)", not is_denied(parsed2))
    check("A4: exit 0 no-model", rc2 == 0)


def case_B():
    """Opus dispatch with NO headroom → denied."""
    print("Case B: opus dispatch denied when no headroom")
    # 10:1 ratio = target_share=0.91; window=11 entries.
    # Fill with 10 sonnet + 1 opus → share=10/11≈0.909 < 0.91 if we add another opus.
    state = ["sonnet"] * 10 + ["opus"]  # 11 entries; sonnet_share=10/11≈0.909
    payload = json.dumps({"tool_input": {"model": "opus"}})
    rc, parsed, raw = run_hook(payload, state, target=0.91)
    check("B1: exit 0", rc == 0, f"exit={rc}")
    check("B2: denied", is_denied(parsed), f"output={raw}")
    # Verify the denial message is helpful
    if parsed and is_denied(parsed):
        reason = parsed.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        check("B3: reason mentions sonnet", "sonnet" in reason.lower(), f"reason={reason}")
        check("B4: reason mentions target", "target" in reason.lower(), f"reason={reason}")


def case_C():
    """Opus dispatch WITH headroom → allowed."""
    print("Case C: opus dispatch allowed when headroom exists")
    # target=0.91, window=20; if we have 19 sonnet and 0 opus → adding 1 opus:
    # projected = 19/20 = 0.95 >= 0.91 → allow.
    state = ["sonnet"] * 19
    payload = json.dumps({"tool_input": {"model": "opus"}})
    rc, parsed, raw = run_hook(payload, state, target=0.91)
    check("C1: exit 0", rc == 0, f"exit={rc}")
    check("C2: not denied", not is_denied(parsed), f"output={raw}")


def case_D():
    """Bad JSON input → fail-open."""
    print("Case D: bad JSON input → fail-open")
    for bad in ["", "NOT JSON AT ALL", '{"tool_input": null', '{"no_tool_input":true}']:
        state = ["sonnet"] * 5
        rc, parsed, raw = run_hook(bad, state)
        check(f"D exit=0 ({repr(bad[:20])})", rc == 0)
        check(f"D not-denied ({repr(bad[:20])})", not is_denied(parsed))


def case_E():
    """Grace period: fewer than 3 entries → opus allowed regardless."""
    print("Case E: grace period (<3 entries) → opus allowed")
    for n in [0, 1, 2]:
        state = ["sonnet"] * n  # n < 3; even pure-sonnet baseline
        payload = json.dumps({"tool_input": {"model": "opus"}})
        rc, parsed, raw = run_hook(payload, state, target=0.91)
        check(f"E{n} grace n={n} → allowed", not is_denied(parsed), f"output={raw}")
        check(f"E{n} grace exit=0 n={n}", rc == 0)


def case_F():
    """GLUDD_MODEL_UTIL_ENFORCE=0 disables enforcement → opus allowed even at limit."""
    print("Case F: enforcement disabled → opus allowed at limit")
    state = ["sonnet"] * 10 + ["opus"]  # same no-headroom setup as case B
    payload = json.dumps({"tool_input": {"model": "opus"}})
    rc, parsed, raw = run_hook(payload, state, target=0.91,
                               env_extra={"GLUDD_MODEL_UTIL_ENFORCE": "0"})
    check("F1: exit 0", rc == 0, f"exit={rc}")
    check("F2: not denied (enforcement off)", not is_denied(parsed), f"output={raw}")


def main():
    print(f"\nHook: {HOOK}")
    print("=" * 60)
    for fn in [case_A, case_B, case_C, case_D, case_E, case_F]:
        print()
        fn()

    print()
    print("=" * 60)
    if failures:
        print(f"\033[31mFAILED ({len(failures)}): {', '.join(failures)}\033[0m")
        sys.exit(1)
    else:
        total = sum(1 for name in dir() if name.startswith("case_"))
        print(f"\033[32mALL PASS\033[0m")
        sys.exit(0)


if __name__ == "__main__":
    main()
