"""Content-addressable Merkle DAG with IPLD-style links.

Each node is identified by a CID (hash of data + links), forming a
tamper-evident directed acyclic graph.  Links are named, allowing
path-based traversal (`/child/grandchild`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


# ---------------------------------------------------------------------------
# CID — content identifier
# ---------------------------------------------------------------------------


class CID:
    """A SHA-256 content identifier.  Deterministic for the same bytes."""

    __slots__ = ("_digest", "_hex")

    def __init__(self, digest: bytes) -> None:
        """Create a CID from a 32-byte digest."""
        if len(digest) != 32:
            raise ValueError(f"CID digest must be 32 bytes, got {len(digest)}")
        self._digest: bytes = digest
        self._hex: str = digest.hex()

    @property
    def digest(self) -> bytes:
        """Return the raw 32-byte digest."""
        return self._digest

    @property
    def hex(self) -> str:
        """Return the hex-encoded digest."""
        return self._hex

    @classmethod
    def from_hex(cls, hex_str: str) -> CID:
        """Build a CID from a hex string."""
        return cls(bytes.fromhex(hex_str))

    def __eq__(self, other: object) -> bool:
        """Return whether two CIDs carry identical digests."""
        if not isinstance(other, CID):
            return NotImplemented
        return self._digest == other._digest

    def __lt__(self, other: CID) -> bool:
        """Return whether this digest sorts before *other*."""
        return self._digest < other._digest

    def __le__(self, other: CID) -> bool:
        """Return whether this digest sorts at or before *other*."""
        return self._digest <= other._digest

    def __gt__(self, other: CID) -> bool:
        """Return whether this digest sorts after *other*."""
        return self._digest > other._digest

    def __ge__(self, other: CID) -> bool:
        """Return whether this digest sorts at or after *other*."""
        return self._digest >= other._digest

    def __hash__(self) -> int:
        """Return a hash of the digest."""
        return hash(self._digest)

    def __str__(self) -> str:
        """Return the hex digest string."""
        return self._hex

    def __repr__(self) -> str:
        """Return a compact developer representation."""
        return f"CID({self._hex[:12]}…)"


# ---------------------------------------------------------------------------
# MerkleLink — named edge to another node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MerkleLink:
    """A named, typed link from one Merkle node to another."""

    name: str
    cid: CID
    size: int = 0


# ---------------------------------------------------------------------------
# MerkleNode — a content-addressable node
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MerkleNode(Generic[T]):
    """Content-addressable node with data and named links.

    The CID is the SHA-256 of the serialized `(data, links)` tuple,
    computed once at construction so a node remains addressable by its
    original CID even if its fields are mutated afterwards.
    """

    data: T
    links: list[MerkleLink] = field(default_factory=list)
    cid: CID = field(init=False)

    def __post_init__(self) -> None:
        """Cache the content-derived CID at construction time."""
        self.cid = _cid_from_parts(self.data, self.links)

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-compatible dict for bulk export."""
        return {
            "data": self.data,
            "links": [{"name": link.name, "cid": link.cid.hex, "size": link.size} for link in self.links],
        }

    @classmethod
    def deserialize(cls, obj: dict[str, Any]) -> MerkleNode[T]:
        """Rebuild a node from a dict produced by ``serialize``."""
        links = [
            MerkleLink(
                name=li["name"],
                cid=CID.from_hex(li["cid"]),
                size=li.get("size", 0),
            )
            for li in obj.get("links", [])
        ]
        return cls(data=cast(T, obj["data"]), links=links)

    def verify(self) -> None:
        """Check that the stored CID matches the recomputed hash."""
        expected = self.cid
        actual = _cid_from_parts(self.data, self.links)
        if actual != expected:
            raise NodeValidationError(
                f"Node content mismatch: stored CID {expected.hex[:16]}…, computed {actual.hex[:16]}…"
            )

    def __repr__(self) -> str:
        """Return a compact developer representation."""
        return f"MerkleNode(cid={self.cid.hex[:12]}…, links={len(self.links)})"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cid_from_parts(data: object, links: list[MerkleLink]) -> CID:
    """Compute CID from data + ordered link tuples."""
    payload: list[object] = [
        data,
        sorted(
            [(link.name, link.cid.hex, link.size) for link in links],
            key=lambda t: t[0],
        ),
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return CID(hashlib.sha256(raw.encode()).digest())


# ---------------------------------------------------------------------------
# MerkleDAG — node registry with traversal and verification
# ---------------------------------------------------------------------------


class NodeValidationError(Exception):
    """A node failed integrity verification."""


class PathResolutionError(Exception):
    """A path segment could not be resolved."""


class MerkleDAG(Generic[T]):
    """In-memory registry of Merkle nodes with traversal and verification.

    Put nodes into the DAG, then walk, verify, or resolve paths.
    """

    def __init__(self) -> None:
        """Create an empty DAG registry."""
        self._nodes: dict[CID, MerkleNode[T]] = {}

    # -- node storage --------------------------------------------------------

    def put(self, node: MerkleNode[T]) -> None:
        """Store *node* under its CID (first put wins)."""
        self._nodes.setdefault(node.cid, node)

    def get(self, cid: CID) -> MerkleNode[T]:
        """Return the node registered under *cid*."""
        return self._nodes[cid]

    def contains(self, cid: CID) -> bool:
        """Return whether *cid* is registered."""
        return cid in self._nodes

    # -- traversal -----------------------------------------------------------

    def walk(
        self,
        root_cid: CID,
        visitor: Callable[[MerkleNode[T]], None],
    ) -> None:
        """Depth-first walk starting from *root_cid*.

        Non-recursive stack-based traversal that visits each node once.
        Links to nodes not in the DAG are silently skipped.
        """
        seen: set[CID] = set()
        stack = [root_cid]
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            node = self._nodes.get(cid)
            if node is None:
                continue
            visitor(node)
            stack.extend(link.cid for link in node.links[::-1])

    # -- verification --------------------------------------------------------

    def verify(self, root_cid: CID) -> None:
        """Verify every reachable node.

        Checks CID integrity and that all linked nodes exist in the DAG.
        """
        seen: set[CID] = set()
        stack = [root_cid]
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            node = self._nodes.get(cid)
            if node is None:
                raise NodeValidationError(f"dangling link: CID {cid.hex[:16]}… not in DAG")
            node.verify()
            stack.extend(link.cid for link in node.links[::-1])

    # -- path resolution -----------------------------------------------------

    def resolve(self, root_cid: CID, path: str) -> MerkleNode[T]:
        """Follow a ``/``-delimited path from *root_cid*.

        ``resolve(cid, "")`` returns the root node itself.
        """
        current = self.get(root_cid)
        if not path:
            return current
        segments = path.lstrip("/").split("/")
        for segment in segments:
            if not segment:
                continue
            current = self._follow_link(current, segment)
        return current

    def _follow_link(self, node: MerkleNode[T], name: str) -> MerkleNode[T]:
        for link in node.links:
            if link.name == name:
                return self.get(link.cid)
        raise PathResolutionError(f"link {name!r} not found in node {node.cid.hex[:12]}…")

    # -- bulk operations -----------------------------------------------------

    def iter_nodes(self) -> list[MerkleNode[T]]:
        """Return all registered nodes (snapshot)."""
        return list(self._nodes.values())

    def root_count(self) -> int:
        """Count nodes that are not linked to by any other node."""
        targets: set[CID] = set()
        for node in self._nodes.values():
            for link in node.links:
                targets.add(link.cid)
        return sum(1 for cid in self._nodes if cid not in targets)

    def leaf_count(self) -> int:
        """Count nodes that have no outgoing links."""
        return sum(1 for n in self._nodes.values() if not n.links)

    def export_dicts(self) -> list[dict[str, Any]]:
        """Serialize every node into a list of dicts."""
        return [n.serialize() for n in self._nodes.values()]

    def import_dicts(self, dicts: list[dict[str, Any]]) -> None:
        """Import nodes from ``export_dicts`` output (first import wins)."""
        for d in dicts:
            n: MerkleNode[Any] = MerkleNode.deserialize(d)
            self._nodes.setdefault(n.cid, n)
