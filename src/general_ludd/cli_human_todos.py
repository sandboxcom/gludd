"""CLI subcommand: ``gludd human-todo``.

A separate interface for humans to see and resolve the requests that agents
file against them (HumanTodo). Mirrors the daemon's ``/api/human-todos``
endpoints. Write operations (done/dismiss/in-progress/comment) send the
``GLUDD_AUTH_PSK`` env var as a Bearer token so the daemon's PSK middleware
authenticates them.
"""

from __future__ import annotations

import argparse
import json as _json
import os
import sys
import time
from typing import Any

import httpx


def _psk_headers() -> dict[str, str]:
    psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if psk:
        headers["Authorization"] = "Bearer " + psk
    return headers


def _http(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    params: Any = None,
    timeout: float = 10.0,
    ok_codes: tuple[int, ...] = (200, 201),
) -> Any:
    try:
        resp = httpx.request(
            method,
            url,
            json=json_body,
            params=params,
            headers=_psk_headers(),
            timeout=timeout,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code not in ok_codes:
        print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    try:
        return resp.json()
    except Exception:
        return None


def _print_json(obj: Any) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(no human-todos)")
        return
    headers = ["ID", "STATUS", "PRIORITY", "CATEGORY", "AGENT", "TITLE"]
    width_id = max(len(headers[0]), max((len(str(r.get("id", ""))) for r in rows), default=0))
    width_st = max(len(headers[1]), max((len(str(r.get("status", ""))) for r in rows), default=0))
    width_pr = max(len(headers[2]), max((len(str(r.get("priority", ""))) for r in rows), default=0))
    width_ca = max(len(headers[3]), max((len(str(r.get("category", ""))) for r in rows), default=0))
    width_ag = max(len(headers[4]), max((len(str(r.get("agent_id", ""))) for r in rows), default=0))
    width_ti = max(len(headers[5]), max((len(str(r.get("title", ""))[:60]) for r in rows), default=0))
    fmt = f"{{:<{width_id}}} {{:<{width_st}}} {{:<{width_pr}}} {{:<{width_ca}}} {{:<{width_ag}}} {{:<{width_ti}}}"
    print(fmt.format(*headers))
    print(fmt.format("-" * width_id, "-" * width_st, "-" * width_pr, "-" * width_ca, "-" * width_ag, "-" * width_ti))
    for r in rows:
        title = str(r.get("title", ""))[:60]
        agent = str(r.get("agent_id", ""))[:20]
        print(
            fmt.format(
                str(r.get("id", "")),
                str(r.get("status", "")),
                str(r.get("priority", "")),
                str(r.get("category", "")),
                agent,
                title,
            )
        )


def _cmd_list(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.status:
        params["status"] = args.status
    if args.category:
        params["category"] = args.category
    if args.priority:
        params["priority"] = args.priority
    if args.agent_id:
        params["agent_id"] = args.agent_id
    rows = _http("GET", f"{args.daemon_url}/api/human-todos", params=params or None) or []
    if args.json:
        _print_json(rows)
        return
    _print_table(rows)


def _cmd_show(args: argparse.Namespace) -> None:
    row = _http("GET", f"{args.daemon_url}/api/human-todos/{args.id}")
    if args.json:
        _print_json(row)
        return
    if not row:
        print("not found")
        return
    print(f"ID:          {row.get('id')}")
    print(f"Status:      {row.get('status')}")
    print(f"Priority:    {row.get('priority')}")
    print(f"Category:    {row.get('category')}")
    print(f"Agent:       {row.get('agent_id')}")
    if row.get("parent_agent_todo_id"):
        print(f"Parent todo: {row.get('parent_agent_todo_id')}")
    print(f"Created:     {row.get('created_at')}")
    print(f"Updated:     {row.get('updated_at')}")
    if row.get("resolved_at"):
        print(f"Resolved:    {row.get('resolved_at')} by {row.get('human_resolver')}")
    print()
    print("Title:")
    print(row.get("title", ""))
    print()
    print("Body:")
    print(row.get("body", ""))
    if row.get("human_resolution"):
        print()
        print("Resolution:")
        print(row.get("human_resolution"))
    tags = row.get("tags") or []
    if tags:
        print()
        print(f"Tags: {', '.join(tags)}")


def _cmd_done(args: argparse.Namespace) -> None:
    row = _http(
        "PATCH",
        f"{args.daemon_url}/api/human-todos/{args.id}",
        json_body={
            "status": "done",
            "human_resolution": args.resolution,
            "human_resolver": args.resolver,
        },
    )
    if args.json:
        _print_json(row)
    else:
        print(f"Marked done: {args.id}")


def _cmd_dismiss(args: argparse.Namespace) -> None:
    if not args.reason:
        print("Error: --reason is required for dismiss", file=sys.stderr)
        sys.exit(2)
    row = _http(
        "PATCH",
        f"{args.daemon_url}/api/human-todos/{args.id}",
        json_body={
            "status": "dismissed",
            "human_resolution": args.reason,
            "human_resolver": args.resolver,
        },
    )
    if args.json:
        _print_json(row)
    else:
        print(f"Dismissed: {args.id}")


def _cmd_in_progress(args: argparse.Namespace) -> None:
    row = _http(
        "PATCH",
        f"{args.daemon_url}/api/human-todos/{args.id}",
        json_body={"status": "in_progress"},
    )
    if args.json:
        _print_json(row)
    else:
        print(f"Marked in-progress: {args.id}")


def _cmd_comment(args: argparse.Namespace) -> None:
    # Stored as a tag prefix so we don't need a separate comment table.
    text = args.text
    if not text:
        print("Error: comment text required", file=sys.stderr)
        sys.exit(2)
    tag = "comment:" + text if not text.startswith("comment:") else text
    row = _http(
        "POST",
        f"{args.daemon_url}/api/human-todos/{args.id}/tags",
        json_body={"tag": tag},
    )
    if args.json:
        _print_json(row)
    else:
        print(f"Commented: {args.id}")


def _cmd_watch(args: argparse.Namespace) -> None:
    seen: set[str] = set()
    last = None
    poll = max(1, args.poll)
    print(f"watching /api/human-todos/feed (poll={poll}s, ctrl-C to stop)")
    try:
        while True:
            params: dict[str, Any] = {}
            if last is not None:
                params["since"] = last
            rows = _http("GET", f"{args.daemon_url}/api/human-todos/feed", params=params or None) or []
            for r in rows:
                key = f"{r.get('id')}@{r.get('updated_at')}"
                if key in seen:
                    continue
                seen.add(key)
                print(f"[{r.get('updated_at')}] {r.get('id')} {r.get('status')} {r.get('category')} — {r.get('title')}")
                if r.get("human_resolution"):
                    print(f"    resolution: {r.get('human_resolution')}")
            if rows:
                last = rows[-1].get("updated_at")
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nstopped.")


def _cmd_stats(args: argparse.Namespace) -> None:
    rows = _http("GET", f"{args.daemon_url}/api/human-todos", params={"limit": 500}) or []
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for r in rows:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
        by_category[r.get("category", "?")] = by_category.get(r.get("category", "?"), 0) + 1
        by_priority[r.get("priority", "?")] = by_priority.get(r.get("priority", "?"), 0) + 1
    stats = {
        "total": len(rows),
        "by_status": by_status,
        "by_category": by_category,
        "by_priority": by_priority,
    }
    if args.json:
        _print_json(stats)
        return
    print(f"total: {len(rows)}")
    for label, bucket in (("status", by_status), ("category", by_category), ("priority", by_priority)):
        print(f"\nby {label}:")
        for k, v in sorted(bucket.items()):
            print(f"  {k:<24} {v}")


def add_human_todo_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``human-todo`` subcommand tree for bot-to-human requests."""
    p = sub.add_parser("human-todo", help="Manage bot-to-human requests (HumanTodo)")
    p.set_defaults(func=None)
    hsub = p.add_subparsers(dest="human_todo_command")

    lst = hsub.add_parser("list", help="List human-todos (table)")
    lst.add_argument("--status", default=None)
    lst.add_argument("--category", default=None)
    lst.add_argument("--priority", default=None)
    lst.add_argument("--agent-id", dest="agent_id", default=None)
    lst.add_argument("--daemon-url", default="http://localhost:8000")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=_cmd_list)

    show = hsub.add_parser("show", help="Full detail of one human-todo")
    show.add_argument("id")
    show.add_argument("--daemon-url", default="http://localhost:8000")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)

    done = hsub.add_parser("done", help="Mark a human-todo done")
    done.add_argument("id")
    done.add_argument("--resolution", required=True)
    done.add_argument("--resolver", default=os.environ.get("USER", "operator"))
    done.add_argument("--daemon-url", default="http://localhost:8000")
    done.add_argument("--json", action="store_true")
    done.set_defaults(func=_cmd_done)

    dis = hsub.add_parser("dismiss", help="Dismiss a human-todo (agent should try another approach)")
    dis.add_argument("id")
    dis.add_argument("--reason", default=None)
    dis.add_argument("--resolver", default=os.environ.get("USER", "operator"))
    dis.add_argument("--daemon-url", default="http://localhost:8000")
    dis.add_argument("--json", action="store_true")
    dis.set_defaults(func=_cmd_dismiss)

    inp = hsub.add_parser("in-progress", help="Mark a human-todo in-progress")
    inp.add_argument("id")
    inp.add_argument("--daemon-url", default="http://localhost:8000")
    inp.add_argument("--json", action="store_true")
    inp.set_defaults(func=_cmd_in_progress)

    com = hsub.add_parser("comment", help="Add a comment (stored as a comment: tag)")
    com.add_argument("id")
    com.add_argument("text")
    com.add_argument("--daemon-url", default="http://localhost:8000")
    com.add_argument("--json", action="store_true")
    com.set_defaults(func=_cmd_comment)

    watch = hsub.add_parser("watch", help="Poll the feed for live updates")
    watch.add_argument("--poll", type=int, default=5)
    watch.add_argument("--daemon-url", default="http://localhost:8000")
    watch.set_defaults(func=_cmd_watch)

    st = hsub.add_parser("stats", help="Counts by status/category/priority")
    st.add_argument("--daemon-url", default="http://localhost:8000")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_cmd_stats)
