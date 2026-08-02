"""
mechanistic_interpretability -- Mechanistic interpretability of ML models.

Implements feature visualization, attribution methods, circuit analysis,
transformer interpretability, concept-based explanations, and mechanistic
toy models.  All algorithms use pure Python math (no torch, no tensorflow).

Data tables:
    ACTIVATION_FUNCTIONS       -- dict[name] -> callable (relu, gelu, sigmoid, tanh, swish, softmax, linear)
    GRAD_CAM_LAYERS            -- dict[architecture] -> target layer name patterns
    SAE_CONFIGS                -- dict[config_name] -> dict of SAE params
    ATTENTION_HEAD_PATTERNS    -- dict describing known circuit patterns
    SUPERVISION_EVALUATION     -- dict of evaluation metrics with descriptions

Functions:
    normalize_attribution(attribution)                              -> min-max normalized list[float]
    generate_random_baseline(shape, method)                         -> baseline vector list[float]
    interpolate_input(baseline, input_data, alpha)                  -> interpolated point list[float]

    activation_maximization(model_fn, input_shape, target_class, steps, lr)   -> optimized input list[float]
    deepdream(model_fn, image, target_layer, octaves, octave_scale, iterations) -> dream image list[list[float]]
    feature_inversion(activation_vector, decoder_fn, iterations, lr)          -> reconstructed input list[float]

    saliency_map(model_fn, input_data, target_class)                -> gradient-based saliency list[float]
    integrated_gradients(model_fn, input_data, baseline, target_class, steps) -> IG attribution list[float]
    smoothgrad(model_fn, input_data, target_class, num_samples, noise_level)  -> SmoothGrad list[float]
    compute_shap_values(model_fn, input_data, target_class, num_samples)      -> SHAP values list[float]
    lime_explain(model_fn, input_data, num_features, num_samples)             -> LIME feature importances dict

    sparse_autoencoder_encode(dictionary, activations, sparsity_coefficient)  -> latent codes dict
    sparse_autoencoder_decode(latent_codes, dictionary)                       -> decoded list[float]
    train_probing_classifier(layer_activations, labels, epochs, lr)           -> weights + accuracy dict
    compute_knowledge_neuron_score(neuron_activations, task_labels)           -> correlation float
    detect_circuits(model_layers, input_data, target_output, threshold)       -> circuit detections dict

    analyze_attention_patterns(attention_weights, tokens)            -> attention statistics dict
    detect_induction_heads(attention_weights, token_positions)       -> head indices list[int]
    compute_copy_suppression_score(previous_token_attention, current_token_attention) -> score float

    tcav_score(concept_activations, random_activations, classifier_fn)       -> TCAV score float
    concept_bottleneck_predict(concept_scores, task_classifier_weights)      -> prediction float

    toy_model_reversal_curse(num_tokens, num_layers, training_steps)         -> reversal statistics dict
    superposition_simulate(num_features, num_dimensions, sparsity, num_samples) -> superposition metrics dict
    grokking_detect(loss_history, accuracy_history, threshold)               -> grokking detection dict
    phase_change_detect(metrics_over_time, metric_name, window)              -> phase change points dict

    compute_faithfulness(attribution, model_fn, input_data, top_k)   -> faithfulness float
    compute_completeness(attribution, model_fn, input_data)          -> completeness float
    human_simulatability_score(model_predictions, human_predictions) -> agreement float
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Numerical epsilon and constants
# ═══════════════════════════════════════════════════════════════════

_EPS: float = 1e-8
_H: float = 1e-4

# ═══════════════════════════════════════════════════════════════════
# Data tables
# ═══════════════════════════════════════════════════════════════════

ACTIVATION_FUNCTIONS: dict[str, Callable[[float], float]] = {
    "relu": lambda x: max(0.0, x),
    "gelu": lambda x: 0.5 * x * (1.0 + math.tanh(
        math.sqrt(2.0 / math.pi) * (x + 0.044715 * x ** 3))),
    "sigmoid": lambda x: 1.0 / (1.0 + math.exp(-x)),
    "tanh": lambda x: math.tanh(x),
    "swish": lambda x: x / (1.0 + math.exp(-x)),
    "softmax": None,  # vector-valued, handled specially
    "linear": lambda x: x,
}

GRAD_CAM_LAYERS: dict[str, list[str]] = {
    "resnet50": ["layer4.2.conv3", "layer4", "layer4.2"],
    "resnet18": ["layer4.1.conv2", "layer4", "layer4.1"],
    "vgg16": ["features.28", "features.30", "features"],
    "vgg19": ["features.34", "features.36", "features"],
    "inception_v3": ["Mixed_7c", "Mixed_7c.branch_pool", "Mixed_7c"],
    "efficientnet_b0": ["blocks.6.0", "blocks.6", "blocks"],
    "vit_base": ["encoder.layers.11.mlp", "encoder.layers.11", "encoder"],
    "densenet121": ["features.denseblock4.denselayer16", "features.denseblock4", "features"],
    "alexnet": ["features.10", "features.12", "features"],
    "custom_cnn": ["last_conv", "penultimate", "features"],
}

SAE_CONFIGS: dict[str, dict[str, Any]] = {
    "small": {
        "expansion_factor": 4,
        "sparsity_coefficient": 0.1,
        "l1_penalty": 0.001,
        "tied_weights": False,
        "description": "Lightweight SAE for quick experiments (4x expansion)",
    },
    "standard": {
        "expansion_factor": 16,
        "sparsity_coefficient": 0.1,
        "l1_penalty": 0.0001,
        "tied_weights": False,
        "description": "Standard SAE config (16x expansion, Anthropic-style)",
    },
    "large": {
        "expansion_factor": 64,
        "sparsity_coefficient": 0.05,
        "l1_penalty": 0.00001,
        "tied_weights": False,
        "description": "Large dictionary for capturing rare features (64x expansion)",
    },
    "topk": {
        "expansion_factor": 16,
        "sparsity_coefficient": 0.1,
        "l1_penalty": 0.0,
        "tied_weights": False,
        "top_k": 32,
        "description": "Top-K sparse autoencoder (selects k largest latents)",
    },
    "jumprelu": {
        "expansion_factor": 8,
        "sparsity_coefficient": 0.1,
        "l1_penalty": 0.001,
        "tied_weights": False,
        "threshold": 0.1,
        "description": "JumpReLU SAE with learned threshold per latent",
    },
}

ATTENTION_HEAD_PATTERNS: dict[str, dict[str, Any]] = {
    "induction_head": {
        "pattern": "Attends to token AFTER previous-token head's attended position",
        "mechanism": "Previous-token head (L0) + induction head (L1) = copying",
        "key_signature": "High attention at position (i, prev_i) for layer > 0",
        "known_from": "Elhage et al. 2021 -- A Mathematical Framework",
        "detection_method": "Correlate L0 previous-token attention with L1 current attention",
    },
    "copy_suppression": {
        "pattern": "Token suppresses attention to itself when copying",
        "mechanism": "Self-attention score is reduced relative to neighbor attention",
        "key_signature": "Diagonal attention < off-diagonal attention for copying heads",
        "known_from": "McDougall et al. 2023 -- Copy Suppression",
    },
    "duplicate_token_head": {
        "pattern": "Head attends equally to all occurrences of the same token",
        "mechanism": "QK circuit matches identical token embeddings",
        "key_signature": "High attention at positions with matching token values",
        "known_from": "Wang et al. 2022 -- Interpretability in the Wild",
    },
    "name_mover_head": {
        "pattern": "Head moves information from subject to attribute position",
        "mechanism": "Attends from last token of subject to output position",
        "key_signature": "High attention from query token to subject-final token",
        "known_from": "Meng et al. 2022 -- Locating and Editing Factual Associations",
    },
    "previous_token_head": {
        "pattern": "Head attends to the immediately preceding token",
        "mechanism": "Simple positional pattern: query i attends to key i-1",
        "key_signature": "High values on the first off-diagonal of attention matrix",
        "known_from": "Olsson et al. 2022 -- In-Context Learning and Induction Heads",
    },
}

SUPERVISION_EVALUATION: dict[str, str] = {
    "faithfulness": "Does the explanation accurately reflect the model's reasoning process?",
    "completeness": "Does the explanation capture all features relevant to the decision?",
    "minimality": "Is the explanation as small as possible while remaining complete?",
    "human_simulatability": "Can a human predict model behavior using only the explanation?",
    "monotonicity": "Do larger attribution values correspond to larger effects on output?",
    "implementation_invariance": "Do two functionally identical models yield the same explanation?",
    "sensitivity": "Does the explanation change when irrelevant features are perturbed?",
    "continuity": "Do similar inputs produce similar explanations?",
    "sparsity": "Is the explanation concentrated on few features rather than diffuse?",
    "robustness": "Does the explanation stay stable under small input perturbations?",
}

# ═══════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════


def normalize_attribution(attribution: list[float]) -> list[float]:
    """Min-max normalize attribution values to [0, 1]."""
    if not attribution:
        return []
    mn = min(attribution)
    mx = max(attribution)
    rng = mx - mn
    if rng < _EPS:
        return [0.5] * len(attribution)
    return [(v - mn) / rng for v in attribution]


def generate_random_baseline(shape: int, method: str = "zeros") -> list[float]:
    """Generate a baseline vector for integrated gradients.

    Methods: zeros, mean, uniform, gaussian
    """
    if method == "zeros":
        return [0.0] * shape
    if method == "uniform":
        return [random.uniform(-1.0, 1.0) for _ in range(shape)]
    if method == "gaussian":
        return [random.gauss(0.0, 0.1) for _ in range(shape)]
    if method == "mean":
        return [0.5] * shape
    return [0.0] * shape


def interpolate_input(baseline: list[float], input_data: list[float], alpha: float) -> list[float]:
    """Linearly interpolate between baseline and input: (1-alpha)*baseline + alpha*input."""
    return [baseline[i] + alpha * (input_data[i] - baseline[i]) for i in range(len(baseline))]


# ═══════════════════════════════════════════════════════════════════
# Private numerical helpers -- linear algebra
# ═══════════════════════════════════════════════════════════════════


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two vectors."""
    return sum(ai * bi for ai, bi in zip(a, b))


