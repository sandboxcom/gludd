# Python Knowledge Reference for gludd

Comprehensive Python reference for gludd agents working on Python code,
answering Python questions, or debugging Python issues.

**Maintained by:** gludd agentic system
**Last updated:** 2026-07-25
**Status of:** CPython 3.13 (free-threading experimental), 3.12 (stable)

---

## 1. Python Language Model & Execution

### 1.1 CPython Reference Implementation

CPython executes Python through a bytecode interpreter loop. Source code is
compiled to bytecode, then executed on a stack-based virtual machine.

```python
# Compilation: source -> AST -> CFG -> bytecode
import dis

def add(a, b):
    return a + b

dis.dis(add)
#   2           0 LOAD_FAST                0 (a)
#               2 LOAD_FAST                1 (b)
#               4 BINARY_OP                0 (+)
#               8 RETURN_VALUE

# Code objects encapsulate compiled bytecode
code = add.__code__
print(code.co_varnames)   # ('a', 'b')
print(code.co_stacksize)  # 2
print(code.co_consts)     # (None,)
print(code.co_code)       # b'|\x00|\x01z\x00\x00\x00S\x00'

# Stack frames exist at runtime
import inspect
def outer():
    x = 1
    def inner():
        return inspect.currentframe().f_back.f_locals['x']
    return inner()

print(outer())  # 1
```

The interpreter loop: `_PyEval_EvalFrameDefault` in `Python/ceval.c` (or
`Python/generated_cases.c.h` in 3.12+ with computed gotos). Each frame has:
- A reference to its code object
- A program counter (next instruction offset)
- A value stack (for operand passing between instructions)
- Local variable slots indexed by name

### 1.2 The Global Interpreter Lock (GIL)

The GIL is a mutex that prevents multiple native threads from executing Python
bytecode simultaneously. One thread holds the GIL at a time.

```python
# The GIL is released during blocking I/O:
import threading, time, urllib.request

def io_bound(url):
    # GIL released during socket I/O — other threads can run
    return urllib.request.urlopen(url).read()

# The GIL is HELD during CPU-bound work:
def cpu_bound():
    total = 0
    for i in range(10_000_000):
        total += i  # Pure Python — GIL prevents parallelism
    return total

# Contention pattern: threads fight for the GIL
# Default switch interval: 5ms (sys.setswitchinterval)
import sys
print(sys.getswitchinterval())  # 0.005

# Python 3.13 free-threading (experimental):
# Build with --disable-gil, run with PYTHON_GIL=0
# import sysconfig
# print(sysconfig.get_config_var('Py_GIL_DISABLED'))
```

**When the GIL is released:**
- I/O operations (file reads, network, etc.)
- `time.sleep()` — explicitly yields
- C extensions that call `Py_BEGIN_ALLOW_THREADS`
- `threading.Lock.acquire()` (briefly, while waiting)

**When the GIL is NOT released:**
- Pure Python arithmetic
- String operations
- List/dict comprehensions
- Most standard library functions written in Python

**3.13 free-threading (PEP 703):** When enabled, the GIL is removed entirely via
`Py_GIL_DISABLED`. Objects use per-object locks (biased reference counting +
immortalization for common objects). `sys._is_gil_enabled()` checks state.
Still experimental; many C extensions are not thread-safe without the GIL.

### 1.3 Memory Management

**Reference counting** is the primary mechanism. Each object has a `ob_refcnt`
field. When refcnt hits 0, the object is immediately deallocated (no sweep phase).

```python
import sys

x = []
print(sys.getrefcount(x))  # 2 (x + getrefcount arg)
y = x
print(sys.getrefcount(x))  # 3
del y
print(sys.getrefcount(x))  # 2

# Circular references defeat refcounting:
a = {}; b = {}
a['b'] = b; b['a'] = a
del a; del b  # Still alive — collected by GC
```

**Garbage collection** handles cycles. CPython uses a generational GC with 3
generations (0, 1, 2). Objects that survive a generation N collection are
promoted to N+1.

```python
import gc

print(gc.get_threshold())  # (700, 10, 10) — allocations, gen0->gen1, gen1->gen2
print(gc.get_count())      # Current allocation counts per generation

# Manual control:
gc.disable()
gc.collect()      # Full collection
gc.collect(0)     # Generation 0 only

# Monitor:
gc.set_debug(gc.DEBUG_SAVEALL)  # Keep unreachable objects
gc.set_debug(gc.DEBUG_LEAK)     # Print leaking objects

# Weak references break cycles without preventing collection:
import weakref
obj = SomeClass()
ref = weakref.ref(obj)
ref()  # Returns obj or None if collected
```

**Object memory layout:**
- Every Python object has a header: `ob_refcnt` + `ob_type` pointer (16 bytes
  on 64-bit). Custom objects add `ob_size`, `ob_dict`, `ob_weaklist`.
- Small objects (<512 bytes) use a specialized allocator (`obmalloc`) with
  arenas (256KB), pools (4KB), and blocks (fixed-size for each size class).
- Large objects (>512 bytes) go through `malloc()` directly.
- Integers from -5 to 256 are singletons (pre-allocated).

```python
# Integer singletons
a = 256; b = 256
print(a is b)  # True
a = 257; b = 257
print(a is b)  # True in same compilation unit, may be False across modules

# Small-object allocator size classes:
import sys
print(sys.getsizeof(0))       # 28 bytes
print(sys.getsizeof(""))      # 49 bytes
print(sys.getsizeof([]))      # 56 bytes
print(sys.getsizeof({}))      # 64 bytes
```

### 1.4 Bytecode Inspection

```python
import dis, inspect, types

# Full bytecode listing
dis.dis(len)  # Disassemble a built-in (won't work — C function)

# Get bytecode as string:
print(dis.Bytecode(lambda: [x*2 for x in range(5)]).dis())

# Common opcodes:
# LOAD_FAST    — load local variable
# LOAD_GLOBAL  — load global/__builtins__
# LOAD_CONST   — push constant from co_consts
# LOAD_ATTR    — attribute access (replaces PyObject_GetAttr)
# STORE_FAST   — store to local
# BINARY_OP    — binary operation with inline argument
# CALL         — function call
# RESUME       — 3.11+ zero-cost exception handling marker
# POP_TOP      — discard top of stack
# RETURN_VALUE — return top of stack

# Instruction size in 3.11+: 2 bytes per instruction (wordcode)
# Previously: 2–8 bytes variable-length

# Inspect all opcodes:
print(len(dis.opmap))    # Number of opcodes (~170 in 3.12)
print(dis.HAVE_ARGUMENT) # 90 — opcodes >= this have an argument

# .pyc files:
import py_compile, marshal
# py_compile.compile('mymodule.py')  # Creates __pycache__/mymodule.cpython-312.pyc
# The .pyc format: magic (4 bytes) + flags (4 bytes) + timestamp/source-hash + code size + marshalled code
```

---

## 2. Type System & Object Model

### 2.1 Everything Is an Object

```python
# Types are objects:
print(type(int))           # <class 'type'>
print(type(type))          # <class 'type'> — metacircle
print(type(None))          # <class 'NoneType'>

# Functions are objects:
def f(): pass
print(type(f))             # <class 'function'>
print(f.__code__)          # Code object
print(f.__closure__)       # Tuple of cell objects (for closures)
print(f.__defaults__)      # Default argument values
print(f.__kwdefaults__)    # Keyword-only defaults
print(f.__annotations__)   # Type annotations dict

# Modules are objects:
import os
print(type(os))            # <class 'module'>
print(os.__dict__.keys())  # Module namespace

# Classes are objects (instances of `type`):
class Foo: pass
print(type(Foo))           # <class 'type'>
print(Foo.__mro__)         # Method Resolution Order
print(Foo.__bases__)       # Base classes
```

