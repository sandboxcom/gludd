"""Keep filelock Windows ctypes analysis local to its native platform."""

import sys

# Only the Windows implementation's dead POSIX ctypes edge is omitted.
# The module and all Windows imports remain available on Windows.
excludedimports: list[str] = ["ctypes"] if sys.platform != "win32" else []