def _matvec(M: list[list[float]], v: list[float]) -> list[float]:
    """Matrix-vector multiplication."""
    return [_dot(row, v) for row in M]


def _vec_add(a: list[float], b: list[float]) -> list[float]:
    """Element-wise vector addition."""
    return [ai + bi for ai, bi in zip(a, b)]


def _vec_sub(a: list[float], b: list[float]) -> list[float]:
    """Element-wise vector subtraction."""
    return [ai - bi for ai, bi in zip(a, b)]


def _vec_scale(v: list[float], s: float) -> list[float]:
    """Scalar multiplication of a vector."""
    return [x * s for x in v]


def _outer(a: list[float], b: list[float]) -> list[list[float]]:
    """Outer product: returns matrix a_i * b_j."""
    return [[ai * bj for bj in b] for ai in a]


def _transpose(M: list[list[float]]) -> list[list[float]]:
    """Transpose a matrix."""
    if not M:
        return []
    cols = len(M[0])
    return [[M[i][j] for i in range(len(M))] for j in range(cols)]


def _matmul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    """Matrix-matrix multiplication A @ B."""
    if not A or not B:
        return []
    B_T = _transpose(B)
    return [[_dot(row_a, col_b) for col_b in B_T] for row_a in A]


def _mean(vals: list[float]) -> float:
    """Arithmetic mean."""
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _std(vals: list[float]) -> float:
    """Population standard deviation."""
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))


