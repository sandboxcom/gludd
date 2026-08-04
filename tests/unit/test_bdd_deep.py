"""Deep tests for BDD: ROBDD with apply, restrict, compose, satcount, ite, tautology.

Pure-stdlib, no fixtures.
"""

from __future__ import annotations

from general_ludd.algorithms.bdd import BDD


class TestBDDTerminal:
    def test_terminal_false_is_int_0(self) -> None:
        b = BDD(3)
        assert b.terminal(False) == 0

    def test_terminal_true_is_int_1(self) -> None:
        b = BDD(3)
        assert b.terminal(True) == 1


class TestBDDVar:
    def test_var_0_is_ite_structure(self) -> None:
        b = BDD(4)
        v = b.var(0)
        assert v != 0
        assert v != 1

    def test_var_out_of_range_raises(self) -> None:
        b = BDD(3)
        try:
            b.var(5)
            raise AssertionError("should have raised")
        except IndexError:
            pass


class TestBDDNot:
    def test_not_true_is_false(self) -> None:
        b = BDD(3)
        assert b.not_(b.terminal(True)) == 0

    def test_not_false_is_true(self) -> None:
        b = BDD(3)
        assert b.not_(b.terminal(False)) == 1

    def test_double_not_is_identity(self) -> None:
        b = BDD(4)
        v = b.var(1)
        assert b.not_(b.not_(v)) is v


class TestBDDAndOrXor:
    def test_and_identities(self) -> None:
        b = BDD(2)
        v0 = b.var(0)
        assert b.and_(v0, b.terminal(True)) is v0
        assert b.and_(v0, b.terminal(False)) == 0
        assert b.and_(v0, v0) is v0

    def test_or_identities(self) -> None:
        b = BDD(2)
        v0 = b.var(0)
        assert b.or_(v0, b.terminal(False)) is v0
        assert b.or_(v0, b.terminal(True)) == 1

    def test_xor_identities(self) -> None:
        b = BDD(2)
        v0 = b.var(0)
        assert b.xor(v0, b.terminal(False)) is v0
        assert b.xor(v0, b.terminal(True)) == b.not_(v0)
        assert b.xor(v0, v0) == 0

    def test_de_morgan_and_or(self) -> None:
        b = BDD(4)
        u = b.var(0)
        v = b.var(1)
        nand = b.not_(b.and_(u, v))
        ornot = b.or_(b.not_(u), b.not_(v))
        assert b.equals(nand, ornot)


class TestBDDRestrict:
    def test_restrict_var0_to_0_kills_positive(self) -> None:
        b = BDD(3)
        f = b.var(0)
        r = b.restrict(f, 0, 0)
        assert r == 0

    def test_restrict_var0_to_1_kills_negative(self) -> None:
        b = BDD(3)
        f = b.var(0)
        r = b.restrict(f, 0, 1)
        assert r == 1

    def test_restrict_irrelevant_var_no_op(self) -> None:
        b = BDD(5)
        f = b.var(2)
        r = b.restrict(f, 4, 0)
        assert r is f

    def test_restrict_deep_function(self) -> None:
        b = BDD(3)
        f = b.and_(b.var(0), b.var(1))
        r = b.restrict(f, 1, 1)
        assert b.equals(r, b.var(0))


class TestBDDCompose:
    def test_compose_var0_with_true(self) -> None:
        b = BDD(3)
        f = b.var(0)
        r = b.compose(f, 0, b.terminal(True))
        assert r == 1

    def test_compose_var0_with_false(self) -> None:
        b = BDD(3)
        f = b.var(0)
        r = b.compose(f, 0, b.terminal(False))
        assert r == 0

    def test_compose_var_with_another_var(self) -> None:
        b = BDD(4)
        f = b.and_(b.var(0), b.var(1))
        r = b.compose(f, 1, b.var(2))
        s = b.and_(b.var(0), b.var(2))
        assert b.equals(r, s)


class TestBDDIte:
    def test_ite_true_then_t_else_anything(self) -> None:
        b = BDD(2)
        assert b.ite(b.terminal(True), b.var(0), b.var(1)) is b.var(0)

    def test_ite_false_then_e(self) -> None:
        b = BDD(2)
        assert b.ite(b.terminal(False), b.var(0), b.var(1)) is b.var(1)

    def test_ite_var_self_mux(self) -> None:
        b = BDD(3)
        v = b.var(0)
        m = b.ite(v, b.var(1), b.var(2))
        r0 = b.restrict(m, 0, 0)
        r1 = b.restrict(m, 0, 1)
        assert b.equals(r0, b.var(2))
        assert b.equals(r1, b.var(1))


class TestBDDSatcount:
    def test_false_satcount_zero(self) -> None:
        b = BDD(4)
        assert b.satcount(b.terminal(False)) == 0

    def test_true_satcount_pow2(self) -> None:
        b = BDD(4)
        assert b.satcount(b.terminal(True)) == 16

    def test_single_var_satcount(self) -> None:
        b = BDD(3)
        assert b.satcount(b.var(0)) == 4

    def test_xor_satcount(self) -> None:
        b = BDD(2)
        f = b.xor(b.var(0), b.var(1))
        assert b.satcount(f) == 2

    def test_and_satcount(self) -> None:
        b = BDD(3)
        f = b.and_(b.var(0), b.var(1))
        assert b.satcount(f) == 2


class TestBDDTautology:
    def test_true_is_tautology(self) -> None:
        b = BDD(2)
        assert b.is_tautology(b.terminal(True))

    def test_var_is_not_tautology(self) -> None:
        b = BDD(3)
        assert not b.is_tautology(b.var(0))

    def test_tautology_via_or_not(self) -> None:
        b = BDD(2)
        f = b.or_(b.var(0), b.not_(b.var(0)))
        assert b.is_tautology(f)


class TestBDDSatisfiability:
    def test_false_not_satisfiable(self) -> None:
        b = BDD(2)
        assert not b.is_satisfiable(b.terminal(False))

    def test_var_is_satisfiable(self) -> None:
        b = BDD(3)
        assert b.is_satisfiable(b.var(1))


class TestBDDAnySat:
    def test_any_sat_var0(self) -> None:
        b = BDD(3)
        a = b.any_sat(b.var(0))
        assert a is not None
        assert a[0] in (0, 1)

    def test_any_sat_false_is_none(self) -> None:
        b = BDD(2)
        assert b.any_sat(b.terminal(False)) is None


class TestBDDNodeCount:
    def test_terminal_node_count_zero(self) -> None:
        b = BDD(4)
        assert b.node_count(b.terminal(True)) == 0

    def test_var_node_count_one(self) -> None:
        b = BDD(4)
        assert b.node_count(b.var(0)) == 1

    def test_and_of_two_vars_shares(self) -> None:
        b = BDD(4)
        f = b.and_(b.var(0), b.var(1))
        nc = b.node_count(f)
        assert nc >= 1


class TestBDDCanonicity:
    def test_same_function_same_node(self) -> None:
        b = BDD(4)
        u = b.and_(b.var(0), b.var(1))
        v = b.and_(b.var(0), b.var(1))
        assert u is v

    def test_different_functions_different_nodes(self) -> None:
        b = BDD(4)
        u = b.var(0)
        v = b.var(1)
        assert u is not v

    def test_equals_by_identity(self) -> None:
        b = BDD(3)
        assert b.equals(b.var(0), b.var(0))
        assert not b.equals(b.var(0), b.var(1))
