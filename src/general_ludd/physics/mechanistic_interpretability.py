"""Mechanistic interpretability and model analysis.

Feature visualization, attribution methods, sparse autoencoders,
circuit detection, transformer interpretability, concept-based methods,
mechanistic toy models, and evaluation metrics.

All algorithms use pure Python math with no GPU framework dependency.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

_EPS: float = 1e-8
_H: float = 1e-4


# ---------------------------------------------------------------------------
# Data tables
# ---------------------------------------------------------------------------

ACTIVATION_FUNCTIONS: dict[str, Callable[[float], float]] = {
    "relu": lambda x: max(0.0, x),
    "gelu": lambda x: 0.5 * x * (1.0 + math.tanh(
        math.sqrt(2.0 / math.pi) * (x + 0.044715 * x ** 3))),
    "sigmoid": lambda x: 1.0 / (1.0 + math.exp(-x)),
    "tanh": lambda x: math.tanh(x),
    "swish": lambda x: x / (1.0 + math.exp(-x)),
    "leaky_relu": lambda x: x if x >= 0 else 0.01 * x,
    "elu": lambda x: x if x >= 0 else math.exp(x) - 1.0,
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
        "description": "Top-K sparse autoencoder",
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
        "pattern": "Attends to token AFTER previous-token head position",
        "mechanism": "Previous-token head + induction head = copying",
        "key_signature": "High attention at (i, i-1) for layer > 0",
        "known_from": "Elhage et al. 2021 — A Mathematical Framework",
        "detection_method": "Correlate L0 previous-token with L1 current attention",
    },
    "copy_suppression": {
        "pattern": "Token suppresses attention to itself when copying",
        "mechanism": "Self-attention score reduced relative to neighbors",
        "key_signature": "Diagonal < off-diagonal for copying heads",
        "known_from": "McDougall et al. 2023 — Copy Suppression",
    },
    "duplicate_token_head": {
        "pattern": "Head attends equally to all occurrences of the same token",
        "mechanism": "QK circuit matches identical token embeddings",
        "key_signature": "High attention at positions with matching token values",
        "known_from": "Wang et al. 2022 — Interpretability in the Wild",
    },
    "name_mover_head": {
        "pattern": "Head moves information from subject to attribute position",
        "mechanism": "Attends from last token of subject to output position",
        "key_signature": "High attention from query to subject-final token",
        "known_from": "Meng et al. 2022 — Locating and Editing Factual Associations",
    },
    "previous_token_head": {
        "pattern": "Head attends to the immediately preceding token",
        "mechanism": "Simple positional pattern: query i attends to key i-1",
        "key_signature": "High values on the first off-diagonal",
        "known_from": "Olsson et al. 2022 — In-Context Learning and Induction Heads",
    },
}

SUPERVISION_EVALUATION: dict[str, str] = {
    "faithfulness": "Does the explanation accurately reflect the model's reasoning?",
    "completeness": "Does the explanation capture all relevant features?",
    "minimality": "Is the explanation as small as possible while remaining complete?",
    "human_simulatability": "Can a human predict model behavior using only the explanation?",
    "monotonicity": "Do larger attribution values correspond to larger effects on output?",
    "implementation_invariance": "Do functionally identical models yield the same explanation?",
    "sensitivity": "Does the explanation change when irrelevant features are perturbed?",
    "continuity": "Do similar inputs produce similar explanations?",
    "sparsity": "Is the explanation concentrated on few features rather than diffuse?",
    "robustness": "Does the explanation stay stable under small input perturbations?",
}


# ---------------------------------------------------------------------------
# Private numerical helpers — linear algebra
# ---------------------------------------------------------------------------

def _dot(a: list[float], b: list[float]) -> float:
    return sum(ai * bi for ai, bi in zip(a, b, strict=False))


def _matvec(M: list[list[float]], v: list[float]) -> list[float]:
    return [_dot(row, v) for row in M]


def _vec_add(a: list[float], b: list[float]) -> list[float]:
    return [ai + bi for ai, bi in zip(a, b, strict=False)]


def _vec_sub(a: list[float], b: list[float]) -> list[float]:
    return [ai - bi for ai, bi in zip(a, b, strict=False)]


def _vec_scale(v: list[float], s: float) -> list[float]:
    return [x * s for x in v]


def _outer(a: list[float], b: list[float]) -> list[list[float]]:
    return [[ai * bj for bj in b] for ai in a]


def _transpose(M: list[list[float]]) -> list[list[float]]:
    if not M:
        return []
    cols = len(M[0])
    return [[M[i][j] for i in range(len(M))] for j in range(cols)]


def _matmul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    if not A or not B:
        return []
    B_T = _transpose(B)
    return [[_dot(row_a, col_b) for col_b in B_T] for row_a in A]


def _mean(vals: list[float]) -> float:
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))


def _correlation(x: list[float], y: list[float]) -> float:
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
    if not vals:
        raise ValueError("Cannot argmax empty list")
    return max(range(len(vals)), key=lambda i: vals[i])


# ---------------------------------------------------------------------------
# Private numerical helpers — activation functions
# ---------------------------------------------------------------------------

def _relu(x: float) -> float:
    return max(0.0, x)


def _sigmoid_scalar(x: float) -> float:
    return 1.0 / (1.0 + math.exp(max(-50.0, min(50.0, -x))))


def _softmax(vals: list[float]) -> list[float]:
    if not vals:
        return []
    mx = max(vals)
    exp_vals = [math.exp(max(-50.0, min(50.0, v - mx))) for v in vals]
    total = sum(exp_vals)
    if total < _EPS:
        n = len(vals)
        return [1.0 / n] * n
    return [e / total for e in exp_vals]


# ---------------------------------------------------------------------------
# Private numerical helpers — gradient and noise
# ---------------------------------------------------------------------------

def _numerical_gradient(
    fn: Callable[[list[float]], float], x: list[float], h: float = _H,
) -> list[float]:
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
    return [xi + random.gauss(0.0, sigma) for xi in x]


# ---------------------------------------------------------------------------
# Private — SGD linear fit
# ---------------------------------------------------------------------------

def _sgd_linear_fit(
    X: list[list[float]],
    y: list[float],
    epochs: int = 100,
    lr: float = 0.01,
) -> tuple[list[float], float, list[float]]:
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


# ============================================================================
# Public API — Utility functions
# ============================================================================


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

    Methods: zeros, uniform, gaussian, mean
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


def interpolate_input(
    baseline: list[float], input_data: list[float], alpha: float,
) -> list[float]:
    """Linearly interpolate: (1 - alpha) * baseline + alpha * input."""
    return [baseline[i] + alpha * (input_data[i] - baseline[i])
            for i in range(len(baseline))]


# ============================================================================
# Feature visualization
# ============================================================================


def activation_maximization(
    model_fn: Callable[[list[float]], list[float]],
    input_shape: int,
    target_class: int = 0,
    steps: int = 100,
    lr: float = 0.01,
) -> list[float]:
    """Optimize an input to maximally activate a target class via gradient ascent."""
    x = [random.gauss(0.0, 0.1) for _ in range(input_shape)]

    def _objective(v: list[float]) -> float:
        return model_fn(v)[target_class]

    for _ in range(steps):
        grad = _numerical_gradient(_objective, x)
        for i in range(input_shape):
            x[i] += lr * grad[i]
    return x


def deepdream(
    model_fn: Callable[[list[float]], list[float]],
    image: list[float],
    target_layer: int = 0,
    octaves: int = 4,
    octave_scale: float = 1.4,
    iterations: int = 20,
) -> list[float]:
    """DeepDream: enhance patterns by maximizing target layer activations at
    multiple scales.
    """
    result = list(image)

    for octave in range(octaves):
        step_lr = 0.01 * (octave_scale ** (-octave))
        for _ in range(iterations):

            def _layer_objective(v: list[float]) -> float:
                activations = model_fn(v)
                idx = target_layer % len(activations)
                return activations[idx]

            grad = _numerical_gradient(_layer_objective, result)
            for i in range(len(result)):
                result[i] += step_lr * grad[i]

            mx = max(abs(v) for v in result)
            if mx > 10.0:
                scl = 10.0 / mx
                result = [v * scl for v in result]
    return result


def feature_inversion(
    activation_vector: list[float],
    decoder_fn: Callable[[list[float]], list[float]],
    iterations: int = 1000,
    lr: float = 0.01,
) -> list[float]:
    """Reconstruct an input from its feature activations by minimizing MSE."""
    input_dim = len(activation_vector)
    z = [random.gauss(0.0, 0.1) for _ in range(input_dim)]

    def _loss(v: list[float]) -> float:
        pred = decoder_fn(v)
        return sum((pred[i] - activation_vector[i]) ** 2
                   for i in range(min(len(pred), len(activation_vector))))

    for _ in range(iterations):
        grad = _numerical_gradient(_loss, z)
        for i in range(input_dim):
            z[i] -= lr * grad[i]
        if _loss(z) < _EPS:
            break
    return z


# ============================================================================
# Attribution methods
# ============================================================================


def saliency_map(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    target_class: int = 0,
) -> list[float]:
    """Vanilla gradient-based saliency: |d(output_class)/d(input)|."""
    target = target_class

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target]

    grad = _numerical_gradient(_fn, input_data)
    return [abs(g) for g in grad]


def integrated_gradients(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    baseline: list[float] | None = None,
    target_class: int = 0,
    steps: int = 50,
) -> list[float]:
    """Integrated Gradients: average gradients along path from baseline to input."""
    n = len(input_data)
    base = baseline if baseline is not None else [0.0] * n

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target_class]

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
    target_class: int = 0,
    num_samples: int = 50,
    noise_level: float = 0.15,
) -> list[float]:
    """SmoothGrad: average saliency maps over noisy copies of the input."""
    n = len(input_data)

    def _fn(v: list[float]) -> float:
        return model_fn(v)[target_class]

    accumulated = [0.0] * n
    scale = noise_level * (max(abs(v) for v in input_data) + _EPS)

    for _ in range(num_samples):
        noisy = _add_gaussian_noise(input_data, scale)
        grad = _numerical_gradient(_fn, noisy)
        for i in range(n):
            accumulated[i] += abs(grad[i])
    return [a / num_samples for a in accumulated]


def compute_shap_values(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    target_class: int = 0,
    num_samples: int = 100,
) -> list[float]:
    """Approximate SHAP values via permutation-based sampling."""
    n = len(input_data)
    baseline = [0.0] * n
    shap = [0.0] * n

    for _ in range(num_samples):
        perm = list(range(n))
        random.shuffle(perm)
        x_current = list(baseline)
        pred_before = model_fn(x_current)[target_class]
        for idx in perm:
            x_current[idx] = input_data[idx]
            pred_after = model_fn(x_current)[target_class]
            shap[idx] += pred_after - pred_before
            pred_before = pred_after
    return [s / num_samples for s in shap]


def lime_explain(
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
    num_features: int = 10,
    num_samples: int = 500,
) -> dict[str, Any]:
    """LIME-style local explanation via perturbed samples and linear regression."""
    n = len(input_data)
    target = _argmax(model_fn(input_data))
    X_perturbed: list[list[float]] = []
    y_values: list[float] = []
    sample_weights: list[float] = []

    for _ in range(num_samples):
        mask = [random.random() < 0.5 for _ in range(n)]
        perturbed = [input_data[i] if mask[i] else 0.0 for i in range(n)]
        pred = model_fn(perturbed)[target]
        dist = sum((perturbed[i] - input_data[i]) ** 2 for i in range(n))
        w = math.exp(-dist / (n * 0.75 + _EPS))
        X_perturbed.append(perturbed)
        y_values.append(pred)
        sample_weights.append(w)

    if not X_perturbed:
        return {"features": [], "importance": [], "intercept": 0.0,
                "prediction": float(model_fn(input_data)[target])}

    wgt_coeffs, wgt_bias, _ = _sgd_linear_fit(
        X_perturbed, y_values, epochs=50, lr=0.005)
    importance = [abs(c) for c in wgt_coeffs]

    return {
        "features": list(range(len(importance))),
        "importance": importance,
        "intercept": wgt_bias,
        "prediction": float(model_fn(input_data)[target]),
    }


# ============================================================================
# Sparse autoencoders
# ============================================================================


def sparse_autoencoder_encode(
    dictionary: list[list[float]],
    activations: list[float],
) -> dict[str, Any]:
    """Encode activations through a sparse autoencoder dictionary.

    Uses W_enc (dictionary) to project activations into the sparse latent
    space, applies ReLU + L1 threshold for sparsity, then reconstructs via
    W_dec (transpose of dictionary).
    """
    if not dictionary or not activations:
        return {"latent_codes": [], "reconstruction": []}

    W_dec = _transpose(dictionary)
    W_enc = dictionary
    n_latent = len(W_enc)
    n_input = len(W_enc[0]) if n_latent > 0 else 0

    if n_input != len(activations):
        return {"latent_codes": [], "reconstruction": []}

    pre_activation = _matvec(W_enc, activations)
    threshold = 0.03
    latent = [_relu(p - threshold) for p in pre_activation]

    reconstruction = _matvec(W_dec, latent)
    return {"latent_codes": latent, "reconstruction": reconstruction}


def sparse_autoencoder_decode(
    latent_codes: list[float],
    dictionary: list[list[float]],
) -> list[float]:
    """Decode latent representation back to activation space via W_dec."""
    if not latent_codes or not dictionary:
        return []
    W_dec = _transpose(dictionary)
    if not W_dec:
        return []
    return _matvec(W_dec, latent_codes)


# ============================================================================
# Probing and knowledge neurons
# ============================================================================


def train_probing_classifier(
    layer_activations: list[list[float]],
    labels: list[float],
    epochs: int = 100,
    lr: float = 0.01,
) -> dict[str, Any]:
    """Train a linear probe on layer activations to predict labels."""
    if not layer_activations or not labels:
        return {"weights": [], "bias": 0.0, "accuracy": 0.0}

    weights, bias, _loss_history = _sgd_linear_fit(
        layer_activations, labels, epochs=epochs, lr=lr)

    correct = 0
    threshold = _mean(labels)
    for idx, row in enumerate(layer_activations):
        pred = _dot(weights, row) + bias
        if (pred >= threshold) == (labels[idx] >= threshold):
            correct += 1
    accuracy = correct / len(layer_activations)

    return {"weights": weights, "bias": bias, "accuracy": accuracy}


def compute_knowledge_neuron_score(
    neuron_activations: list[float],
    task_labels: list[float],
) -> float:
    """Score a neuron's knowledge relevance via absolute Pearson correlation."""
    if len(neuron_activations) < 3:
        return 0.0
    return abs(_correlation(neuron_activations, task_labels))


