"""Red-team regression tests for path sanitization (finding F-1).

F-1 (MED): security/sanitize.py's _PATH_TRAVERSAL regex matched only
"../" / "..\\" (a parent ref FOLLOWED BY a separator). A bare ".." or a
trailing "foo/.." component therefore slipped through and sanitize_path
returned the string unchanged (FAIL-OPEN). The filestore routes
(routers/filestore.py) feed user-supplied paths through sanitize_path, so
a trailing-".." payload could escape the store root.

FIX (fail-closed): split the path on BOTH separators and reject (return
None) if ANY segment is exactly "..", in addition to the existing
classic-traversal and absolute-path checks. Legitimate relative paths
still pass.
"""

from __future__ import annotations

from general_ludd.security.sanitize import sanitize_path


class TestSanitizePathTraversalFailClosed:
    def test_bare_dotdot_rejected(self):
        assert sanitize_path("..") is None, (
            "F-1: a bare '..' must be rejected (was fail-open)."
        )

    def test_trailing_dotdot_component_rejected(self):
        assert sanitize_path("foo/..") is None, (
            "F-1: a trailing 'foo/..' component must be rejected (was fail-open)."
        )

    def test_trailing_dotdot_backslash_rejected(self):
        assert sanitize_path("foo\\..") is None

    def test_mid_path_dotdot_rejected(self):
        assert sanitize_path("foo/../bar") is None
        assert sanitize_path("foo\\..\\bar") is None

    def test_classic_leading_traversal_rejected(self):
        assert sanitize_path("../foo") is None
        assert sanitize_path("..\\foo") is None
        assert sanitize_path("../../etc/passwd") is None

    def test_absolute_paths_rejected(self):
        assert sanitize_path("/etc/passwd") is None
        assert sanitize_path("C:\\Windows") is None

    def test_dotdot_followed_by_more_rejected(self):
        assert sanitize_path("../../../root/.ssh/id_rsa") is None


class TestSanitizePathLegitPaths:
    def test_normal_relative_path_passes(self):
        assert sanitize_path("config/settings.yml") == "config/settings.yml"
        assert sanitize_path("playbooks/run.yml") == "playbooks/run.yml"

    def test_plain_filename_passes(self):
        assert sanitize_path("file.txt") == "file.txt"

    def test_leading_dot_slash_stripped(self):
        assert sanitize_path("./config.yml") == "config.yml"

    def test_single_dot_segments_not_rejected(self):
        # "." segments are harmless (current dir), only ".." is traversal.
        assert sanitize_path("././foo") == "./foo"
