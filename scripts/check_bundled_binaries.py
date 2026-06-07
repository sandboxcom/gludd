"""Check for bundled binaries in dist/binaries/ and report status."""
from general_ludd.filestore.bootstrap import BinaryBootstrapper

boot = BinaryBootstrapper()
for name in ("openbao", "opentofu"):
    bundled = boot.get_bundled_binary_path(name)
    if bundled:
        print(f"  {name}: bundled at {bundled}")
    else:
        print(f"  {name}: not bundled — run gludd filestore bootstrap to download")
