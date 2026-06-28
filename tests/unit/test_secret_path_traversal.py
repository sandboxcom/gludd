"""Regression tests: cross-project secret path traversal (HIGH security fix).

Covers:
- cosign._scoped_path rejects traversal in project_id and key_name
- gitsign._scoped_path rejects traversal in project_id
- SecretsManager.write_secret / read_secret / delete_secret reject traversal paths
"""

from __future__ import annotations

import pytest

from general_ludd.secrets.cosign import _scoped_path as cosign_scoped_path
from general_ludd.secrets.gitsign import _scoped_path as gitsign_scoped_path
from general_ludd.secrets.manager import SecretsManager

# ---------------------------------------------------------------------------
# cosign._scoped_path
# ---------------------------------------------------------------------------

class TestCosignScopedPath:
    def test_valid_ids_produce_expected_path(self) -> None:
        assert cosign_scoped_path("proj-1", "default") == "projects/proj-1/cosign/default"

    def test_underscore_and_hyphen_are_allowed(self) -> None:
        assert cosign_scoped_path("my_project", "my-key") == "projects/my_project/cosign/my-key"

    # --- traversal in key_name ---
    def test_dotdot_key_name_raises(self) -> None:
        with pytest.raises(ValueError, match="key_name"):
            cosign_scoped_path("proj1", "../../victim/cosign/default")

    def test_dotdot_single_segment_key_name_raises(self) -> None:
        with pytest.raises(ValueError, match="key_name"):
            cosign_scoped_path("proj1", "..")

    def test_slash_in_key_name_raises(self) -> None:
        with pytest.raises(ValueError, match="key_name"):
            cosign_scoped_path("proj1", "a/b")

    def test_dot_in_key_name_raises(self) -> None:
        with pytest.raises(ValueError, match="key_name"):
            cosign_scoped_path("proj1", "a.b")

    # --- traversal in project_id ---
    def test_dotdot_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            cosign_scoped_path("../../etc", "default")

    def test_slash_in_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            cosign_scoped_path("proj/evil", "default")

    def test_empty_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            cosign_scoped_path("", "default")

    def test_empty_key_name_raises(self) -> None:
        with pytest.raises(ValueError, match="key_name"):
            cosign_scoped_path("proj1", "")


# ---------------------------------------------------------------------------
# gitsign._scoped_path
# ---------------------------------------------------------------------------

class TestGitsignScopedPath:
    def test_valid_project_id_produces_expected_path(self) -> None:
        assert gitsign_scoped_path("proj-1") == "projects/proj-1/gitsign/config"

    def test_dotdot_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            gitsign_scoped_path("../../victim")

    def test_slash_in_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            gitsign_scoped_path("proj/evil")

    def test_empty_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            gitsign_scoped_path("")

    def test_dot_in_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id"):
            gitsign_scoped_path("proj.evil")


# ---------------------------------------------------------------------------
# SecretsManager._validate_secret_path (via write/read/delete)
# ---------------------------------------------------------------------------

class TestSecretsManagerPathValidation:
    """Manager rejects traversal paths before any backend call is made."""

    def _mgr(self) -> SecretsManager:
        # No client needed — validation fires before the client is consulted.
        return SecretsManager(client=None)

    # write_secret
    def test_write_dotdot_segment_raises(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().write_secret("projects/../victim/cosign/default", {})

    def test_write_path_with_invalid_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid secret path"):
            self._mgr().write_secret("projects/evil project/key", {})

    def test_write_valid_path_does_not_raise_on_validation(self) -> None:
        # Validation passes; RuntimeError from missing client is expected next.
        with pytest.raises(RuntimeError, match="Not connected"):
            self._mgr().write_secret("projects/proj-1/cosign/default", {"x": 1})

    # read_secret
    def test_read_dotdot_segment_raises(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().read_secret("projects/../victim/cosign/default")

    def test_read_absolute_path_raises(self) -> None:
        # Leading slash — fails _PATH_RE (starts with '/')
        with pytest.raises(ValueError, match="invalid secret path"):
            self._mgr().read_secret("/etc/passwd")

    def test_read_valid_path_proceeds_to_connection_check(self) -> None:
        # Validation passes; SecretsUnavailableError from missing client follows.
        from general_ludd.secrets.manager import SecretsUnavailableError
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            self._mgr().read_secret("projects/proj-1/cosign/default")

    # delete_secret
    def test_delete_dotdot_segment_raises(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().delete_secret("projects/../victim/cosign/default")

    def test_delete_valid_path_does_not_raise_on_validation(self) -> None:
        with pytest.raises(RuntimeError, match="Not connected"):
            self._mgr().delete_secret("projects/proj-1/cosign/default")

    # explicit traversal payloads from the bug report
    def test_bug_report_payload_cosign_write(self) -> None:
        """key_name='../../victim/cosign/default' must be rejected at the manager layer."""
        with pytest.raises(ValueError):
            self._mgr().write_secret(
                "projects/attacker/cosign/../../victim/cosign/default", {}
            )

    # --- regression: dots and colons in image-pin paths must be ACCEPTED ---

    def test_image_pin_path_with_dots_and_colons_accepted_write(self) -> None:
        """image-pins/ghcr.io/openbao/openbao must pass validation (dots + colons allowed)."""
        with pytest.raises(RuntimeError, match="Not connected"):
            self._mgr().write_secret("image-pins/ghcr.io/openbao/openbao", {"x": 1})

    def test_image_pin_path_with_dots_and_colons_accepted_read(self) -> None:
        from general_ludd.secrets.manager import SecretsUnavailableError
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            self._mgr().read_secret("image-pins/ghcr.io/openbao/openbao")

    def test_image_pin_path_with_dots_and_colons_accepted_delete(self) -> None:
        with pytest.raises(RuntimeError, match="Not connected"):
            self._mgr().delete_secret("image-pins/ghcr.io/openbao/openbao")

    def test_path_with_colon_accepted(self) -> None:
        """Paths like 'ns:key/value' must pass validation."""
        with pytest.raises(RuntimeError, match="Not connected"):
            self._mgr().write_secret("ns:key/value", {"x": 1})

    # --- traversal must still be REJECTED even with dots/colons ---

    def test_dotdot_segment_mid_path_rejected(self) -> None:
        """a/../b must be rejected: '..' segment present."""
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().write_secret("a/../b", {})

    def test_dotdot_leading_segment_rejected(self) -> None:
        """../x must be rejected: '..' as first segment."""
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().write_secret("../x", {})

    def test_dotdot_in_image_pin_path_rejected(self) -> None:
        """image-pins/../etc must be rejected despite dots being otherwise allowed."""
        with pytest.raises(ValueError, match=r"'\.\.'"):
            self._mgr().write_secret("image-pins/../etc/passwd", {})
