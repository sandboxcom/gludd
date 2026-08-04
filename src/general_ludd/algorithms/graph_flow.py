"""Maximum flow algorithms: Ford-Fulkerson (DFS), Edmonds-Karp (BFS),
Dinic (level graph + blocking flow), min-cut via reachable set, and
bipartite maximum matching via flow reduction.

Pure-Python, stdlib only.
"""

from __future__ import annotations

from collections import deque

Node = str
CapacityGraph = dict[Node, dict[Node, int]]
FlowGraph = dict[Node, dict[Node, int]]


# ── Internal helpers (residual capacity) ──────────────────────────────


def _build_residual(
    graph: CapacityGraph,
) -> dict[tuple[Node, Node], int]:
    cap: dict[tuple[Node, Node], int] = {}
    for u in graph:
        for v, c in graph[u].items():
            cap[(u, v)] = c
            cap.setdefault((v, u), 0)
    return cap


def _neighbors(cap: dict[tuple[Node, Node], int], u: Node) -> list[Node]:
    return sorted({b for (a, b) in cap if a == u and cap[(a, b)] > 0})


# ── Ford-Fulkerson (DFS augmenting paths) ─────────────────────────────


def _ff_dfs(
    node: Node,
    sink: Node,
    f: int,
    cap: dict[tuple[Node, Node], int],
    visited: set[Node],
) -> int:
    if node == sink:
        return f
    visited.add(node)
    for v in _neighbors(cap, node):
        if v in visited:
            continue
        residual = cap.get((node, v), 0)
        if residual > 0:
            pushed = _ff_dfs(v, sink, min(f, residual), cap, visited)
            if pushed > 0:
                cap[(node, v)] -= pushed
                cap[(v, node)] = cap.get((v, node), 0) + pushed
                return pushed
    return 0


def ford_fulkerson(
    graph: CapacityGraph,
    source: Node,
    sink: Node,
) -> tuple[int, FlowGraph]:
    if source == sink:
        return 0, {u: {} for u in graph}
    cap = _build_residual(graph)
    total: int = 0
    while True:
        visited: set[Node] = set()
        pushed = _ff_dfs(source, sink, 10**18, cap, visited)
        if pushed == 0:
            break
        total += pushed

    flow: FlowGraph = {u: {} for u in graph}
    for u in graph:
        for v, c in graph[u].items():
            fwd = c - cap.get((u, v), 0)
            if fwd > 0:
                flow[u][v] = fwd
    return total, flow


# ── Edmonds-Karp (BFS shortest augmenting path) ───────────────────────


def _ek_bfs_path(
    cap: dict[tuple[Node, Node], int],
    source: Node,
    sink: Node,
) -> tuple[list[Node] | None, dict[Node, Node | None]]:
    parent: dict[Node, Node | None] = {source: None}
    q: deque[Node] = deque([source])
    while q:
        u = q.popleft()
        if u == sink:
            break
        for v in _neighbors(cap, u):
            if v not in parent:
                parent[v] = u
                q.append(v)
    if sink not in parent:
        return None, parent
    path: list[Node] = []
    cur: Node | None = sink
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path, parent


def edmonds_karp(
    graph: CapacityGraph,
    source: Node,
    sink: Node,
) -> tuple[int, FlowGraph, dict[Node, Node | None]]:
    if source == sink:
        return 0, {u: {} for u in graph}, {source: None}
    cap = _build_residual(graph)
    total: int = 0
    last_parent: dict[Node, Node | None] = {}
    while True:
        path, parent = _ek_bfs_path(cap, source, sink)
        if path is None:
            last_parent = parent
            break
        bottleneck = min(cap[(path[i], path[i + 1])] for i in range(len(path) - 1))
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            cap[(u, v)] -= bottleneck
            cap[(v, u)] = cap.get((v, u), 0) + bottleneck
        total += bottleneck

    flow: FlowGraph = {u: {} for u in graph}
    for u in graph:
        for v, c in graph[u].items():
            fwd = c - cap.get((u, v), 0)
            if fwd > 0:
                flow[u][v] = fwd
    return total, flow, last_parent


