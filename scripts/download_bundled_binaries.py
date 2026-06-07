"""Download OpenBao and OpenTofu binaries into dist/binaries/ for bundling.

Run via: make bundle-binaries

This runs during the normal `make dist` build cycle so binaries are always bundled.
Downloads are skipped if the binary already exists. Platform auto-detected.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx

from general_ludd.filestore.bootstrap import BinaryBootstrapper


async def download_one(client: httpx.AsyncClient, boot: BinaryBootstrapper, name: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  {name}: already bundled at {dest}")
        return True

    url = boot._get_download_url(name)
    version = boot.KNOWN_VERSIONS.get(name, "?")
    print(f"  {name}: downloading v{version} from {url}...")

    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            print(f"  {name}: bundled ({len(resp.content)} bytes)")
            return True
        else:
            print(f"  {name}: download failed HTTP {resp.status_code}")
            return False
    except Exception as exc:
        print(f"  {name}: download error: {exc}")
        return False


async def main() -> int:
    dist_binaries = Path("dist/binaries")
    boot = BinaryBootstrapper()
    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        results = []
        for name in boot.KNOWN_VERSIONS:
            dest = dist_binaries / name
            ok = await download_one(client, boot, name, dest)
            results.append(ok)
    if all(results):
        print("All binaries bundled.")
        return 0
    else:
        print("Some binaries could not be downloaded. The dist will still build.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
