"""Adversarial unit tests for the model failover chain.

Coverage hardening for ``general_ludd.models.failover.ModelFailoverChain``.
These tests pin the CURRENT real behavior of the class as implemented; they
are intentionally NOT a redesign. Where the current behavior is arguably a
defect, the test asserts what the code actually does and a NOTE documents the
sharp edge.

Important: the class as written is a *passive data structure*. It does not
itself walk providers, perform health checks, or execute requests. It:
  * builds an ordered chain (``get_chain``),
  * records caller-driven failover transitions (``record_failover`` /
    ``get_failover_events``),
  * exposes a retry predicate (``should_retry``).

So "exhausted chain", "all providers unhealthy", and "provider raising
mid-chain" are modeled here at the level the class supports: by driving the
recording API the way a real failover loop would, and by feeding ``should_retry``
adversarial errors.
"""

from __future__ import annotations

import itertools

import pytest

from general_ludd.models.failover import ModelFailoverChain


# --------------------------------------------------------------------------- #
# Chain construction / ordering invariants
# --------------------------------------------------------------------------- #
class TestChainConstruction:
    def test_single_provider_chain_is_just_primary(self):
        chain = ModelFailoverChain("primary")
        assert chain.get_chain() == ["primary"]

    def test_none_fallbacks_normalized_to_empty(self):
        chain = ModelFailoverChain("primary", fallback_profiles=None)
        assert chain.get_chain() == ["primary"]
        assert chain._fallbacks == []

    def test_explicit_empty_fallbacks(self):
        chain = ModelFailoverChain("primary", fallback_profiles=[])
        assert chain.get_chain() == ["primary"]

    def test_chain_preserves_primary_first_then_fallback_order(self):
        chain = ModelFailoverChain("p", ["f1", "f2", "f3"])
        assert chain.get_chain() == ["p", "f1", "f2", "f3"]

    def test_get_chain_returns_fresh_list_each_call(self):
        # get_chain builds a new list via splat; mutating it must not corrupt state.
        chain = ModelFailoverChain("p", ["f1"])
        first = chain.get_chain()
        first.append("INJECTED")
        assert chain.get_chain() == ["p", "f1"], "returned chain must not alias internal state"

    def test_duplicate_profiles_are_not_deduplicated(self):
        # NOTE: current behavior keeps duplicates verbatim (no dedup).
        chain = ModelFailoverChain("p", ["p", "f1", "f1"])
        assert chain.get_chain() == ["p", "p", "f1", "f1"]

    def test_primary_can_be_empty_string(self):
        # Current behavior does not validate the primary profile name.
        chain = ModelFailoverChain("")
        assert chain.get_chain() == [""]

    def test_fallback_list_is_not_copied_defensively(self):
        # NOTE: __init__ stores the caller's list by reference when it is truthy.
        # Mutating the original list AFTER construction leaks into the chain.
        # This documents a real sharp edge in the current implementation.
        fallbacks = ["f1"]
        chain = ModelFailoverChain("p", fallbacks)
        fallbacks.append("f2")
        assert chain.get_chain() == ["p", "f1", "f2"], (
            "current impl aliases the caller-supplied fallback list"
        )


