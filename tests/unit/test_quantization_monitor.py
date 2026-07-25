"""Tests for QuantizationMonitor — tunable demon subsystem for quantization detection."""

from __future__ import annotations


class TestQuantizationMonitor:
    def test_detects_repetitive_patterns(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        response = "hello world hello world hello world hello world hello world " * 3
        score = monitor.check_response("test-model", response)
        assert score.score > 0.0
        assert any("repetitive" in a for a in score.artifacts)

    def test_detects_vocabulary_reduction(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        response = (
            "This is is a very very very simple response. "
            "The the result was was computed. "
            "It was very very straightforward and um er um quite quite simple."
        )
        score = monitor.check_response("test-model", response)
        assert score.score > 0.0
        assert any("reduction" in a for a in score.artifacts)

    def test_detects_precision_loss(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        response = (
            "The value is approximately 3.14, roughly 2.71, and about 1.41. "
            "There are some numbers around 1.61 plus or minus 0.01. "
            "Multiple values and several results in the range 2.5 to 3.5."
        )
        score = monitor.check_response("test-model", response)
        assert score.score > 0.0
        assert any("precision_loss" in a for a in score.artifacts)

    def test_low_score_on_clean_response(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        response = (
            "The function calculates the area of a circle given its radius. "
            "The formula is A = pi * r^2. For a radius of 5, the area is "
            "78.53981633974483. The implementation handles "
            "edge cases for negative and zero radii appropriately."
        )
        score = monitor.check_response("test-model", response)
        assert score.score < 0.3

    def test_configure_tunable(self):
        from general_ludd.quantization.monitor import MonitorConfig, QuantizationMonitor

        monitor = QuantizationMonitor(MonitorConfig(alert_threshold=0.5, check_interval_s=120))
        assert monitor.config.alert_threshold == 0.5
        assert monitor.config.check_interval_s == 120

        monitor.configure(alert_threshold=0.9, check_interval_s=600)
        assert monitor.config.alert_threshold == 0.9
        assert monitor.config.check_interval_s == 600

    def test_alert_fires_at_threshold(self):
        from general_ludd.quantization.monitor import MonitorConfig, QuantizationMonitor

        monitor = QuantizationMonitor(MonitorConfig(alert_threshold=0.1, cooldown_alerts_s=0))

        response = "hello world hello world hello world hello world hello world " * 5
        score = monitor.check_response("test-model", response)
        assert score.threshold_exceeded
        alerts = monitor.get_alerts()
        assert len(alerts) >= 1
        assert alerts[0]["model_id"] == "test-model"

    def test_history_tracks_scores(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        monitor.check_response("m1", "clean response here")
        monitor.check_response("m1", "hello hello hello hello hello hello " * 3)
        monitor.check_response("m2", "another clean response")

        history = monitor.get_history()
        assert "m1" in history
        assert "m2" in history
        assert len(history["m1"]) == 2
        assert len(history["m2"]) == 1

    def test_status_returns_counts(self):
        from general_ludd.quantization.monitor import MonitorConfig, QuantizationMonitor

        monitor = QuantizationMonitor(MonitorConfig(alert_threshold=0.05, cooldown_alerts_s=0))

        monitor.check_response("m1", "hello world hello world hello world hello world hello world " * 5)
        monitor.check_response("m2", "clean response")

        status = monitor.status()
        assert status["models_tracked"] == 2
        assert status["total_checks"] == 2
        assert status["alerts_fired"] >= 1

    def test_start_stop_lifecycle(self):
        import asyncio

        from general_ludd.quantization.monitor import MonitorConfig, QuantizationMonitor

        async def _run():
            monitor = QuantizationMonitor(MonitorConfig())
            assert not monitor._running
            await monitor.start()
            assert monitor._running
            await monitor.stop()
            assert not monitor._running

        asyncio.run(_run())

    def test_alert_cooldown_respected(self):
        from general_ludd.quantization.monitor import MonitorConfig, QuantizationMonitor

        monitor = QuantizationMonitor(
            MonitorConfig(alert_threshold=0.1, cooldown_alerts_s=3600)
        )

        response = "hello world hello world hello world hello world hello world " * 5
        monitor.check_response("test-model", response)
        assert len(monitor.get_alerts()) == 1

        monitor.check_response("test-model", response)
        assert len(monitor.get_alerts()) == 1


class TestScoringFunctions:
    def test_repetitive_patterns_high_density(self):
        from general_ludd.quantization.monitor import _score_repetitive_patterns

        response = "abc abc abc abc abc " * 20
        score, desc = _score_repetitive_patterns(response)
        assert score > 0.5
        assert "repetitive" in desc

    def test_repetitive_patterns_clean(self):
        from general_ludd.quantization.monitor import _score_repetitive_patterns

        response = "unique words in a varied and natural sentence"
        score, _desc = _score_repetitive_patterns(response)
        assert score == 0.0

    def test_vocabulary_reduction(self):
        from general_ludd.quantization.monitor import _score_vocabulary_reduction

        response = "this is is a very very very repetitive the the sentence"
        score, desc = _score_vocabulary_reduction(response)
        assert score > 0.0
        assert "reduction" in desc

    def test_precision_loss_approx(self):
        from general_ludd.quantization.monitor import _score_precision_loss

        response = "The value is approximately 3.14 and roughly 2.71"
        score, desc = _score_precision_loss(response)
        assert score > 0.0
        assert "precision_loss" in desc

    def test_precision_loss_clean(self):
        from general_ludd.quantization.monitor import _score_precision_loss

        response = "The exact value is 3.14159265358979323846 calculated precisely."
        score, _desc = _score_precision_loss(response)
        assert score < 0.3


class TestMonitorConfig:
    def test_default_values(self):
        from general_ludd.quantization.monitor import MonitorConfig

        config = MonitorConfig()
        assert config.alert_threshold == 0.7
        assert config.check_interval_s == 300
        assert config.max_history_samples == 1000
        assert config.cooldown_alerts_s == 600

    def test_to_dict(self):
        from general_ludd.quantization.monitor import MonitorConfig

        config = MonitorConfig(alert_threshold=0.5)
        d = config.to_dict()
        assert d["alert_threshold"] == 0.5
        assert d["check_interval_s"] == 300


class TestQuantizationScore:
    def test_to_dict(self):
        from general_ludd.quantization.monitor import QuantizationScore

        score = QuantizationScore(
            model_id="test",
            score=0.85,
            artifacts=["repetitive_patterns"],
            threshold_exceeded=True,
            checked_at=1000.0,
        )
        d = score.to_dict()
        assert d["model_id"] == "test"
        assert d["score"] == 0.85
        assert d["threshold_exceeded"] is True