### 2.2 Method Resolution Order (MRO) & C3 Linearization

```python
class A: pass
class B(A): pass
class C(A): pass
class D(B, C): pass

print(D.__mro__)  # D, B, C, A, object
# C3 linearization algorithm:
# L[C] = C + merge(L[B], L[C], [B, C])
# merge picks the first head not in any tail of other lists

# super() follows MRO, not parent class:
class A:
    def method(self):
        print("A")

class B(A):
    def method(self):
        super().method()
        print("B")

class C(A):
    def method(self):
        super().method()
        print("C")

class D(B, C):
    def method(self):
        super().method()
        print("D")

D().method()  # A C B D — follows MRO: D->B->C->A
```

### 2.3 Descriptors

Descriptors implement `__get__`, `__set__`, or `__delete__`. They control
attribute access at the class level.

```python
class TypedProperty:
    """A data descriptor — enforces type on attribute assignment."""
    def __init__(self, name, expected_type):
        self.name = name
        self.expected_type = expected_type

    def __set_name__(self, owner, name):
        # Called at class creation — captures the attribute name
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name)

    def __set__(self, obj, value):
        if not isinstance(value, self.expected_type):
            raise TypeError(f"{self.name} must be {self.expected_type}")
        obj.__dict__[self.name] = value

class Person:
    name = TypedProperty("name", str)  # __set_name__ overrides "name"
    age = TypedProperty("age", int)

# Property is a descriptor:
class Circle:
    def __init__(self, radius):
        self._radius = radius

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        if value < 0:
            raise ValueError("Radius must be non-negative")
        self._radius = value

# __getattr__ vs __getattribute__:
class LazyLoader:
    def __init__(self):
        self._loaded = {}

    def __getattr__(self, name):
        # Called ONLY when normal attribute lookup fails
        print(f"Lazy loading: {name}")
        self._loaded[name] = f"value_for_{name}"
        return self._loaded[name]

    # def __getattribute__(self, name):
    #     # Called on EVERY attribute access — easy infinite recursion
    #     return super().__getattribute__(name)
```

### 2.4 Metaclasses

Metaclasses are classes whose instances are classes. `type` is the default.

```python
# Metaclass that registers all subclasses
class PluginRegistry(type):
    registry = {}

    def __new__(mcls, name, bases, namespace):
        cls = super().__new__(mcls, name, bases, namespace)
        if name != "PluginBase":
            mcls.registry[name] = cls
        return cls

class PluginBase(metaclass=PluginRegistry):
    pass

class EmailPlugin(PluginBase):
    pass

class S3Plugin(PluginBase):
    pass

print(PluginRegistry.registry)
# {'EmailPlugin': <class 'EmailPlugin'>, 'S3Plugin': <class 'S3Plugin'>}

# __init_subclass__ — simpler alternative (no metaclass needed):
class Base:
    registry = {}
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__name__ != "Base":
            Base.registry[cls.__name__] = cls

# __set_name__ — called when descriptor is assigned in class body:
class Field:
    def __set_name__(self, owner, name):
        self.private_name = f"_{name}"
```

**When to use metaclasses:**
- Registering all subclasses automatically
- Enforcing invariants across a class hierarchy
- Modifying class namespace at creation time
- Building ORMs, serializers, API frameworks

**When NOT to use metaclasses:**
- Simple subclass registration (use `__init_subclass__`)
- Descriptors (use `__set_name__`)
- Class decorators can handle most other cases

### 2.5 Protocols & Structural Subtyping

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class SupportsClose(Protocol):
    def close(self) -> None: ...

class File:
    def close(self) -> None:
        pass

class Socket:
    def close(self) -> None:
        pass

# Structural: File and Socket are SupportsClose without inheriting
def cleanup(resource: SupportsClose):
    resource.close()

# runtime_checkable enables isinstance:
print(isinstance(File(), SupportsClose))   # True
print(isinstance(Socket(), SupportsClose)) # True

# ABCs use nominal subtyping (explicit registration):
from collections.abc import MutableMapping

class MyDict(MutableMapping):
    def __getitem__(self, key): ...
    def __setitem__(self, key, value): ...
    def __delitem__(self, key): ...
    def __iter__(self): ...
    def __len__(self): ...
    # MutableMapping provides: keys(), values(), items(), get(), pop(), etc.

# collections.abc hierarchy:
# Container -> Sized -> Iterable -> Collection
#   -> Sequence (list/tuple/str), MutableSequence
#   -> Set, MutableSet
#   -> Mapping, MutableMapping
# Callable, Hashable, Iterator, Generator, Coroutine, Awaitable
```

### 2.6 Type Hints (Advanced)

```python
from typing import (
    TypeVar, Generic, ParamSpec, Concatenate, overload,
    Final, Never, Literal, TypedDict, Unpack
)

# TypeVar — binds types across a generic function:
T = TypeVar('T')

def first(items: list[T]) -> T:
    return items[0]

x: int = first([1, 2, 3])
y: str = first(["a", "b"])

# Constrained TypeVar:
Mode = TypeVar('Mode', int, str)

# ParamSpec — captures callable parameter types:
P = ParamSpec('P')
R = TypeVar('R')

def log_call(f: Callable[P, R]) -> Callable[P, R]:
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        print(f"Calling {f.__name__}")
        return f(*args, **kwargs)
    return wrapper

# Concatenate — prepend parameters:
def add_session(
    f: Callable[Concatenate[Session, P], R]
) -> Callable[P, R]:
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        session = Session.current()
        return f(session, *args, **kwargs)
    return wrapper

# overload — multiple signatures for the same function:
@overload
def process(data: bytes) -> str: ...
@overload
def process(data: str) -> bytes: ...

def process(data: bytes | str) -> str | bytes:
    if isinstance(data, bytes):
        return data.decode()
    return data.encode()

# Final — prevents subclassing or reassignment:
class Base:
    FINAL: Final[int] = 42  # Can't be reassigned on instances
    def method(self) -> None: ...  # Can't be overridden if class is Final

# Never — bottom type (function never returns):
def halt() -> Never:
    raise SystemExit(1)

# TypedDict — typed dictionary:
class Point3D(TypedDict):
    x: float
    y: float
    z: float
    label: str  # total=True by default (all required)

# Unpack — for **kwargs typing:
from typing import TypedDict
class Options(TypedDict, total=False):
    timeout: int
    retries: int

def connect(url: str, **options: Unpack[Options]) -> None: ...
connect("http://x", timeout=5)         # OK
connect("http://x", retries=3)         # OK
connect("http://x", timeout=5, retries=3)  # OK

# Variadic generics:
from typing import TypeVarTuple
Ts = TypeVarTuple('Ts')
def first_of(*args: Unpack[Ts]) -> Union[Unpack[Ts]]:
    return args[0]
```

---

## 3. Concurrency & Async

### 3.1 asyncio Fundamentals

```python
import asyncio

# Coroutine: defined with async def, returns a coroutine object
async def fetch(url: str) -> str:
    # await yields control to the event loop
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()

# Task: schedules a coroutine on the event loop
async def main():
    # create_task — fire and forget (runs in background)
    task = asyncio.create_task(fetch("https://example.com"))

    # gather — run coroutines concurrently, wait for all
    results = await asyncio.gather(
        fetch("https://a.com"),
        fetch("https://b.com"),
        return_exceptions=True,  # Don't raise on first failure
    )

    # as_completed — process results as they finish
    urls = ["https://a.com", "https://b.com", "https://c.com"]
    for coro in asyncio.as_completed([fetch(u) for u in urls]):
        result = await coro
        print(f"Got result: {len(result)} chars")

    # wait_for — timeout
    try:
        result = await asyncio.wait_for(fetch("https://slow.com"), timeout=5.0)
    except asyncio.TimeoutError:
        print("Timed out")

    # TaskGroup (3.11+) — structured concurrency
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(fetch("https://a.com"))
        t2 = tg.create_task(fetch("https://b.com"))
    # All tasks are done here; if any failed, exceptions propagate

