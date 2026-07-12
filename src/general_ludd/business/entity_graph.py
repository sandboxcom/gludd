"""Entity relationship graph for business intelligence research.

Models entities (organizations, companies, non-profits) and their
associations (parent/subsidiary, board membership, investment, etc.)
as a directed property graph with query and clustering support.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityNode:
    name: str
    entity_type: str = "organization"
    jurisdiction: str = ""
    industry: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EntityNode):
            return NotImplemented
        return self.name == other.name


@dataclass
class Association:
    from_node: str
    to_node: str
    assoc_type: str
    strength: float = 0.5
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    ASSOCIATION_CLASSIFICATIONS: dict[str, str] = field(default_factory=lambda: {
        "parent_company": "financial",
        "subsidiary": "financial",
        "board_member": "personal",
        "executive": "personal",
        "shareholder": "financial",
        "investor": "financial",
        "supplier": "contractual",
        "partner": "contractual",
        "customer": "contractual",
        "competitor": "competitive",
        "acquirer": "financial",
        "acquisition_target": "financial",
        "joint_venture": "contractual",
        "licensor": "contractual",
        "consultant": "contractual",
        "founder": "personal",
        "advisor": "personal",
    })

    @property
    def classification(self) -> str:
        result: str | None = self.ASSOCIATION_CLASSIFICATIONS.get(self.assoc_type)
        return result if result is not None else "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "type": self.assoc_type,
            "classification": self.classification,
            "strength": self.strength,
            "evidence": self.evidence,
        }


class EntityGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, EntityNode] = {}
        self._adj: dict[str, list[Association]] = defaultdict(list)

    def add_node(self, node: EntityNode) -> None:
        self._nodes[node.name] = node

    def add_nodes(self, nodes: list[EntityNode]) -> None:
        for node in nodes:
            self._nodes[node.name] = node

    def add_association(self, assoc: Association) -> None:
        if assoc.from_node not in self._nodes:
            self._nodes[assoc.from_node] = EntityNode(name=assoc.from_node)
        if assoc.to_node not in self._nodes:
            self._nodes[assoc.to_node] = EntityNode(name=assoc.to_node)
        self._adj[assoc.from_node].append(assoc)

    def add_associations(self, associations: list[Association]) -> None:
        for assoc in associations:
            self.add_association(assoc)

    @property
    def nodes(self) -> dict[str, EntityNode]:
        return dict(self._nodes)

    @property
    def associations(self) -> list[Association]:
        result: list[Association] = []
        for edges in self._adj.values():
            result.extend(edges)
        return result

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._adj.values())

    def get_node(self, name: str) -> EntityNode | None:
        return self._nodes.get(name)

    def get_associations(self, name: str) -> list[Association]:
        return list(self._adj.get(name, []))

    def find_related(
        self, entity_name: str, max_depth: int = 3
    ) -> list[Association]:
        if entity_name not in self._nodes:
            return []

        results: list[Association] = []
        visited: set[str] = {entity_name}
        queue: deque[tuple[str, int]] = deque([(entity_name, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for assoc in self._adj.get(current, []):
                if assoc not in results:
                    results.append(assoc)
                if assoc.to_node not in visited:
                    visited.add(assoc.to_node)
                    queue.append((assoc.to_node, depth + 1))

        return results

    def find_paths(
        self, from_entity: str, to_entity: str, max_depth: int = 5
    ) -> list[list[Association]]:
        if from_entity not in self._nodes or to_entity not in self._nodes:
            return []

        all_paths: list[list[Association]] = []

        def _dfs(
            current: str,
            target: str,
            path: list[Association],
            visited: set[str],
            depth: int,
        ) -> None:
            if depth > max_depth or current in visited:
                return
            if current == target and path:
                all_paths.append(list(path))
                return
            visited.add(current)
            for assoc in self._adj.get(current, []):
                path.append(assoc)
                _dfs(assoc.to_node, target, path, visited, depth + 1)
                path.pop()
            visited.discard(current)

        _dfs(from_entity, to_entity, [], set(), 0)
        return all_paths

    def detect_clusters(self, min_size: int = 3) -> list[set[str]]:
        visited: set[str] = set()
        clusters: list[set[str]] = []

        for node_name in self._nodes:
            if node_name in visited:
                continue
            cluster: set[str] = set()
            queue: deque[str] = deque([node_name])
            while queue:
                current = queue.popleft()
                if current in cluster:
                    continue
                cluster.add(current)
                visited.add(current)
                for assoc in self._adj.get(current, []):
                    if assoc.to_node not in cluster:
                        queue.append(assoc.to_node)
            if len(cluster) >= min_size:
                clusters.append(cluster)

        return clusters

    def get_associations_by_type(
        self, assoc_type: str
    ) -> list[Association]:
        return [
            assoc
            for assocs in self._adj.values()
            for assoc in assocs
            if assoc.assoc_type == assoc_type
        ]

    def get_associations_by_classification(
        self, classification: str
    ) -> list[Association]:
        return [
            assoc
            for assocs in self._adj.values()
            for assoc in assocs
            if assoc.classification == classification
        ]

    def get_degree(self, name: str) -> int:
        return len(self._adj.get(name, []))

    def get_hub_entities(self, top_n: int = 10) -> list[tuple[str, int]]:
        degrees = [(name, self.get_degree(name)) for name in self._nodes]
        degrees.sort(key=lambda x: x[1], reverse=True)
        return degrees[:top_n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "name": node.name,
                    "type": node.entity_type,
                    "jurisdiction": node.jurisdiction,
                    "industry": node.industry,
                    "metadata": node.metadata,
                }
                for node in self._nodes.values()
            ],
            "associations": [assoc.to_dict() for assoc in self.associations],
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def to_dot(self, path: str) -> None:
        lines = ["digraph EntityGraph {"]
        lines.append('  rankdir="LR";')
        lines.append('  node [shape="box", style="rounded"];')

        assoc_colors = {
            "financial": "blue",
            "personal": "green",
            "contractual": "orange",
            "competitive": "red",
            "unknown": "gray",
        }

        for node in self._nodes.values():
            label = (
                f"{node.name}\\n[{node.entity_type}]"
                if node.industry
                else node.name
            )
            lines.append(f'  "{node.name}" [label="{label}"];')

        seen: set[tuple[str, str, str]] = set()
        for assocs in self._adj.values():
            for assoc in assocs:
                key = (assoc.from_node, assoc.to_node, assoc.assoc_type)
                if key in seen:
                    continue
                seen.add(key)
                color = assoc_colors.get(assoc.classification, "gray")
                lines.append(
                    f'  "{assoc.from_node}" -> "{assoc.to_node}" '
                    f'[label="{assoc.assoc_type}", color="{color}", '
                    f"penwidth={max(1.0, assoc.strength * 3):.1f}];"
                )

        lines.append("}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")


def build_graph(
    entities: list[EntityNode], associations: list[Association]
) -> EntityGraph:
    graph = EntityGraph()
    graph.add_nodes(entities)
    graph.add_associations(associations)
    return graph


def find_related(
    graph: EntityGraph, entity_name: str, max_depth: int = 3
) -> list[Association]:
    return graph.find_related(entity_name, max_depth)
