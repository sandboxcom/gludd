"""TDD tests for service catalog — DiscoveredService and ServiceCatalog."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime

from general_ludd.infra.service_catalog import (
    DiscoveredService,
    ServiceCatalog,
    diff_catalog,
    merge_catalog,
)


class TestDiscoveredService:
    def test_creation_with_required_fields(self):
        svc = DiscoveredService(name="api-gateway", url="https://gw.example.com")
        assert svc.name == "api-gateway"
        assert svc.url == "https://gw.example.com"
        assert svc.status == "unknown"
        assert svc.api_docs_url is None
        assert svc.pricing_url is None

    def test_creation_with_all_fields(self):
        now = datetime(2025, 7, 1, tzinfo=UTC)
        svc = DiscoveredService(
            name="auth-service",
            url="https://auth.example.com",
            api_docs_url="https://auth.example.com/docs",
            pricing_url="https://auth.example.com/pricing",
            status="active",
            discovered_at=now,
            last_seen=now,
            description="OAuth2 provider",
            source_engine="consul",
        )
        assert svc.name == "auth-service"
        assert svc.url == "https://auth.example.com"
        assert svc.api_docs_url == "https://auth.example.com/docs"
        assert svc.pricing_url == "https://auth.example.com/pricing"
        assert svc.status == "active"
        assert svc.discovered_at == now
        assert svc.last_seen == now
        assert svc.description == "OAuth2 provider"
        assert svc.source_engine == "consul"

    def test_default_datetime_is_utc(self):
        svc = DiscoveredService(name="test", url="https://test.example.com")
        assert svc.discovered_at.tzinfo == UTC
        assert svc.last_seen.tzinfo == UTC

    def test_to_dict_and_from_dict_roundtrip(self):
        svc = DiscoveredService(
            name="roundtrip",
            url="https://rt.example.com",
            api_docs_url="https://rt.example.com/docs",
            status="active",
            source_engine="k8s",
        )
        data = svc.to_dict()
        restored = DiscoveredService.from_dict(data)
        assert restored.name == svc.name
        assert restored.url == svc.url
        assert restored.api_docs_url == svc.api_docs_url
        assert restored.status == svc.status
        assert restored.source_engine == svc.source_engine

    def test_to_dict_excludes_none_values(self):
        svc = DiscoveredService(name="bare", url="https://bare.example.com")
        data = svc.to_dict()
        assert "api_docs_url" not in data
        assert "description" not in data

    def test_from_dict_minimal(self):
        svc = DiscoveredService.from_dict({"name": "min", "url": "https://min.com"})
        assert svc.name == "min"
        assert svc.url == "https://min.com"
        assert svc.status == "unknown"

    def test_from_dict_missing_datetime_uses_now(self):
        svc = DiscoveredService.from_dict({"name": "nodate", "url": "https://nodate.com"})
        assert svc.discovered_at is not None
        assert svc.last_seen is not None


class TestServiceCatalog:
    def test_add_and_get(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        svc = DiscoveredService(name="svc1", url="https://svc1.example.com", status="active")
        cat.add(svc)
        assert cat.get("svc1") is svc
        assert cat.get("nonexistent") is None

    def test_add_upserts_by_name(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        svc1 = DiscoveredService(name="svc1", url="https://old.example.com", status="active")
        svc2 = DiscoveredService(name="svc1", url="https://new.example.com", status="inactive")
        cat.add(svc1)
        cat.add(svc2)
        result = cat.get("svc1")
        assert result is not None
        assert result.url == "https://new.example.com"
        assert result.status == "inactive"

    def test_add_updates_last_seen(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        svc = DiscoveredService(name="svc1", url="https://svc1.example.com")
        original = svc.last_seen
        cat.add(svc)
        assert svc.last_seen >= original

    def test_remove_marks_inactive(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        svc = DiscoveredService(name="svc1", url="https://svc1.example.com", status="active")
        cat.add(svc)
        assert cat.remove("svc1") is True
        result = cat.get("svc1")
        assert result is not None
        assert result.status == "inactive"

    def test_remove_nonexistent_returns_false(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        assert cat.remove("nope") is False

    def test_list_active(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        cat.add(DiscoveredService(name="a", url="https://a.example.com", status="active"))
        cat.add(DiscoveredService(name="b", url="https://b.example.com", status="inactive"))
        cat.add(DiscoveredService(name="c", url="https://c.example.com", status="active"))
        active = cat.list_active()
        assert len(active) == 2
        assert {svc.name for svc in active} == {"a", "c"}

    def test_list_inactive(self):
        cat = ServiceCatalog(path="/tmp/gludd-test-catalog.yml")
        cat.add(DiscoveredService(name="a", url="https://a.example.com", status="active"))
        cat.add(DiscoveredService(name="b", url="https://b.example.com", status="inactive"))
        inactive = cat.list_inactive()
        assert len(inactive) == 1
        assert inactive[0].name == "b"

    def test_save_and_load_yaml_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_path = os.path.join(tmpdir, "catalog.yml")
            cat1 = ServiceCatalog(path=catalog_path)
            svc = DiscoveredService(
                name="svc1",
                url="https://svc1.example.com",
                api_docs_url="https://svc1.example.com/docs",
                status="active",
                description="Test service",
                source_engine="consul",
            )
            cat1.add(svc)
            cat1.save()

            cat2 = ServiceCatalog(path=catalog_path)
            assert cat2.get("svc1") is not None
            loaded = cat2.get("svc1")
            assert loaded is not None
            assert loaded.name == "svc1"
            assert loaded.url == "https://svc1.example.com"
            assert loaded.api_docs_url == "https://svc1.example.com/docs"
            assert loaded.status == "active"
            assert loaded.description == "Test service"
            assert loaded.source_engine == "consul"

    def test_save_and_load_json_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_path = os.path.join(tmpdir, "catalog.json")
            cat1 = ServiceCatalog(path=catalog_path)
            svc = DiscoveredService(
                name="svc1",
                url="https://svc1.example.com",
                status="active",
            )
            cat1.add(svc)
            cat1.save()

            assert os.path.exists(catalog_path)
            with open(catalog_path, encoding="utf-8") as f:
                raw = json.load(f)
            assert "services" in raw
            assert len(raw["services"]) == 1

            cat2 = ServiceCatalog(path=catalog_path)
            loaded = cat2.get("svc1")
            assert loaded is not None
            assert loaded.name == "svc1"
            assert loaded.url == "https://svc1.example.com"

    def test_init_loads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_path = os.path.join(tmpdir, "catalog.yml")
            cat1 = ServiceCatalog(path=catalog_path)
            cat1.add(DiscoveredService(name="pre", url="https://pre.example.com", status="active"))
            cat1.save()

            cat2 = ServiceCatalog(path=catalog_path)
            assert cat2.get("pre") is not None
            assert cat2.get("pre").url == "https://pre.example.com"

    def test_init_nonexistent_file_creates_empty(self):
        cat = ServiceCatalog(path="/nonexistent/path/catalog.yml")
        assert len(cat.services) == 0

    def test_save_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = os.path.join(tmpdir, "a", "b", "c", "catalog.yml")
            cat = ServiceCatalog(path=deep_path)
            cat.add(DiscoveredService(name="deep", url="https://deep.example.com", status="active"))
            cat.save()
            assert os.path.exists(deep_path)


class TestDiffCatalog:
    def test_added_services(self):
        old = ServiceCatalog(path="/tmp/gludd-old.yml")
        new = ServiceCatalog(path="/tmp/gludd-new.yml")
        new.add(DiscoveredService(name="new-svc", url="https://new.example.com", status="active"))

        added, removed, changed = diff_catalog(old, new)
        assert len(added) == 1
        assert added[0].name == "new-svc"
        assert len(removed) == 0
        assert len(changed) == 0

    def test_removed_services(self):
        old = ServiceCatalog(path="/tmp/gludd-old.yml")
        new = ServiceCatalog(path="/tmp/gludd-new.yml")
        old.add(DiscoveredService(name="gone", url="https://gone.example.com", status="active"))

        added, removed, changed = diff_catalog(old, new)
        assert len(added) == 0
        assert len(removed) == 1
        assert removed[0].name == "gone"
        assert len(changed) == 0

    def test_changed_services_url(self):
        old = ServiceCatalog(path="/tmp/gludd-old.yml")
        new = ServiceCatalog(path="/tmp/gludd-new.yml")
        old.add(DiscoveredService(name="svc", url="https://old.example.com", status="active"))
        new.add(DiscoveredService(name="svc", url="https://new.example.com", status="active"))

        added, removed, changed = diff_catalog(old, new)
        assert len(added) == 0
        assert len(removed) == 0
        assert len(changed) == 1
        assert changed[0].url == "https://new.example.com"

    def test_changed_services_status(self):
        old = ServiceCatalog(path="/tmp/gludd-old.yml")
        new = ServiceCatalog(path="/tmp/gludd-new.yml")
        old.add(DiscoveredService(name="svc", url="https://svc.example.com", status="active"))
        new.add(DiscoveredService(name="svc", url="https://svc.example.com", status="inactive"))

        _added, _removed, changed = diff_catalog(old, new)
        assert len(changed) == 1
        assert changed[0].status == "inactive"

    def test_unchanged_not_in_changed(self):
        old = ServiceCatalog(path="/tmp/gludd-old.yml")
        new = ServiceCatalog(path="/tmp/gludd-new.yml")
        svc = DiscoveredService(name="svc", url="https://svc.example.com", status="active")
        old.add(svc)
        new.add(svc)

        added, removed, changed = diff_catalog(old, new)
        assert len(added) == 0
        assert len(removed) == 0
        assert len(changed) == 0


class TestMergeCatalog:
    def test_new_service_added(self):
        target = ServiceCatalog(path="/tmp/gludd-target.yml")
        source = ServiceCatalog(path="/tmp/gludd-source.yml")
        source.add(DiscoveredService(name="new-svc", url="https://new.example.com", status="active"))

        merge_catalog(target, source)
        assert target.get("new-svc") is not None

    def test_vanished_service_marked_inactive(self):
        target = ServiceCatalog(path="/tmp/gludd-target.yml")
        source = ServiceCatalog(path="/tmp/gludd-source.yml")
        target.add(DiscoveredService(name="gone", url="https://gone.example.com", status="active"))

        merge_catalog(target, source)
        assert target.get("gone") is not None
        assert target.get("gone").status == "inactive"

    def test_changed_service_updated(self):
        target = ServiceCatalog(path="/tmp/gludd-target.yml")
        source = ServiceCatalog(path="/tmp/gludd-source.yml")
        target.add(DiscoveredService(name="svc", url="https://old.example.com", status="active"))
        source.add(DiscoveredService(name="svc", url="https://new.example.com", status="active"))

        merge_catalog(target, source)
        assert target.get("svc").url == "https://new.example.com"

    def test_inactive_target_reactivated_by_source(self):
        target = ServiceCatalog(path="/tmp/gludd-target.yml")
        source = ServiceCatalog(path="/tmp/gludd-source.yml")
        target.add(DiscoveredService(name="svc", url="https://svc.example.com", status="inactive"))
        source.add(DiscoveredService(name="svc", url="https://svc.example.com", status="active"))

        merge_catalog(target, source)
        assert target.get("svc").status == "active"

    def test_unchanged_service_not_altered(self):
        target = ServiceCatalog(path="/tmp/gludd-target.yml")
        source = ServiceCatalog(path="/tmp/gludd-source.yml")
        tgt_svc = DiscoveredService(name="svc", url="https://svc.example.com", status="active")
        target.add(tgt_svc)
        source.add(DiscoveredService(name="svc", url="https://svc.example.com", status="active"))

        merge_catalog(target, source)
        assert target.get("svc") is tgt_svc