# Running:
asyncio.run(main())
```

**Event loop lifecycle:**
1. `asyncio.run()` creates a new event loop
2. Runs the coroutine until complete
3. Cancels any remaining tasks
4. Closes the loop

```python
# Custom event loop with policy:
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    loop.run_until_complete(main())
finally:
    loop.close()
```

### 3.2 threading vs multiprocessing

```python
# THREADING: shared memory, I/O-bound tasks
# Pros: low overhead, shared state, simple communication
# Cons: GIL limits CPU-bound parallelism, race conditions

import threading, queue

def worker(q: queue.Queue, results: list):
    while True:
        try:
            item = q.get(timeout=1)
        except queue.Empty:
            return
        results.append(item * 2)

q = queue.Queue()
for i in range(100):
    q.put(i)

results = []
threads = [threading.Thread(target=worker, args=(q, results))
           for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# MULTIPROCESSING: separate memory, CPU-bound tasks
# Pros: true parallelism (separate interpreter per process)
# Cons: serialization overhead, memory overhead per process

from multiprocessing import Pool

def cpu_heavy(n: int) -> int:
    return sum(i * i for i in range(n))

with Pool(processes=4) as pool:
    results = pool.map(cpu_heavy, [10_000_000] * 4)

# ProcessPoolExecutor — higher-level API:
from concurrent.futures import ProcessPoolExecutor, as_completed

with ProcessPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(cpu_heavy, n) for n in [10_000_000] * 4]
    for future in as_completed(futures):
        print(future.result())
```

### 3.3 Subinterpreters (PEP 554/734)

Python 3.12+ supports per-interpreter GILs — each subinterpreter has its
own GIL, enabling true parallelism within a single process.

```python
# interpreters module (3.12+, requires --experimental-isolated-subinterpreters)
import interpreters

interp = interpreters.create()
# Run code in subinterpreter:
interp.run("print('Hello from subinterpreter')")

# Message passing via channels:
# Each subinterpreter has its own objects; data is copied, not shared
# PEP 734 (3.13+) adds Queues for structured communication
```

### 3.4 Common Patterns & Pitfalls

```python
# PRODUCER-CONSUMER with asyncio:
import asyncio

async def producer(queue: asyncio.Queue):
    for i in range(10):
        await queue.put(i)
        await asyncio.sleep(0.1)

async def consumer(queue: asyncio.Queue, name: str):
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return
        print(f"{name}: {item}")
        queue.task_done()

async def main():
    q = asyncio.Queue(maxsize=5)
    producers = [asyncio.create_task(producer(q))]
    consumers = [asyncio.create_task(consumer(q, f"c{i}"))
                 for i in range(3)]
    await asyncio.gather(*producers)
    await q.join()  # Wait until all items are processed
    for c in consumers:
        c.cancel()

# PITFALL: async code calling sync blocking functions
# WRONG — blocks the event loop:
async def bad():
    import time
    time.sleep(10)  # BLOCKS ALL OTHER COROUTINES

# RIGHT — use run_in_executor:
async def good():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, time.sleep, 10)

# PITFALL: sync code calling async
# Use asyncio.run() — but only from sync entry points:
def sync_entry():
    asyncio.run(async_main())

# WRONG — calling asyncio.run() if an event loop is already running:
async def async_main():
    # asyncio.run(some_coro)  # RuntimeError: asyncio.run() cannot be called
    pass                        # from a running event loop
```

---

## 4. Import System

### 4.1 How Import Works

```python
import sys

# The import chain: finder -> loader -> module

# sys.meta_path — list of finders (searched in order):
for finder in sys.meta_path:
    print(type(finder).__name__)
# BuiltinImporter    — built-in modules (sys, itertools)
# FrozenImporter     — frozen modules (_frozen_importlib)
# PathFinder         — filesystem imports (uses sys.path)

# sys.path — where PathFinder searches:
print(sys.path[:3])
# ['', '/usr/lib/python312.zip', '/usr/lib/python312']

# sys.path_hooks — determines how to load from sys.path entries:
# Each entry is a callable that takes a path and returns a finder

# Find a module spec:
spec = importlib.util.find_spec("json")
print(spec.origin)   # '/usr/lib/python3.12/json/__init__.py'
print(spec.loader)   # <_frozen_importlib_external.SourceFileLoader ...>

# Manual import:
import importlib
mod = importlib.import_module("json")

# Reload a module:
importlib.reload(mod)
```

### 4.2 Package Types

```python
# Regular package: has __init__.py
# mypak/
#   __init__.py
#   submodule.py

# Namespace package (PEP 420): no __init__.py, directories merge
# /site-packages/myns/sub_a.py
# /another-site/myns/sub_b.py
# import myns.sub_a, import myns.sub_b both work

# Implicit namespace packages (3.3+ default)
# Explicit: __init__.py with __path__ manipulation

# Relative imports (inside a package):
# from . import sibling         # same package
# from .sibling import name     # same package
# from .. import parent_module  # parent package
# from ..sibling import name    # sibling of parent

# Absolute imports (always explicit):
# from mypackage.submodule import name

# __getattr__ in __init__.py (3.7+) — lazy submodule loading:
# __init__.py:
import importlib

__all__ = ["submod_a", "submod_b"]

def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Lazy imports (PEP 690, 3.12+):
# import lazyload
# lazyload.LazyLoader.install()  # makes all imports lazy
# import huge_module             # Not actually loaded until used
```

### 4.3 Editable Installs

```bash
# PEP 660 — editable installs:
pip install -e .                    # Traditional (setup.py develop)
pip install -e . --config-settings editable_mode=compat  # Copy of files
pip install -e . --config-settings editable_mode=strict  # Symlinks

# Editable install with pyproject.toml:
# [build-system]
# requires = ["hatchling"]
# build-backend = "hatchling.build"
```

---

## 5. Common Python Code Patterns

### 5.1 Context Managers

```python
# Class-based context manager:
class DatabaseConnection:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def __enter__(self):
        self.conn = connect(self.dsn)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.close()
        # Return True to suppress exception, False/None to propagate
        return False

with DatabaseConnection("postgresql://...") as conn:
    conn.execute("...")

# contextlib — decorator-based:
from contextlib import contextmanager

@contextmanager
def transaction(conn):
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

# contextlib utilities:
from contextlib import ExitStack, suppress, redirect_stdout, nullcontext

# ExitStack — dynamic context manager stacking:
with ExitStack() as stack:
    files = [stack.enter_context(open(f"file_{i}.txt"))
             for i in range(5)]
    # All files auto-closed on exit

# suppress — context manager that swallows specific exceptions:
with suppress(FileNotFoundError):
    os.remove("maybe_nonexistent.txt")

# nullcontext — no-op context manager:
def maybe_lock(should_lock: bool):
    return threading.Lock() if should_lock else nullcontext()
```

### 5.2 Decorators

```python
# Basic decorator:
def log(func):
    @functools.wraps(func)  # Preserves __name__, __doc__, etc.
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        result = func(*args, **kwargs)
        print(f"{func.__name__} returned {result}")
        return result
    return wrapper