# --------------------------------------------------------------------------- #
# Failover event recording / state invariants
# --------------------------------------------------------------------------- #
class TestFailoverEventRecording:
    def test_no_events_on_fresh_chain(self):
        chain = ModelFailoverChain("p", ["f1"])
        assert chain.get_failover_events() == []

    def test_record_single_failover_shape(self):
        chain = ModelFailoverChain("p", ["f1"])
        chain.record_failover("p", "f1", "429 too many requests")
        events = chain.get_failover_events()
        assert events == [{"from": "p", "to": "f1", "error": "429 too many requests"}]

    def test_events_recorded_in_order_walking_the_chain(self):
        # Simulate a real loop exhausting the chain p -> f1 -> f2.
        chain = ModelFailoverChain("p", ["f1", "f2"])
        chain.record_failover("p", "f1", "timeout")
        chain.record_failover("f1", "f2", "503 unavailable")
        events = chain.get_failover_events()
        assert [e["from"] for e in events] == ["p", "f1"]
        assert [e["to"] for e in events] == ["f1", "f2"]

    def test_get_failover_events_returns_copy_not_internal_list(self):
        chain = ModelFailoverChain("p", ["f1"])
        chain.record_failover("p", "f1", "err")
        snapshot = chain.get_failover_events()
        snapshot.append({"from": "x", "to": "y", "error": "z"})
        assert len(chain.get_failover_events()) == 1, (
            "returned events list must be a defensive copy"
        )

    def test_event_dicts_are_shared_references_not_deep_copied(self):
        # NOTE: get_failover_events does a shallow copy (list(...)). The dicts
        # inside are the SAME objects. Mutating a returned dict mutates internal
        # state. This pins the current (shallow) behavior, which is a sharp edge.
        chain = ModelFailoverChain("p", ["f1"])
        chain.record_failover("p", "f1", "err")
        returned = chain.get_failover_events()
        returned[0]["error"] = "TAMPERED"
        assert chain.get_failover_events()[0]["error"] == "TAMPERED", (
            "shallow copy shares the inner event dicts"
        )

    def test_all_providers_unhealthy_records_full_exhaustion_path(self):
        # All providers fail: every hop is recorded, including the terminal hop
        # off the last fallback (the loop has nowhere to go).
        chain = ModelFailoverChain("p", ["f1", "f2"])
        full = chain.get_chain()
        for src, dst in itertools.pairwise(full):
            chain.record_failover(src, dst, "unavailable")
        # n providers => n-1 transitions recorded by this walk.
        assert len(chain.get_failover_events()) == len(full) - 1

    def test_record_failover_does_not_validate_profiles_against_chain(self):
        # Current behavior: record_failover blindly appends; it does not check
        # that from/to are actually in the configured chain.
        chain = ModelFailoverChain("p", ["f1"])
        chain.record_failover("ghost", "phantom", "err")
        assert chain.get_failover_events()[0] == {
            "from": "ghost",
            "to": "phantom",
            "error": "err",
        }

    def test_duplicate_failover_events_are_appended_not_collapsed(self):
        chain = ModelFailoverChain("p", ["f1"])
        chain.record_failover("p", "f1", "err")
        chain.record_failover("p", "f1", "err")
        assert len(chain.get_failover_events()) == 2


# --------------------------------------------------------------------------- #
# should_retry predicate — adversarial error inputs
# --------------------------------------------------------------------------- #
class _ErrWithStatusCode(Exception):
    def __init__(self, status_code: int, msg: str = "") -> None:
        super().__init__(msg)
        self.status_code = status_code


class _ErrWithStatus(Exception):
    def __init__(self, status: int, msg: str = "") -> None:
        super().__init__(msg)
        self.status = status


