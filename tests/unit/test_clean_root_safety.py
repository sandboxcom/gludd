from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "clean-root.sh"


def test_clean_root_never_deletes_sandboxcom_ssh_key() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "sandboxcom_github_rsa" not in source


def test_clean_root_only_removes_explicitly_allowlisted_junk() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "JUNK_FILES=(" in source
    assert "rm -f \"$f\"" in source
    assert "SSH_KEY" not in source