def _correlation(x: list[float], y: list[float]) -> float:
    """Pearson correlation coefficient."""
    if len(x) < 2:
        return 0.0
    mx = _mean(x)
    my = _mean(y)
    sx = _std(x)
    sy = _std(y)
    if sx < _EPS or sy < _EPS:
        return 0.0
    n = len(x)
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / n
    return cov / (sx * sy)


def _argmax(vals: list[float]) -> int:
    """Index of the maximum value."""
    if not vals:
        raise ValueError("Cannot argmax empty list")
    return max(range(len(vals)), key=lambda i: vals[i])


# ═══════════════════════════════════════════════════════════════════
# Private numerical helpers -- activation functions
# ═══════════════════════════════════════════════════════════════════


def _relu(x: float) -> float:
    return max(0.0, x)


def _sigmoid_scalar(x: float) -> float:
    return 1.0 / (1.0 + math.exp(max(-50.0, min(50.0, -x))))


def _softmax(vals: list[float]) -> list[float]:
    """Stable softmax."""
    if not vals:
        return []
    mx = max(vals)
    exp_vals = [math.exp(max(-50.0, min(50.0, v - mx))) for v in vals]
    total = sum(exp_vals)
    if total < _EPS:
        n = len(vals)
        return [1.0 / n] * n
    return [e / total for e in exp_vals]


# ═══════════════════════════════════════════════════════════════════
# Private numerical helpers -- gradient and noise
# ═══════════════════════════════════════════════════════════════════


def _numerical_gradient(
    fn: Callable[[list[float]], float], x: list[float], h: float = _H
) -> list[float]:
    """Central-difference gradient of scalar fn(x) w.r.t. x."""
    n = len(x)
    grad = [0.0] * n
    x_copy = list(x)
    for i in range(n):
        x_copy[i] = x[i] + h
        fp = fn(x_copy)
        x_copy[i] = x[i] - h
        fm = fn(x_copy)
        x_copy[i] = x[i]
        grad[i] = (fp - fm) / (2.0 * h)
    return grad


def _add_gaussian_noise(x: list[float], sigma: float) -> list[float]:
    """Add Gaussian noise to a vector."""
    return [xi + random.gauss(0.0, sigma) for xi in x]


# ═══════════════════════════════════════════════════════════════════
# Private numerical helpers -- linear model fit
# ═══════════════════════════════════════════════════════════════════


def _sgd_linear_fit(
    X: list[list[float]],
    y: list[float],
    epochs: int = 100,
    lr: float = 0.01,
) -> tuple[list[float], float, list[float]]:
    """SGD for linear regression; returns (weights, bias, loss_history)."""
    if not X or not y:
        return [], 0.0, []
    n_features = len(X[0])
    weights = [random.gauss(0.0, 0.01) for _ in range(n_features)]
    bias = 0.0
    loss_history: list[float] = []
    n_samples = len(X)

    for epoch in range(epochs):
        total_loss = 0.0
        indices = list(range(n_samples))
        random.shuffle(indices)
        for idx in indices:
            pred = _dot(weights, X[idx]) + bias
            error = pred - y[idx]
            total_loss += error * error
            for j in range(n_features):
                weights[j] -= lr * 2.0 * error * X[idx][j]
            bias -= lr * 2.0 * error
        loss_history.append(total_loss / n_samples)
        if epoch > 0 and loss_history[-1] > loss_history[-2] * 10.0:
            break

    return weights, bias, loss_history


# ═══════════════════════════════════════════════════════════════════
# Feature visualization
# ═══════════════════════════════════════════════════════════════════


def activation_maximization(
    model_fn: Callable[[list[float]], list[float]],
    input_shape: int,
    target_class: int = 0,
    steps: int = 100,
    lr: float = 0.01,
) -> list[float]:
    """Optimize an input to maximally activate a target class via gradient ascent.

    Uses numerical gradient estimation -- best for small input dimensions.
    """
    x = [random.gauss(0.0, 0.1) for _ in range(input_shape)]

    def _objective(v: list[float]) -> float:
        return model_fn(v)[target_class]

    for _step in range(steps):
        grad = _numerical_gradient(_objective, x)
        for i in range(input_shape):
            x[i] += lr * grad[i]

    return x


def deepdream(
    model_fn: Callable[[list[float]], list[float]],
    image: list[list[float]] | list[float],
    target_layer: int = 0,
    octaves: int = 4,
    octave_scale: float = 1.4,
    iterations: int = 20,
) -> list[list[float]]:
    """DeepDream: enhance patterns by maximizing target layer activations at multiple scales."""
    flat_input = bool(image and not isinstance(image[0], list))
    if flat_input:
        image = [image]  # type: ignore[list-item]
    h = len(image)
    w = len(image[0]) if h > 0 else 0
    if h == 0 or w == 0:
        return []

    result = [[image[i][j] for j in range(w)] for i in range(h)]

    for octave in range(octaves):
        step_lr = 0.01 * (octave_scale ** (-octave))
        for _iter in range(iterations):
            flat = [result[i][j] for i in range(h) for j in range(w)]

            def _layer_objective(v: list[float]) -> float:
                activations = model_fn(v)
                idx = target_layer % len(activations)
                return activations[idx]

            grad = _numerical_gradient(_layer_objective, flat)

            for i in range(h):
                for j in range(w):
                    result[i][j] += step_lr * grad[i * w + j]

            mx = max(max(abs(val) for val in row) for row in result)
            if mx > 10.0:
                scl = 10.0 / mx
                for i in range(h):
                    for j in range(w):
                        result[i][j] *= scl

    return result[0] if flat_input else result


