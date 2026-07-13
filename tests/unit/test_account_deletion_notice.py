"""Unit tests for account/deletion_notice.py."""

from __future__ import annotations

import pytest

from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_all_notices,
    get_deletion_policy,
    get_policy_text,
)


class TestGetPolicyText:
    def test_deepseek(self):
        text = get_policy_text("deepseek")
        assert "DeepSeek" in text
        assert "30 days" in text

    def test_openai(self):
        text = get_policy_text("openai")
        assert "OpenAI" in text
        assert "30 days" in text

    def test_zai_normalized(self):
        text = get_policy_text("z.ai")
        assert "Z.AI" in text

    def test_aws(self):
        text = get_policy_text("aws")
        assert "AWS" in text
        assert "90-day" in text

    def test_gcp(self):
        text = get_policy_text("gcp")
        assert "GCP" in text

    def test_azure(self):
        text = get_policy_text("azure")
        assert "Azure" in text
        assert "180 days" in text

    def test_case_insensitive(self):
        text = get_policy_text("DeepSeek")
        assert "DeepSeek" in text

    def test_with_whitespace(self):
        text = get_policy_text("  openai  ")
        assert "OpenAI" in text

    def test_zai_key_directly(self):
        text = get_policy_text("zai")
        assert "Z.AI" in text

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="unknown service"):
            get_policy_text("nonexistent")

    def test_supported_services_frozenset(self):
        assert "deepseek" in SUPPORTED_SERVICES
        assert "openai" in SUPPORTED_SERVICES
        assert "zai" in SUPPORTED_SERVICES
        assert "aws" in SUPPORTED_SERVICES
        assert "gcp" in SUPPORTED_SERVICES
        assert "azure" in SUPPORTED_SERVICES
        assert len(SUPPORTED_SERVICES) == 6


class TestGetDeletionPolicy:
    def test_is_alias_for_get_policy_text(self):
        assert get_deletion_policy("openai") == get_policy_text("openai")


class TestBuildDeletionNotice:
    def test_includes_display_name_and_retention(self):
        notice = build_deletion_notice("deepseek")
        assert "DeepSeek" in notice
        assert "data retention notice" in notice
        assert "30 days" in notice

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="unknown service"):
            build_deletion_notice("unknown")

    def test_zai_dot_notation(self):
        notice = build_deletion_notice("z.ai")
        assert "Z.AI" in notice


class TestGetAllNotices:
    def test_returns_mapping(self):
        notices = get_all_notices()
        assert isinstance(notices, dict)
        assert len(notices) == 6

    def test_all_services_present(self):
        notices = get_all_notices()
        for svc in SUPPORTED_SERVICES:
            assert svc in notices

    def test_all_have_data_retention_text(self):
        notices = get_all_notices()
        for notice in notices.values():
            assert "data retention notice" in notice
