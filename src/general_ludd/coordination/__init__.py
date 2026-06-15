"""Agent file-overlap coordination package.

Provides FileClaimRegistry for in-memory tracking of which agent workers are
editing which files, enabling concurrent workers to detect conflicts and decide
to wait or merge before writing.
"""

from general_ludd.coordination.file_claims import FileClaimRegistry

__all__ = ["FileClaimRegistry"]