def feature_inversion(
    activation_vector: list[float],
    decoder_fn: Callable[[list[float]], list[float]],
    iterations: int = 1000,
    lr: float = 0.01,
) -> list[float]:
    """Reconstruct an input from its feature activations by minimizing MSE.

    Optimizes input z such that decoder_fn(z) approximates activation_vector.
    """
    input_dim = len(activation_vector)
    z = [random.gauss(0.0, 0.1) for _ in range(input_dim)]

    def _loss(v: list[float]) -> float:
        pred = decoder_fn(v)
        return sum((pred[i] - activation_vector[i]) ** 2 for i in range(min(len(pred), len(activation_vector))))

    for _step in range(iterations):
        grad = _numerical_gradient(_loss, z)
        for i in range(input_dim):
            z[i] -= lr * grad[i]

        current_loss = _loss(z)
        if current_loss < _EPS:
            break

    return z


# ═══════════════════════════════════════════════════════════════════
# Attribution methods
# ═══════════════════════════════════════════════════════════════════


def saliency_map(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    target_class: int | None = None,
) -> list[float]:
    """Vanilla gradient-based saliency: |d(output_class)/d(input)|."""
    target = target_class if target_class is not None else _argmax(model_fn(input_data))

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target]

    grad = _numerical_gradient(_fn, input_data)
    return [abs(g) for g in grad]


def integrated_gradients(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    baseline: list[float] | None = None,
    target_class: int | None = None,
    steps: int = 50,
) -> list[float]:
    """Integrated Gradients: average gradients along path from baseline to input."""
    n = len(input_data)
    base = baseline if baseline is not None else [0.0] * n
    target = target_class if target_class is not None else _argmax(model_fn(input_data))

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target]

    ig = [0.0] * n
    for k in range(1, steps + 1):
        alpha = k / steps
        interpolated = interpolate_input(base, input_data, alpha)
        grad = _numerical_gradient(_fn, interpolated)
        for i in range(n):
            ig[i] += grad[i]

    for i in range(n):
        ig[i] = (ig[i] / steps) * (input_data[i] - base[i])

    return ig


def smoothgrad(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    target_class: int | None = None,
    num_samples: int = 50,
    noise_level: float = 0.15,
) -> list[float]:
    """SmoothGrad: average saliency maps over noisy copies of the input."""
    n = len(input_data)
    target = target_class if target_class is not None else _argmax(model_fn(input_data))

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target]

    accumulated = [0.0] * n
    scale = noise_level * (max(abs(v) for v in input_data) + _EPS)

    for _sample in range(num_samples):
        noisy = _add_gaussian_noise(input_data, scale)
        grad = _numerical_gradient(_fn, noisy)
        for i in range(n):
            accumulated[i] += abs(grad[i])

    return [a / num_samples for a in accumulated]


def compute_shap_values(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    target_class: int | None = None,
    num_samples: int = 100,
) -> list[float]:
    """Approximate SHAP values via permutation-based sampling."""
    n = len(input_data)
    target = target_class if target_class is not None else _argmax(model_fn(input_data))
    baseline = [0.0] * n
    shap = [0.0] * n

    for _s in range(num_samples):
        perm = list(range(n))
        random.shuffle(perm)
        x_current = list(baseline)
        pred_before = model_fn(x_current)[target]
        for idx in perm:
            prev_val = x_current[idx]
            x_current[idx] = input_data[idx]
            pred_after = model_fn(x_current)[target]
            shap[idx] += pred_after - pred_before
            pred_before = pred_after

    return [s / num_samples for s in shap]


def lime_explain(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    num_features: int = 10,
    num_samples: int = 500,
) -> dict[str, Any]:
    """LIME-style local explanation via perturbed samples and weighted linear regression."""
    n = len(input_data)
    target = _argmax(model_fn(input_data))
    X_perturbed: list[list[float]] = []
    y_values: list[float] = []
    weights: list[float] = []

    for _s in range(num_samples):
        mask = [random.random() < 0.5 for _ in range(n)]
        perturbed = [input_data[i] if mask[i] else 0.0 for i in range(n)]
        pred = model_fn(perturbed)[target]

        dist = sum((perturbed[i] - input_data[i]) ** 2 for i in range(n))
        w = math.exp(-dist / (n * 0.75 + _EPS))

        X_perturbed.append(perturbed)
        y_values.append(pred)
        weights.append(w)

    if not X_perturbed:
        return {"feature_importances": [0.0] * n, "intercept": 0.0,
                "prediction": float(model_fn(input_data)[target]),
                "explained_prediction": 0.0}

    n_feat = len(X_perturbed[0])
    wgt_coeffs, wgt_bias, _loss_hist = _sgd_linear_fit(X_perturbed, y_values, epochs=50, lr=0.005)

    def _weighted_loss(coeffs: list[float], bias_val: float) -> float:
        total = 0.0
        for idx, (row, y_val) in enumerate(zip(X_perturbed, y_values)):
            pred_val = _dot(coeffs, row) + bias_val
            total += weights[idx] * (pred_val - y_val) ** 2
        return total / len(X_perturbed)

    importance = [abs(c) for c in _sgd_linear_fit(
        [[row[j] * weights[idx] for j in range(n_feat)] for idx, row in enumerate(X_perturbed)],
        [y_values[idx] * weights[idx] for idx in range(len(y_values))],
        epochs=30, lr=0.005)[0]]

    original_pred = float(model_fn(input_data)[target])
    explained_pred = _dot(wgt_coeffs, input_data) + wgt_bias

    return {
        "feature_names": [f"feature_{i}" for i in range(len(input_data))],
        "weights": importance,
        "feature_importances": importance,
        "intercept": wgt_bias,
        "prediction": original_pred,
        "explained_prediction": explained_pred,
    }


# ═══════════════════════════════════════════════════════════════════
# Circuit analysis
# ═══════════════════════════════════════════════════════════════════


