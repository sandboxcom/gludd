"""Aho-Corasick automaton: trie construction, failure links (BFS),
output links, and streaming pattern matching. Pure-Python, stdlib only.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator


class AhoCorasick:
    """Builds the automaton from a list of pattern strings, then searches
    text for all occurrences in a single pass (`search`).

    Each match is yielded as ``(end_index, pattern)``.
    """

    def __init__(self, patterns: list[str]) -> None:
        self._patterns = list(patterns)
        self._goto: dict[int, dict[str, int]] = {0: {}}
        self._fail: dict[int, int] = {0: 0}
        self._output: dict[int, list[str]] = {}
        self._built = False

    def _build(self) -> None:
        if self._built:
            return
        self._build_trie()
        self._build_failure_links()
        self._built = True

    # ── trie ────────────────────────────────────────────────────────

    def _build_trie(self) -> None:
        for pat in self._patterns:
            if not pat:
                continue
            state = 0
            for ch in pat:
                nxt = self._goto[state].get(ch)
                if nxt is None:
                    nxt = len(self._goto)
                    self._goto[state][ch] = nxt
                    self._goto[nxt] = {}
                state = nxt
            self._output.setdefault(state, []).append(pat)

    # ── failure / output links ──────────────────────────────────────

    def _build_failure_links(self) -> None:
        queue: deque[int] = deque()
        depth1_keys: set[str] = set()

        # depth-1 children point back to root
        for ch, child in self._goto[0].items():
            self._fail[child] = 0
            queue.append(child)
            depth1_keys.add(ch)

        # BFS over remaining states
        while queue:
            r = queue.popleft()
            for ch, s in list(self._goto.get(r, {}).items()):
                queue.append(s)
                # follow failure link until a transition on ch exists
                f = self._fail[r]
                while f != 0 and ch not in self._goto.get(f, {}):
                    f = self._fail.get(f, 0)
                self._fail[s] = self._goto.get(f, {}).get(ch, 0)
                # merge output of failure state
                self._output.setdefault(s, []).extend(self._output.get(self._fail[s], []))

    # ── search ──────────────────────────────────────────────────────

    def search(self, text: str) -> Iterator[tuple[int, str]]:
        """Yield ``(end_index, pattern)`` for every match found in *text*.

        Matches are emitted left-to-right in the order their end index is
        reached. When multiple patterns end at the same position they are
        yielded in trie-insertion order.
        """
        self._build()
        state = 0
        for i, ch in enumerate(text):
            while state != 0 and ch not in self._goto.get(state, {}):
                state = self._fail.get(state, 0)
            nxt = self._goto.get(state, {}).get(ch)
            if nxt is not None:
                state = nxt
            # emit all patterns that end at this position
            out = self._output.get(state, [])
            if out:
                for pat in out:
                    yield (i, pat)

    # ── introspection helpers (for tests) ───────────────────────────

    @property
    def state_count(self) -> int:
        self._build()
        return len(self._goto)

    @property
    def failure(self) -> dict[int, int]:
        self._build()
        return dict(self._fail)

    @property
    def output_map(self) -> dict[int, list[str]]:
        self._build()
        return {k: list(v) for k, v in self._output.items()}
