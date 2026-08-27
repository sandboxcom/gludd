"""Deep entropy and information theory tests.

Covers shannon_entropy, joint_entropy, conditional_entropy, mutual_information,
kl_divergence, cross_entropy, and helper functions with 20+ test cases.
"""

from __future__ import annotations

import math

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils.entropy import (
    build_joint_from_counts,
    conditional_entropy,
    cross_entropy,
    distribution_from_counts,
    entropy_from_counts,
    joint_entropy,
    kl_divergence,
    marginal_from_joint,
    mutual_information,
    shannon_entropy,
)


class TestShannonEntropy:
    def test_fair_coin_1_bit(self) -> None:
        result = shannon_entropy({"H": 0.5, "T": 0.5})
        assert math.isclose(result, 1.0, rel_tol=1e-10)

    def test_deterministic_zero_entropy(self) -> None:
        result = shannon_entropy({"a": 1.0})
        assert math.isclose(result, 0.0, abs_tol=1e-12)

    def test_uniform_k_outcomes_log_k(self) -> None:
        n = 8
        dist = {str(i): 1.0 / n for i in range(n)}
        result = shannon_entropy(dist)
        assert math.isclose(result, 3.0, rel_tol=1e-10)

    def test_non_uniform_distribution(self) -> None:
        result = shannon_entropy({"a": 0.7, "b": 0.2, "c": 0.1})
        h = -(0.7 * math.log2(0.7) + 0.2 * math.log2(0.2) + 0.1 * math.log2(0.1))
        assert math.isclose(result, h, rel_tol=1e-10)

    def test_base_e_nats(self) -> None:
        result = shannon_entropy({"H": 0.5, "T": 0.5}, base=math.e)
        assert math.isclose(result, math.log(2), rel_tol=1e-10)

    def test_base_10_dits(self) -> None:
        result = shannon_entropy({"H": 0.5, "T": 0.5}, base=10.0)
        assert math.isclose(result, math.log10(2), rel_tol=1e-10)

    def test_sum_not_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            shannon_entropy({"a": 0.5, "b": 0.4})

    def test_negative_probability_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            shannon_entropy({"a": 0.6, "b": -0.1, "c": 0.5})

    def test_zero_probability_terms_ignored_silently(self) -> None:
        result = shannon_entropy({"a": 1.0, "b": 0.0})
        assert math.isclose(result, 0.0, abs_tol=1e-12)


class TestJointEntropy:
    def test_independent_fair_coins_2_bits(self) -> None:
        joint = {
            ("H", "H"): 0.25,
            ("H", "T"): 0.25,
            ("T", "H"): 0.25,
            ("T", "T"): 0.25,
        }
        result = joint_entropy(joint)
        assert math.isclose(result, 2.0, rel_tol=1e-10)

    def test_joint_equals_shannon_when_y_deterministic(self) -> None:
        joint = {("a", "z"): 0.5, ("b", "z"): 0.5}
        h = shannon_entropy({"a": 0.5, "b": 0.5})
        j = joint_entropy(joint)
        assert math.isclose(h, j, rel_tol=1e-10)

    def test_perfectly_correlated_dice(self) -> None:
        n = 6
        joint = {(i, i): 1.0 / n for i in range(1, n + 1)}
        result = joint_entropy(joint)
        assert math.isclose(result, math.log2(n), rel_tol=1e-10)


class TestConditionalEntropy:
    def test_deterministic_conditional_zero(self) -> None:
        joint = {("a", "x"): 0.5, ("b", "y"): 0.5}
        result = conditional_entropy(joint)
        assert math.isclose(result, 0.0, abs_tol=1e-12)

    def test_independent_equals_marginal(self) -> None:
        joint = {
            ("H", "H"): 0.25,
            ("H", "T"): 0.25,
            ("T", "H"): 0.25,
            ("T", "T"): 0.25,
        }
        h_cond = conditional_entropy(joint)
        h_x = shannon_entropy({"H": 0.5, "T": 0.5})
        assert math.isclose(h_cond, h_x, rel_tol=1e-10)

    def test_chain_rule_identity(self) -> None:
        joint = {
            ("a", "x"): 0.2,
            ("a", "y"): 0.3,
            ("b", "x"): 0.1,
            ("b", "y"): 0.4,
        }
        h_joint = joint_entropy(joint)
        h_y = shannon_entropy(marginal_from_joint(joint, axis=1))
        h_cond = conditional_entropy(joint)
        assert math.isclose(h_joint, h_y + h_cond, rel_tol=1e-10)


