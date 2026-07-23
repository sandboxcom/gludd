"""Behavioral tests for src/general_ludd/physics/mechanistic_interpretability.py."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from general_ludd.physics.mechanistic_interpretability import (
    ACTIVATION_FUNCTIONS,
    ATTENTION_HEAD_PATTERNS,
    GRAD_CAM_LAYERS,
    SAE_CONFIGS,
    SUPERVISION_EVALUATION,
    activation_maximization,
    analyze_attention_patterns,
    compute_completeness,
    compute_copy_suppression_score,
    compute_faithfulness,
    compute_knowledge_neuron_score,
    compute_shap_values,
    concept_bottleneck_predict,
    deepdream,
    detect_circuits,
    detect_induction_heads,
    feature_inversion,
    generate_random_baseline,
    grokking_detect,
    human_simulatability_score,
    integrated_gradients,
    interpolate_input,
    lime_explain,
    normalize_attribution,
    phase_change_detect,
    saliency_map,
    smoothgrad,
    sparse_autoencoder_decode,
    sparse_autoencoder_encode,
    superposition_simulate,
    tcav_score,
    toy_model_reversal_curse,
    train_probing_classifier,
)


def _linear_model(input_data: list[float]) -> list[float]:
    w = [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01]
    score = sum(a * b for a, b in zip(input_data, w[:len(input_data)], strict=False))
    return [score * 0.2, score * 0.5, score * 0.3]


def _identity_decoder(activation_vector: list[float]) -> list[float]:
    return activation_vector


def _make_classifier_fn() -> Callable[[list[float]], float]:
    def classifier(activations: list[float]) -> float:
        return 1.0 if sum(activations) / max(len(activations), 1) > 0.5 else -1.0
    return classifier


class TestDataTables:
    def test_activation_functions_has_min_entries(self) -> None:
        assert len(ACTIVATION_FUNCTIONS) >= 5

    def test_activation_functions_relu_works(self) -> None:
        fn = ACTIVATION_FUNCTIONS["relu"]
        assert fn(5.0) == 5.0
        assert fn(-3.0) == 0.0

    def test_activation_functions_sigmoid_range(self) -> None:
        fn = ACTIVATION_FUNCTIONS["sigmoid"]
        assert 0.5 <= fn(0.0) <= 1.0
        assert 0.0 <= fn(-10.0) <= 0.5

    def test_grad_cam_layers_has_entries(self) -> None:
        assert len(GRAD_CAM_LAYERS) >= 3

    def test_sae_configs_has_entries(self) -> None:
        assert len(SAE_CONFIGS) >= 2
        for name in ("small", "standard"):
            assert name in SAE_CONFIGS
            assert "expansion_factor" in SAE_CONFIGS[name]

    def test_attention_head_patterns_has_entries(self) -> None:
        assert len(ATTENTION_HEAD_PATTERNS) >= 3
        assert "induction_head" in ATTENTION_HEAD_PATTERNS

    def test_supervision_evaluation_has_entries(self) -> None:
        assert len(SUPERVISION_EVALUATION) >= 1
        assert "faithfulness" in SUPERVISION_EVALUATION


class TestNormalizeAttribution:
    def test_range_zero_to_one(self) -> None:
        result = normalize_attribution([3.0, -1.0, 5.0, 0.0, 2.0])
        assert min(result) == pytest.approx(0.0, 1e-6)
        assert max(result) == pytest.approx(1.0, 1e-6)

    def test_all_equal_returns_half(self) -> None:
        result = normalize_attribution([2.0, 2.0, 2.0])
        assert all(v == pytest.approx(0.5, 1e-6) for v in result)

    def test_empty_list(self) -> None:
        assert normalize_attribution([]) == []


class TestGenerateRandomBaseline:
    def test_correct_shape(self) -> None:
        result = generate_random_baseline(7)
        assert len(result) == 7

    def test_zeros_method(self) -> None:
        result = generate_random_baseline(4, "zeros")
        assert all(v == 0.0 for v in result)

    def test_random_method_nonzero(self) -> None:
        result = generate_random_baseline(50, "uniform")
        assert len(result) == 50
        assert any(v != 0.0 for v in result)

    def test_unknown_method_defaults_to_zeros(self) -> None:
        result = generate_random_baseline(3, "bogus")
        assert all(v == 0.0 for v in result)


class TestInterpolateInput:
    def test_alpha_half(self) -> None:
        result = interpolate_input([0.0, 0.0], [1.0, 2.0], 0.5)
        assert result[0] == pytest.approx(0.5, 1e-6)
        assert result[1] == pytest.approx(1.0, 1e-6)

    def test_alpha_zero_returns_baseline(self) -> None:
        result = interpolate_input([0.0, 0.0], [1.0, 2.0], 0.0)
        assert result == [0.0, 0.0]

    def test_alpha_one_returns_input(self) -> None:
        result = interpolate_input([0.0, 0.0], [1.0, 2.0], 1.0)
        assert result == [1.0, 2.0]


class TestActivationMaximization:
    def test_returns_list_of_floats(self) -> None:
        result = activation_maximization(
            model_fn=_linear_model, input_shape=8, target_class=1,
            steps=10, lr=0.01,
        )
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(v, float) for v in result)

    def test_correct_input_shape(self) -> None:
        result = activation_maximization(
            model_fn=_linear_model, input_shape=5, target_class=0,
            steps=5, lr=0.01,
        )
        assert len(result) == 5


class TestDeepDream:
    def test_returns_non_negative_values(self) -> None:
        image = [0.5] * 64
        result = deepdream(
            model_fn=_linear_model, image=image, target_layer=2,
            iterations=5, octaves=2, octave_scale=1.2,
        )
        assert isinstance(result, list)
        assert all(v >= 0 for v in result)

    def test_same_shape_as_input(self) -> None:
        image = [0.1] * 32
        result = deepdream(
            model_fn=_linear_model, image=image, target_layer=0,
            iterations=3, octaves=2,
        )
        assert len(result) == len(image)


class TestFeatureInversion:
    def test_returns_reconstructed_input(self) -> None:
        features = [0.5, 0.3, 0.8, 0.1, 0.9]
        result = feature_inversion(
            activation_vector=features, decoder_fn=_identity_decoder,
            iterations=50, lr=0.05,
        )
        assert isinstance(result, list)
        assert len(result) == len(features)


class TestSaliencyMap:
    def test_same_dimensionality(self) -> None:
        input_tensor = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = saliency_map(model_fn=_linear_model, input_data=input_tensor, target_class=1)
        assert len(result) == len(input_tensor)

    def test_returns_attribution_values(self) -> None:
        result = saliency_map(model_fn=_linear_model, input_data=[0.5] * 10, target_class=0)
        assert all(isinstance(v, (int, float)) for v in result)


class TestIntegratedGradients:
    def test_correct_shape(self) -> None:
        input_tensor = [0.2] * 8
        result = integrated_gradients(
            model_fn=_linear_model, input_data=input_tensor,
            baseline=[0.0] * 8, target_class=0, steps=20,
        )
        assert len(result) == len(input_tensor)
        assert all(isinstance(v, (int, float)) for v in result)

    def test_default_baseline(self) -> None:
        input_tensor = [0.3] * 4
        result = integrated_gradients(
            model_fn=_linear_model, input_data=input_tensor,
            target_class=0, steps=10,
        )
        assert len(result) == 4


class TestSmoothGrad:
    def test_returns_smoothed_attribution(self) -> None:
        result = smoothgrad(
            model_fn=_linear_model, input_data=[0.3] * 6,
            target_class=1, num_samples=10, noise_level=0.1,
        )
        assert len(result) == 6
        assert all(isinstance(v, (int, float)) for v in result)


class TestComputeShapValues:
    def test_returns_contribution_values(self) -> None:
        result = compute_shap_values(
            model_fn=_linear_model, input_data=[0.5, 0.2, 0.8, 0.1],
            target_class=0, num_samples=20,
        )
        assert len(result) == 4
        assert all(isinstance(v, (int, float)) for v in result)


class TestLimeExplain:
    def test_returns_importance_and_features(self) -> None:
        result = lime_explain(
            model_fn=_linear_model, input_data=[0.1, 0.4, 0.7, 0.2, 0.9],
            num_features=5, num_samples=50,
        )
        assert isinstance(result, dict)
        assert "importance" in result
        assert "features" in result

    def test_empty_input_graceful(self) -> None:
        result = lime_explain(model_fn=_linear_model, input_data=[], num_samples=10)
        assert isinstance(result, dict)


class TestSparseAutoencoder:
    def test_encode_returns_dict(self) -> None:
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        activations = [0.5, 0.3, 0.9]
        result = sparse_autoencoder_encode(dictionary=dictionary, activations=activations)
        assert "latent_codes" in result
        assert "reconstruction" in result

    def test_decode_returns_reconstruction(self) -> None:
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        latent = [0.5, 1.2, 0.0]
        result = sparse_autoencoder_decode(latent_codes=latent, dictionary=dictionary)
        assert isinstance(result, list)

    def test_encode_then_decode_roundtrip_has_same_length(self) -> None:
        dictionary = [[0.5, 0.1, 0.0], [0.2, 0.8, 0.1], [0.0, 0.1, 0.7]]
        activations = [0.6, 0.4, 0.2]
        encoded = sparse_autoencoder_encode(dictionary=dictionary, activations=activations)
        decoded = sparse_autoencoder_decode(
            latent_codes=encoded["latent_codes"], dictionary=dictionary)
        assert len(decoded) == len(activations)

    def test_empty_dictionary_encode(self) -> None:
        result = sparse_autoencoder_encode(dictionary=[], activations=[0.5])
        assert "latent_codes" in result
        assert result["latent_codes"] == []

    def test_empty_latent_decode(self) -> None:
        result = sparse_autoencoder_decode(latent_codes=[], dictionary=[])
        assert result == []


class TestTrainProbingClassifier:
    def test_returns_dict_with_weights_accuracy(self) -> None:
        activations = [[0.5, 0.2], [0.3, 0.7], [0.8, 0.1], [0.1, 0.9]]
        labels = [0.0, 1.0, 0.0, 1.0]
        result = train_probing_classifier(activations, labels)
        assert "weights" in result
        assert "accuracy" in result

    def test_empty_input(self) -> None:
        result = train_probing_classifier([], [])
        assert result["accuracy"] == 0.0


class TestKnowledgeNeuronScore:
    def test_returns_float_between_zero_one(self) -> None:
        activations = [0.5, 0.3, 0.9, 0.1, 0.7]
        labels = [0.5, 0.3, 0.9, 0.1, 0.7]
        score = compute_knowledge_neuron_score(activations, labels)
        assert 0.0 <= score <= 1.0

    def test_perfect_correlation(self) -> None:
        activations = [1.0, 2.0, 3.0, 4.0, 5.0]
        labels = [2.0, 4.0, 6.0, 8.0, 10.0]
        score = compute_knowledge_neuron_score(activations, labels)
        assert score == pytest.approx(1.0, 1e-3)

    def test_short_input_returns_zero(self) -> None:
        assert compute_knowledge_neuron_score([1.0], [1.0]) == 0.0


class TestDetectCircuits:
    def test_returns_dict_with_circuits(self) -> None:
        model_layers = [[[0.5, 0.3], [0.1, 0.5]]]
        result = detect_circuits(
            model_layers=model_layers, input_data=[0.2, 0.8],
            target_output=1.0, threshold=0.3,
        )
        assert "circuits" in result
        assert "num_circuits" in result

    def test_empty_layers(self) -> None:
        result = detect_circuits([], [0.5], 1.0)
        assert result["num_circuits"] == 0


class TestAnalyzeAttentionPatterns:
    def test_returns_analysis_dict(self) -> None:
        attention = [[[0.2, 0.8], [0.5, 0.5]]]
        result = analyze_attention_patterns(attention)
        assert "num_heads" in result
        assert "mean_attention_per_head" in result

    def test_empty_attention(self) -> None:
        result = analyze_attention_patterns([])
        assert result["num_heads"] == 0


class TestDetectInductionHeads:
    def test_returns_list_of_head_indices(self) -> None:
        attention = [[[0.1, 0.9], [0.0, 0.0]], [[0.0, 0.0], [0.5, 0.5]]]
        result = detect_induction_heads(attention)
        assert isinstance(result, list)
        assert all(isinstance(h, int) for h in result)

    def test_empty_input(self) -> None:
        assert detect_induction_heads([]) == []


class TestCopySuppressionScore:
    def test_returns_float(self) -> None:
        prev = [0.2, 0.5, 0.3]
        curr = [0.1, 0.1, 0.8]
        score = compute_copy_suppression_score(prev, curr)
        assert isinstance(score, float)

    def test_empty_input(self) -> None:
        assert compute_copy_suppression_score([], []) == 0.0


class TestTcavScore:
    def test_returns_float(self) -> None:
        concept = [0.5, 0.3, 0.8, 0.1, 0.9, 0.7, 0.6, 0.4]
        random_vals = [0.2, 0.6, 0.1, 0.7, 0.3, 0.8, 0.5, 0.9]
        score = tcav_score(
            concept_activations=concept, random_activations=random_vals,
            classifier_fn=_make_classifier_fn(),
        )
        assert isinstance(score, float)

    def test_empty_input(self) -> None:
        assert tcav_score([], [], _make_classifier_fn()) == 0.0


class TestConceptBottleneckPredict:
    def test_returns_float(self) -> None:
        concepts = [0.8, 0.2, 0.6, 0.9, 0.1]
        weights = [0.3, -0.1, 0.5, 0.2, 0.0]
        prediction = concept_bottleneck_predict(concepts, weights)
        assert isinstance(prediction, float)

    def test_linear_combination(self) -> None:
        concepts = [2.0, 3.0]
        weights = [0.5, 0.5]
        result = concept_bottleneck_predict(concepts, weights)
        assert result == pytest.approx(2.5, 1e-6)


class TestReversalCurse:
    def test_returns_dict_with_results(self) -> None:
        result = toy_model_reversal_curse(num_tokens=10, num_layers=1, training_steps=5)
        assert "forward_accuracy" in result
        assert "reverse_accuracy" in result
        assert "reversal_gap" in result


class TestSuperpositionSimulate:
    def test_returns_dict_with_metrics(self) -> None:
        result = superposition_simulate(
            num_features=10, num_dimensions=4, sparsity=0.3, num_samples=50,
        )
        assert "reconstruction_error" in result
        assert "feature_recovery_rate" in result
        assert "compression_ratio" in result
        assert result["num_features"] == 10


class TestGrokkingDetect:
    def test_detects_grokking(self) -> None:
        loss = [2.0, 1.5, 1.0, 0.8, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1,
                0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.015, 0.01, 0.008, 0.005,
                0.004, 0.003, 0.002, 0.001, 0.001]
        acc = [0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
               0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.88, 0.92, 0.95,
               0.96, 0.97, 0.98, 0.99, 0.99]
        result = grokking_detect(loss, acc)
        assert "detected" in result
        assert "grokking_step" in result

    def test_short_history_no_detect(self) -> None:
        result = grokking_detect([1.0, 2.0], [0.1, 0.2])
        assert result["detected"] is False


class TestPhaseChangeDetect:
    def test_returns_dict_with_change_points(self) -> None:
        metrics = [0.2, 0.2, 0.3, 0.3, 0.8, 0.9, 0.9, 0.9]
        result = phase_change_detect(metrics, "loss", window=2)
        assert "change_points" in result
        assert "metric_name" in result

    def test_short_series(self) -> None:
        result = phase_change_detect([1.0, 2.0], "loss", window=50)
        assert result["change_points"] == []


class TestFaithfulnessCompleteness:
    def test_faithfulness_returns_float(self) -> None:
        score = compute_faithfulness(
            attribution=[0.3, 0.7, 0.0, 0.0, 0.0],
            model_fn=_linear_model, input_data=[0.5, 0.2, 0.1, 0.8, 0.3],
        )
        assert isinstance(score, float)

    def test_faithfulness_empty_input(self) -> None:
        assert compute_faithfulness([], _linear_model, []) == 0.0

    def test_completeness_returns_float(self) -> None:
        score = compute_completeness(
            attribution=[0.2, 0.6, 0.1, 0.05, 0.05],
            model_fn=_linear_model, input_data=[0.3, 0.4, 0.9, 0.1, 0.7],
        )
        assert isinstance(score, float)

    def test_completeness_empty_input(self) -> None:
        assert compute_completeness([], _linear_model, []) == 0.0


class TestHumanSimulatabilityScore:
    def test_returns_float(self) -> None:
        model_preds = [1.0, 0.0, 1.0, 1.0, 0.0]
        human_preds = [1.0, 0.0, 1.0, 0.0, 0.0]
        score = human_simulatability_score(model_preds, human_preds)
        assert isinstance(score, float)

    def test_perfect_agreement(self) -> None:
        score = human_simulatability_score([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert score == 1.0

    def test_empty_lists(self) -> None:
        assert human_simulatability_score([], []) == 0.0