# Decorator with arguments:
def retry(max_attempts: int = 3, delay: float = 1.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry(max_attempts=5, delay=0.5)
def flaky_network_call():
    return requests.get("https://api.example.com")

# Class-based decorator:
class cached_property:
    def __init__(self, func):
        self.func = func
        self.name = func.__name__

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        value = self.func(obj)
        obj.__dict__[self.name] = value
        return value

# functools utilities:
from functools import lru_cache, singledispatch, partial

@lru_cache(maxsize=128)
def expensive(n: int) -> int:
    return sum(i * i for i in range(n))

@singledispatch
def serialize(obj) -> str:
    raise TypeError(f"Can't serialize {type(obj)}")

@serialize.register
def _(obj: int) -> str:
    return str(obj)

@serialize.register
def _(obj: list) -> str:
    return "[" + ", ".join(serialize(item) for item in obj) + "]"
```

### 5.3 Iterators, Generators, and Coroutines

```python
# Generator — yields values lazily:
def fibonacci():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b

fib = fibonacci()
print([next(fib) for _ in range(10)])  # [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

# yield from — delegate to sub-generator:
def chain(*iterables):
    for it in iterables:
        yield from it  # delegates to each iterable

# send — inject values into generator:
def accumulator():
    total = 0
    while True:
        value = yield total
        if value is None:
            break
        total += value

acc = accumulator()
next(acc)         # Prime the generator
print(acc.send(10))  # 10
print(acc.send(20))  # 30
acc.send(None)       # Stop

# throw — inject exceptions into generator:
def resilient():
    try:
        while True:
            yield "working"
    except ValueError as e:
        yield f"Caught: {e}"

gen = resilient()
print(next(gen))        # 'working'
print(gen.throw(ValueError, "boom"))  # 'Caught: boom'

# Generator expressions:
squares = (x*x for x in range(10))  # <generator object>
print(list(squares))  # [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

# Python iterator protocol:
class Countdown:
    def __init__(self, start):
        self.count = start

    def __iter__(self):
        return self

    def __next__(self):
        if self.count < 0:
            raise StopIteration
        current = self.count
        self.count -= 1
        return current

for n in Countdown(3):
    print(n)  # 3, 2, 1, 0
```

### 5.4 Data Container Classes

```python
# dataclasses — simple data containers:
from dataclasses import dataclass, field, KW_ONLY

@dataclass
class Point:
    x: float
    y: float
    _: KW_ONLY
    label: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def magnitude(self) -> float:
        return (self.x**2 + self.y**2) ** 0.5

# attrs — more features, third-party (pip install attrs):
import attr

@attr.s(auto_attribs=True, frozen=True, slots=True)
class ImmutablePoint:
    x: float
    y: float

# pydantic — validation + serialization (pip install pydantic):
from pydantic import BaseModel, Field, validator

class User(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str
    age: int = Field(ge=0, le=150)

    @validator("email")
    def email_must_contain_at(cls, v):
        if "@" not in v:
            raise ValueError("Invalid email")
        return v.lower()

# namedtuple — immutable, lightweight, tuple-compatible:
from collections import namedtuple
Person = namedtuple("Person", ["name", "age"])
p = Person("Alice", 30)
print(p.name)    # 'Alice'
print(p[0])      # 'Alice' — tuple access works

# Comparison:
# dataclass: mutable by default, slots support, standard library
# attrs: more validation options, converters, validators, frozen
# pydantic: full validation, serialization (JSON), schema generation
# namedtuple: immutable, lightweight, tuple-compatible, no type validation
```

### 5.5 Design Patterns

```python
# Singleton via metaclass:
class Singleton(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

class Config(metaclass=Singleton):
    def __init__(self):
        self.settings = {}

# Registry pattern:
class HandlerRegistry:
    _handlers: dict[str, type] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(handler_cls):
            cls._handlers[name] = handler_cls
            return handler_cls
        return decorator

    @classmethod
    def get(cls, name: str):
        return cls._handlers[name]()

@HandlerRegistry.register("email")
class EmailHandler:
    def handle(self, data):
        print(f"Sending email: {data}")

# Factory pattern:
class ShapeFactory:
    _creators: dict[str, Callable] = {}

    @classmethod
    def register(cls, shape_type: str, creator: Callable):
        cls._creators[shape_type] = creator

    @classmethod
    def create(cls, shape_type: str, **kwargs):
        return cls._creators[shape_type](**kwargs)
```

---

## 6. Testing & Debugging

### 6.1 pytest Patterns

```python
# Fixtures: provide test dependencies
import pytest

@pytest.fixture(scope="function")  # function, class, module, package, session
def db_session():
    """Temporary database for each test."""
    db = create_test_db()
    yield db
    db.cleanup()

@pytest.fixture(params=["sqlite", "postgresql"])
def db_backend(request):
    return create_backend(request.param)

# autouse fixture:
@pytest.fixture(autouse=True)
def enable_debug():
    os.environ["DEBUG"] = "1"
    yield
    del os.environ["DEBUG"]

# Parametrize: run same test with different inputs:
@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
    (100, 200, 300),
])
def test_add(a, b, expected):
    assert a + b == expected

# Marks: skip, xfail, custom:
@pytest.mark.skip(reason="Not implemented yet")
def test_future_feature():
    pass

@pytest.mark.xfail(raises=ValueError, strict=True)
def test_expected_failure():
    raise ValueError("Known issue")

@pytest.mark.slow
def test_heavy_computation():
    pass

# conftest.py hierarchy: cascade from test dir to project root
# tests/conftest.py        — global fixtures
# tests/unit/conftest.py   — unit-test fixtures
# tests/unit/db/conftest.py — database-specific fixtures

# Custom markers in pytest.ini or pyproject.toml:
# [tool.pytest.ini_options]
# markers = [
#     "slow: marks tests as slow (deselect with '-m \"not slow\"')",
#     "integration: marks tests as integration tests",
# ]

# Assertion rewriting: pytest rewrites assert statements for better messages
assert {"a": 1, "b": 2} == {"a": 1}
# E   AssertionError: assert {'a': 1, 'b': 2} == {'a': 1}
# E     Extra items in the left set:
# E     'b'
# E     ...Full diff...

# Fixture teardown with yield + finalizers:
@pytest.fixture
def resource():
    res = allocate_resource()
    yield res
    res.deallocate()
```

### 6.2 Mocking

```python
from unittest.mock import Mock, patch, MagicMock, sentinel, create_autospec

# Mock — generic replacement object:
mock = Mock(return_value=42)
print(mock())         # 42
print(mock.called)    # True
print(mock.call_count) # 1
mock.assert_called_once()
mock.some_attr        # Creates another Mock

# MagicMock — Mock with default magic methods:
mm = MagicMock()
print(len(mm))        # 0 (__len__ implemented)
print(mm[0])          # Another MagicMock (__getitem__ implemented)

# patch — temporarily replace an object:
@patch("mymodule.requests.get")
def test_fetch(mock_get):
    mock_get.return_value.json.return_value = {"key": "value"}
    result = mymodule.fetch("https://api.example.com")
    assert result == {"key": "value"}
    mock_get.assert_called_once_with("https://api.example.com")

# patch as context manager:
def test_with_patch():
    with patch("mymodule.requests.post") as mock_post:
        mock_post.return_value.status_code = 201
        result = mymodule.create("data")
        assert result.status_code == 201

# sentinel — unique marker objects:
from unittest.mock import sentinel
x = sentinel.unique_name  # sentinel.unique_name

# create_autospec — mock that mimics the real interface:
from mymodule import DatabaseClient
mock_db = create_autospec(DatabaseClient)
mock_db.query.return_value = [1, 2, 3]
# mock_db.nonexistent()  # AttributeError — autospec catches this

# Side effects:
mock = Mock(side_effect=[1, 2, ValueError("boom"), 3])
print(mock())  # 1
print(mock())  # 2
# mock()        # raises ValueError

# AsyncMock (3.8+):
from unittest.mock import AsyncMock
async_mock = AsyncMock(return_value=42)
result = await async_mock()  # 42
```

### 6.3 Coverage

```python
# coverage.py configuration (pyproject.toml):
# [tool.coverage.run]
# branch = true           # Branch coverage, not just line
# source = ["src"]
# omit = ["tests/*", "*/migrations/*"]
#
# [tool.coverage.report]
# fail_under = 85
# precision = 2
# skip_covered = true
# exclude_lines = [
#     "pragma: no cover",
#     "if TYPE_CHECKING:",
#     "raise NotImplementedError",
#     "if __name__ == .__main__.:",
# ]

# Commands:
# coverage run -m pytest
# coverage report           # Terminal report
# coverage html             # HTML report in htmlcov/
# coverage json             # JSON for machine consumption
# coverage report --show-missing  # Show uncovered lines

# Branch vs line coverage:
# Line: was this line executed?
# Branch: were all branch paths taken? (if/else, try/except, etc.)
# def f(x):
#     if x > 0:      # branch: took True and False
#         return 1
#     return -1
```

### 6.4 Profiling

```python
# cProfile — built-in deterministic profiler:
import cProfile, pstats

profiler = cProfile.Profile()
profiler.enable()
# ... code to profile ...
profiler.disable()

stats = pstats.Stats(profiler)
stats.sort_stats("cumtime")   # Sort by cumulative time
stats.print_stats(20)         # Top 20 functions

# Command-line:
# python -m cProfile -s cumtime my_script.py

# py-spy — sampling profiler (doesn't need instrumentation):
# pip install py-spy
# py-spy top -- python my_script.py
# py-spy record -o profile.svg -- python my_script.py  # Flamegraph

# line_profiler — per-line timing:
# pip install line_profiler
# @profile  # decorator
# def slow_function():
#     ...

# memory_profiler — per-line memory:
# pip install memory_profiler
# @profile
# def memory_hungry():
#     ...

# tracemalloc — built-in memory tracking:
import tracemalloc

tracemalloc.start()
# ... code ...
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:10]:
    print(stat)
