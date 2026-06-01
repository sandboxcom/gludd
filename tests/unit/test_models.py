"""Unit tests for model gateway."""

from general_ludd.models.gateway import ModelGateway, ModelProfile


class TestModelGateway:
    def test_stub_model_profile_available(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="test",
            enabled=True,
            provider="openai",
        )])
        assert gw.is_available("test") is True

    def test_disabled_model_not_available(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="disabled",
            enabled=False,
        )])
        assert gw.is_available("disabled") is False

    def test_missing_model_not_available(self):
        gw = ModelGateway()
        assert gw.is_available("nonexistent") is False

    def test_model_gateway_rejects_over_budget_call(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="budget_test",
            enabled=True,
            api_metered=True,
            run_budget_usd=200.0,
        )])
        assert gw.check_budget("budget_test", 250.0, 300.0) is False

    def test_model_gateway_allows_within_budget(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="budget_test",
            enabled=True,
            api_metered=True,
            run_budget_usd=200.0,
        )])
        assert gw.check_budget("budget_test", 50.0, 150.0) is True

    def test_local_model_profile_requires_explicit_config(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="local_llm",
            enabled=False,
            resource_profile="local_heavy",
        )])
        assert gw.is_available("local_llm") is False

    def test_list_profiles(self):
        profiles = [
            ModelProfile(model_profile_id="a", enabled=True),
            ModelProfile(model_profile_id="b", enabled=False),
        ]
        gw = ModelGateway(profiles)
        assert len(gw.list_profiles()) == 2
