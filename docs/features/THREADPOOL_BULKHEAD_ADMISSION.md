# Thread pool bulkhead admission

`ThreadPoolBulkhead` isolates resource saturation with a fixed worker budget and
a bounded backlog. Admission reserves one of `max_workers + max_queue_size`
outstanding execution slots before submitting to the standard library executor.
The first `max_workers` reservations belong to workers even before their threads
start; subsequent reservations consume backlog capacity. A worker startup delay
therefore cannot cause an otherwise available reservation to be rejected.

`queue_size` reports reservations beyond the worker budget and never exceeds
`max_queue_size`. `is_saturated` means every worker and backlog reservation is
occupied. A zero backlog admits only the worker budget; it does not request an
unlimited queue. Nonblocking overflow raises `BulkheadRejectedError` immediately
and increments `rejected_count` once. Existing timeout behavior remains:
`blocking=True` with a timeout waits for available capacity, while submissions
without a timeout attempt immediate admission.
Negative blocking timeouts retain the previous Queue.put ValueError contract,
before capacity or rejection counters can change; nonblocking timeout arguments
retain their previous ignored behavior.

## Execution and resource ownership

The executor owns thread creation, work dispatch, and joining. Bulkhead threads
have an instance-specific `gludd-bulkhead-` name. Admission and shutdown share one
condition lock so shutdown cannot race a successful reservation into an executor
that has already stopped. Shutdown rejects new work, wakes waiting submitters,
and delegates draining and resource reclamation to `ThreadPoolExecutor.shutdown`.
`wait=False` permits accepted work to finish; `wait=True` joins every worker.

Each accepted reservation is released on normal completion, exceptions, or
cancellation when its executor wrapper is consumed. A cancelled pending future
does not execute its callable. Its reservation remains occupied until dequeue:
otherwise repeated submit/cancel operations could accumulate unbounded cancelled
work in the executor's internal queue while all workers are blocked. Completion
publishes its result or exception after releasing capacity, allowing completion
callbacks and callers of `Future.result()` to reuse the slot immediately.

These local admission changes do not restart service processes or change the
deployment handoff; the existing ZDD release and promotion gates remain required.

## Prior art and long-lived reports

The existing mature implementation is CPython's
[ThreadPoolExecutor](https://github.com/python/cpython/blob/3.11/Lib/concurrent/futures/thread.py).
Its submission path uses an unbounded `SimpleQueue`, and its worker dispatch
checks cancellation through `Future.set_running_or_notify_cancel()`. Its
[public shutdown contract](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)
drains accepted work and frees executor resources, including after `wait=False`.
The bulkhead retains that implementation and controls admission around it.

Two long-lived user reports inform the reservation design (reviewed 2026-10-12):

- [Python Help, November 2023: bounded executors behave differently](https://discuss.python.org/t/why-isnt-this-boundedthreadpoolexecutor-bounded-as-expected/38428)
  compares queue-size limits with a semaphore limiting outstanding calls. Workers
  remove tasks from a queue before they complete, so backlog and outstanding-work
  budgets describe different limits. The bulkhead explicitly includes workers in
  its outstanding-work limit.
- [Stack Overflow, January 2018: limiting ThreadPoolExecutor queues](https://stackoverflow.com/questions/48263704/threadpoolexecutor-how-to-limit-the-queue-maxsize)
  contains subsequent reports that replacing the executor's private work queue
  can block shutdown when the shutdown sentinel needs a full queue. The bulkhead
  leaves executor queue and shutdown internals intact.

The reports provide historical failure modes; the implementation and shutdown
behavior above are checked against primary CPython documentation and source.

## Acceptance and release proof

`tests/unit/test_bulkhead_admission.py` holds real executor workers in their
initializer, before any work can be dequeued. This reproduces startup scheduling
delays deterministically, without sleep-based scheduling assumptions. Regression
coverage includes worker/backlog reservations, zero backlog, completion and
exception reclamation, cancellation bounds, timed admission, concurrent producers,
and shutdown wakeup/resource cleanup. The original deep-suite rejection,
counter, isolation, and result assertions remain required.

Focused acceptance uses:

```text
make test-files TESTFILES='tests/unit/test_bulkhead_deep.py tests/unit/test_bulkhead_admission.py' NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-W error -n 0'
make lint-files FILES='src/general_ludd/resilience/bulkhead.py tests/unit/test_bulkhead_deep.py tests/unit/test_bulkhead_admission.py'
make typecheck-scope FILES='src/general_ludd/resilience/bulkhead.py'
```

The release owner must also run focused coverage for
`src/general_ludd/resilience/bulkhead.py` with the same two test files, enforcing
85% aggregate and 75% per-file floors, then collect the unchanged full gate and
hosted CI evidence for the sealed candidate SHA. Focused acceptance is not
full-gate or release-completion evidence.

The actual failing-first draft reproduced 13 failures and four passes. All 54
original/new cases then passed at 97% targeted coverage; a source review adds
two failing-first negative-timeout compatibility cases before the final replay.
Strict source/test typing, Ruff and the unchanged 250-resource inventory pass.
The final 56-case replay passes at 97% targeted coverage. The complete observed
early owner passes all 192 regressions in 18.78 seconds (19.5 seconds observed).
The existing early compatibility owner runs both complete files before hosted
fan-out, without increasing its 90-second runtime or 60-second quiet bound.