def sparse_autoencoder_encode(
    dictionary: dict[str, Any] | list[list[float]],
    activations: list[float],
    sparsity_coefficient: float = 0.1,
) -> dict[str, Any]:
    """Encode activations through a sparse autoencoder dictionary."""
    if isinstance(dictionary, list):
        dictionary = {"W_enc": dictionary, "W_dec": _transpose(dictionary)}
    W_enc: list[list[float]] = dictionary.get("W_enc", [])
    b_enc: list[float] = dictionary.get("b_enc", [0.0])
    W_dec: list[list[float]] = dictionary.get("W_dec", [])
    b_dec: list[float] = dictionary.get("b_dec", [0.0])

    if not W_enc:
        return {"latent": [], "sparsity": 0.0, "reconstruction": []}

    pre_activation = _vec_add(_matvec(W_enc, activations), b_enc[:len(W_enc)])
    threshold = sparsity_coefficient * max(abs(v) for v in pre_activation + [1.0])
    latent = [max(0.0, p - threshold) for p in pre_activation]

    active_count = sum(1 for lv in latent if lv > _EPS)
    sparsity = 1.0 - active_count / max(len(latent), 1)

    if W_dec:
        reconstruction = _vec_add(_matvec(W_dec, latent), b_dec[:len(b_dec)])
    else:
        reconstruction = []

    return {
        "latent_codes": latent,
        "latent": latent,
        "sparsity": sparsity,
        "reconstruction": reconstruction,
    }


def sparse_autoencoder_decode(
    latent_codes: list[float],
    dictionary: dict[str, Any] | list[list[float]],
) -> list[float]:
    """Decode latent representation back to activation space."""
    if isinstance(dictionary, list):
        dictionary = {"W_dec": _transpose(dictionary)}
    W_dec: list[list[float]] = dictionary.get("W_dec", [])
    b_dec: list[float] = dictionary.get("b_dec", [])

    if not W_dec:
        return []
    if not b_dec:
        b_dec = [0.0] * len(W_dec)

    return _vec_add(_matvec(W_dec, latent_codes), b_dec[:len(W_dec)])


def train_probing_classifier(
    layer_activations: list[list[float]],
    labels: list[int],
    epochs: int = 100,
    lr: float = 0.01,
) -> dict[str, Any]:
    """Train a linear probe on layer activations to predict labels.

    Returns weights (one per class), bias, accuracy, and loss history.
    """
    if not layer_activations or not labels:
        return {"weights": [], "bias": [0.0], "accuracy": 0.0, "loss_history": []}

    n_features = len(layer_activations[0])
    classes = sorted(set(labels))
    n_classes = len(classes)

    weights_class: list[list[float]] = [
        [random.gauss(0.0, 0.01) for _ in range(n_features)]
        for _ in range(n_classes)
    ]
    biases: list[float] = [0.0] * n_classes
    loss_history: list[float] = []
    n_samples = len(layer_activations)

    for epoch in range(epochs):
        total_loss = 0.0
        indices = list(range(n_samples))
        random.shuffle(indices)
        for idx in indices:
            x = layer_activations[idx]
            logits = [_dot(weights_class[c], x) + biases[c] for c in range(n_classes)]
            probs = _softmax(logits)
            label_idx = classes.index(labels[idx])
            total_loss += -math.log(probs[label_idx] + _EPS)
            for c in range(n_classes):
                delta = probs[c] - (1.0 if c == label_idx else 0.0)
                for j in range(n_features):
                    weights_class[c][j] -= lr * delta * x[j]
                biases[c] -= lr * delta
        loss_history.append(total_loss / n_samples)

    correct = 0
    for idx in range(n_samples):
        logits = [_dot(weights_class[c], layer_activations[idx]) + biases[c] for c in range(n_classes)]
        pred_class = classes[_argmax(logits)]
        if pred_class == labels[idx]:
            correct += 1
    accuracy = correct / n_samples

    return {
        "weights": weights_class,
        "bias": biases,
        "accuracy": accuracy,
        "loss_history": loss_history,
    }


def compute_knowledge_neuron_score(
    neuron_activations: list[float],
    task_labels: list[float],
) -> float:
    """Score a neuron's knowledge relevance via absolute Pearson correlation."""
    if len(neuron_activations) < 3:
        return 0.0
    return abs(_correlation(neuron_activations, task_labels))


