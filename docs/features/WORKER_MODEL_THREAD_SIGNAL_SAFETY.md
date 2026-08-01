# Worker model-call thread signal safety

Status: implemented with focused regression coverage; full repository gate is
tracked in `TASKS.md` as WMT.1.

## Failure

The worker deliberately runs the synchronous model gateway through
`asyncio.to_thread`, keeping long provider latency off the FastAPI event loop.
The first generation call lazily imports agent capabilities and deployment
modules from that executor thread. `general_ludd.infra.deployment` previously
called `signal.signal` at module import, so the import raised before
`gateway.call_model` and the job silently continued with no generated result:

```text
Worker model call failed: signal only works in main thread of the main interpreter
```

`cloud.resource_lifecycle.get_lifecycle` had the same defect when its singleton
was first requested by a non-main worker thread.

## Behavioral contract

1. The model call remains offloaded with `asyncio.to_thread`; fixing signal
   initialization must not make provider latency block the event loop.
2. Importing deployment code outside the main thread is side-effect safe. It
   constructs no signal handler and never prevents the gateway call.
3. Signal installation is idempotent and attempted only from Python's main
   thread. A `ValueError` is still handled because embedded interpreters can
   disagree with `threading.main_thread()` about the main interpreter thread.
4. A lifecycle singleton first created in a worker remains usable and retains
   its `atexit` cleanup. A later main-thread `get_lifecycle()` call retries and
   installs SIGTERM/SIGINT handlers.
5. Signals are not used for worker-thread communication. Ordinary async/thread
   boundaries remain responsible for model-call completion and errors.

## Upstream evidence

Python's official
[`signal` thread rules](https://docs.python.org/3/library/signal.html#signals-and-threads)
state that handlers execute in the main Python thread and only the main thread
of the main interpreter may install a handler. They also warn against acquiring
synchronization locks inside handlers. The Gludd installers therefore make
their thread decision before invoking `signal.signal`; the lifecycle singleton
lock is an initialization lock, not inter-thread signal communication.

The long-lived CPython community report
[`bpo-38904`](https://bugs.python.org/issue38904), open since November 2019,
shows that even code guarded by `threading.main_thread()` can receive this exact
`ValueError` in embedded runtimes because `threading` and the main interpreter
historically used different thread identities. That report is why Gludd both
checks the thread and catches `ValueError` rather than trusting the check alone.

CPython issue
[`#126434`](https://github.com/python/cpython/issues/126434) demonstrates a
separate reentrancy/deadlock hazard when a signal handler touches a
non-reentrant multiprocessing synchronization primitive. It reinforces the
boundary here: installation is main-thread-only and signal handling is never a
worker coordination mechanism.

## Verification

- `tests/unit/test_worker_model_thread_signal.py` covers a real TestClient
  generation request and worker-first lifecycle initialization followed by
  main-thread handler installation.
- `tests/e2e/test_obj03_worker.py` proves the generation response reaches the
  job result and the gateway sees the prompt.
- `tests/unit/test_port_8000_occupied.py` runs the complete worker E2E file in a
  nested pytest process while port 8000 is occupied, reproducing the original
  failing wrapper boundary.

