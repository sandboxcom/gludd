"""Tests for sprint0 playbooks that are referenced but missing."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent


class TestMissingPlaybooks:
    def test_return_review_playbook_exists(self):
        pb = REPO_ROOT / "playbooks" / "return_review.yml"
        assert pb.exists(), f"Missing playbook: {pb}"

    def test_return_review_playbook_is_valid_yaml(self):
        pb = REPO_ROOT / "playbooks" / "return_review.yml"
        if not pb.exists():
            return
        with open(pb) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list), "return_review.yml should be a list of plays"
        assert len(data) > 0, "return_review.yml should have at least one play"
        play = data[0]
        assert "hosts" in play or "name" in play

    def test_system_load_scrape_playbook_exists(self):
        pb = REPO_ROOT / "playbooks" / "system_load_scrape.yml"
        assert pb.exists(), f"Missing playbook: {pb}"

    def test_system_load_scrape_playbook_is_valid_yaml(self):
        pb = REPO_ROOT / "playbooks" / "system_load_scrape.yml"
        if not pb.exists():
            return
        with open(pb) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list), "system_load_scrape.yml should be a list of plays"
        assert len(data) > 0, "system_load_scrape.yml should have at least one play"
