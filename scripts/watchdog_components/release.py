"""Fail-closed release, CI-tag, and committed-secret health checks."""

from __future__ import annotations

from typing import Any

from scripts.watchdog_components.types import ReleaseData


def _check_stale_release(runtime: Any) -> dict[str, object] | None:
    """Detect tags that exist but have no GitHub Release after a timeout.

    A tag pushed more than STALE_RELEASE_MINUTES ago that still has no
    release is stale — the CI release pipeline either failed or was never
    triggered. Writes to /tmp/gludd-stale-release.json.
    """
    if not runtime._should_run_check("stale_release", cooldown_secs=runtime.RELEASE_CHECK_COOLDOWN_SECS):
        return None
    try:
        tag_commits = runtime._get_tags_with_commits()
        if not tag_commits:
            runtime._mark_check_run("stale_release")
            runtime.Path(runtime.STALE_RELEASE_FILE).write_text(
                runtime.json.dumps({"ts": runtime.time.time(), "stale": False})
            )
            return None
        stale_findings: list[dict[str, object]] = []
        now = runtime.time.time()
        for tag, sha in tag_commits:
            try:
                commit_result = runtime.subprocess.run(
                    ["git", "log", "-1", "--format=%ct", sha],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(runtime._WORKSPACE),
                )
                commit_ts = float(commit_result.stdout.strip() or 0)
            except Exception:
                continue
            tag_age_minutes = (now - commit_ts) / 60.0 if commit_ts > 0 else 0
            if tag_age_minutes < runtime.STALE_RELEASE_MINUTES:
                continue
            exists, release_data = runtime._gh_release_exists(tag)
            if exists and (not release_data.get("_error")):
                continue
            error = release_data.get("_error", "")
            if error:
                runtime._log(f"STALE RELEASE WARN: tag {tag} (age {tag_age_minutes:.0f}m) — gh error: {error}")
                continue
            runtime._log(f"STALE RELEASE: tag {tag} (age {tag_age_minutes:.0f}m) has no GitHub Release")
            stale_findings.append(
                {
                    "tag": tag,
                    "sha": sha,
                    "age_minutes": round(tag_age_minutes, 1),
                    "reason": "no release created within timeout",
                }
            )
            if len(tag) > 0 and commit_ts > 0:
                break
        if stale_findings:
            stale_data = {"ts": runtime.time.time(), "stale": True, "findings": stale_findings}
            runtime.Path(runtime.STALE_RELEASE_FILE).write_text(runtime.json.dumps(stale_data))
            runtime._mark_check_run("stale_release")
            return stale_data
        runtime.Path(runtime.STALE_RELEASE_FILE).write_text(
            runtime.json.dumps({"ts": runtime.time.time(), "stale": False})
        )
        runtime._mark_check_run("stale_release")
        return None
    except Exception as e:
        runtime._log(f"_check_stale_release error: {e}")
        runtime._mark_check_run("stale_release")
        return None


