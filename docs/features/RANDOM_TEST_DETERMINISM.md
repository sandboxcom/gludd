# Random Distribution Test Determinism

Status: implemented 2026-08-20

## Contract

Gludd's distribution tests use local, explicitly seeded pseudo-random streams
when a fixed sample is required for a release gate. The tests still apply
SciPy's one-sample Kolmogorov-Smirnov and Pearson chi-square goodness-of-fit
implementations at the original 5% significance level. They do not loosen a
threshold, retry a rejection, or skip a result.

The `secrets` and `os.urandom` production APIs remain backed by the operating
system's secure entropy. Their distribution tests replace only the bound
`SystemRandom` index source or `os.urandom` byte source for the lifetime of that
test, then pytest restores it. Token and byte invariant tests continue to
exercise the real secure source.

Local RED evidence on 2026-08-20 reproduced the normal KS rejection on bounded
serial attempt 9 (`D=0.0166`, critical value `0.0136`) and the `secrets.choice`
chi-square rejection on attempt 14 (`22.21`, critical value `16.90`). Both are
valid false rejections for tests using a 5% significance level against a new
unrecorded random sample on every run.

On 2026-08-26, the canonical eight-shard release lane exposed the remaining
missed instance: `unit-3a` batch 21 stopped after the
`test_os_urandom_byte_distribution_chi_squared` node rejected a fresh sample,
while its 608 sibling tests passed. An immediate full ordered replay had already
shown why an unrecorded sample is inadequate evidence: an earlier run stopped
on a different non-reproducible statistical rejection. The repaired byte test
now uses a test-scoped fixed stream and SciPy's unchanged Pearson chi-square
calculation at `alpha=0.05`; it does not retry or loosen the gate.

## Upstream and user evidence

- On 2015-01-25, pytest issue
  [#667](https://github.com/pytest-dev/pytest/issues/667) reported that tests
  consuming unrecorded random values cannot reproduce failures and that test
  order is especially troublesome with xdist. The discussion's practical
  recommendation was a scoped, fixed seed that does not leak between tests.
- On 2024-01-12, pytest-randomly issue
  [#600](https://github.com/pytest-dev/pytest-randomly/issues/600) proposed a
  deterministic per-test stream derived from test identity while retaining
  repeatability. Gludd follows the same isolation property with constants local
  to this test module.
- The SciPy references for
  [`kstest`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.kstest.html)
  and
  [`chisquare`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.chisquare.html),
  accessed 2026-08-20, define the supported goodness-of-fit calculations used
  here. This replaces hand-maintained approximations for the repaired nodes.
- CPython's
  [`secrets` documentation](https://docs.python.org/3/library/secrets.html),
  accessed 2026-08-20, specifies operating-system secure randomness. Therefore
  the deterministic source is injected only inside the test and never exposed
  by application code.
- CPython's
  [`os.urandom` documentation](https://docs.python.org/3/library/os.html#os.urandom),
  accessed 2026-08-26, defines the production cryptographic byte source. Gludd
  leaves that source unchanged outside the pytest monkeypatch lifetime and
  retains direct secure-source invariant coverage.

## Stability evidence

The regression computes each controlled sample twice with deliberate global
`random` state perturbation in between. Focused release verification runs the
same nodes repeatedly both serially and through xdist so execution order and
worker assignment cannot alter their samples.

## Deep parser fuzz harness incident

On 2026-08-31, GHA run `33366948990` reached 85% of the hosted Python 3.11
`unit-2` batch before
`TestRegexFuzz.test_compile_random_patterns` failed. A fresh
`os.urandom(32)` value happened to contain an ambiguous nested character set;
CPython emitted `FutureWarning: Possible nested set at position 10`, and the
release lane correctly promoted the warning to an error. Because the bytes were
not recorded, the exact case could not be replayed locally.

The deep parser harness now derives every pseudo-random byte sequence from
domain-separated SHAKE-256 input containing a stable corpus version, domain,
and case number. It also derives dictionary keys and values from that source
instead of `uuid.uuid4`. Production entropy is unchanged. The regex corpus
contains an explicit ambiguous nested-set case, captures warnings for each
case, and accepts only CPython's five documented ambiguous-set
`FutureWarning` forms; an unexpected warning still fails the test.

CPython's Python 3.11
[`re` documentation](https://docs.python.org/3.11/library/re.html)
documents that ambiguous nested sets and set-operation sequences raise
`FutureWarning` because their future semantics may change. The long-lived
pytest issue [#667](https://github.com/pytest-dev/pytest/issues/667), opened
2015-01-25, records the practitioner problem: an unreported random seed makes a
failed test impossible to reproduce, especially under reordered execution.
Hypothesis issue
[#702](https://github.com/HypothesisWorks/hypothesis/issues/702), opened
2017-06-22, likewise tracks explicit seeding of global randomness to minimize
flakiness. Gludd uses a smaller standard-library boundary here because this
existing harness needs fixed byte corpora rather than shrinking.

The ZDD boundary is test-only and additive: generate the same bounded corpus,
validate every parser and warning, and publish no state. Each case is at most
256 bytes; there are no new processes, files, network calls, or production
dependencies. Rollback is the single atomic harness commit. The deterministic
contract test fails if unreplayable OS entropy or UUID generation returns.