```

### 6.5 Debugging

```python
# pdb — Python debugger:
import pdb

# Set a breakpoint:
breakpoint()  # 3.7+; equivalent to import pdb; pdb.set_trace()

# pdb commands:
# n(ext)    — next line
# s(tep)    — step into
# c(ontinue) — continue execution
# l(ist)    — show source around current line
# p <expr>  — print expression
# pp <expr> — pretty print
# u(p) / d(own) — move up/down the call stack
# w(here)   — show stack trace
# b <line>  — set breakpoint
# condition <bpnum> <expr> — conditional breakpoint

# Command-line:
# python -m pdb my_script.py  # Start at first line
# python -m pdb -c continue my_script.py  # Run, stop at breakpoints

# Post-mortem: debug after an exception
import sys, traceback

try:
    1 / 0
except Exception:
    # Print full traceback:
    traceback.print_exc()

    # Get traceback object:
    ex_type, ex_value, ex_tb = sys.exc_info()
    traceback.print_tb(ex_tb)

    # Post-mortem debug:
    import pdb; pdb.post_mortem(ex_tb)

# sys.settrace — trace function used by debuggers and coverage:
def trace_calls(frame, event, arg):
    if event == "call":
        print(f"Calling {frame.f_code.co_name} at {frame.f_lineno}")
    return trace_calls

sys.settrace(trace_calls)
# ... code ...
sys.settrace(None)

# Custom exception hooks:
import sys
def global_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    print(f"Unhandled exception: {exc_type.__name__}: {exc_value}")
    traceback.print_tb(exc_traceback)

sys.excepthook = global_exception_handler
```

---

## 7. Packaging & Distribution

### 7.1 pyproject.toml Structure (PEP 517/518/621)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mypackage"
version = "0.1.0"
description = "A sample package"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.0",
]
[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4", "mypy>=1.10"]
all = ["mypackage[dev]"]

[project.scripts]
mycli = "mypackage.cli:main"

[project.gui-scripts]
mygui = "mypackage.gui:main"

[project.entry-points.console_scripts]
another-cli = "mypackage.other:main"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q --strict-markers"

[tool.mypy]
strict = true
python_version = "3.12"

[tool.coverage.run]
branch = true
source = ["src"]
```

### 7.2 Build Backends

```python
# setuptools — most mature, most features, most complex config
# hatchling — modern, fast, minimal config, good defaults
# flit — ultra-simple, pure Python packages only
# poetry — dependency resolver + build + publish in one tool

# Comparison:
# |               | setuptools | hatchling | flit   | poetry  |
# | C extensions  | yes        | no        | no     | no      |
# | src-layout    | manual     | yes       | yes    | yes     |
# | Editable      | yes        | yes       | yes    | yes     |
# | Lock files    | no         | no        | no     | yes     |
# | Config file   | setup.cfg  | pyproject | pyproject| pyproject |
```

### 7.3 Wheel Tags

```markdown
# Wheel filename format: {name}-{ver}-{pyver}-{abi}-{plat}.whl
# numpy-1.26.0-cp312-cp312-manylinux_2_17_x86_64.whl

# Platform compatibility tags (PEP 425):
# py3-none-any         — pure Python, runs anywhere
# cp312-cp312-macosx_14_0_arm64  — CPython 3.12, macOS 14 ARM
# cp312-abi3-manylinux_2_17_x86_64  — Stable ABI, Linux x86_64
# pp39-pypy39_pp73-macosx_14_0_arm64  — PyPy 3.9

# Audit wheel tags:
# python -m pip debug --verbose | grep "Compatible tags"

# manylinux — standard for Linux wheel compatibility:
# manylinux1 (PEP 513)   — CentOS 5
# manylinux2010 (PEP 571) — CentOS 6
# manylinux2014 (PEP 599) — CentOS 7
# manylinux_2_24         — Debian 9+
# manylinux_2_28         — RHEL 8 / AlmaLinux 8
```

### 7.4 Version Specifiers (PEP 440)

```python
# Exact: ==1.0.0
# Compatible: ~=1.4.2  (>=1.4.2, ==1.4.*)
# Greater: >=1.0, >1.0
# Less: <2.0, <=2.0
# Not equal: !=1.0.1
# Arbitrary equality: === (discouraged)

# Pre-release: >=1.0a1, >=1.0b2, >=1.0rc1
# Pre-release handling: a (alpha) < b (beta) < rc (release candidate) < final
# Post-release: 1.0.post1
# Dev release: 1.0.dev1
# Local version: 1.0+ubuntu.1

# Epoch: 1!1.0  (epoch 1, version 1.0) — for major compatibility breaks
# Examples:
# django>=3.2,<4.0         — Django 3.2.x
# requests>=2.28.0,!=2.31.0
# numpy>=1.26.0rc1          — includes release candidates
# pydantic~=2.0             — 2.0 <= v < 2.1
```

### 7.5 Entry Points

```python
# Entry points are defined in pyproject.toml or setup.cfg:
# [project.scripts]
# my-tool = "mypackage.cli:main"

# This creates a script that does:
# from mypackage.cli import main
# sys.exit(main())

# Entry point groups:
# console_scripts  — CLI tools (cross-platform with .exe wrapper on Windows)
# gui_scripts      — GUI tools (no console window on Windows)
# mypkg.plugins    — Custom plugin discovery
# pytest11         — pytest plugins

# Loading entry points at runtime:
import importlib.metadata

for ep in importlib.metadata.entry_points(group="console_scripts"):
    print(f"{ep.name} = {ep.value}")
    func = ep.load()
    func()
```

---

## 8. Python Implementations

### 8.1 CPython

The reference implementation. Written in C and Python. All other implementations
are measured against CPython behavior.

- **Key files**: `Python/ceval.c` (interpreter), `Objects/` (built-in types),
  `Lib/` (standard library in Python)
- **Memory**: reference counting + generational GC, small-object allocator
- **Performance**: 3.11 brought ~25% speedup (specializing adaptive interpreter);
  3.12 added comprehensions inlining; 3.13 adds JIT (copy-and-patch, experimental)
