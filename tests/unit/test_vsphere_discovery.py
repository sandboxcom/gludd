"""Unit tests for VSphereProbe discovery and _generate_vsphere kwargs."""

from __future__ import annotations

import builtins
import types
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.discovery import VSphereProbe
from general_ludd.infra.terraform import TerraformGenerator

_original_import = builtins.__import__


def _block_pyvmomi_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.startswith("pyV") or name.startswith("pyVmomi"):
        raise ImportError(f"No module named '{name}'")
    return _original_import(name, globals, locals, fromlist, level)


def test_probe_init_defaults() -> None:
    probe = VSphereProbe(host="vcenter.example.com", username="admin", password="secret")
    assert probe.host == "vcenter.example.com"
    assert probe.username == "admin"
    assert probe.password == "secret"
    assert probe.port == 443
    assert probe.verify_ssl is True


def test_probe_init_explicit() -> None:
    probe = VSphereProbe(
        host="vcenter.example.com",
        username="admin",
        password="secret",
        port=8443,
        verify_ssl=False,
    )
    assert probe.port == 8443
    assert probe.verify_ssl is False


def test_discover_pyvmomi_not_installed() -> None:
    probe = VSphereProbe("host", "user", "pass")
    with patch("builtins.__import__", new=_block_pyvmomi_import):
        result = probe.discover()
        assert result is None


def _build_vim_type_module() -> types.ModuleType:
    m = types.ModuleType("pyVmomi.vim")
    m.Datacenter = type("Datacenter", (), {})
    m.Datastore = type("Datastore", (), {})
    m.Network = type("Network", (), {})
    m.ClusterComputeResource = type("ClusterComputeResource", (), {})
    return m


@contextmanager
def _inject_pyvmomi_modules() -> Generator[tuple[types.ModuleType, types.ModuleType], None, None]:
    vmomi_mod = types.ModuleType("pyVmomi")
    vmomi_mod.vim = _build_vim_type_module()

    connect_mod = types.ModuleType("pyVim.connect")
    connect_mod.SmartConnect = MagicMock()
    connect_mod.Disconnect = MagicMock()

    connect_pkg = types.ModuleType("pyVim")
    connect_pkg.connect = connect_mod

    with patch.dict(
        "sys.modules",
        {
            "pyVmomi": vmomi_mod,
            "pyVmomi.vim": vmomi_mod.vim,
            "pyVim": connect_pkg,
            "pyVim.connect": connect_mod,
        },
    ):
        yield vmomi_mod, connect_mod


def _build_vsphere_service_instance(
    vim: types.ModuleType,
    dc_name: str = "MyDC",
    cluster_name: str | None = "MyCluster",
    datastore_name: str | None = "MyDatastore",
    network_name: str | None = "MyNetwork",
    include_dc: bool = True,
) -> MagicMock:
    dc_hostfolder = MagicMock()
    if cluster_name is not None:
        cluster_obj = vim.ClusterComputeResource()
        cluster_obj.name = cluster_name
        dc_hostfolder.childEntity = [cluster_obj]
    else:
        dc_hostfolder.childEntity = []

    dc_datastorefolder = MagicMock()
    if datastore_name is not None:
        ds_obj = vim.Datastore()
        ds_obj.name = datastore_name
        dc_datastorefolder.childEntity = [ds_obj]
    else:
        dc_datastorefolder.childEntity = []

    dc_networkfolder = MagicMock()
    if network_name is not None:
        net_obj = vim.Network()
        net_obj.name = network_name
        dc_networkfolder.childEntity = [net_obj]
    else:
        dc_networkfolder.childEntity = []

    datacenter_obj = vim.Datacenter()
    datacenter_obj.name = dc_name
    datacenter_obj.hostFolder = dc_hostfolder
    datacenter_obj.datastoreFolder = dc_datastorefolder
    datacenter_obj.networkFolder = dc_networkfolder

    root_folder = MagicMock()
    root_folder.childEntity = [datacenter_obj] if include_dc else []

    content = MagicMock()
    content.rootFolder = root_folder

    si = MagicMock()
    si.RetrieveContent.return_value = content

    return si