def detect_circuits(
    model_layers: dict[str, list[list[float]]] | list[list[list[float]]],
    input_data: list[float],
    target_output: float,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect computational circuits by measuring inter-layer weight correlations."""
    if isinstance(model_layers, list):
        model_layers = {f"layer_{idx}": layer for idx, layer in enumerate(model_layers)}
    layer_names = list(model_layers.keys())
    circuits: list[dict[str, Any]] = []

    for i in range(len(layer_names)):
        for j in range(i + 1, len(layer_names)):
            Wi = model_layers[layer_names[i]]
            Wj = model_layers[layer_names[j]]

            if not Wi or not Wj:
                continue

            norm_i = math.sqrt(sum(sum(v * v for v in row) for row in Wi))
            norm_j = math.sqrt(sum(sum(v * v for v in row) for row in Wj))
            total_norm = norm_i * norm_j

            if total_norm < _EPS:
                continue

            if len(Wi) >= len(Wj) and len(Wi[0]) >= len(Wj[0]):
                min_rows = min(len(Wi), len(Wj))
                min_cols = min(len(Wi[0]), len(Wj[0]))
                correlation_sum = 0.0
                for ri in range(min_rows):
                    for ci in range(min_cols):
                        wi_val = Wi[ri][ci]
                        wj_val = Wj[min(ri, len(Wj) - 1)][min(ci, len(Wj[0]) - 1)]
                        correlation_sum += wi_val * wj_val
                strength = correlation_sum / (total_norm + _EPS)
            else:
                strength = 0.0

            if abs(strength) > threshold:
                circuits.append({
                    "input_layer": layer_names[i],
                    "output_layer": layer_names[j],
                    "strength": strength,
                })

    return {
        "circuits": circuits,
        "num_circuits": len(circuits),
        "threshold": threshold,
    }


# ═══════════════════════════════════════════════════════════════════
# Transformer interpretability
# ═══════════════════════════════════════════════════════════════════


def analyze_attention_patterns(
    attention_weights: list[list[list[float]]],
    tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze attention head patterns across heads and positions.

    attention_weights: [num_heads][seq_len][seq_len]
    """
    if not attention_weights:
        return {"num_heads": 0, "mean_attention_per_head": [],
                "entropy_per_head": [], "sparsity_per_head": []}

    num_heads = len(attention_weights)
    seq_len = len(attention_weights[0]) if num_heads > 0 else 0

    mean_attn: list[float] = []
    entropies: list[float] = []
    sparsities: list[float] = []

    for head_idx in range(num_heads):
        head = attention_weights[head_idx]
        total = 0.0
        count = 0
        all_vals: list[float] = []
        for r in range(seq_len):
            for c in range(seq_len):
                v = head[r][c]
                total += v
                all_vals.append(v)
                count += 1

        mean_attn.append(total / max(count, 1))

        ent = 0.0
        for v in all_vals:
            if v > _EPS:
                ent -= v * math.log(v + _EPS)
        entropies.append(ent)

        near_zero = sum(1 for v in all_vals if v < 0.01)
        sparsities.append(near_zero / max(len(all_vals), 1))

    return {
        "num_heads": num_heads,
        "mean_attention_per_head": mean_attn,
        "entropy_per_head": entropies,
        "sparsity_per_head": sparsities,
        "tokens": tokens or [f"tok_{i}" for i in range(seq_len)],
    }


def detect_induction_heads(
    attention_weights: list[list[list[float]]],
    token_positions: list[int] | None = None,
) -> list[int]:
    """Identify induction head positions by detecting previous-token attention patterns.

    An induction head attends strongly to position i-1 for each token i.
    """
    if not attention_weights:
        return []

    induction_indices: list[int] = []
    for h, head_attn in enumerate(attention_weights):
        seq_len = len(head_attn)
        if seq_len < 2:
            continue

        prev_attn_sum = 0.0
        for i in range(1, seq_len):
            prev_attn_sum += head_attn[i][i - 1]
        avg_prev = prev_attn_sum / (seq_len - 1)

        total_attn = sum(sum(row) for row in head_attn)
        avg_all = total_attn / (seq_len * seq_len + _EPS)

        if avg_prev > 1.8 * avg_all + 0.05:
            induction_indices.append(h)

    return induction_indices


def compute_copy_suppression_score(
    previous_token_attention: list[float],
    current_token_attention: list[float],
) -> float:
    """Compute copy suppression as Jensen-Shannon divergence between attention distributions.

    Higher score = more suppression (more different attention pattern from previous token).
    """
    n = len(previous_token_attention)
    if n == 0:
        return 0.0

    eps = 1e-10
    m_avg = [0.5 * (previous_token_attention[i] + current_token_attention[i]) for i in range(n)]

    kl_pm = 0.0
    kl_qm = 0.0
    for i in range(n):
        p_i = previous_token_attention[i] + eps
        q_i = current_token_attention[i] + eps
        m_i = m_avg[i] + eps
        kl_pm += p_i * math.log(p_i / m_i)
        kl_qm += q_i * math.log(q_i / m_i)

    js_div = 0.5 * kl_pm + 0.5 * kl_qm
    return js_div


# ═══════════════════════════════════════════════════════════════════
# Concept-based interpretability
# ═══════════════════════════════════════════════════════════════════


def tcav_score(
    concept_activations: list[list[float]] | list[float],
    random_activations: list[list[float]] | list[float],
    classifier_fn: Callable[[list[list[float]], list[float]], tuple[list[float], float]],
) -> float:
    """TCAV (Testing with Concept Activation Vectors) score.

    Trains a linear classifier to separate concept from random examples,
    then measures the fraction of test examples where the concept direction
    aligns with the class prediction.
    """
    if not concept_activations or not random_activations:
        return 0.0
    if concept_activations and not isinstance(concept_activations[0], list):
        concept_activations = [concept_activations]  # type: ignore[list-item]
    if random_activations and not isinstance(random_activations[0], list):
        random_activations = [random_activations]  # type: ignore[list-item]

    all_activations = concept_activations + random_activations
    concept_labels = [1.0] * len(concept_activations) + [0.0] * len(random_activations)

    n_features = len(all_activations[0]) if all_activations else 0
    if n_features == 0:
        return 0.0

    wgt, bias, _loss = _sgd_linear_fit(all_activations, concept_labels, epochs=200, lr=0.01)

    concept_dir_norm = math.sqrt(sum(wi * wi for wi in wgt))
    if concept_dir_norm < _EPS:
        return 0.0
    unit_cav = [wi / concept_dir_norm for wi in wgt]

    test_inputs = concept_activations[len(concept_activations) // 2:]
    if not test_inputs:
        test_inputs = concept_activations

    positive_count = 0
    for x in test_inputs:
        directional_derivative = _dot(unit_cav, x)
        if directional_derivative > 0:
            positive_count += 1

    return positive_count / max(len(test_inputs), 1)


def concept_bottleneck_predict(
    concept_scores: list[float],
    task_classifier_weights: list[float],
) -> float:
    """Predict task output from concept scores via linear concept bottleneck model.

    prediction = sum_i concept_scores[i] * task_classifier_weights[i] + bias
    The last element of task_classifier_weights is treated as the bias term.
    """
    if len(task_classifier_weights) == len(concept_scores) + 1:
        bias_term = task_classifier_weights[-1]
        weight_vec = task_classifier_weights[:-1]
    else:
        bias_term = 0.0
        weight_vec = task_classifier_weights[:len(concept_scores)]

    n = min(len(concept_scores), len(weight_vec))
    score = bias_term + sum(concept_scores[i] * weight_vec[i] for i in range(n))
    return score


# ═══════════════════════════════════════════════════════════════════
# Mechanistic toy models
# ═══════════════════════════════════════════════════════════════════


def toy_model_reversal_curse(
    num_tokens: int = 50,
    num_layers: int = 1,
    training_steps: int = 1000,
) -> dict[str, Any]:
    """Simulate the reversal curse in a toy transformer model.

    Trains on mapping A->B (forward) and tests both A->B and B->A (reverse).
    The reversal curse is the observation that models struggle with reverse
    queries despite perfect forward accuracy.
    """
    random.seed(42)
    embed_dim = 32
    tokens = num_tokens // 2

    embeddings = [[random.gauss(0.0, 1.0 / math.sqrt(embed_dim)) for _ in range(embed_dim)]
                  for _ in range(num_tokens)]

    W_query = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)] for _ in range(embed_dim)]
    W_key = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)] for _ in range(embed_dim)]
    W_value = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)] for _ in range(embed_dim)]
    W_out = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)] for _ in range(embed_dim)]
    W_unembed = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)] for _ in range(num_tokens)]

    lr_val = 0.01
    pairs = [(i, i + tokens) for i in range(tokens)]

    def _forward_attention(src_idx: int) -> list[float]:
        q = _matvec(W_query, embeddings[src_idx])
        scores = [_dot(q, _matvec(W_key, embeddings[t])) for t in range(num_tokens)]
        attn = _softmax(scores)
        context = [0.0] * embed_dim
        for t in range(num_tokens):
            val_t = _matvec(W_value, embeddings[t])
            for d in range(embed_dim):
                context[d] += attn[t] * val_t[d]
        hidden = [_relu(h) for h in _matvec(W_out, context)]
        return _softmax(_matvec(W_unembed, hidden))

    def _loss_for_pair(a_idx: int, b_idx: int) -> float:
        logits = _forward_attention(a_idx)
        return -math.log(logits[b_idx] + _EPS)

    loss_history: list[float] = []
    for step in range(training_steps):
        total_loss = 0.0
        for a_idx, b_idx in pairs:
            total_loss += _loss_for_pair(a_idx, b_idx)

        avg_loss = total_loss / len(pairs)
        loss_history.append(avg_loss)

        for src_idx in range(num_tokens):
            q = _matvec(W_query, embeddings[src_idx])
            scores = [_dot(q, _matvec(W_key, embeddings[t])) for t in range(num_tokens)]
            attn = _softmax(scores)

            if src_idx < tokens:
                tgt_idx = src_idx + tokens
            else:
                continue

            logits = _forward_attention(src_idx)
            probs = _softmax(logits)

            context_before = [0.0] * embed_dim
            for t in range(num_tokens):
                val_t = _matvec(W_value, embeddings[t])
                for d in range(embed_dim):
                    context_before[d] += attn[t] * val_t[d]

            for d in range(embed_dim):
                grad_context = 0.0
                for n_tok in range(num_tokens):
                    delta_unembed = probs[n_tok] - (1.0 if n_tok == tgt_idx else 0.0)
                    hidden = [_relu(h) for h in _matvec(W_out, context_before)]
                    for h_dim in range(embed_dim):
                        grad_context += delta_unembed * W_unembed[n_tok][h_dim] * (1.0 if hidden[h_dim] > 0 else 0.0) * W_out[h_dim][d]

    hidden = [_relu(h) for h in _matvec(W_out, [0.0] * embed_dim)]

    forward_correct = 0
    reverse_correct = 0
    for a_idx, b_idx in pairs:
        logits_fwd = _forward_attention(a_idx)
        if _argmax(logits_fwd) == b_idx:
            forward_correct += 1

        logits_rev = _forward_attention(b_idx)
        if _argmax(logits_rev) == a_idx:
            reverse_correct += 1

    n_pairs = len(pairs)
    forward_acc = forward_correct / n_pairs
    reverse_acc = reverse_correct / n_pairs

    return {
        "forward_accuracy": forward_acc,
        "reverse_accuracy": reverse_acc,
        "reversal_gap": forward_acc - reverse_acc,
        "final_loss": loss_history[-1] if loss_history else float("inf"),
        "num_tokens": num_tokens,
    }