- **C API**: `Python.h` — stable ABI (PEP 384) for limited API extensions

### 8.2 PyPy

JIT-compiled Python written in RPython (a restricted subset of Python).

- **JIT**: traces hot loops and compiles to machine code (tracing JIT, not method JIT)
- **GC**: no reference counting — uses a generational, incremental, moving GC.
  This means `__del__` is called non-deterministically (GC cycles).
- **Compatibility**: runs most pure Python code. C extension support is partial
  — `cpyext` emulation layer, but slow. `cffi` is preferred.
- **Performance**: 4-7x faster than CPython on pure Python code, slower on C
  extension-heavy code. Higher memory usage due to JIT traces.

```python
# Detect PyPy:
import platform
is_pypy = platform.python_implementation() == "PyPy"

# PyPy-specific:
# - sys._getframe() exists but may be slower
# - gc.get_referents() returns different results (moving GC)
# - ctypes works but is slower
# - cffi is the recommended FFI
```

### 8.3 Jython / IronPython / GraalPy

**Jython:** Python on the JVM. Accesses any Java class. No C extensions. No
support for Python 3.x beyond a 3.8-compatible 2.7 fork; effectively
unmaintained.

**IronPython:** Python on the .NET CLR. Accesses any .NET assembly. No C
extensions. Python 3 support (IronPython 3) targets Python 3.4 compatibility.

**GraalPy:** Python on GraalVM (Truffle framework). Supports `polyglot.eval()`
for calling JS/Ruby/Java from Python. Native image support. C extension support
via GraalVM's LLVM runtime (experimental).

### 8.4 MicroPython / CircuitPython

**MicroPython:** Python 3.4+ subset for microcontrollers. Key differences:
- No `__del__` (timers, interrupts can't hold objects)
- Limited `sys` module, no `ctypes`
- `machine` module for hardware (Pin, I2C, SPI, PWM)
- Async/await supported (uasyncio)
- `gc.collect()` is a best practice after large allocations

**CircuitPython:** Adafruit's fork of MicroPython. Easier USB workflow (appears
as a USB drive, edit code.py directly). More built-in libraries for Adafruit
hardware.

### 8.5 Cinder (Meta)

Meta's performance fork of CPython. Notable features:
- **Immortal objects**: objects that never get deallocated (PEP 683, now in 3.12)
- **Shadowcode**: tracks which opcodes are specialized for better JIT
- **Strict modules**: opt-in stricter semantics (no monkey-patching)
- **Await-aware iterators**: better async generators
- Mostly merged upstream or abandoned after Meta's Python team restructuring.

---

## 9. Key PEPs Reference

### Type System
| PEP | Title | Status |
|-----|-------|--------|
| 484 | Type Hints | Final (3.5) |
| 526 | Syntax for Variable Annotations | Final (3.6) |
| 544 | Protocols: Structural Subtyping | Final (3.8) |
| 560 | Core Support for typing module | Final (3.7) |
| 563 | Postponed Evaluation of Annotations | Final (3.7), reverted in 3.13 |
| 585 | Type Hinting Generics In Standard Collections | Final (3.9) |
| 586 | Literal Types | Final (3.8) |
| 589 | TypedDict: Type Hints for Dictionaries | Final (3.8) |
| 591 | Adding a final qualifier to typing | Final (3.8) |
| 604 | Allow writing union types as X \| Y | Final (3.10) |
| 612 | Parameter Specification Variables | Final (3.10) |
| 613 | Explicit Type Aliases | Final (3.10) |
| 646 | Variadic Generics | Final (3.11) |
| 647 | User-Defined Type Guards | Final (3.10) |
| 655 | Required[] for TypedDict total=False | Final (3.11) |
| 673 | Self Type | Final (3.11) |
| 675 | Arbitrary Literal String Type | Final (3.11) |
| 681 | Data Class Transforms | Final (3.11) |
| 692 | Using TypedDict for **kwargs typing | Final (3.12) |
| 695 | Type Parameter Syntax (class Foo[T]) | Final (3.12) |
| 698 | Override Decorator for Static Typing | Final (3.12) |
| 702 | Marking deprecations using the type system | Final (3.13) |
| 705 | TypedDict: Read-only items | Accepted (3.13) |
| 715 | Disabling bpo-46769 by default | Final (3.14) |

### Async/Await
| PEP | Title | Status |
|-----|-------|--------|
| 492 | Coroutines with async and await syntax | Final (3.5) |
| 525 | Asynchronous Generators | Final (3.6) |
| 530 | Asynchronous Comprehensions | Final (3.6) |
| 567 | Context Variables | Final (3.7) |
| 615 | Support for the IANA Time Zone Database | Final (3.9) |

### Packaging
| PEP | Title | Status |
|-----|-------|--------|
| 440 | Version Identification and Dependency Specification | Final |
| 517 | A build-system independent format for source trees | Final |
| 518 | Specifying Minimum Build System Requirements | Final |
| 621 | Storing project metadata in pyproject.toml | Final |
| 660 | Editable installs for pyproject.toml based builds | Final |
| 668 | Marking Python base environments as "externally managed" | Final |
| 708 | Extending the Repository API to mitigate dependency confusion | Draft |
| 723 | Single-file scripts with inline metadata | Accepted |
| 735 | Dependency Groups in pyproject.toml | Accepted |

### Performance
| PEP | Title | Status |
|-----|-------|--------|
| 659 | Specializing Adaptive Interpreter | Final (3.11) |
| 703 | Making the Global Interpreter Lock Optional | Accepted (3.13, experimental) |
| 744 | Bytecode optimization for more efficient executables | Draft (JIT) |

### Language Features
| PEP | Title | Status |
|-----|-------|--------|
| 572 | Assignment Expressions (`:=` walrus operator) | Final (3.8) |
| 584 | Union Operators for dict (`|`, `|=`) | Final (3.9) |
| 614 | Relaxing Grammar Restrictions On Decorators | Final (3.9) |
| 616 | String methods to remove prefixes and suffixes | Final (3.9) |
| 634 | Structural Pattern Matching: Specification | Final (3.10) |
| 636 | Structural Pattern Matching: Tutorial | Final (3.10) |
| 701 | Syntactic formalization of f-strings | Final (3.12) |
| 678 | Enriching Exceptions with Notes | Final (3.11) |

### Import System
| PEP | Title | Status |
|-----|-------|--------|
| 302 | New Import Hooks | Final (2.3) |
| 328 | Multi-line and Absolute/Relative Imports | Final (2.5) |
| 366 | Main module explicit relative imports | Final (2.6) |
| 420 | Implicit Namespace Packages | Final (3.3) |
| 451 | ModuleSpec for import system | Final (3.4) |
| 690 | Lazy Imports | Draft |

### Other Key PEPs
| PEP | Title | Status |
|-----|-------|--------|
| 8 | Style Guide for Python Code | Active |
| 20 | The Zen of Python | Active |
| 257 | Docstring Conventions | Active |
| 343 | The "with" Statement | Final (2.5) |
| 380 | Syntax for Delegating to a Subgenerator | Final (3.3) |
| 435 | Adding an Enum type to the Python standard library | Final (3.4) |
| 557 | Data Classes | Final (3.7) |
| 587 | Python Initialization Configuration | Final (3.8) |
| 594 | Removing dead batteries from the standard library | Final (3.13) |
| 649 | Deferred Evaluation Of Annotations | Draft |
| 554 | Multiple Interpreters in the Stdlib | Draft |
| 734 | Multiple Interpreters — Message Passing | Draft |
| 3156 | Asynchronous IO Support Rebooted (asyncio) | Final (3.4) |
| 3119 | Introducing Abstract Base Classes | Final (3.0) |
| 3129 | Class Decorators | Final (3.0) |
| 3135 | New Super | Final (3.0) |
| 3180 | Function Decorators | Final (2.4) |

