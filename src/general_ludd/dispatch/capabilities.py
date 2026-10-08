"""Capability discovery backbone.

Reads standard Galaxy metadata, Gludd's separate capability contract, and role
metadata, then builds a capability-to-collection/module registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from general_ludd.ansible.paths import resolve_collections_paths

logger = logging.getLogger(__name__)


@dataclass
class CollectionMeta:
    """Extracted metadata for one Ansible collection."""

    name: str
    namespace: str
    version: str = "unknown"
    description: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    roles: list[dict[str, str]] = field(default_factory=list)
    raw_tags: list[str] = field(default_factory=list)
    model_capabilities: list[dict[str, object]] = field(default_factory=list)
    role_capabilities: dict[str, list[str]] = field(default_factory=dict)

    @staticmethod
    def from_galaxy(data: dict[str, Any]) -> CollectionMeta:
        """Build collection metadata from a parsed Galaxy mapping."""
        tags_raw = data.get("tags", [])
        if not isinstance(tags_raw, list):
            tags_raw = []
        tags_raw = [str(t) for t in tags_raw]

        model_caps: list[dict[str, object]] = []
        model_caps_raw = data.get("model_capabilities")
        if isinstance(model_caps_raw, list):
            for mc in model_caps_raw:
                if isinstance(mc, dict):
                    entry: dict[str, object] = {
                        "name": str(mc.get("name", "")),
                        "description": str(mc.get("description", "")),
                        "quality_class": str(mc.get("quality_class", "medium")),
                    }
                    roles_raw = mc.get("roles")
                    entry["roles"] = [str(r) for r in roles_raw] if isinstance(roles_raw, list) else []
                    model_needs_raw = mc.get("model_needs")
                    entry["model_needs"] = (
                        [str(mn) for mn in model_needs_raw] if isinstance(model_needs_raw, list) else []
                    )
                    aliases_raw = mc.get("aliases")
                    entry["aliases"] = [str(a) for a in aliases_raw] if isinstance(aliases_raw, list) else []
                    model_caps.append(entry)

        role_caps: dict[str, list[str]] = {}
        role_caps_raw = data.get("role_capabilities")
        if isinstance(role_caps_raw, dict):
            for role_name, cap_list in role_caps_raw.items():
                if isinstance(cap_list, list):
                    role_caps[str(role_name)] = [str(c) for c in cap_list]

        return CollectionMeta(
            name=str(data.get("name", "")),
            namespace=str(data.get("namespace", "")),
            version=str(data.get("version", "unknown")),
            description=str(data.get("description", "")).strip(),
            tags=frozenset(tags_raw),
            raw_tags=tags_raw,
            model_capabilities=model_caps,
            role_capabilities=role_caps,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize this collection's public capability metadata."""
        result: dict[str, object] = {
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "description": self.description,
            "tags": sorted(self.tags),
            "roles": self.roles,
        }
        if self.model_capabilities:
            result["model_capabilities"] = self.model_capabilities
        if self.role_capabilities:
            result["role_capabilities"] = self.role_capabilities
        return result