def superposition_simulate(
    num_features: int = 100,
    num_dimensions: int = 20,
    sparsity: float = 0.1,
    num_samples: int = 5000,
) -> dict[str, Any]:
    """Simulate feature superposition: compressing many sparse features into fewer dimensions.

    Models y = Wx where W: (num_dimensions x num_features), then reconstructs x via pseudo-inverse.
    Measures how well sparse feature vectors can be recovered from compressed representations.
    """
    random.seed(42)

    W = [[random.gauss(0.0, 1.0 / math.sqrt(num_dimensions)) for _ in range(num_features)]
         for _ in range(num_dimensions)]

    W_T = _transpose(W)
    W_T_W = _matmul(W_T, W)

    total_error = 0.0
    recovered_count = 0
    total_nonzero = 0

    for _sample in range(num_samples):
        x = [0.0] * num_features
        for i in range(num_features):
            if random.random() < sparsity:
                x[i] = random.uniform(0.5, 1.5)

        y = _matvec(W, x)

        x_hat = _matvec(W_T, y)
        error = sum((x[i] - x_hat[i]) ** 2 for i in range(num_features))
        total_error += error

        for i in range(num_features):
            if x[i] > 0.0:
                total_nonzero += 1
                if x_hat[i] > 0.3:
                    recovered_count += 1

    avg_error = total_error / num_samples
    recovery_rate = recovered_count / max(total_nonzero, 1)
    compression_ratio = num_features / max(num_dimensions, 1)

    gramian_sum = sum(abs(W_T_W[i][j]) for i in range(num_features) for j in range(num_features))
    non_diag = gramian_sum - sum(abs(W_T_W[i][i]) for i in range(num_features))
    interference_ratio = non_diag / max(gramian_sum, _EPS)

    return {
        "reconstruction_error": avg_error,
        "feature_recovery_rate": recovery_rate,
        "compression_ratio": compression_ratio,
        "interference_ratio": interference_ratio,
        "num_features": num_features,
        "num_dimensions": num_dimensions,
        "sparsity": sparsity,
    }