---

## 10. Package Search & Discovery

### 10.1 PyPI (Python Package Index)

```python
# PyPI JSON API (warehouse):
# GET https://pypi.org/pypi/{package}/json
# Returns: info dict, releases dict, urls list

import requests
resp = requests.get("https://pypi.org/pypi/requests/json").json()
print(resp["info"]["summary"])   # Package description
print(resp["info"]["version"])   # Latest version
print(resp["info"]["classifiers"])  # Trove classifiers

# Simple index (PEP 503):
# https://pypi.org/simple/{package}/
# Returns: HTML page with download links

# RSS feeds:
# https://pypi.org/rss/updates.xml      — latest updates
# https://pypi.org/rss/packages.xml     — new packages
# https://pypi.org/rss/project/{name}/releases.xml

# Trove classifiers (standard taxonomy):
# Development Status :: 5 - Production/Stable
# Intended Audience :: Developers
# License :: OSI Approved :: MIT License
# Programming Language :: Python :: 3 :: Only
# Topic :: Software Development :: Libraries :: Python Modules
```

### 10.2 Pip Search Alternatives

```bash
# pip search was removed (PyPI XML-RPC API discontinued in 2020)
# Alternatives:

# pipgrip — dependency tree resolver:
# pip install pipgrip
# pipgrip --tree fastapi

# pypi-simple — query the Simple API:
# pip install pypi-simple
# pypi-simple search <query>

# pip_search — pip search clone using PyPI JSON:
# pip install pip-search
# pip_search <query>

# pipdeptree — show dependency tree of installed packages:
# pip install pipdeptree
# pipdeptree

# pip-audit — audit dependencies for known vulnerabilities:
# pip install pip-audit
# pip-audit
```

### 10.3 Conda Ecosystem

```bash
# conda manages environments AND non-Python dependencies (C libs, compilers)
# conda-forge: community-maintained channel, >25K packages

# conda vs pip:
# conda: binary packages, manages system deps, env isolation with hard links
# pip: source/wheel packages, Python-only, requires system deps pre-installed

# Best practice: install via conda first, fall back to pip
# conda install numpy scipy pandas        # C-optimized, tested together
# pip install some-pure-python-package    # Only available on PyPI

# Environment management:
# conda create -n myenv python=3.12
# conda env export > environment.yml      # Cross-platform
# conda env export --from-history > environment.yml  # Pinned only
# conda list --explicit > spec-file.txt   # Exact replication
```

### 10.4 Security Scanning

```bash
# pip-audit — vuln scanning for installed packages (uses PyPA advisory DB):
pip install pip-audit
pip-audit                              # Audit current environment
pip-audit -r requirements.txt         # Audit from requirements
pip-audit --fix                       # Auto-upgrade vulnerable packages

# safety — commercial + free tier:
pip install safety
safety check
safety check -r requirements.txt

# bandit — static analysis for security issues in your code:
pip install bandit
bandit -r src/                        # Recursive scan
bandit -c bandit.yaml -r src/         # With config
# Common bandit findings: B101 (assert), B301 (pickle), B602 (subprocess)

# Dependency confusion protection:
# Use --index-url (not --extra-index-url) for private packages
# Pin hashes in requirements.txt:
# requests==2.31.0 \
#   --hash=sha256:abc123...
```

### 10.5 Pip's Dependency Resolver

```python
# 2020 resolver (pip 20.3+): proper backtracking dependency resolution
# Previously: "first found wins" — could produce broken environments

# Resolution strategy:
# 1. Collect all requirements (direct + transitive)
# 2. Build dependency graph
# 3. Backtrack when conflicts found (SAT-solver-like)
# 4. Generate install plan

# Diagnostics:
# pip install --verbose           # Show resolution steps
# pip check                       # Verify installed packages are consistent
# pip install --dry-run --report report.json  # Generate install plan JSON
```

---

## 11. File Structure Conventions

### 11.1 src-layout vs Flat Layout

```markdown
# src-layout (RECOMMENDED for libraries):
# Prevents accidentally importing the package from the source directory
# Forces you to install the package to test it
mypackage/
  pyproject.toml
  src/
    mypackage/
      __init__.py
      module_a.py
      subpackage/
        __init__.py
        module_b.py
  tests/
    test_module_a.py
    test_subpackage/
      test_module_b.py

# flat layout (simpler for applications):
mypackage/
  pyproject.toml
  mypackage/
    __init__.py
    module_a.py
  tests/
    test_module_a.py
```

### 11.2 tests/ Mirroring

```python
# tests/ should mirror src/ structure:
# src/mypackage/db/models.py   -> tests/unit/db/test_models.py
# src/mypackage/api/routes.py   -> tests/unit/api/test_routes.py
# src/mypackage/services/user.py -> tests/unit/services/test_user.py

# Test hierarchy:
tests/
  conftest.py              # Global fixtures
  unit/                    # Fast, isolated, no I/O
  integration/             # Multiple components, real DB/files
  e2e/                     # Full system, external services
  fixtures/                # Test data files (JSON, YAML, SQL dumps)
```

### 11.3 __init__.py Conventions

```python
# __init__.py serves multiple roles:

# 1. Re-exports — define the public API:
__all__ = ["Client", "Server", "connect", "Protocol"]

from .client import Client
from .server import Server
from .connection import connect
from .protocols import Protocol

# 2. Lazy imports — defer expensive submodule loading:
import importlib

def __getattr__(name: str):
    if name in ("_heavy_module",):
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 3. Package metadata:
__version__ = "1.0.0"
__author__ = "Team"
__all__ = ["version"]

# 4. Initialization:
import logging
_logger = logging.getLogger(__name__)

# Common anti-patterns to avoid in __init__.py:
# - Heavy imports at module level (slows down all imports)
# - Side effects (network calls, file creation)
# - Wildcard imports from submodules (confusing API surface)
```

### 11.4 Config Files Layout

```markdown
# Typical Python project config files:
pyproject.toml           # Build, lint, typecheck, test config (PEP 621)
setup.cfg                # Declarative setuptools config (legacy, still valid)
tox.ini                  # Test runner across Python versions
.flake8                  # Flake8 linting config
.pre-commit-config.yaml  # Pre-commit hooks
.ruff.toml               # Ruff config (alternative to [tool.ruff] in pyproject)
.mypy.ini                # MyPy config (alternative to [tool.mypy] in pyproject)
.coveragerc              # Coverage.py config (alternative to [tool.coverage])

# Prefer pyproject.toml for all tool config when possible:
[tool.ruff]
[tool.mypy]
[tool.pytest.ini_options]
[tool.coverage]
[tool.black]
[tool.isort]
```

### 11.5 Project Structure Templates

```python
# LIBRARY:
libproject/
  pyproject.toml
  README.md
  LICENSE
  CHANGELOG.md
  src/
    libproject/
      __init__.py          # Package docstring, __version__, re-exports
      _version.py          # Single source of truth for version
      py.typed             # PEP 561 — marks package as typed
      core.py
      utils.py
      subpkg/
        __init__.py
  tests/
    conftest.py
    test_core.py
    test_utils.py
  .github/
    workflows/
      ci.yml

# APPLICATION:
myapp/
  pyproject.toml
  README.md
  myapp/
    __init__.py
    __main__.py            # python -m myapp
    cli.py                 # Click/argparse entry point
    config.py              # Configuration loading
    services/
    models/
    templates/
  tests/
  scripts/                 # Dev tooling scripts

# CLI TOOL:
mycli/
  pyproject.toml
  src/
    mycli/
      __init__.py
      main.py              # def main(): ... (entry point)
      commands/
        __init__.py
        install.py
        build.py
      utils.py
  tests/
  [project.scripts]
  mycli = "mycli.main:main"
```

