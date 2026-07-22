from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import cast


@dataclass(frozen=True)
class EntityNode:
    id: str
    name: str
    entity_type: str = "organization"
    jurisdiction: str | None = None
    industry: str | None = None
    metadata: object = field(default_factory=dict, hash=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
        }
        if self.jurisdiction is not None:
            result["jurisdiction"] = self.jurisdiction
        if self.industry is not None:
            result["industry"] = self.industry
        if isinstance(self.metadata, dict) and self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass(frozen=True)
class Association:
    source_id: str
    target_id: str
    assoc_type: str
    weight: float = 1.0
    description: str | None = None
    metadata: object = field(default_factory=dict, hash=False, compare=False)

    @classmethod
    def classify_type(cls, description: str) -> str:
        desc_lower = description.lower()
        if any(w in desc_lower for w in ("founder", "employee", "executive", "board", "advisor", "family")):
            return "personal"
        if any(w in desc_lower for w in ("competitor", "rival", "alternative", "competing")):
            return "competitive"
        if any(
            w in desc_lower
            for w in ("invest", "fund", "acqui", "merger", "merge", "divest", "loan", "debt", "equity")
        ):
            return "financial"
        if any(w in desc_lower for w in ("contract", "agreement", "partner", "vendor", "client")):
            return "contractual"
        return "other"

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "assoc_type": self.assoc_type,
            "weight": self.weight,
        }
        if self.description is not None:
            result["description"] = self.description
        if isinstance(self.metadata, dict) and self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class EntityGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, EntityNode] = {}
        self._edges: dict[tuple[str, str], Association] = {}
        self._adjacency: dict[str, list[str]] = {}

    @property
    def nodes(self) -> dict[str, EntityNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> dict[tuple[str, str], Association]:
        return dict(self._edges)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def add_node(self, node: EntityNode) -> None:
        self._nodes[node.id] = node
        if node.id not in self._adjacency:
            self._adjacency[node.id] = []

    def add_edge(self, assoc: Association) -> None:
        if assoc.source_id not in self._nodes:
            raise ValueError(f"Source node '{assoc.source_id}' not in graph")
        if assoc.target_id not in self._nodes:
            raise ValueError(f"Target node '{assoc.target_id}' not in graph")
        key = (assoc.source_id, assoc.target_id)
        self._edges[key] = assoc
        if assoc.source_id not in self._adjacency:
            self._adjacency[assoc.source_id] = []
        if assoc.target_id not in self._adjacency:
            self._adjacency[assoc.target_id] = []
        if assoc.target_id not in self._adjacency[assoc.source_id]:
            self._adjacency[assoc.source_id].append(assoc.target_id)
        if assoc.source_id not in self._adjacency[assoc.target_id]:
            self._adjacency[assoc.target_id].append(assoc.source_id)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def get_node(self, node_id: str) -> EntityNode | None:
        return self._nodes.get(node_id)

    def get_related(
        self, entity_id: str, max_depth: int = 1
    ) -> dict[str, list[str]]:
        if entity_id not in self._nodes:
            return {}
        result: dict[str, list[str]] = {}
        visited: set[str] = {entity_id}
        current: set[str] = {entity_id}
        for depth in range(1, max_depth + 1):
            next_level: set[str] = set()
            neighbors_at_depth: list[str] = []
            for node_id in current:
                for neighbor_id in self._adjacency.get(node_id, []):
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        next_level.add(neighbor_id)
                        neighbors_at_depth.append(neighbor_id)
            if not next_level:
                break
            result[f"depth_{depth}"] = sorted(neighbors_at_depth)
            current = next_level
        return result

    def find_path(self, source_id: str, target_id: str) -> list[str] | None:
        if source_id not in self._nodes or target_id not in self._nodes:
            return None
        if source_id == target_id:
            return [source_id]
        queue: deque[list[str]] = deque()
        queue.append([source_id])
        visited: set[str] = {source_id}
        while queue:
            path = queue.popleft()
            node_id = path[-1]
            for neighbor_id in self._adjacency.get(node_id, []):
                if neighbor_id == target_id:
                    return [*path, target_id]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append([*path, neighbor_id])
        return None

    def detect_clusters(self) -> list[list[str]]:
        visited: set[str] = set()
        clusters: list[list[str]] = []
        for node_id in self._nodes:
            if node_id in visited:
                continue
            component: list[str] = []
            stack = [node_id]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor in self._adjacency.get(current, []):
                    if neighbor not in visited:
                        stack.append(neighbor)
            clusters.append(sorted(component))
        return clusters

    def find_by_type(self, entity_type: str) -> list[EntityNode]:
        return [n for n in self._nodes.values() if n.entity_type == entity_type]

    def find_by_jurisdiction(self, jurisdiction: str) -> list[EntityNode]:
        return [n for n in self._nodes.values() if n.jurisdiction == jurisdiction]

    def find_by_industry(self, industry: str) -> list[EntityNode]:
        return [n for n in self._nodes.values() if n.industry == industry]

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EntityGraph:
        graph = cls()
        nodes_raw: object = data.get("nodes", [])
        for node_data in nodes_raw if isinstance(nodes_raw, list) else []:
            assert isinstance(node_data, dict)
            graph.add_node(EntityNode(
                id=cast(str, node_data.get("id", "")),
                name=cast(str, node_data.get("name", "")),
                entity_type=cast(str, node_data.get("entity_type", "organization")),
                jurisdiction=cast(str | None, node_data.get("jurisdiction")),
                industry=cast(str | None, node_data.get("industry")),
                metadata=cast(object, node_data.get("metadata", {})),
            ))
        edges_raw: object = data.get("edges", [])
        for edge_data in edges_raw if isinstance(edges_raw, list) else []:
            assert isinstance(edge_data, dict)
            weight_raw = edge_data.get("weight", 1.0)
            description_raw = edge_data.get("description")
            graph.add_edge(Association(
                source_id=str(edge_data.get("source_id", "")),
                target_id=str(edge_data.get("target_id", "")),
                assoc_type=str(edge_data.get("assoc_type", "other")),
                weight=float(weight_raw) if isinstance(weight_raw, (int, float, str)) else 1.0,
                description=str(description_raw) if description_raw is not None else None,
                metadata=edge_data.get("metadata", {}),
            ))
        return graph

    @classmethod
    def from_json(cls, json_str: str) -> EntityGraph:
        data = json.loads(json_str)
        assert isinstance(data, dict)
        return cls.from_dict(data)