def _check_ci_red_after_tag_push(runtime: Any) -> dict[str, object] | None:
    """Detect when a tag push exists but CI is red (release blocked).

    If a recent tag has a CI run that is FAILURE, the release pipeline is
    blocked. Returns a findings dict or None.
    """
    if not runtime._should_run_check("ci_red_after_tag", cooldown_secs=runtime.CI_CHECK_INTERVAL):
        return None
    try:
        tags = runtime._get_tags_with_commits()
        if not tags:
            runtime._mark_check_run("ci_red_after_tag")
            return None
        latest_tag, tag_sha = tags[0]
        result = runtime.subprocess.run(
            [
                "gh",
                "run",
                "list",
                f"--commit={tag_sha}",
                "--json",
                "status,conclusion,createdAt,databaseId",
                "--jq",
                ".[0]",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        runtime._mark_check_run("ci_red_after_tag")
        if not result.stdout.strip():
            return None
        decoded: object = runtime.json.loads(result.stdout)
        data = runtime._as_record(decoded)
        if data is None:
            return None
        conclusion = data.get("conclusion", "")
        status = data.get("status", "")
        if conclusion == "failure" or (status == "completed" and conclusion != "success"):
            runtime._log(f"CI RED AFTER TAG PUSH: tag={latest_tag} sha={tag_sha[:8]} conclusion={conclusion}")
            return {
                "ci_red_after_tag": True,
                "tag": latest_tag,
                "sha": tag_sha,
                "conclusion": conclusion,
                "status": status,
                "run_id": data.get("databaseId"),
            }
    except runtime.subprocess.TimeoutExpired:
        runtime._mark_check_run("ci_red_after_tag")
    except Exception as e:
        runtime._log(f"_check_ci_red_after_tag_push error: {e}")
        runtime._mark_check_run("ci_red_after_tag")
    return None


def _get_tags_with_commits(runtime: Any) -> list[tuple[str, str]]:
    """Return list of (tag, commit_hash) for all tags, newest first."""
    try:
        result = runtime.subprocess.run(
            ["git", "for-each-ref", "--sort=-creatordate", "--format=%(refname:short) %(objectname)", "refs/tags"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(runtime._WORKSPACE),
        )
        if result.returncode != 0:
            return []
        pairs: list[tuple[str, str]] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                pairs.append((parts[0], parts[1]))
        return pairs
    except Exception:
        return []


def _check_secrets_committed(runtime: Any) -> dict[str, object] | None:
    """Periodically scan for secrets committed to tracked files.

    Runs `make secrets-scan` which checks against `.secrets.baseline`.
    Writes findings to /tmp/gludd-secrets-violation.json.
    """
    if not runtime._should_run_check("secrets_scan", cooldown_secs=runtime.SECRETS_SCAN_COOLDOWN_SECS):
        return None
    try:
        result = runtime.subprocess.run(
            ["make", "secrets-scan"], capture_output=True, text=True, timeout=60, cwd=str(runtime._WORKSPACE)
        )
        runtime._mark_check_run("secrets_scan")
        output = result.stdout + result.stderr
        if result.returncode != 0:
            runtime._log(f"SECRETS VIOLATION: secrets-scan exited {result.returncode}")
            violation_data = {
                "ts": runtime.time.time(),
                "violation": True,
                "exit_code": result.returncode,
                "output_snippet": output[:500],
            }
            runtime.Path(runtime.SECRETS_VIOLATION_FILE).write_text(runtime.json.dumps(violation_data))
            return violation_data
        runtime.Path(runtime.SECRETS_VIOLATION_FILE).write_text(
            runtime.json.dumps({"ts": runtime.time.time(), "violation": False})
        )
        return None
    except runtime.subprocess.TimeoutExpired:
        runtime._mark_check_run("secrets_scan")
        runtime._log("SECRETS SCAN TIMEOUT: >60s")
        return {"ts": runtime.time.time(), "violation": None, "reason": "timeout"}
    except Exception as e:
        runtime._mark_check_run("secrets_scan")
        runtime._log(f"_check_secrets_committed error: {e}")
        return None


def _get_tags(runtime: Any) -> list[str]:
    """Return all annotated/lightweight tags in the repo, newest first."""
    try:
        result = runtime.subprocess.run(
            ["git", "tag", "--sort=-creatordate"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(runtime._WORKSPACE),
        )
        if result.returncode != 0:
            return []
        return [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
    except Exception:
        return []


def _gh_release_exists(runtime: Any, tag: str) -> tuple[bool, ReleaseData]:
    """Check if a GitHub Release exists for the given tag.

    Returns (exists, release_data). release_data contains keys:
      - isDraft, isPrerelease, assetCount, publishedAt
    """
    try:
        result = runtime.subprocess.run(
            ["gh", "release", "view", tag, "--json", "isDraft,isPrerelease,assets,publishedAt,url"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return (False, {})
        decoded: object = runtime.json.loads(result.stdout)
        data = runtime._as_record(decoded)
        if data is None:
            return (False, {"_error": "invalid response"})
        assets = data.get("assets", [])
        asset_count = len(assets) if isinstance(assets, list) else 0
        return (
            True,
            {
                "isDraft": bool(data.get("isDraft", True)),
                "isPrerelease": bool(data.get("isPrerelease", False)),
                "assetCount": asset_count,
                "publishedAt": runtime._as_text(data.get("publishedAt")),
                "url": runtime._as_text(data.get("url")),
            },
        )
    except runtime.subprocess.TimeoutExpired:
        return (False, {"_error": "timeout"})
    except Exception as e:
        return (False, {"_error": str(e)})


def _check_release_completeness(runtime: Any) -> dict[str, object] | None:
    """Verify that the latest tag has a complete GitHub Release with expected artifacts.

    Writes to /tmp/gludd-release-completeness.json for enforce-stop.ts consumption.
    Returns a findings dict or None.
    """
    if not runtime._should_run_check("release_completeness", cooldown_secs=runtime.RELEASE_CHECK_COOLDOWN_SECS):
        return None
    try:
        tags = runtime._get_tags()
        if not tags:
            runtime._mark_check_run("release_completeness")
            runtime.Path(runtime.RELEASE_COMPLETENESS_FILE).write_text(
                runtime.json.dumps({"ts": runtime.time.time(), "incomplete": False, "reason": "no tags found"})
            )
            return None
        latest_tag = tags[0]
        exists, release_data = runtime._gh_release_exists(latest_tag)
        errors = release_data.get("_error")
        if errors:
            runtime._log(f"RELEASE CHECK SKIPPED: gh API error for tag {latest_tag}: {errors}")
            runtime._mark_check_run("release_completeness")
            return None
        if not exists:
            runtime._log(f"RELEASE INCOMPLETE: tag {latest_tag} has no GitHub Release")
            result_data = {
                "ts": runtime.time.time(),
                "tag": latest_tag,
                "incomplete": True,
                "reason": "no release created",
                "assetCount": 0,
            }
            runtime.Path(runtime.RELEASE_COMPLETENESS_FILE).write_text(runtime.json.dumps(result_data))
            runtime._mark_check_run("release_completeness")
            return result_data
        is_draft = release_data.get("isDraft", True)
        asset_count = release_data.get("assetCount", 0)
        if is_draft:
            runtime._log(f"RELEASE INCOMPLETE: tag {latest_tag} release is still a draft, {asset_count} assets")
            result_data = {
                "ts": runtime.time.time(),
                "tag": latest_tag,
                "incomplete": True,
                "reason": "release is draft" if asset_count == 0 else f"draft with {asset_count} assets",
                "assetCount": asset_count,
                "isDraft": True,
            }
            runtime.Path(runtime.RELEASE_COMPLETENESS_FILE).write_text(runtime.json.dumps(result_data))
            runtime._mark_check_run("release_completeness")
            return result_data
        if asset_count == 0:
            runtime._log(f"RELEASE INCOMPLETE: tag {latest_tag} release has 0 artifacts")
            result_data = {
                "ts": runtime.time.time(),
                "tag": latest_tag,
                "incomplete": True,
                "reason": "zero artifacts",
                "assetCount": 0,
                "isDraft": is_draft,
            }
            runtime.Path(runtime.RELEASE_COMPLETENESS_FILE).write_text(runtime.json.dumps(result_data))
            runtime._mark_check_run("release_completeness")
            return result_data
        runtime.Path(runtime.RELEASE_COMPLETENESS_FILE).write_text(
            runtime.json.dumps(
                {
                    "ts": runtime.time.time(),
                    "tag": latest_tag,
                    "incomplete": False,
                    "reason": f"ok — {asset_count} assets",
                    "assetCount": asset_count,
                    "isDraft": is_draft,
                }
            )
        )
        runtime._mark_check_run("release_completeness")
        return None
    except Exception as e:
        runtime._log(f"_check_release_completeness error: {e}")
        runtime._mark_check_run("release_completeness")
        return None
