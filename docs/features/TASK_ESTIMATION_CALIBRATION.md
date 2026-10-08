# Task Estimation Calibration

Status: S11a implemented as bounded shadow feedback; exact-head full-gate and
release evidence remain pending.

## Runtime contract

The daemon creates one `EstimationTracker` and shares that exact object with the
`ExecutionEngine` and `ReturnReviewer`. After budget and spend admission, and
immediately before the provider call, the engine records the projected cost for
the job's todo identifier. A finite, non-negative provider cost closes the
observation. The reviewer later reads the resulting variance and adds an
`ESTIMATION_SUSPECT` audit note when absolute cost drift exceeds the configured
threshold.

This phase is deliberately cost-only. Provider-call duration is measured with
Python's monotonic `perf_counter_ns()` and retained as diagnostic context, but it
does not participate in the variance decision. Lines of code and elapsed time
are not inferred. Missing, boolean, negative, NaN, or infinite provider cost is
unobserved rather than converted to zero. Exceptions and every invalid-cost exit
discard the pending estimate.

The shadow path does not call `get_corrected_estimate`, mutate model profiles,
change budget admission, or influence routing or scheduling. Its observations
also do not update calibration multipliers. A projected USD 1.00 call reported
by the provider as USD 0.10 therefore becomes an auditable cost suspect without
changing the next dispatch.

## Bounds and resource ownership

Pending estimates, completed actuals, variance records, and paired history use
one hard maximum of 1,000 entries. A caller may request a smaller test or
deployment bound, but values above 1,000 are clamped. New entries evict the
oldest retained entry, and the engine explicitly removes its pending entry when
the provider observation closes. The feature creates no process, task, thread,
listener, file, database table, or network request.

No todo identifier is exported as a Prometheus label. Prometheus warns that
each label set creates a separate time series with RAM, CPU, disk, and network
cost, and recommends another analysis system when cardinality can grow beyond
roughly 100. The in-process bounded tracker is therefore kept separate from
metric labels. See Prometheus's
[instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/)
and
[metric naming guidance](https://prometheus.io/docs/practices/naming/).

Python documents `perf_counter_ns()` as the integer nanosecond form of the
highest-resolution performance counter for short durations; only differences
between readings are meaningful. This is why the observation stores elapsed
duration and never treats the counter value as wall-clock time. See the
[Python time documentation](https://docs.python.org/3/library/time.html#time.perf_counter_ns).

## Practitioner evidence and routing boundary

Dask Distributed issue
[#1851](https://github.com/dask/distributed/issues/1851) has remained open since
2018 and describes adaptive resource scheduling assigning queued work to the
first qualifying worker, then idling later workers that arrive seconds later.
That practitioner report is a concrete warning against allowing incomplete live
observations to steer routing. S11a consequently records provider-cost drift for
review only. A later proposal to use calibration for routing must define its own
sample sufficiency, concurrency, admission, and rollback evidence.

## ZDD rollout and rollback

This is a zero-downtime additive path. Existing requests keep their admission,
provider call, task return, and review behavior if observation fails; feedback
exceptions are contained and logged at debug level. Deployments may mix old and
new workers because no persisted schema or wire payload changes.

Set `GLUDD_ESTIMATION_FEEDBACK=0` before worker startup to disable construction
and engine use of the tracker. Rolling workers with that value removes the
shadow path without draining traffic, changing routes, or migrating data.
Re-enable it with the variable unset or set to any value other than the exact
rollback value `0`.

## Verification

`tests/unit/test_estimation_runtime_wiring.py` exercises a real
`ExecutionEngine`, the concrete tracker, and `ReturnReviewer`; it pins valid,
missing, non-finite, provider-error, hard-bound, monotonic-duration, lifecycle,
no-calibration, and rollback behavior. Focused coverage is configured in
`config/coverage_estimation_runtime.ini`. S11 remains in progress until the
exact committed head passes the full repository gate.
