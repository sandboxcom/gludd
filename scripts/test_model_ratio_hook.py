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
    """Run the hook with a pre-seeded state and return (exit_code, parsed_output, raw_out).

    HERMETIC: the test must not inherit the host's GLUDD_MAIN_MODEL /
    .claude/main_model setting — that would flip enforcement off for ALL cases.
    We point GLUDD_MAIN_MODEL_FILE at a nonexistent path and strip GLUDD_MAIN_MODEL
    from the inherited env by default. Cases that explicitly test non-expensive
    detection (G/H) pass the override via env_extra.
    """
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
    # Isolate from host main-model config so enforcement-active cases aren't
    # accidentally skipped by a stray .claude/main_model on the test machine.
    env["GLUDD_MAIN_MODEL_FILE"] = "/nonexistent-test-main-model-isolated"
    env.pop("GLUDD_MAIN_MODEL", None)
    env.pop("OPENCODE_MODEL", None)
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

    # With the fix, a MISSING model field inherits the parent (opus) and is
    # treated as NON-sonnet. Against this all-opus window (no headroom) it is now
    # DENIED (previously it was wrongly waved through as "default sonnet").
    payload2 = json.dumps({"tool_input": {}})
    rc2, parsed2, raw2 = run_hook(payload2, state)
    check("A3: no-model-field treated non-sonnet → denied (no headroom)",
          is_denied(parsed2), f"output={raw2}")
    check("A4: exit 0 no-model", rc2 == 0)

    # ...but a no-model dispatch WITH ample sonnet headroom is still allowed.
    state_room = ["sonnet"] * 19
    rc3, parsed3, raw3 = run_hook(json.dumps({"tool_input": {}}), state_room)
    check("A5: no-model allowed when headroom exists",
          not is_denied(parsed3), f"output={raw3}")


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
    # Only genuine PARSE failures fail open. (A valid JSON object with no
    # tool_input is NOT a parse failure — it is a real no-model dispatch, now
    # treated as non-sonnet and gated, so it is covered by case A, not here.)
    for bad in ["", "NOT JSON AT ALL", '{"tool_input": null']:
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


def case_G():
    """Non-expensive main model (env override) → non-sonnet allowed even with no headroom.

    Rationale: the sonnet-ratio enforcer exists to bias subagents toward a CHEAP
    model when the PARENT is an EXPENSIVE one (opus). When the parent is itself
    cheap (glm-5.2 / haiku / etc.), there is no cost asymmetry to optimize and
    the harness may not expose model:"sonnet" on the Task tool — enforcement is
    skipped (record-only) so dispatch is never blocked by an unsatisfiable ratio.
    """
    print("Case G: non-expensive main model (env) → opus allowed at limit")
    state = ["sonnet"] * 10 + ["opus"]  # same no-headroom setup as case B
    payload = json.dumps({"tool_input": {"model": "opus"}})
    rc, parsed, raw = run_hook(payload, state, target=0.91,
                               env_extra={"GLUDD_MAIN_MODEL": "glm-5.2"})
    check("G1: exit 0", rc == 0, f"exit={rc}")
    check("G2: not denied (non-expensive main)", not is_denied(parsed), f"output={raw}")
    check("G3: no misleading advisory", raw == "" or "denied" not in raw, f"output={raw}")

    # Also cover a no-model dispatch (inherits cheap parent) — must pass.
    payload2 = json.dumps({"tool_input": {}})
    rc2, parsed2, raw2 = run_hook(payload2, state, target=0.91,
                                  env_extra={"GLUDD_MAIN_MODEL": "glm-5.2"})
    check("G4: no-model dispatch allowed (non-expensive parent)",
          not is_denied(parsed2), f"output={raw2}")


def case_H():
    """Non-expensive main model via config file → non-sonnet allowed at limit.

    Same as case G but the detection comes from a .claude/main_model file rather
    than an env var — this is the persistent per-environment path.
    """
    print("Case H: non-expensive main model (config file) → opus allowed at limit")
    main_model_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    main_model_file.write("glm-5.2\n")
    main_model_file.close()
    try:
        state = ["sonnet"] * 10 + ["opus"]  # no headroom
        payload = json.dumps({"tool_input": {"model": "opus"}})
        rc, parsed, raw = run_hook(payload, state, target=0.91,
                                   env_extra={"GLUDD_MAIN_MODEL_FILE": main_model_file.name})
        check("H1: exit 0", rc == 0, f"exit={rc}")
        check("H2: not denied (config-file non-expensive main)",
              not is_denied(parsed), f"output={raw}")
    finally:
        os.unlink(main_model_file.name)


def case_I():
    """Expensive main model (opus) → enforcement still active (regression guard).

    Ensures the main-model-aware skip did NOT silently disable enforcement for
    the real opus-main use case the enforcer was built for.
    """
    print("Case I: expensive main model (opus) → enforcement still active")
    state = ["sonnet"] * 10 + ["opus"]  # no headroom
    payload = json.dumps({"tool_input": {"model": "opus"}})
    rc, parsed, raw = run_hook(payload, state, target=0.91,
                               env_extra={"GLUDD_MAIN_MODEL": "claude-3-opus-20240229"})
    check("I1: exit 0", rc == 0, f"exit={rc}")
    check("I2: DENIED (expensive main, no headroom)", is_denied(parsed), f"output={raw}")

    # Unknown/empty main model → fail-safe to expensive (preserve old behavior).
    rc2, parsed2, raw2 = run_hook(payload, state, target=0.91,
                                  env_extra={"GLUDD_MAIN_MODEL": ""})
    check("I3: unknown main model → fail-safe deny", is_denied(parsed2), f"output={raw2}")


def main():
    print(f"\nHook: {HOOK}")
    print("=" * 60)
    for fn in [case_A, case_B, case_C, case_D, case_E, case_F, case_G, case_H, case_I]:
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