class TestShouldRetryStatusCodes:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_retryable_status_codes(self, code):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(_ErrWithStatusCode(code)) is True

    @pytest.mark.parametrize("code", [200, 400, 401, 403, 404, 418, 501, 505])
    def test_non_retryable_status_codes(self, code):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(_ErrWithStatusCode(code)) is False

    def test_status_attribute_fallback_used_when_no_status_code(self):
        # getattr(error, "status_code", getattr(error, "status", 0))
        chain = ModelFailoverChain("p")
        assert chain.should_retry(_ErrWithStatus(503)) is True
        assert chain.should_retry(_ErrWithStatus(404)) is False

    def test_status_code_takes_precedence_over_status(self):
        err = _ErrWithStatusCode(404)
        err.status = 503  # type: ignore[attr-defined]
        chain = ModelFailoverChain("p")
        # status_code (404, non-retryable) wins over status (503, retryable).
        assert chain.should_retry(err) is False

    def test_non_int_status_code_falls_through_to_keyword_match(self):
        # NOTE: `isinstance(status, int)` guards the membership test. A non-int
        # status_code (e.g. a string "503") does NOT trigger the status branch;
        # it falls through to the keyword scan over str(error).
        class _StrStatus(Exception):
            status_code = "503"

        chain = ModelFailoverChain("p")
        # str(error) here carries no keyword => not retryable.
        assert chain.should_retry(_StrStatus("boom")) is False
        # But if the message carries a keyword, it retries via the keyword path.
        assert chain.should_retry(_StrStatus("service unavailable")) is True

    def test_bool_status_code_is_treated_as_int(self):
        # NOTE: bool is a subclass of int in Python. status_code=True == 1,
        # which is not in the retryable set, so it is non-retryable. This pins
        # the (quirky but real) behavior rather than asserting a "should".
        class _BoolStatus(Exception):
            status_code = True

        chain = ModelFailoverChain("p")
        assert chain.should_retry(_BoolStatus()) is False


class TestShouldRetryKeywordMatching:
    @pytest.mark.parametrize(
        "msg",
        [
            "Request timeout after 30s",
            "TIMEOUT",
            "Rate limit exceeded",
            "RATE LIMIT hit",
            "Service Unavailable",
            "at capacity, try later",
        ],
    )
    def test_retryable_keywords_case_insensitive(self, msg):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "permission denied",
            "invalid api key",
            "malformed request",
            "not found",
            "",
        ],
    )
    def test_non_retryable_messages(self, msg):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception(msg)) is False

    def test_plain_exception_with_no_status_and_no_keyword(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("kaboom")) is False

    def test_keyword_substring_false_positive_is_current_behavior(self):
        # NOTE: keyword match is a naive substring scan. A benign message that
        # happens to contain "capacity" (e.g. "incapacity in logic") will be
        # treated as retryable. Pin this sharp edge.
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("incapacity in logic")) is True

    def test_error_with_zero_default_status_uses_keyword_path(self):
        # Bare exception => getattr chain yields 0 => 0 not in retry set =>
        # keyword path. "ratelimit" (no space) is NOT one of the keywords
        # ("rate limit" has a space), so it must be non-retryable.
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("ratelimit")) is False
        assert chain.should_retry(Exception("rate limit")) is True


# --------------------------------------------------------------------------- #
# A "provider raising mid-chain" modeled against the recording contract
# --------------------------------------------------------------------------- #
class TestMidChainRaise:
    def test_retry_decision_then_record_models_one_hop(self):
        # Simulate: primary raises a retryable 503; the loop decides to retry,
        # then records the hop to the next provider.
        chain = ModelFailoverChain("p", ["f1", "f2"])
        err = _ErrWithStatusCode(503, "upstream 503")
        assert chain.should_retry(err) is True
        chain.record_failover("p", "f1", str(err))
        assert chain.get_failover_events()[-1]["to"] == "f1"

    def test_non_retryable_raise_mid_chain_means_no_further_hop(self):
        # A non-retryable error (e.g. 400) means a real loop would NOT failover.
        # The class can't enforce that, but should_retry communicates it.
        chain = ModelFailoverChain("p", ["f1"])
        err = _ErrWithStatusCode(400, "bad request")
        assert chain.should_retry(err) is False
        # No event recorded because the caller would not have failed over.
        assert chain.get_failover_events() == []

    def test_single_provider_chain_has_no_fallback_target(self):
        # With only a primary, the chain offers no failover target; a real loop
        # has nowhere to hop even if should_retry is True.
        chain = ModelFailoverChain("solo")
        assert chain.get_chain() == ["solo"]
        assert chain.should_retry(_ErrWithStatusCode(503)) is True
        # No failover recorded — nowhere to go.
        assert chain.get_failover_events() == []
