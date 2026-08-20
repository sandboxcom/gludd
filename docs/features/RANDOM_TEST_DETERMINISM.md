# Random Distribution Test Determinism

Status: implemented 2026-08-20

## Contract

Gludd's distribution tests use local, explicitly seeded pseudo-random streams
when a fixed sample is required for a release gate. The tests still apply
SciPy's one-sample Kolmogorov-Smirnov and Pearson chi-square goodness-of-fit
implementations at the original 5% significance level. They do not loosen a
threshold, retry a rejection, or skip a result.

The `secrets` production API remains backed by the operating system's secure
entropy. Its uniform-choice test replaces only the bound `SystemRandom` index
source for the lifetime of that test, then pytest restores it. Token and byte
invariant tests continue to exercise the real secure source.

Local RED evidence on 2026-08-20 reproduced the normal KS rejection on bounded
serial attempt 9 (`D=0.0166`, critical value `0.0136`) and the `secrets.choice`
chi-square rejection on attempt 14 (`22.21`, critical value `16.90`). Both are
valid false rejections for tests using a 5% significance level against a new
unrecorded random sample on every run.

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

## Stability evidence

The regression computes each controlled sample twice with deliberate global
`random` state perturbation in between. Focused release verification runs the
same nodes repeatedly both serially and through xdist so execution order and
worker assignment cannot alter their samples.
