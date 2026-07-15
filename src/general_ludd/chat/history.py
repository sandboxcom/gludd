"""ChatHistory: read-only session index wrapper with search and stats.

Provides a structured interface over the ~/.gludd/chat_history/ directory
and its index.json file.  Complements ChatSession which owns the write path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from general_ludd.chat.session import DEFAULT_HISTORY_DIR, SESSION_INDEX_FILE


class ChatHistory:
    """Structured access to persisted chat session history."""

    def __init__(self, history_dir: Path | None = None) -> None:
        self.history_dir = history_dir or DEFAULT_HISTORY_DIR
        self._index_path = self.history_dir / SESSION_INDEX_FILE

    def _read_index(self) -> list[dict[str, object]]:
        if not self._index_path.exists():
            return []
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            return cast("list[dict[str, object]]", raw.get("sessions", []))
        except (json.JSONDecodeError, OSError):
            return []

    def list_sessions(
        self,
        limit: int = 20,
        model_filter: str | None = None,
    ) -> list[dict[str, object]]:
        sessions = sorted(
            self._read_index(),
            key=lambda s: str(s.get("timestamp", "")),
            reverse=True,
        )
        if model_filter:
            sessions = [
                s for s in sessions
                if model_filter.lower() in str(s.get("model", "")).lower()
            ]
        return sessions[:limit]

    def get_session(self, file_path: str) -> dict[str, object] | None:
        sessions = self._read_index()
        for s in sessions:
            if s.get("file") == file_path:
                return s
        return None

    def get_messages(self, file_path: str) -> list[dict[str, object]]:
        session_path = Path(file_path)
        if not session_path.exists():
            return []
        messages: list[dict[str, object]] = []
        try:
            for line in session_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return messages

    def search(self, query: str, limit: int = 20) -> list[dict[str, object]]:
        query_lower = query.lower()
        results: list[dict[str, object]] = []
        for session in self._read_index():
            preview = str(session.get("preview", "")).lower()
            model = str(session.get("model", "")).lower()
            file_path = str(session.get("file", ""))
            pre_hit = query_lower in preview or query_lower in model
            deep_hit = False
            if not pre_hit and file_path:
                messages = self.get_messages(file_path)
                for m in messages:
                    content = str(m.get("content", "")).lower()
                    if query_lower in content:
                        deep_hit = True
                        break
            if pre_hit or deep_hit:
                entry = dict(session)
                entry["match_source"] = "preview" if pre_hit else "content"
                results.append(entry)
            if len(results) >= limit:
                break
        return results

    def delete_session(self, file_path: str) -> bool:
        session_path = Path(file_path)
        removed_file = False
        if session_path.exists():
            session_path.unlink()
            removed_file = True
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            sessions: list[dict[str, object]] = raw.get("sessions", [])
            updated = [s for s in sessions if s.get("file") != file_path]
            changed = len(updated) < len(sessions)
            raw["sessions"] = updated
            self._index_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            return removed_file or changed
        except (json.JSONDecodeError, OSError):
            return removed_file

    def stats(self) -> dict[str, object]:
        sessions = self._read_index()
        if not sessions:
            return {
                "total_sessions": 0,
                "total_messages": 0,
                "unique_models": [],
            }
        total_messages = sum(cast(int, s.get("message_count", 0)) for s in sessions)
        models: set[str] = set()
        for s in sessions:
            m = str(s.get("model", "unknown"))
            if m:
                models.add(m)
        return {
            "total_sessions": len(sessions),
            "total_messages": total_messages,
            "unique_models": sorted(models),
        }
