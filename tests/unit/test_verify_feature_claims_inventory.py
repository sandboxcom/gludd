"""Regression tests for warning-free feature-claim verification."""

from pathlib import Path


def test_verify_feature_claims_uses_explicit_localhost_inventory() -> None:
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )
    marker = "verify-feature-claims:"
    assert marker in makefile
    recipe = makefile.split(marker, 1)[1].split("\n\n", 1)[0]

    assert "-i localhost," in recipe, (
        "verify-feature-claims must pass an explicit localhost inventory so "
        "Ansible does not emit the no-inventory/implicit-localhost warnings"
    )
    assert "-c local" in recipe, (
        "an explicit localhost inventory must also select the local connection "
        "instead of attempting SSH to the developer workstation"
    )


def test_verify_feature_claims_pins_controller_python() -> None:
    playbook = (
        Path(__file__).resolve().parents[2] / "playbooks" / "verify_feature_claims.yml"
    ).read_text(encoding="utf-8")

    assert 'ansible_python_interpreter: "{{ ansible_playbook_python }}"' in playbook, (
        "localhost verification must use the controller interpreter explicitly "
        "instead of emitting Ansible's interpreter-discovery warning"
    )