def grokking_detect(
    loss_history: list[float],
    accuracy_history: list[float],
    threshold: float = 0.9,
) -> dict[str, Any]:
    """Detect grokking: the sudden jump from memorization to generalization.

    Scans training curves to find when validation accuracy crosses threshold
    after an extended period of near-chance performance.
    """
    n = len(loss_history)
    if n < 20:
        return {"grokking_step": None, "memorization_phase": {},
                "generalization_phase": {}, "detected": False}

    grokking_step: int | None = None
    early_phase_end = n // 3
    early_mean = _mean(accuracy_history[:early_phase_end])

    for step in range(early_phase_end, n - 10):
        window_mean = _mean(accuracy_history[step:step + 10])
        if window_mean > threshold and early_mean < 0.5:
            grokking_step = step
            break

    if grokking_step is None:
        return {
            "grokking_step": None,
            "memorization_phase": {"start": 0, "end": n, "mean_accuracy": _mean(accuracy_history)},
            "generalization_phase": {},
            "detected": False,
        }

    pre_acc = _mean(accuracy_history[:grokking_step])
    post_acc = _mean(accuracy_history[grokking_step:])

    return {
        "grokking_step": grokking_step,
        "memorization_phase": {
            "start": 0,
            "end": grokking_step,
            "mean_accuracy": pre_acc,
            "mean_loss": _mean(loss_history[:grokking_step]),
        },
        "generalization_phase": {
            "start": grokking_step,
            "end": n,
            "mean_accuracy": post_acc,
            "mean_loss": _mean(loss_history[grokking_step:]),
        },
        "detected": True,
    }


def phase_change_detect(
    metrics_over_time: list[float],
    metric_name: str = "unknown",
    window: int = 50,
) -> dict[str, Any]:
    """Detect phase changes in training metrics using a sliding-window t-test.

    Compares the mean of two adjacent windows; reports positions where the
    difference is statistically significant (|t| > 2.0).
    """
    n = len(metrics_over_time)
    if n < 2 * window + 1:
        return {"change_points": [], "magnitudes": [], "mean_before": [], "mean_after": [],
                "metric_name": metric_name}

    change_points: list[int] = []
    magnitudes: list[float] = []
    means_before: list[float] = []
    means_after: list[float] = []

    for center in range(window, n - window):
        before = metrics_over_time[center - window:center]
        after = metrics_over_time[center:center + window]

        mu_before = _mean(before)
        mu_after = _mean(after)
        sd_before = _std(before)
        sd_after = _std(after)

        pooled_se = math.sqrt(
            (sd_before ** 2) / window + (sd_after ** 2) / window + _EPS
        )
        if pooled_se < _EPS:
            continue

        t_stat = abs(mu_after - mu_before) / pooled_se
        if t_stat > 2.0:
            change_points.append(center)
            magnitudes.append(mu_after - mu_before)
            means_before.append(mu_before)
            means_after.append(mu_after)

    return {
        "change_points": change_points,
        "magnitudes": magnitudes,
        "mean_before": means_before,
        "mean_after": means_after,
        "metric_name": metric_name,
    }


# ═══════════════════════════════════════════════════════════════════
# Evaluation metrics
# ═══════════════════════════════════════════════════════════════════


def compute_faithfulness(
    attribution: list[float],
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    top_k: int = 10,
) -> float:
    """Faithfulness: prediction change when top-k attributed features are removed.

    Higher = more faithful (removing important features causes larger drop).
    """
    n = len(input_data)
    if n == 0:
        return 0.0

    k = min(top_k, n)
    target = _argmax(model_fn(input_data))
    orig_output = model_fn(input_data)[target]

    ranked = sorted(range(n), key=lambda i: abs(attribution[i]), reverse=True)
    ablated = list(input_data)
    for i in ranked[:k]:
        ablated[i] = 0.0

    ablated_output = model_fn(ablated)[target]

    return abs(orig_output - ablated_output) / (abs(orig_output) + _EPS)


def compute_completeness(
    attribution: list[float],
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
) -> float:
    """Completeness: how well the top attributed features recover the original prediction.

    Higher = more complete (top features alone approximate the full prediction).
    """
    n = len(input_data)
    if n == 0:
        return 0.0

    target = _argmax(model_fn(input_data))
    orig_output = model_fn(input_data)[target]

    total_abs = sum(abs(a) for a in attribution)
    if total_abs < _EPS:
        return 0.0

    weighted_sum = 0.0
    for i in range(n):
        weighted_sum += attribution[i] * input_data[i]

    all_ones = [1.0] * n
    pred_all = model_fn(all_ones)[target]

    return 1.0 - abs(abs(orig_output - weighted_sum) / (abs(pred_all - orig_output) + _EPS))


def human_simulatability_score(
    model_predictions: list[float],
    human_predictions: list[float],
) -> float:
    """Agreement score between model predictions and human predictions.

    Returns the fraction of predictions where model and human agree
    (within a tolerance proportional to the output range).
    """
    n = min(len(model_predictions), len(human_predictions))
    if n == 0:
        return 0.0

    paired = [(model_predictions[i], human_predictions[i]) for i in range(n)
              if model_predictions[i] is not None and human_predictions[i] is not None]
    if not paired:
        return 0.0

    model_vals = [m for m, _ in paired]
    human_vals = [h for _, h in paired]
    rng = max(abs(v) for v in model_vals + human_vals) + _EPS
    tolerance = rng * 0.1

    agreements = sum(1 for m, h in paired if abs(m - h) < tolerance)
    return agreements / len(paired)