def test_discover_smartconnect_failure() -> None:
    probe = VSphereProbe("host", "user", "pass")
    with _inject_pyvmomi_modules() as (_vmomi_mod, connect_mod):
        connect_mod.SmartConnect.side_effect = OSError("connection refused")
        result = probe.discover()
        assert result is None


def test_discover_success() -> None:
    probe = VSphereProbe("host", "user", "pass")
    with _inject_pyvmomi_modules() as (vmomi_mod, connect_mod):
        si = _build_vsphere_service_instance(vmomi_mod.vim)
        connect_mod.SmartConnect.return_value = si
        result = probe.discover()
    assert result is not None
    assert result["datacenter"] == "MyDC"
    assert result["cluster"] == "MyCluster"
    assert result["datastore"] == "MyDatastore"
    assert result["network"] == "MyNetwork"


def test_discover_empty_inventory() -> None:
    probe = VSphereProbe("host", "user", "pass")
    with _inject_pyvmomi_modules() as (vmomi_mod, connect_mod):
        si = _build_vsphere_service_instance(vmomi_mod.vim, include_dc=False)
        connect_mod.SmartConnect.return_value = si
        result = probe.discover()
        assert result is None


def test_discover_partial_inventory_returns_defaults_for_missing() -> None:
    probe = VSphereProbe("host", "user", "pass")
    with _inject_pyvmomi_modules() as (vmomi_mod, connect_mod):
        si = _build_vsphere_service_instance(
            vmomi_mod.vim,
            dc_name="OnlyDC",
            cluster_name=None,
            datastore_name=None,
            network_name=None,
        )
        connect_mod.SmartConnect.return_value = si
        result = probe.discover()
    assert result is not None
    assert result["datacenter"] == "OnlyDC"
    assert result["cluster"] == "Cluster0"
    assert result["datastore"] == "datastore0"
    assert result["network"] == "VM Network"


def test_discover_verify_ssl_false_uses_unverified_context() -> None:
    probe = VSphereProbe("host", "user", "pass", verify_ssl=False)
    with _inject_pyvmomi_modules() as (vmomi_mod, connect_mod):
        si = _build_vsphere_service_instance(vmomi_mod.vim, dc_name="DC")
        connect_mod.SmartConnect.return_value = si
        result = probe.discover()
    assert result is not None
    call_kwargs = connect_mod.SmartConnect.call_args.kwargs
    assert call_kwargs["sslContext"] is not None


def test_generate_vsphere_kwargs_override() -> None:
    config = ComputeConfig(
        provider=ComputeProvider.VMWARE,
        gpu_type=GPUType.T4,
        model_name="test/model",
    )
    gen = TerraformGenerator()
    hcl = gen._generate_vsphere(
        config,
        datacenter="ProdDC",
        cluster="ProdCluster",
        datastore="ssd-datastore",
        network="Prod Network",
    )
    assert 'datacenter       = "ProdDC"' in hcl
    assert 'cluster          = "ProdCluster"' in hcl
    assert 'datastore        = "ssd-datastore"' in hcl
    assert 'network          = "Prod Network"' in hcl


def test_generate_vsphere_no_kwargs_defaults() -> None:
    config = ComputeConfig(
        provider=ComputeProvider.VMWARE,
        gpu_type=GPUType.T4,
        model_name="test/model",
    )
    gen = TerraformGenerator()
    hcl = gen._generate_vsphere(config)
    assert 'datacenter       = "DC0"' in hcl
    assert 'cluster          = "Cluster0"' in hcl
    assert 'datastore        = "datastore0"' in hcl
    assert 'network          = "VM Network"' in hcl


def test_generate_vsphere_partial_kwargs() -> None:
    config = ComputeConfig(
        provider=ComputeProvider.VMWARE,
        gpu_type=GPUType.T4,
        model_name="test/model",
    )
    gen = TerraformGenerator()
    hcl = gen._generate_vsphere(config, datacenter="CustomDC")
    assert 'datacenter       = "CustomDC"' in hcl
    assert 'cluster          = "Cluster0"' in hcl
    assert 'datastore        = "datastore0"' in hcl
    assert 'network          = "VM Network"' in hcl