# ============================================================================
# Circuit and attention analysis
# ============================================================================


def detect_circuits(
    model_layers: list[list[list[float]]],
    input_data: list[float],
    target_output: float,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect computational circuits by measuring inter-layer weight correlations.

    model_layers: list of weight matrices [layer][output_dim][input_dim].
    Returns circuits connecting layers with correlated weight patterns.
    """
    n_layers = len(model_layers)
    circuits: list[dict[str, Any]] = []

    for i in range(n_layers):
        for j in range(i + 1, n_layers):
            Wi = model_layers[i]
            Wj = model_layers[j]
            if not Wi or not Wj:
                continue

            flat_i = [val for row in Wi for val in row]
            flat_j = [val for row in Wj for val in row]
            min_len = min(len(flat_i), len(flat_j))
            corr = abs(_correlation(flat_i[:min_len], flat_j[:min_len]))

            if corr > threshold:
                circuits.append({
                    "layer_from": i,
                    "layer_to": j,
                    "correlation": corr,
                })

    return {
        "circuits": circuits,
        "num_circuits": len(circuits),
        "layers_analyzed": n_layers,
        "threshold": threshold,
    }


def analyze_attention_patterns(
    attention_weights: list[list[list[float]]],
) -> dict[str, Any]:
    """Analyze attention head patterns across heads and positions.

    attention_weights: [num_heads][seq_len][seq_len]
    """
    if not attention_weights:
        return {"num_heads": 0, "mean_attention_per_head": [],
                "entropy_per_head": [], "sparsity_per_head": []}

    num_heads = len(attention_weights)
    len(attention_weights[0]) if num_heads > 0 else 0

    mean_attn: list[float] = []
    entropies: list[float] = []
    sparsities: list[float] = []

    for head in attention_weights:
        all_vals: list[float] = []
        for row in head:
            all_vals.extend(row)
        mean_attn.append(_mean(all_vals))
        ent = sum(-v * math.log(v + _EPS) if v > _EPS else 0.0 for v in all_vals)
        entropies.append(ent)
        near_zero = sum(1 for v in all_vals if v < 0.01)
        sparsities.append(near_zero / max(len(all_vals), 1))

    return {
        "num_heads": num_heads,
        "mean_attention_per_head": mean_attn,
        "entropy_per_head": entropies,
        "sparsity_per_head": sparsities,
    }


def detect_induction_heads(
    attention_weights: list[list[list[float]]],
    token_positions: list[int] | None = None,
) -> list[int]:
    """Identify induction head positions by detecting previous-token attention
    patterns.
    """
    if not attention_weights:
        return []

    induction_indices: list[int] = []
    for h, head_attn in enumerate(attention_weights):
        seq_len = len(head_attn)
        if seq_len < 2:
            continue
        prev_attn_sum = sum(head_attn[i][i - 1] for i in range(1, seq_len))
        avg_prev = prev_attn_sum / (seq_len - 1)
        total_attn = sum(sum(row) for row in head_attn)
        avg_all = total_attn / (seq_len * seq_len + _EPS)
        if avg_prev > 2.0 * avg_all + 0.05:
            induction_indices.append(h)
    return induction_indices


def compute_copy_suppression_score(
    previous_token_attention: list[float],
    current_token_attention: list[float],
) -> float:
    """Compute copy suppression as Jensen-Shannon divergence between attention
    distributions.
    """
    n = len(previous_token_attention)
    if n == 0:
        return 0.0
    eps = 1e-10
    m_avg = [0.5 * (previous_token_attention[i] + current_token_attention[i])
             for i in range(n)]
    kl_pm = sum(
        (previous_token_attention[i] + eps) *
        math.log((previous_token_attention[i] + eps) / (m_avg[i] + eps))
        for i in range(n))
    kl_qm = sum(
        (current_token_attention[i] + eps) *
        math.log((current_token_attention[i] + eps) / (m_avg[i] + eps))
        for i in range(n))
    return 0.5 * kl_pm + 0.5 * kl_qm


# ============================================================================
# Concept-based interpretability
# ============================================================================


def tcav_score(
    concept_activations: list[float],
    random_activations: list[float],
    classifier_fn: Callable[[list[float]], float],
) -> float:
    """TCAV (Testing with Concept Activation Vectors) score.

    Measures how often the concept direction aligns with the classifier
    prediction on test examples.
    """
    if not concept_activations or not random_activations:
        return 0.0

    n = len(concept_activations)
    if n != len(random_activations):
        return 0.0

    combined = concept_activations + random_activations
    labels = [1.0] * n + [0.0] * n

    X = [[v] for v in combined]
    weights, _bias, _ = _sgd_linear_fit(X, labels, epochs=200, lr=0.01)

    concept_dir = weights[0] if weights else 0.0
    if abs(concept_dir) < _EPS:
        return 0.0

    direction = 1.0 if concept_dir > 0 else -1.0
    test_start = n // 2
    test_concepts = concept_activations[test_start:]
    if not test_concepts:
        test_concepts = concept_activations

    positive_count = 0
    for v in test_concepts:
        if direction * classifier_fn([v]) > 0:
            positive_count += 1
    return positive_count / max(len(test_concepts), 1)


def concept_bottleneck_predict(
    concept_scores: list[float],
    task_classifier_weights: list[float],
) -> float:
    """Predict task output from concept scores via linear concept bottleneck."""
    n = min(len(concept_scores), len(task_classifier_weights))
    return sum(concept_scores[i] * task_classifier_weights[i] for i in range(n))


# ============================================================================
# Mechanistic toy models
# ============================================================================


def toy_model_reversal_curse(
    num_tokens: int = 50,
    num_layers: int = 2,
    training_steps: int = 10,
) -> dict[str, Any]:
    """Simulate the reversal curse in a toy model.

    Trains on mapping A->B (forward) and tests both A->B and B->A (reverse).
    """
    random.seed(42)
    embed_dim = 8
    half = num_tokens // 2

    embeddings = [[random.gauss(0.0, 0.1) for _ in range(embed_dim)]
                  for _ in range(num_tokens)]
    W_fwd = [[random.gauss(0.0, 0.02) for _ in range(embed_dim)]
             for _ in range(embed_dim)]
    lr_val = 0.01
    pairs = [(i, i + half) for i in range(half)]

    loss_history: list[float] = []
    for _step in range(training_steps):
        total_loss = 0.0
        for a_idx, b_idx in pairs:
            hidden = _matvec(W_fwd, embeddings[a_idx])
            hidden_relu = [_relu(h) for h in hidden]
            pred = _dot(hidden_relu, embeddings[b_idx])
            target = 1.0
            error = pred - target
            total_loss += error ** 2
            for r in range(embed_dim):
                for c in range(embed_dim):
                    if hidden[c] > 0:
                        W_fwd[r][c] -= lr_val * 2.0 * error * embeddings[a_idx][c] * embeddings[b_idx][r]
        loss_history.append(total_loss / len(pairs))

    forward_correct = 0
    reverse_correct = 0
    for a_idx, b_idx in pairs:
        hidden_fwd = [_relu(h) for h in _matvec(W_fwd, embeddings[a_idx])]
        _dot(hidden_fwd, embeddings[b_idx])
        fwd_best = a_idx
        fwd_best_score = -float("inf")
        for t in range(num_tokens):
            s = _dot([_relu(h) for h in _matvec(W_fwd, embeddings[a_idx])],
                     embeddings[t])
            if s > fwd_best_score:
                fwd_best_score = s
                fwd_best = t
        if fwd_best == b_idx:
            forward_correct += 1

        [_relu(h) for h in _matvec(W_fwd, embeddings[b_idx])]
        rev_best = b_idx
        rev_best_score = -float("inf")
        for t in range(num_tokens):
            s = _dot([_relu(h) for h in _matvec(W_fwd, embeddings[b_idx])],
                     embeddings[t])
            if s > rev_best_score:
                rev_best_score = s
                rev_best = t
        if rev_best == a_idx:
            reverse_correct += 1

    n_pairs = len(pairs)
    return {
        "forward_accuracy": forward_correct / n_pairs,
        "reverse_accuracy": reverse_correct / n_pairs,
        "reversal_gap": (forward_correct - reverse_correct) / n_pairs,
        "final_loss": loss_history[-1] if loss_history else float("inf"),
        "num_tokens": num_tokens,
    }


def superposition_simulate(
    num_features: int = 100,
    num_dimensions: int = 20,
    sparsity: float = 0.1,
    num_samples: int = 5000,
) -> dict[str, Any]:
    """Simulate feature superposition.

    Compresses sparse features into fewer dimensions then attempts recovery
    via pseudo-inverse.
    """
    random.seed(42)
    W = [[random.gauss(0.0, 1.0 / math.sqrt(num_dimensions))
          for _ in range(num_features)]
         for _ in range(num_dimensions)]
    W_T = _transpose(W)

    total_error = 0.0
    recovered_count = 0
    total_nonzero = 0

    for _ in range(num_samples):
        x = [0.0] * num_features
        for i in range(num_features):
            if random.random() < sparsity:
                x[i] = random.uniform(0.5, 1.5)
        y = _matvec(W, x)
        x_hat = _matvec(W_T, y)
        total_error += sum((x[i] - x_hat[i]) ** 2 for i in range(num_features))
        for i in range(num_features):
            if x[i] > 0.0:
                total_nonzero += 1
                if x_hat[i] > 0.3:
                    recovered_count += 1

    return {
        "reconstruction_error": total_error / num_samples,
        "feature_recovery_rate": recovered_count / max(total_nonzero, 1),
        "compression_ratio": num_features / max(num_dimensions, 1),
        "num_features": num_features,
        "num_dimensions": num_dimensions,
        "sparsity": sparsity,
    }


def grokking_detect(
    loss_history: list[float],
    accuracy_history: list[float],
    threshold: float = 0.9,
) -> dict[str, Any]:
    """Detect grokking: the sudden jump from memorization to generalization."""
    n = len(loss_history)
    if n < 20:
        return {"grokking_step": None, "detected": False,
                "memorization_phase": {}, "generalization_phase": {}}

    grokking_step: int | None = None
    early_mean = _mean(accuracy_history[:n // 3])
    for step in range(n // 3, n - 10):
        if _mean(accuracy_history[step:step + 10]) > threshold and early_mean < 0.5:
            grokking_step = step
            break

    if grokking_step is None:
        return {"grokking_step": None, "detected": False,
                "memorization_phase": {"mean_accuracy": _mean(accuracy_history)},
                "generalization_phase": {}}

    return {
        "grokking_step": grokking_step,
        "detected": True,
        "memorization_phase": {
            "mean_accuracy": _mean(accuracy_history[:grokking_step]),
            "mean_loss": _mean(loss_history[:grokking_step]),
        },
        "generalization_phase": {
            "mean_accuracy": _mean(accuracy_history[grokking_step:]),
            "mean_loss": _mean(loss_history[grokking_step:]),
        },
    }


def phase_change_detect(
    metrics_over_time: list[float],
    metric_name: str = "unknown",
    window: int = 50,
) -> dict[str, Any]:
    """Detect phase changes in training metrics using a sliding-window t-test."""
    n = len(metrics_over_time)
    if n < 2 * window + 1:
        return {"change_points": [], "metric_name": metric_name}

    change_points: list[int] = []
    for center in range(window, n - window):
        before = metrics_over_time[center - window:center]
        after = metrics_over_time[center:center + window]
        mu_before = _mean(before)
        mu_after = _mean(after)
        sd_before = _std(before)
        sd_after = _std(after)
        pooled_se = math.sqrt((sd_before ** 2) / window + (sd_after ** 2) / window + _EPS)
        if pooled_se < _EPS:
            continue
        t_stat = abs(mu_after - mu_before) / pooled_se
        if t_stat > 2.0:
            change_points.append(center)
    return {"change_points": change_points, "metric_name": metric_name}


# ============================================================================
# Evaluation metrics
# ============================================================================


def compute_faithfulness(
    attribution: list[float],
    model_fn: Callable[[list[float]], list[float]],
    input_data: list[float],
) -> float:
    """Faithfulness: prediction change when top-k attributed features are removed.

    Higher = more faithful (removing important features causes larger drop).
    """
    n = len(input_data)
    if n == 0:
        return 0.0
    k = max(1, n // 2)
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
    """Completeness: how well the top features alone approximate the full
    prediction.

    Higher = more complete.
    """
    n = len(input_data)
    if n == 0:
        return 0.0
    target = _argmax(model_fn(input_data))
    orig_output = model_fn(input_data)[target]
    total_abs = sum(abs(a) for a in attribution)
    if total_abs < _EPS:
        return 0.0
    weighted_sum = sum(attribution[i] * input_data[i] for i in range(n))
    all_ones = [1.0] * n
    pred_all = model_fn(all_ones)[target]
    return 1.0 - abs(abs(orig_output - weighted_sum)
                     / (abs(pred_all - orig_output) + _EPS))


def human_simulatability_score(
    model_predictions: list[float],
    human_predictions: list[float],
) -> float:
    """Agreement score between model predictions and human predictions.

    Returns the fraction of predictions where both agree within a tolerance.
    """
    n = min(len(model_predictions), len(human_predictions))
    if n == 0:
        return 0.0
    correct = 0
    for i in range(n):
        if abs(model_predictions[i] - human_predictions[i]) < 0.5:
            correct += 1
    return correct / n