---

## 12. Common Pitfalls & Gotchas

### 12.1 Mutable Default Arguments

```python
# WRONG — the list is created once, shared across all calls:
def append_to(element, target=[]):
    target.append(element)
    return target

print(append_to(1))  # [1]
print(append_to(2))  # [1, 2] — SURPRISE!

# RIGHT — create a new object each call:
def append_to(element, target=None):
    if target is None:
        target = []
    target.append(element)
    return target
```

### 12.2 Late Binding Closures

```python
# WRONG — all lambdas capture the same variable:
funcs = [lambda: i for i in range(5)]
print([f() for f in funcs])  # [4, 4, 4, 4, 4]

# RIGHT — capture via default argument:
funcs = [lambda i=i: i for i in range(5)]
print([f() for f in funcs])  # [0, 1, 2, 3, 4]

# Also works with functools.partial:
from functools import partial
funcs = [partial(print, i) for i in range(5)]
```

### 12.3 `is` vs `==`

```python
# is: identity (same object in memory)
# ==: equality (same value)

a = [1, 2, 3]
b = [1, 2, 3]
print(a == b)  # True — same value
print(a is b)  # False — different objects

# Use is for: None, True, False, sentinel objects
# Use == for: everything else

# Singletons guarantee is works:
print(None is None)     # True
# But beware:
a = 256; b = 256; print(a is b)  # True (interned)
a = 257; b = 257; print(a is b)  # Implementation-dependent (CPython: may be True in same compilation unit)
```

### 12.4 Shallow vs Deep Copy

```python
import copy

original = [[1, 2, 3], [4, 5, 6]]
shallow = copy.copy(original)  # New outer list, same inner lists
deep = copy.deepcopy(original) # Entirely independent

original[0][0] = 99
print(shallow[0])  # [99, 2, 3] — inner list IS shared
print(deep[0])     # [1, 2, 3]  — inner list is NOT shared

# List slice, list(), dict() — shallow copies:
shallow_via_slice = original[:]

# Common bug: default mutable in copy
class Node:
    def __init__(self, children=None):
        self.children = children or []  # OK: creates new list per call
```

### 12.5 Circular Imports

```python
# WRONG — circular import:
# a.py:
#   from b import func_b
#   def func_a(): return func_b()
#
# b.py:
#   from a import func_a
#   def func_b(): return func_a()

# FIXES (pick one):

# 1. Import inside function (lazy import):
# a.py:
#   def func_a():
#       from b import func_b
#       return func_b()

# 2. Import the module, not the name:
# a.py:
#   import b
#   def func_a(): return b.func_b()

# 3. Restructure — extract shared dependency to c.py:
# c.py:
#   shared = ...
# a.py: from c import shared
# b.py: from c import shared
```

### 12.6 `__del__` and Garbage Collection

```python
# __del__ is called when reference count hits 0 (CPython)
# BUT: it can prevent garbage collection of cycles:

class Node:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        print(f"Deleting {self.name}")

# No cycle — __del__ called immediately:
a = Node("a")  # Deleting a (immediate, if no other references)

# Cycle — __del__ may NEVER be called:
x = Node("x")
y = Node("y")
x.child = y
y.child = x
del x, y  # __del__ NOT called (cyclic references)
gc.collect()  # STILL not called — GC won't collect objects with __del__ cycles

# NEVER use __del__ for cleanup; use context managers instead:
class Resource:
    def __init__(self):
        self.handle = acquire()

    def close(self):
        if self.handle:
            release(self.handle)
            self.handle = None

    def __del__(self):
        # Last-resort safety net, but don't rely on it
        if self.handle:
            release(self.handle)

# Right pattern:
with Resource() as r:  # __enter__ / __exit__
    r.do_work()
```

### 12.7 Thread Safety

```python
# Thread-unsafe operations in Python (even with GIL):
# - x += 1                  # Read-modify-write, not atomic
# - list.append()            # Generally safe but not guaranteed
# - dict[key] = value        # Generally safe if key is hashable
# - del dict[key]            # Can cause segfault during iteration in another thread

# Thread-safe approaches:

# 1. threading.Lock:
lock = threading.Lock()
counter = 0

def increment():
    global counter
    with lock:
        counter += 1

# 2. queue.Queue — thread-safe by design:
q = queue.Queue()
q.put(item)   # Thread-safe
q.get()       # Thread-safe (blocks if empty)

# 3. concurrent.futures — high-level API:
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(worker, arg) for arg in args]

# 4. threading.local — per-thread storage:
thread_data = threading.local()
thread_data.user = current_user()  # Each thread has its own

# 5. collections.deque — thread-safe append/pop:
from collections import deque
dq = deque(maxlen=1000)
dq.append(item)  # Thread-safe

# Common non-thread-safe traps:
# - Printing from threads (output interleaving)
# - Logging from threads (use QueueHandler)
# - File writes from threads (use a single writer thread)
```

### 12.8 Floating Point Precision

```python
# IEEE 754 double precision (64-bit):
# 53 bits of mantissa (~15-17 decimal digits)

print(0.1 + 0.2)        # 0.30000000000000004
print(0.1 + 0.2 == 0.3) # False

# Solutions:

# 1. decimal — exact decimal arithmetic:
from decimal import Decimal
print(Decimal("0.1") + Decimal("0.2"))  # 0.3

# 2. Fractions — exact rational arithmetic:
from fractions import Fraction
print(Fraction(1, 10) + Fraction(2, 10))  # 3/10
print(Fraction(1, 3) + Fraction(2, 3))    # 1

# 3. math.isclose — comparison with tolerance:
import math
print(math.isclose(0.1 + 0.2, 0.3))  # True
print(math.isclose(0.1 + 0.2, 0.3, rel_tol=1e-15))

# 4. Round appropriately for display:
print(f"{0.1 + 0.2:.1f}")  # 0.3

# Known edge cases:
print(float('inf'))   # Infinity
print(float('nan'))   # NaN (NaN != NaN, use math.isnan())
print(1.0 / 0.0)      # inf
print(-1.0 / 0.0)     # -inf
print(float('inf') > 1e308)  # True
```

### 12.9 Other Common Gotchas

```python
# Modifying a list while iterating:
items = [1, 2, 3, 4, 5]
for item in items:
    if item % 2 == 0:
        items.remove(item)  # WRONG — skips elements
# Fix: iterate over a copy or use list comprehension:
items = [x for x in items if x % 2 != 0]

# Default argument evaluation at definition time, not call time:
from datetime import datetime
def log(msg, timestamp=datetime.now()):  # WRONG — evaluated once at def
    print(f"[{timestamp}] {msg}")

# Class variable vs instance variable:
class Bad:
    items = []  # CLASS variable — shared across all instances
b1 = Bad(); b2 = Bad()
b1.items.append(1)
print(b2.items)  # [1] — surprise!

# Unicode vs bytes:
# Python 3: str is Unicode, bytes is raw bytes
s = "café"  # str (Unicode)
b = s.encode("utf-8")  # bytes: b'caf\xc3\xa9'
s2 = b.decode("utf-8")  # back to str
# Never: b"abc" + "def"  # TypeError
# Use encoding="utf-8" for all text I/O

# Chained comparisons are allowed:
print(1 < x < 10)      # Equivalent to (1 < x) and (x < 10)
print(a == b == c)     # Equivalent to (a == b) and (b == c)

# Operator precedence gotcha:
print(not a == b)      # not (a == b) — fine
print(a == not b)      # SyntaxError — "not" binds loosely

# Multiple context managers (3.1+):
with open("a.txt") as f1, open("b.txt") as f2:
    pass
# 3.10+ parenthesized context managers:
with (
    open("a.txt") as f1,
    open("b.txt") as f2,
):
    pass
```
