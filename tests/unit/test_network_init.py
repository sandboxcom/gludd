"""Package-level tests for general_ludd.network __init__.py —
verify clean import, namespace integrity, and submodule accessibility."""

from __future__ import annotations

import importlib

import pytest


class TestNetworkPackageImport:
    def test_package_imports_cleanly(self) -> None:
        import general_ludd.network as net

        assert net is not None

    def test_package_name_and_file(self) -> None:
        import general_ludd.network as net

        assert net.__name__ == "general_ludd.network"
        assert net.__file__ is not None
        assert net.__file__.endswith("__init__.py")

    def test_package_path_is_directory(self) -> None:
        import general_ludd.network as net

        assert net.__path__ is not None
        assert len(net.__path__) >= 1


class TestSubmoduleAccessibility:
    @pytest.mark.parametrize(
        "submodule",
        [
            "nat_traversal",
            "packet_filter",
            "proxy_protocol",
            "routing_table",
            "sliding_window",
            "stream_mux",
            "token_bucket_v2",
        ],
    )
    def test_submodule_importable(self, submodule: str) -> None:
        full = f"general_ludd.network.{submodule}"
        mod = importlib.import_module(full)
        assert mod is not None
        assert mod.__name__ == full

    @pytest.mark.parametrize(
        "submodule,expected_class",
        [
            ("nat_traversal", "NatClassifier"),
            ("packet_filter", "MatchNode"),
            ("proxy_protocol", "ProxyProtocolHeader"),
            ("routing_table", "RoutingTable"),
            ("sliding_window", "SlidingLog"),
            ("stream_mux", "StreamMux"),
            ("token_bucket_v2", "Bucket"),
        ],
    )
    def test_key_classes_present(self, submodule: str, expected_class: str) -> None:
        full = f"general_ludd.network.{submodule}"
        mod = importlib.import_module(full)
        assert hasattr(mod, expected_class), f"{expected_class} missing from {full}"


class TestNamespaceIntegrity:
    def test_repeated_import_idempotent(self) -> None:
        a = __import__("general_ludd.network")
        b = __import__("general_ludd.network")
        assert a is b

    def test_submodule_access_consistent(self) -> None:
        from general_ludd.network import token_bucket_v2 as tb1
        from general_ludd.network.token_bucket_v2 import Bucket

        assert tb1.Bucket is Bucket

    def test_deep_cross_import_consistent(self) -> None:
        import general_ludd.network as net
        import general_ludd.network.token_bucket_v2 as tb

        subnet = net.token_bucket_v2
        assert subnet is tb