# ── Min-Cut (reachable from source in residual graph after max flow) ──


def min_cut(
    graph: CapacityGraph,
    source: Node,
    sink: Node,
) -> tuple[set[Node], set[Node], int]:
    total, _flow, last_parent = edmonds_karp(graph, source, sink)
    source_side: set[Node] = set(last_parent.keys())
    sink_side: set[Node] = set(graph) - source_side
    return source_side, sink_side, total


# ── Dinic (level graph + blocking flow) ───────────────────────────────


def _dinic_bfs_level(
    cap: dict[tuple[Node, Node], int],
    source: Node,
    sink: Node,
) -> dict[Node, int]:
    level: dict[Node, int] = {source: 0}
    q: deque[Node] = deque([source])
    while q:
        u = q.popleft()
        for v in _neighbors(cap, u):
            if v not in level:
                level[v] = level[u] + 1
                q.append(v)
    return level


def _dinic_dfs_blocking(
    u: Node,
    sink: Node,
    f: int,
    cap: dict[tuple[Node, Node], int],
    level: dict[Node, int],
    ptr: dict[Node, int],
) -> int:
    if u == sink:
        return f
    neighbors = _neighbors(cap, u)
    i = ptr.get(u, 0)
    while i < len(neighbors):
        v = neighbors[i]
        if level.get(v) == level.get(u, -1) + 1 and cap.get((u, v), 0) > 0:
            pushed = _dinic_dfs_blocking(v, sink, min(f, cap[(u, v)]), cap, level, ptr)
            if pushed > 0:
                cap[(u, v)] -= pushed
                cap[(v, u)] = cap.get((v, u), 0) + pushed
                return pushed
        i += 1
        ptr[u] = i
    return 0


def dinic(
    graph: CapacityGraph,
    source: Node,
    sink: Node,
) -> tuple[int, FlowGraph]:
    if source == sink:
        return 0, {u: {} for u in graph}
    cap = _build_residual(graph)
    total: int = 0
    while True:
        level = _dinic_bfs_level(cap, source, sink)
        if sink not in level:
            break
        ptr: dict[Node, int] = {}
        while True:
            pushed = _dinic_dfs_blocking(source, sink, 10**18, cap, level, ptr)
            if pushed == 0:
                break
            total += pushed

    flow: FlowGraph = {u: {} for u in graph}
    for u in graph:
        for v, c in graph[u].items():
            fwd = c - cap.get((u, v), 0)
            if fwd > 0:
                flow[u][v] = fwd
    return total, flow


# ── Bipartite maximum matching (via flow reduction) ───────────────────


def bipartite_max_matching(
    left: list[Node],
    right: list[Node],
    edges: list[tuple[Node, Node]],
) -> tuple[int, list[tuple[Node, Node]]]:
    source = "S"
    sink = "T"
    g: CapacityGraph = {source: {}, sink: {}}
    for u in left:
        g[u] = {}
        g[source][u] = 1
    for v in right:
        g[v] = {}
        g[v][sink] = 1
    for u, v in edges:
        g.setdefault(u, {})[v] = 1
        g.setdefault(v, {})

    total, flow, _ = edmonds_karp(g, source, sink)

    matches: list[tuple[Node, Node]] = []
    for u in left:
        for v in right:
            if flow.get(u, {}).get(v, 0) > 0:
                matches.append((u, v))
    return total, matches


# ── Flow verification helper ──────────────────────────────────────────


def verify_flow(
    graph: CapacityGraph,
    source: Node,
    sink: Node,
    flow: FlowGraph,
    expected_value: int,
) -> bool:
    for u in graph:
        for v in graph[u]:
            f = flow.get(u, {}).get(v, 0)
            if f > graph[u][v] or f < 0:
                return False

    for u in graph:
        if u in {source, sink}:
            continue
        inflow = sum(flow.get(v, {}).get(u, 0) for v in graph)
        outflow = sum(flow.get(u, {}).get(v, 0) for v in graph[u])
        if inflow != outflow:
            return False

    source_outflow = sum(flow.get(source, {}).get(v, 0) for v in graph[source])
    return source_outflow == expected_value