class TestMutualInformation:
    def test_independent_zero_mi(self) -> None:
        joint = {
            ("H", "H"): 0.25,
            ("H", "T"): 0.25,
            ("T", "H"): 0.25,
            ("T", "T"): 0.25,
        }
        result = mutual_information(joint)
        assert math.isclose(result, 0.0, abs_tol=1e-12)

    def test_self_mi_equals_entropy(self) -> None:
        px = {i: 0.25 for i in range(4)}
        joint = {(i, i): px[i] for i in px}
        mi = mutual_information(joint)
        h = shannon_entropy(px)
        assert math.isclose(mi, h, rel_tol=1e-10)

    def test_mi_chain_rule_identity(self) -> None:
        joint = {
            ("a", "x"): 0.2,
            ("a", "y"): 0.3,
            ("b", "x"): 0.1,
            ("b", "y"): 0.4,
        }
        mi = mutual_information(joint)
        h_x = shannon_entropy(marginal_from_joint(joint, axis=0))
        h_cond = conditional_entropy(joint)
        assert math.isclose(mi, h_x - h_cond, rel_tol=1e-10)

    def test_mi_identity_with_joint_and_marginals(self) -> None:
        joint = {
            ("a", "x"): 0.2,
            ("a", "y"): 0.3,
            ("b", "x"): 0.1,
            ("b", "y"): 0.4,
        }
        mi = mutual_information(joint)
        px = marginal_from_joint(joint, axis=0)
        py = marginal_from_joint(joint, axis=1)
        h_x = shannon_entropy(px)
        h_y = shannon_entropy(py)
        h_joint = joint_entropy(joint)
        assert math.isclose(mi, h_x + h_y - h_joint, rel_tol=1e-10)


class TestKLDivergence:
    def test_same_distribution_zero_kl(self) -> None:
        p = {"a": 0.5, "b": 0.3, "c": 0.2}
        result = kl_divergence(p, p)
        assert math.isclose(result, 0.0, abs_tol=1e-12)

    def test_different_distributions_positive(self) -> None:
        p = {"a": 0.8, "b": 0.2}
        q = {"a": 0.5, "b": 0.5}
        result = kl_divergence(p, q)
        assert result > 0.0

    def test_uniform_reference(self) -> None:
        n = 4
        p = {str(i): 1.0 / n for i in range(n)}
        q = {"0": 0.7, "1": 0.1, "2": 0.1, "3": 0.1}
        dkl = kl_divergence(q, p)
        expected = shannon_entropy(p, base=2.0) - shannon_entropy(q, base=2.0)
        assert math.isclose(dkl, expected, rel_tol=1e-10)

    def test_epsilon_handles_zero_in_q(self) -> None:
        p = {"a": 0.5, "b": 0.5}
        q = {"a": 1.0}
        result = kl_divergence(p, q)
        assert result > 10.0

    def test_asymmetric_kl(self) -> None:
        p = {"a": 0.8, "b": 0.2}
        q = {"a": 0.3, "b": 0.7}
        dkl_pq = kl_divergence(p, q)
        dkl_qp = kl_divergence(q, p)
        assert not math.isclose(dkl_pq, dkl_qp, rel_tol=1e-6)


class TestCrossEntropy:
    def test_identity_equals_entropy(self) -> None:
        p = {"a": 0.7, "b": 0.2, "c": 0.1}
        result = cross_entropy(p, p)
        assert math.isclose(result, shannon_entropy(p), rel_tol=1e-10)

    def test_cross_entropy_decomposition(self) -> None:
        p = {"a": 0.8, "b": 0.2}
        q = {"a": 0.3, "b": 0.7}
        ce = cross_entropy(p, q)
        hp = shannon_entropy(p)
        dkl = kl_divergence(p, q)
        assert math.isclose(ce, hp + dkl, rel_tol=1e-10)

    def test_cross_entropy_ge_entropy(self) -> None:
        p = {"a": 0.6, "b": 0.4}
        q = {"a": 0.3, "b": 0.7}
        ce = cross_entropy(p, q)
        hp = shannon_entropy(p)
        assert ce >= hp - 1e-12

    def test_perfect_mismatch_cross_entropy(self) -> None:
        p = {"a": 1.0}
        q = {"b": 1.0}
        result = cross_entropy(p, q)
        assert result > 20.0