@dataclass
class CapabilityRegistry:
    """Registry mapping capabilities (tags) to collections and roles."""

    collections: dict[str, CollectionMeta] = field(default_factory=dict)
    tag_index: dict[str, frozenset[str]] = field(default_factory=dict)

    def add_collection(self, meta: CollectionMeta) -> None:
        """Add a collection and index all of its declared tags."""
        self.collections[meta.name] = meta
        for tag in meta.tags:
            current = self.tag_index.get(tag, frozenset())
            self.tag_index[tag] = current | frozenset([meta.name])

    def lookup_by_tag(self, tag: str) -> frozenset[str]:
        """Return collection names that declare the exact tag."""
        return self.tag_index.get(tag, frozenset())

    def to_dict(self) -> dict[str, object]:
        """Serialize the registry and its deterministic tag index."""
        return {
            "collections": {name: meta.to_dict() for name, meta in sorted(self.collections.items())},
            "tag_index": {tag: sorted(collections) for tag, collections in sorted(self.tag_index.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CapabilityRegistry:
        """Restore a registry from its serialized public representation."""
        reg = cls()
        collections_raw = data.get("collections", {})
        if isinstance(collections_raw, dict):
            for _coll_name, coll_data in collections_raw.items():
                if not isinstance(coll_data, dict):
                    continue
                tags = coll_data.get("tags", [])
                tags_set = frozenset(str(t) for t in tags) if isinstance(tags, list) else frozenset()
                roles_raw = coll_data.get("roles")
                roles: list[dict[str, str]] = roles_raw if isinstance(roles_raw, list) else []
                model_caps_raw = coll_data.get("model_capabilities")
                model_caps: list[dict[str, object]] = model_caps_raw if isinstance(model_caps_raw, list) else []
                role_caps_raw = coll_data.get("role_capabilities")
                role_caps: dict[str, list[str]] = {}
                if isinstance(role_caps_raw, dict):
                    for rk, rv in role_caps_raw.items():
                        role_caps[str(rk)] = [str(c) for c in rv] if isinstance(rv, list) else []
                meta = CollectionMeta(
                    name=str(coll_data.get("name", "")),
                    namespace=str(coll_data.get("namespace", "")),
                    version=str(coll_data.get("version", "unknown")),
                    description=str(coll_data.get("description", "")),
                    tags=tags_set,
                    raw_tags=sorted(tags_set),
                    roles=roles,
                    model_capabilities=model_caps,
                    role_capabilities=role_caps,
                )
                reg.add_collection(meta)
        return reg


def _discover_roles(collection_dir: Path) -> list[dict[str, str]]:
    roles: list[dict[str, str]] = []
    roles_dir = collection_dir / "roles"
    if not roles_dir.is_dir():
        return roles
    for role_path in sorted(roles_dir.iterdir()):
        if not role_path.is_dir():
            continue
        meta_dir = role_path / "meta"
        if not meta_dir.is_dir():
            continue
        main_yml = meta_dir / "main.yml"
        if not main_yml.is_file():
            main_yml = meta_dir / "main.yaml"
        if not main_yml.is_file():
            continue
        try:
            data = yaml.safe_load(main_yml.read_text()) or {}
        except yaml.YAMLError:
            logger.debug("malformed role meta: %s", main_yml)
            continue
        galaxy_info = data.get("galaxy_info", {})
        if not isinstance(galaxy_info, dict):
            continue
        name = galaxy_info.get("role_name", role_path.name)
        description = str(galaxy_info.get("description", "")).strip()
        roles.append({"name": str(name), "description": description})
    return roles


def discover_capabilities(colls_root: Path | None = None) -> CapabilityRegistry:
    """Discover standard metadata, capability sidecars, and role metadata."""
    if colls_root is None:
        paths = resolve_collections_paths()
        ac_path: Path | None = None
        for entry in paths:
            candidate = entry.path / "ansible_collections"
            if candidate.is_dir():
                ac_path = candidate
                break
        if ac_path is None:
            ac_path = Path.cwd() / "collections" / "ansible_collections"
        colls_root = ac_path

    if not colls_root.is_dir():
        logger.debug("collections root not found: %s", colls_root)
        return CapabilityRegistry()

    registry = CapabilityRegistry()
    processed = 0
    errors = 0

    for ns_dir in sorted(colls_root.iterdir()):
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for coll_dir in sorted(ns_dir.iterdir()):
            if not coll_dir.is_dir() or coll_dir.name.startswith("."):
                continue
            galaxy_yml = coll_dir / "galaxy.yml"
            if not galaxy_yml.is_file():
                continue
            try:
                data = yaml.safe_load(galaxy_yml.read_text()) or {}
            except yaml.YAMLError:
                logger.debug("malformed galaxy.yml: %s", galaxy_yml)
                errors += 1
                continue
            if not isinstance(data, dict):
                errors += 1
                continue
            capabilities_yml = coll_dir / "capabilities.yml"
            if capabilities_yml.is_file():
                try:
                    capability_data = yaml.safe_load(capabilities_yml.read_text()) or {}
                except yaml.YAMLError:
                    logger.debug("malformed capabilities.yml: %s", capabilities_yml)
                    errors += 1
                    capability_data = {}
                if not isinstance(capability_data, dict):
                    logger.debug("capabilities.yml must contain a mapping: %s", capabilities_yml)
                    errors += 1
                    capability_data = {}
                data = {
                    **data,
                    **{
                        key: capability_data[key]
                        for key in ("model_capabilities", "role_capabilities")
                        if key in capability_data
                    },
                }
            meta = CollectionMeta.from_galaxy(data)
            if not meta.name or not meta.namespace:
                errors += 1
                continue
            meta.roles = _discover_roles(coll_dir)
            registry.add_collection(meta)
            processed += 1

    logger.info(
        "discovered %d collections (%d errors) from %s",
        processed,
        errors,
        colls_root,
    )
    return registry
