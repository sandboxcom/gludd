#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from general_ludd.history.git_indexer import GitHistoryIndexer


def cmd_index(args: argparse.Namespace) -> int:
    indexer = GitHistoryIndexer(repo_path=args.repo, db_path=args.db)
    count = indexer.index()
    print(f"Indexed {count} commits from {args.repo} -> {args.db}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    indexer = GitHistoryIndexer(repo_path=args.repo, db_path=args.db)
    results = indexer.search(
        query=args.query or "",
        since=args.since or "",
        author=args.author or "",
        path_filter=args.path or "",
        limit=args.limit,
        offset=args.offset,
    )
    if args.json:
        json.dump([r.to_dict() for r in results], sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif not results:
        # Make "zero hits" explicit rather than silent, so an empty result is
        # distinguishable from a command that produced no output because it
        # died. JSON mode stays pure JSON (an empty list) for machine callers.
        print("No matches")
    else:
        for r in results:
            print(f"{r.hash[:8]}  {r.date[:10]}  {r.author:<20}  {r.message[:80]}")
            for p in r.matched_paths:
                print(f"    {p}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    indexer = GitHistoryIndexer(repo_path=args.repo, db_path=args.db)
    stats = indexer.stats()
    if args.json:
        json.dump(stats, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Total commits: {stats['total_commits']}")
        print(f"Last indexed:  {stats['last_indexed']}")
        print(f"Unique files:  {stats['unique_files']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Git history SQLite indexer")
    parser.add_argument("--repo", default=".", help="Path to git repo (default: .)")
    parser.add_argument("--db", default=".gludd/git_history.db", help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="Index git log into SQLite")

    search_parser = sub.add_parser("search", help="Search indexed history")
    search_parser.add_argument("--query", "-q", default="", help="Search commit messages and file paths")
    search_parser.add_argument("--since", default="", help="Filter commits since date (YYYY-MM-DD or ISO)")
    search_parser.add_argument("--author", default="", help="Filter by author (partial match)")
    search_parser.add_argument("--path", default="", help="Filter by file path (partial match)")
    search_parser.add_argument("--limit", type=int, default=100)
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")

    stats_parser = sub.add_parser("stats", help="Show index statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    if args.command == "index":
        return cmd_index(args)
    elif args.command == "search":
        return cmd_search(args)
    elif args.command == "stats":
        return cmd_stats(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