class TestMarginalFromJoint:
    def test_axis_0_x_marginal(self) -> None:
        joint = {
            ("a", "x"): 0.3,
            ("a", "y"): 0.2,
            ("b", "x"): 0.4,
            ("b", "y"): 0.1,
        }
        px = marginal_from_joint(joint, axis=0)
        assert math.isclose(px["a"], 0.5, rel_tol=1e-10)
        assert math.isclose(px["b"], 0.5, rel_tol=1e-10)

    def test_axis_1_y_marginal(self) -> None:
        joint = {
            ("a", "x"): 0.3,
            ("a", "y"): 0.2,
            ("b", "x"): 0.4,
            ("b", "y"): 0.1,
        }
        py = marginal_from_joint(joint, axis=1)
        assert math.isclose(py["x"], 0.7, rel_tol=1e-10)
        assert math.isclose(py["y"], 0.3, rel_tol=1e-10)


class TestHelpers:
    def test_build_joint_from_counts(self) -> None:
        counts = {("a", "x"): 3, ("a", "y"): 2, ("b", "x"): 4, ("b", "y"): 1}
        joint = build_joint_from_counts(counts)
        assert math.isclose(joint[("a", "x")], 0.3, rel_tol=1e-10)
        assert math.isclose(sum(joint.values()), 1.0, rel_tol=1e-10)

    def test_build_joint_zero_total_raises(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            build_joint_from_counts({})

    def test_distribution_from_counts(self) -> None:
        dist = distribution_from_counts({"a": 7, "b": 2, "c": 1})
        assert math.isclose(dist["a"], 0.7, rel_tol=1e-10)
        assert math.isclose(sum(dist.values()), 1.0, rel_tol=1e-10)

    def test_entropy_from_counts(self) -> None:
        result = entropy_from_counts({"H": 1, "T": 1})
        assert math.isclose(result, 1.0, rel_tol=1e-10)


class TestEntropyIdentities:
    def test_data_processing_inequality(self) -> None:
        joint = {
            ("a", "x"): 0.3,
            ("a", "y"): 0.1,
            ("b", "x"): 0.2,
            ("b", "y"): 0.4,
        }
        mi_xy = mutual_information(joint)
        assert mi_xy >= 0.0 - 1e-12

    def test_non_negativity_of_mi(self) -> None:
        joint = {
            ("a", "x"): 0.25,
            ("a", "y"): 0.25,
            ("b", "x"): 0.25,
            ("b", "y"): 0.25,
        }
        assert mutual_information(joint) >= -1e-12

    def test_kl_divergence_non_negative(self) -> None:
        p = {"a": 0.5, "b": 0.5}
        q = {"a": 0.9, "b": 0.1}
        assert kl_divergence(p, q) >= -1e-12
        assert kl_divergence(q, p) >= -1e-12

    def test_entropy_boundary_uniform_maximal(self) -> None:
        n = 6
        uniform = {str(i): 1.0 / n for i in range(n)}
        biased = {"0": 0.5, "1": 0.3, "2": 0.2}
        biased.update({str(i): 0.0 for i in range(3, n)})
        assert shannon_entropy(uniform) > shannon_entropy(biased) - 1e-12

    def test_joint_entropy_subadditivity(self) -> None:
        joint = {
            ("a", "x"): 0.3,
            ("a", "y"): 0.1,
            ("b", "x"): 0.2,
            ("b", "y"): 0.4,
        }
        h_joint = joint_entropy(joint)
        h_x = shannon_entropy(marginal_from_joint(joint, axis=0))
        h_y = shannon_entropy(marginal_from_joint(joint, axis=1))
        assert h_joint <= h_x + h_y + 1e-12
