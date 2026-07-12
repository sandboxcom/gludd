"""vSphere inventory discovery via pyvmomi — lazy, fail-safe, optional."""

from __future__ import annotations

import contextlib
import logging
import ssl
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class VSphereProbe:
    """Discover vSphere inventory parameters for Terraform generation.

    Walks the vSphere object hierarchy via pyvmomi to find the first
    datacenter, cluster, datastore and network.  Every method is fail-safe:
    missing pyvmomi, connection errors, and empty inventory all return
    ``None`` rather than raising.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        verify_ssl: bool = True,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.verify_ssl = verify_ssl

    def discover(self) -> dict[str, str] | None:
        """Walk the vSphere inventory and return first-found params.

        Returns a dict with keys ``datacenter``, ``cluster``, ``datastore``,
        ``network``, or ``None`` on any error (missing pyvmomi, connection
        failure, empty inventory).
        """
        try:
            from pyVim.connect import Disconnect, SmartConnect
            from pyVmomi import vim
        except ImportError:
            logger.warning("pyvmomi is not installed; vSphere inventory discovery is unavailable")
            return None

        ssl_context: ssl.SSLContext | None = None
        if not self.verify_ssl:
            ssl_context = ssl._create_unverified_context()

        si = None
        try:
            si = SmartConnect(
                host=self.host,
                user=self.username,
                pwd=self.password,
                port=self.port,
                sslContext=ssl_context,
            )
        except Exception:
            logger.warning("Failed to connect to vSphere host %s", self.host, exc_info=True)
            return None

        try:
            content = si.RetrieveContent()
            root_folder = content.rootFolder

            datacenter: str | None = None
            cluster: str | None = None
            datastore: str | None = None
            network: str | None = None

            for child in root_folder.childEntity:
                if not isinstance(child, vim.Datacenter):
                    continue
                datacenter = child.name

                dc = child
                for c in dc.hostFolder.childEntity:
                    if isinstance(c, vim.ClusterComputeResource):
                        cluster = c.name
                        break

                for ds in dc.datastoreFolder.childEntity:
                    if isinstance(ds, vim.Datastore):
                        datastore = ds.name
                        break

                for net in dc.networkFolder.childEntity:
                    if isinstance(net, vim.Network):
                        network = net.name
                        break

                break

            if datacenter is None:
                logger.warning("No datacenter found in vSphere inventory")
                return None

            return {
                "datacenter": datacenter,
                "cluster": cluster or "Cluster0",
                "datastore": datastore or "datastore0",
                "network": network or "VM Network",
            }
        finally:
            with contextlib.suppress(Exception):
                Disconnect(si)
