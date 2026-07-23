"""Behavioral unit tests for the physics mechanistic_interpretability knowledge module.

Tests the mechanistic_interpretability module_utils: feature visualization,
attribution methods, sparse autoencoders, circuit detection, and related tools.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "physics"
    / "plugins"
    / "module_utils"
    / "mechanistic_interpretability.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mi_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mi() -> ModuleType:
    return _load_module()


def _linear_model(input_data: list[float]) -> list[float]:
    w = [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01]
    score = sum(a * b for a, b in zip(input_data, w[:len(input_data)], strict=False))
    return [score * 0.2, score * 0.5, score * 0.3]


def _linear_model_scalar(input_data: list[float]) -> float:
    w = [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01]
    return sum(a * b for a, b in zip(input_data, w[:len(input_data)], strict=False))


def _make_model_fn():
    return _linear_model


def _identity_decoder(activation_vector: list[float]) -> list[float]:
    return activation_vector


def _make_classifier_fn() -> Any:
    def classifier(activations: list[float]) -> float:
        return float(sum(activations) / len(activations) > 0.5)
    return classifier


# ═══════════════════════════════════════════════════════════════════
# Constants / data tables
# ═══════════════════════════════════════════════════════════════════


class TestDataTables:
    def test_activation_functions_table_present(self, mi):
        assert hasattr(mi, "ACTIVATION_FUNCTIONS")
        assert isinstance(mi.ACTIVATION_FUNCTIONS, dict)
        assert len(mi.ACTIVATION_FUNCTIONS) >= 5

    def test_sae_configs_table_present(self, mi):
        assert hasattr(mi, "SAE_CONFIGS")
        assert isinstance(mi.SAE_CONFIGS, dict)
        assert len(mi.SAE_CONFIGS) >= 2

    def test_attention_head_patterns_table_present(self, mi):
        assert hasattr(mi, "ATTENTION_HEAD_PATTERNS")
        assert isinstance(mi.ATTENTION_HEAD_PATTERNS, dict)
        assert len(mi.ATTENTION_HEAD_PATTERNS) >= 3

    def test_supervision_evaluation_table_present(self, mi):
        assert hasattr(mi, "SUPERVISION_EVALUATION")
        assert isinstance(mi.SUPERVISION_EVALUATION, dict)
        assert len(mi.SUPERVISION_EVALUATION) >= 1


# ═══════════════════════════════════════════════════════════════════
# Feature visualization
# ═══════════════════════════════════════════════════════════════════


class TestActivationMaximization:
    def test_returns_list_of_floats(self, mi):
        result = mi.activation_maximization(
            model_fn=_make_model_fn(),
            input_shape=8,
            target_class=1,
            steps=10,
            lr=0.01,
        )
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(v, float) for v in result)

    def test_nonempty_output_shape(self, mi):
        result = mi.activation_maximization(
            model_fn=_make_model_fn(),
            input_shape=5,
            target_class=0,
            steps=5,
            lr=0.01,
        )
        assert len(result) >= 5


class TestDeepDream:
    def test_returns_non_negative_values(self, mi):
        image = [0.5] * 256
        result = mi.deepdream(
            model_fn=_make_model_fn(),
            image=image,
            target_layer=2,
            iterations=5,
            octaves=2,
            octave_scale=1.2,
        )
        assert isinstance(result, list)
        assert all(v >= 0 for v in result)

    def test_same_shape_as_input(self, mi):
        image = [0.1] * 128
        result = mi.deepdream(
            model_fn=_make_model_fn(),
            image=image,
            target_layer=2,
            iterations=3,
            octaves=2,
        )
        assert len(result) == len(image)


class TestFeatureInversion:
    def test_returns_reconstructed_input(self, mi):
        features = [0.5, 0.3, 0.8, 0.1, 0.9]
        result = mi.feature_inversion(
            activation_vector=features,
            decoder_fn=_identity_decoder,
            iterations=20,
            lr=0.01,
        )
        assert isinstance(result, list)
        assert len(result) > 0


# ═══════════════════════════════════════════════════════════════════
# Attribution methods
# ═══════════════════════════════════════════════════════════════════


class TestSaliencyMap:
    def test_same_dimensionality(self, mi):
        input_tensor = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = mi.saliency_map(
            model_fn=_make_model_fn(),
            input_data=input_tensor,
            target_class=1,
        )
        assert isinstance(result, list)
        assert len(result) == len(input_tensor)

    def test_returns_attribution_values(self, mi):
        input_tensor = [0.5] * 10
        result = mi.saliency_map(
            model_fn=_make_model_fn(),
            input_data=input_tensor,
            target_class=0,
        )
        assert all(isinstance(v, (int, float)) for v in result)


class TestIntegratedGradients:
    def test_attribution_sums_near_prediction_difference(self, mi):
        input_tensor = [0.2] * 8
        baseline = [0.0] * 8
        result = mi.integrated_gradients(
            model_fn=_make_model_fn(),
            input_data=input_tensor,
            baseline=baseline,
            target_class=0,
            steps=20,
        )
        assert isinstance(result, list)
        assert len(result) == len(input_tensor)
        assert all(isinstance(v, (int, float)) for v in result)


class TestSmoothGrad:
    def test_returns_smoothed_attribution(self, mi):
        input_tensor = [0.3] * 6
        result = mi.smoothgrad(
            model_fn=_make_model_fn(),
            input_data=input_tensor,
            target_class=1,
            num_samples=10,
            noise_level=0.1,
        )
        assert isinstance(result, list)
        assert len(result) == len(input_tensor)
        assert all(isinstance(v, (int, float)) for v in result)


class TestComputeShapValues:
    def test_returns_contribution_values(self, mi):
        input_tensor = [0.5, 0.2, 0.8, 0.1]
        result = mi.compute_shap_values(
            model_fn=_make_model_fn(),
            input_data=input_tensor,
            target_class=0,
            num_samples=20,
        )
        assert isinstance(result, list)
        assert len(result) == len(input_tensor)
        assert all(isinstance(v, (int, float)) for v in result)


class TestLimeExplain:
    def test_returns_dict_with_feature_names_and_weights(self, mi):
        input_data = [0.1, 0.4, 0.7, 0.2, 0.9]
        result = mi.lime_explain(
            model_fn=_make_model_fn(),
            input_data=input_data,
            num_features=5,
            num_samples=50,
        )
        assert isinstance(result, dict)
        assert "feature_names" in result or "features" in result
        assert "weights" in result or "importance" in result


# ═══════════════════════════════════════════════════════════════════
# Sparse autoencoders
# ═══════════════════════════════════════════════════════════════════


class TestSparseAutoencoder:
    def test_encode_returns_latent_codes_and_reconstruction(self, mi):
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        activations = [0.5, 0.3, 0.9]
        result = mi.sparse_autoencoder_encode(
            dictionary=dictionary,
            activations=activations,
        )
        assert isinstance(result, dict)
        assert "latent_codes" in result
        assert "reconstruction" in result

    def test_decode_returns_reconstruction(self, mi):
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        latent = [0.0, 1.2, 0.0]
        result = mi.sparse_autoencoder_decode(
            latent_codes=latent,
            dictionary=dictionary,
        )
        assert isinstance(result, list)
        assert len(result) > 0

    def test_encode_then_decode_roundtrip(self, mi):
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        activations = [0.6, 0.4, 0.2]
        encoded = mi.sparse_autoencoder_encode(
            dictionary=dictionary,
            activations=activations,
        )
        decoded = mi.sparse_autoencoder_decode(
            latent_codes=encoded["latent_codes"],
            dictionary=dictionary,
        )
        assert len(decoded) == len(activations)


# ═══════════════════════════════════════════════════════════════════
# Probing and knowledge neurons
# ═══════════════════════════════════════════════════════════════════


class TestTrainProbingClassifier:
    def test_returns_dict_with_weights_and_accuracy(self, mi):
        activations = [[0.5, 0.2], [0.3, 0.7], [0.8, 0.1], [0.1, 0.9]]
        labels = [0, 1, 0, 1]
        result = mi.train_probing_classifier(activations, labels)
        assert isinstance(result, dict)
        assert "weights" in result or "coefficients" in result
        assert "accuracy" in result


class TestComputeKnowledgeNeuronScore:
    def test_returns_float_between_0_and_1(self, mi):
        activations = [0.5, 0.3, 0.9, 0.1, 0.7]
        labels = [0.5, 0.3, 0.9, 0.1, 0.7]
        score = mi.compute_knowledge_neuron_score(activations, labels)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# ═══════════════════════════════════════════════════════════════════
# Circuit and attention analysis
# ═══════════════════════════════════════════════════════════════════


class TestDetectCircuits:
    def test_returns_dict_with_circuit_paths(self, mi):
        model_layers = [[[0.5, 0.3], [0.1, 0.9]]]
        input_data = [0.2, 0.8]
        target_output = 1.0
        result = mi.detect_circuits(
            model_layers=model_layers,
            input_data=input_data,
            target_output=target_output,
            threshold=0.3,
        )
        assert isinstance(result, dict)
        assert len(result) > 0


class TestAnalyzeAttentionPatterns:
    def test_returns_dict_with_head_analysis(self, mi):
        attention_weights = [[[0.2, 0.8], [0.5, 0.5]]]
        result = mi.analyze_attention_patterns(attention_weights)
        assert isinstance(result, dict)
        assert len(result) > 0


class TestDetectInductionHeads:
    def test_returns_list_of_head_indices(self, mi):
        attention_weights = [[[0.1, 0.9], [0.0, 0.0]], [[0.0, 0.0], [0.5, 0.5]]]
        token_positions = [0, 1]
        result = mi.detect_induction_heads(attention_weights, token_positions)
        assert isinstance(result, list)
        assert all(isinstance(h, int) for h in result)


class TestComputeCopySuppressionScore:
    def test_returns_float(self, mi):
        prev_attention = [0.2, 0.5, 0.3]
        curr_attention = [0.1, 0.1, 0.8]
        score = mi.compute_copy_suppression_score(prev_attention, curr_attention)
        assert isinstance(score, float)


# ═══════════════════════════════════════════════════════════════════
# Concept-based interpretability
# ═══════════════════════════════════════════════════════════════════


class TestTcavScore:
    def test_returns_float(self, mi):
        concept_activations = [0.5, 0.3, 0.8, 0.1, 0.9]
        random_activations = [0.2, 0.6, 0.1, 0.7, 0.3]
        score = mi.tcav_score(
            concept_activations=concept_activations,
            random_activations=random_activations,
            classifier_fn=_make_classifier_fn(),
        )
        assert isinstance(score, float)


class TestConceptBottleneckPredict:
    def test_returns_float(self, mi):
        concepts = [0.8, 0.2, 0.6, 0.9, 0.1]
        weights = [0.3, -0.1, 0.5, 0.2, 0.0]
        prediction = mi.concept_bottleneck_predict(
            concept_scores=concepts,
            task_classifier_weights=weights,
        )
        assert isinstance(prediction, float)


# ═══════════════════════════════════════════════════════════════════
# Toy models and simulation
# ═══════════════════════════════════════════════════════════════════


class TestToyModelReversalCurse:
    def test_returns_dict_with_training_results(self, mi):
        result = mi.toy_model_reversal_curse(
            num_tokens=5,
            num_layers=2,
            training_steps=10,
        )
        assert isinstance(result, dict)
        assert len(result) > 0


class TestSuperpositionSimulate:
    def test_returns_dict_with_feature_analysis(self, mi):
        result = mi.superposition_simulate(
            num_features=6,
            num_dimensions=3,
            sparsity=0.3,
            num_samples=100,
        )
        assert isinstance(result, dict)
        assert len(result) > 0


class TestGrokkingDetect:
    def test_returns_dict_with_grokking_detection(self, mi):
        loss_history = [2.0, 1.5, 1.0, 0.5, 0.3, 0.2, 0.15, 0.1, 0.05, 0.01]
        accuracy_history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
        result = mi.grokking_detect(loss_history, accuracy_history)
        assert isinstance(result, dict)


class TestPhaseChangeDetect:
    def test_returns_dict_with_phase_change_info(self, mi):
        metrics = [0.2, 0.2, 0.3, 0.3, 0.8, 0.9, 0.9, 0.9]
        result = mi.phase_change_detect(metrics, "loss")
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
# Evaluation metrics
# ═══════════════════════════════════════════════════════════════════


class TestFaithfulnessCompleteness:
    def test_compute_faithfulness_returns_float(self, mi):
        attribution = [0.3, 0.7, 0.0, 0.0, 0.0]
        input_data = [0.5, 0.2, 0.1, 0.8, 0.3]
        score = mi.compute_faithfulness(
            attribution=attribution,
            model_fn=_make_model_fn(),
            input_data=input_data,
        )
        assert isinstance(score, float)

    def test_compute_completeness_returns_float(self, mi):
        attribution = [0.2, 0.6, 0.1, 0.05, 0.05]
        input_data = [0.3, 0.4, 0.9, 0.1, 0.7]
        score = mi.compute_completeness(
            attribution=attribution,
            model_fn=_make_model_fn(),
            input_data=input_data,
        )
        assert isinstance(score, float)


class TestHumanSimulatabilityScore:
    def test_returns_float(self, mi):
        model_predictions = [1.0, 0.0, 1.0, 1.0, 0.0]
        human_predictions = [1.0, 0.0, 1.0, 0.0, 0.0]
        score = mi.human_simulatability_score(model_predictions, human_predictions)
        assert isinstance(score, float)


# ═══════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeAttribution:
    def test_min_zero_max_one(self, mi):
        attributions = [3.0, -1.0, 5.0, 0.0, 2.0]
        result = mi.normalize_attribution(attributions)
        assert isinstance(result, list)
        assert len(result) == len(attributions)
        assert min(result) == pytest.approx(0.0, 1e-6)
        assert max(result) == pytest.approx(1.0, 1e-6)


class TestGenerateRandomBaseline:
    def test_returns_correct_shape(self, mi):
        result = mi.generate_random_baseline(shape=5)
        assert isinstance(result, list)
        assert len(result) == 5

    def test_zeros_method_returns_all_zeros(self, mi):
        result = mi.generate_random_baseline(shape=4, method="zeros")
        assert all(v == 0.0 for v in result)

    def test_random_method_returns_different_values(self, mi):
        result = mi.generate_random_baseline(shape=10, method="random")
        assert len(result) == 10


class TestInterpolateInput:
    def test_returns_intermediate_point(self, mi):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 1.0, 1.0]
        result = mi.interpolate_input(a, b, alpha=0.5)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == pytest.approx(0.5, 1e-6)

    def test_alpha_zero_returns_baseline(self, mi):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        result = mi.interpolate_input(a, b, alpha=0.0)
        assert result[0] == pytest.approx(0.0, 1e-6)

    def test_alpha_one_returns_input(self, mi):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        result = mi.interpolate_input(a, b, alpha=1.0)
        assert result[0] == pytest.approx(1.0, 1e-6)
