---
name: python-expert
description: Use when writing, debugging, reviewing, or discussing Python code, Python packaging, CPython internals, PyPy, Jython, asyncio, GIL behavior, type annotations, or any Python ecosystem concern. Covers PEPs, common patterns, performance, testing, profiling, security pitfalls, and packaging. Trigger keywords: Python, CPython, PyPy, Jython, MicroPython, PEP, GIL, asyncio, pytest, pyproject.toml, pip, PyPI, mypy, ruff.
---

# Python Expert

This skill is the complete, self-contained Python knowledge base for gludd agents.
Every section is written to be executable, accurate, and comprehensive. No external
references needed -- the skill IS the knowledge.

---

## 1. CPython Internals

### 1.1 The Global Interpreter Lock (GIL)

The GIL is a mutex held by the thread executing Python bytecode in the CPython
interpreter. Only one thread may execute Python bytecode at a time, regardless of
core count.

**What the GIL protects:**
- Reference counting (ob_refcnt) -- without it, concurrent INCREF/DECREF would
  corrupt object lifetimes.
- The small-object allocator (obmalloc) -- arena/pool/block free lists are not
  thread-safe.
- Every C-API function that manipulates Python objects.

**When the GIL is released:**
- I/O operations (file read/write, socket send/recv). The C code calls
  Py_BEGIN_ALLOW_THREADS / Py_END_ALLOW_THREADS around blocking syscalls.
- Long-running C extension operations that explicitly release it (NumPy, hashlib).
- Every sys.setswitchinterval() seconds (default 0.005s, i.e., 5ms).

**Measuring GIL contention:**
    import sys
    import threading
    import time

    def cpu_bound():
        start = time.perf_counter()
        x = 0
        while time.perf_counter() - start < 1.0:
            x += 1
        return x

    single = cpu_bound()  # baseline

    counts = []
    def worker():
        counts.append(cpu_bound())

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()
    print(f"Ratio: {sum(counts) / single:.2f}")  # ~1.0, not 2.0

### 1.2 Python 3.13 Free-Threading (PEP 703)

Experimental build option --disable-gil allows multiple threads to execute Python
bytecode concurrently.

**Key changes:**
- **Biased reference counting:** Per-thread local refcount + shared refcount.
- **Thread-safe allocators:** mimalloc replaces obmalloc, each thread has own heap.
- **Thread-safe collections:** Per-object locks on dict/list/set.
- Py_BEGIN_ALLOW_THREADS / Py_END_ALLOW_THREADS become no-ops.

```python
import sysconfig
print(sysconfig.get_config_var('Py_GIL_DISABLED'))  # 1 in free-threaded
import sys
print(sys._is_gil_enabled())  # False in 3.13 free-threaded
```

### 1.3 Per-Interpreter GIL (PEP 554, PEP 734)

Subinterpreters each get their own GIL. Share no mutable state -- only channels.
PEP 734 extends with higher-level API and per-interpreter Queues.

```python
import _interpreters as interpreters

iid = interpreters.create()
interpreters.run_string(iid, "print('hello from subinterpreter')")

recv, send = interpreters.channel_create()
interpreters.run_string(iid, 'import _interpreters as i; i.channel_send(' + str(send) + ', b"data")')
data = interpreters.channel_recv(recv)
print(data)  # b'data from sub'
interpreters.destroy(iid)
```

### 1.4 Reference Counting and Garbage Collection

Every CPython object starts with: PyObject { ob_refcnt; *ob_type; }.
Py_INCREF(obj) on new reference; Py_DECREF(obj) on dereference.
When refcount reaches zero, tp_dealloc frees the object.

**Generational GC** handles reference cycles (A->B->A):
- Gen 0: young objects, collected when allocations - deallocations > 700
- Gen 1: survived one collection, collected when Gen0 collections > 10
- Gen 2: long-lived, collected when Gen1 collections > 10

Objects that CAN form cycles: list, dict, set, user classes (with __dict__).
Objects that CANNOT: int, float, str, tuple (immutable).

```python
import sys, gc

a = []
print(sys.getrefcount(a))  # 2 (variable + getrefcount arg)
b = a
print(sys.getrefcount(a))  # 3

print(gc.get_threshold())  # (700, 10, 10)
gc.collect()     # force full collection
gc.disable()     # dangerous -- cycles leak
gc.enable()
```

### 1.5 The Small-Object Allocator (obmalloc)

Specialized allocator for objects < 512 bytes. Hierarchy:
Arena (256 KB) -> Pool (4 KB) -> Block (size class: multiples of 8)

**Size classes:**
| Size | Examples |
|------|----------|
| 24-32 | Small tuples |
| 48-56 | int (28B on 64-bit), small str |
| 64-80 | float, complex |
| 144-160 | Small dict entries |
| > 512 | System malloc |

```python
import sys
print(sys.getsizeof(42))          # 28
print(sys.getsizeof('hello'))     # 54
print(sys.getsizeof([]))          # 56
print(sys.getsizeof([1,2,3]))     # 80
```

### 1.6 Stack Frames and Code Objects

```python
import dis, inspect

def add(a: int, b: int) -> int:
    return a + b

code = add.__code__
print(code.co_name)       # 'add'
print(code.co_varnames)   # ('a', 'b')
print(code.co_argcount)   # 2
print(code.co_consts)     # (None,)

def outer(x):
    def inner(y):
        frame = inspect.currentframe()
        print(f'func: {frame.f_code.co_name}')
        return x + y
    return inner(10)

outer(5)
```

### 1.7 Bytecode and the dis Module

Python compiles to stack-machine bytecode. Each instruction = opcode + argument.
3.11+: word-sized instructions (2 bytes each), specialized adaptive interpreter.

```python
import dis

def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)

dis.dis(factorial)

# Show all instructions
for inst in dis.get_instructions(factorial):
    print(f'{inst.offset:4d} {inst.opname:20s} {inst.argrepr}')
```

**Common opcodes (3.11+):** LOAD_FAST, LOAD_CONST, LOAD_GLOBAL, STORE_FAST,
BINARY_OP, COMPARE_OP, POP_JUMP_IF_FALSE, CALL, RETURN_VALUE, RESUME.

### 1.8 ceval.c Main Loop

The interpreter core is _PyEval_EvalFrameDefault in Python/ceval.c. Pre-3.11 was
a giant switch; 3.11+ uses computed-goto dispatch in Python/bytecodes.c.

**PEP 659 specialization (3.11+):** Common opcodes are specialized at runtime:
- LOAD_GLOBAL -> LOAD_GLOBAL_MODULE or LOAD_GLOBAL_BUILTIN
- BINARY_ADD -> BINARY_ADD_INT or BINARY_ADD_FLOAT
- Specialized ops invalidated when type assumptions change.

### 1.9 Memory Model Summary

Allocation: PyObject_New -> _PyObject_Malloc -> obmalloc (if < 512B) or malloc()
Deallocation: Py_DECREF -> (refcount==0) -> tp_dealloc -> _PyObject_Free
Arenas never released to OS in standard builds. Set PYTHONMALLOC=malloc to bypass.

---

## 2. Type System Deep Dive

### 2.1 Method Resolution Order (MRO)

Python uses C3 linearization. Ensures subclasses come before bases; preserves base
class order; no class visited before parents. Inconsistent MRO raises TypeError.

```python
class A: pass
class B(A): pass
class C(A): pass
class D(B, C): pass
print(D.__mro__)  # (D, B, C, A, object)
```

**super() cooperative multiple inheritance:**
```python
class Base:
    def __init__(self, name):
        self.name = name

class HasID(Base):
    def __init__(self, name, id):
        super().__init__(name)  # calls next in MRO, NOT necessarily Base
        self.id = id

class HasEmail(Base):
    def __init__(self, name, email):
        super().__init__(name)
        self.email = email

class User(HasID, HasEmail):
    def __init__(self, name, id, email):
        super().__init__(name, id, email)

u = User('Alice', 1, 'alice@example.com')
# MRO: User -> HasID -> HasEmail -> Base -> object
# HasID.super() calls HasEmail.__init__
print(u.name, u.id, u.email)
```

**How super() works:** Returns a proxy skipping the current class in MRO.
The __class__ cell variable (auto-created when super() used without args) provides
context. In Python 3, super() = super(__class__, self).

### 2.2 Descriptors

Object implementing __get__ / __set__ / __delete__. Data descriptors (with __set__
or __delete__) take precedence over instance __dict__. Non-data descriptors (only
__get__) are shadowed by instance __dict__.

```python
class DataDescriptor:
    def __get__(self, obj, objtype=None):
        return 42
    def __set__(self, obj, value):
        print(f'Setting {value}')

class NonDataDescriptor:
    def __get__(self, obj, objtype=None):
        return 99

class MyClass:
    data_attr = DataDescriptor()
    nondata_attr = NonDataDescriptor()

obj = MyClass()
print(obj.data_attr)          # 42
obj.__dict__['data_attr'] = 100
print(obj.data_attr)          # STILL 42 (data descriptor wins)

print(obj.nondata_attr)       # 99
obj.__dict__['nondata_attr'] = 100
print(obj.nondata_attr)       # 100 (instance dict shadows non-data)
```

**Descriptor validation with __set_name__ (3.6+):**
```python
class Validated:
    def __init__(self, min_value=None, max_value=None, type_=None):
        self.min_value = min_value
        self.max_value = max_value
        self.type_ = type_

    def __set_name__(self, owner, name):
        self._name = name
        self._storage_name = f'_{name}'

    def __get__(self, obj, objtype=None):
        if obj is None: return self
        return obj.__dict__.get(self._storage_name)

    def __set__(self, obj, value):
        if self.type_ and not isinstance(value, self.type_):
            raise TypeError(f'{self._name} must be {self.type_.__name__}')
        if self.min_value is not None and value < self.min_value:
            raise ValueError(f'{self._name} >= {self.min_value} required')
        obj.__dict__[self._storage_name] = value

class Person:
    age = Validated(min_value=0, max_value=150, type_=int)
    name = Validated(type_=str)
    def __init__(self, name, age):
        self.name = name
        self.age = age
```

**property is a data descriptor** (implements __get__, __set__, __delete__):
```python
class Circle:
    def __init__(self, radius):
        self._radius = radius

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        if value <= 0:
            raise ValueError('must be positive')
        self._radius = value

print(type(Circle.radius))  # <class 'property'>
```

### 2.3 Metaclasses

Metaclass is the type of a class; type is the default. Use __init_subclass__
(PEP 487) for registration/validation hooks -- simpler and preferred. Use metaclass
when you need to control class creation itself (modify namespace, custom __call__).

```python
class PluginBase:
    _registry = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = kwargs.get('name', cls.__name__)
        PluginBase._registry[name] = cls

class PDFExporter(PluginBase, name='pdf'): pass
class CSVExporter(PluginBase, name='csv'): pass
print(PluginBase._registry)  # {'pdf': ..., 'csv': ...}

# Metaclass for singleton:
class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

class Database(metaclass=SingletonMeta):
    def __init__(self, uri):
        self.uri = uri

db1 = Database('postgres://a')
db2 = Database('postgres://b')  # ignored
print(db1 is db2)  # True
```

**__new__ vs __init__ in metaclasses:** __new__(mcs, name, bases, namespace) returns
the new class; __init__(cls, name, bases, namespace) for additional setup.

### 2.4 __getattr__ vs __getattribute__

__getattribute__ called for EVERY attribute access (use object.__getattribute__
inside it). __getattr__ only called when __getattribute__ raises AttributeError.

```python
class LazyLoader:
    def __getattr__(self, name):
        if name == 'full_name':
            return 'Jane Doe'
        raise AttributeError(name)

class LoggingProxy:
    def __init__(self, target):
        object.__setattr__(self, '_target', target)
    def __getattribute__(self, name):
        t = object.__getattribute__(self, '_target')
        return getattr(t, name)
```

### 2.5 __slots__

Eliminates per-instance __dict__, saving ~50% memory. Inherited classes need their
own __slots__ or get __dict__ back. Access is C-level offset (faster than dict).

```python
import sys

class WithSlots:
    __slots__ = ('x', 'y')
    def __init__(self, x, y): self.x = x; self.y = y

class WithoutSlots:
    def __init__(self, x, y): self.x = x; self.y = y

ws = WithSlots(1, 2)
wo = WithoutSlots(1, 2)
print(sys.getsizeof(wo), sys.getsizeof(wo.__dict__))  # ~48 + ~104
print(sys.getsizeof(ws))                              # ~48
print(hasattr(ws, '__dict__'))  # False

# Inheritance: child without __slots__ gets __dict__ back
class Parent:
    __slots__ = ('a',)
class Child(Parent): pass  # has __dict__!
```

### 2.6 Protocols (Structural Subtyping)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class SupportsClose(Protocol):
    def close(self) -> None: ...

class FileLike:
    def read(self, size=-1): return 'data'
    def close(self): pass

print(isinstance(FileLike(), SupportsClose))  # True (structural)

# Generic protocols
T = TypeVar('T')
class Container(Protocol[T]):
    def __contains__(self, item: T) -> bool: ...
```

### 2.7 ABCs (collections.abc)

Hierarchy: Container -> Iterable -> Iterator, Sized, Collection, Sequence,
MutableSequence, Mapping, MutableMapping, Set, MutableSet, Callable.

```python
from collections.abc import Sequence, Set, Mapping
print(isinstance([1,2,3], Sequence))  # True
print(isinstance({1,2,3}, Set))       # True
print(isinstance({}, Mapping))        # True

# Virtual subclass registration
from abc import ABC, abstractmethod
class Storage(ABC):
    @abstractmethod
    def get(self, key): ...
    @abstractmethod
    def put(self, key, value): ...

class DictStorage:
    def get(self, key): return self._d.get(key)
    def put(self, key, value): self._d[key] = value
Storage.register(DictStorage)
print(isinstance(DictStorage(), Storage))  # True
```

### 2.8 Generics

```python
from typing import TypeVar, Generic, ParamSpec, Concatenate, Self, Callable

T = TypeVar('T')                  # invariant
T_co = TypeVar('T_co', covariant=True)
T_contra = TypeVar('T_contra', contravariant=True)

class Stack(Generic[T]):
    def __init__(self): self._items: list[T] = []
    def push(self, item: T) -> None: self._items.append(item)
    def pop(self) -> T: return self._items.pop()

# ParamSpec -- preserves decorator signatures (PEP 612)
P = ParamSpec('P'); R = TypeVar('R')
def log(f: Callable[P, R]) -> Callable[P, R]:
    def w(*args: P.args, **kwargs: P.kwargs) -> R:
        return f(*args, **kwargs)
    return w

# Self type (3.11+) -- for method chaining in subclasses
class Builder:
    def set_x(self, x: int) -> Self:
        self._x = x
        return self
```

### 2.9 Type Narrowing and Special Types

```python
from typing import TypeGuard, TypeIs, Never, NoReturn, final, override

# TypeGuard (PEP 647) -- user-defined narrowing
def is_str_list(v: list[object]) -> TypeGuard[list[str]]:
    return all(isinstance(x, str) for x in v)

# TypeIs (PEP 742, 3.13) -- narrower, provides both branches
def is_str(v: object) -> TypeIs[str]:
    return isinstance(v, str)

# Never -- bottom type (function never returns normally)
def halt(msg: str) -> Never:
    raise SystemExit(msg)

# @final -- cannot subclass (class) or override (method)
@final
class ImmutableConfig: pass

# @override (PEP 698) -- ensures method overrides parent
class Derived(Base):
    @override
    def process(self, data: bytes) -> int: ...
```

### 2.10 Variance

Covariant (read-only): Sub[Child] subtype of Sub[Parent].
Contravariant (write-only): Sub[Parent] subtype of Sub[Child].
Invariant (default, read+write): no subtype relationship.

---

## 3. asyncio and Concurrency

### 3.1 The Event Loop

```python
import asyncio

async def main():
    await asyncio.sleep(0.1)
    return 42

result = asyncio.run(main())  # creates loop, runs, cancels tasks, closes
print(result)

# Inside a coroutine:
loop = asyncio.get_running_loop()  # raises if no loop running
# Never nest asyncio.run() -- RuntimeError in 3.9+
```

### 3.2 Coroutines and Tasks

```python
async def fetch(url: str) -> str:
    await asyncio.sleep(1)
    return f'data from {url}'

async def task_demo():
    # create_task runs in background on current loop
    t1 = asyncio.create_task(fetch('a'))
    t2 = asyncio.create_task(fetch('b'))
    print('Working...')
    r1, r2 = await t1, await t2

    # gather -- run multiple concurrently
    results = await asyncio.gather(
        fetch('x'), fetch('y'), fetch('z'),
        return_exceptions=True)

    # as_completed -- process as each finishes
    tasks = [asyncio.create_task(fetch(f'url{i}')) for i in range(5)]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        print(f'Got: {result}')

    # TaskGroup (3.11+) -- structured concurrency
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(fetch('a'))
        t2 = tg.create_task(fetch('b'))
    # Both completed (or cancelled) here
```

### 3.3 Task Cancellation

```python
async def long_op():
    try:
        await asyncio.sleep(10)
        return 'done'
    except asyncio.CancelledError:
        print('Cancelled! Cleaning up...')
        await asyncio.sleep(0.1)  # cleanup
        raise  # propagate

# Cancel: task.cancel(); then try: await task
# Shield: asyncio.shield(task) protects from outer cancellation
```

### 3.4 Synchronization Primitives

```python
# Lock -- mutual exclusion
lock = asyncio.Lock()
async with lock:
    pass  # one coroutine at a time

# Semaphore -- limit concurrency (e.g., max 3 concurrent requests)
sem = asyncio.Semaphore(3)
async with sem:
    await fetch(url)

# Event -- signal between coroutines
event = asyncio.Event()
await event.wait()  # blocks until event.set()
event.set()         # unblocks all waiters

# Queue -- producer-consumer
q = asyncio.Queue(maxsize=10)
await q.put(item); item = await q.get()
await q.join()  # wait until all items processed
```

### 3.5 Thread Safety and Executors

```python
import time, asyncio
import concurrent.futures

def blocking_io(n: int) -> int:
    time.sleep(0.5)  # blocks the OS thread
    return n * 2

async def demo():
    loop = asyncio.get_running_loop()

    # ThreadPoolExecutor (default) -- for blocking I/O
    r = await loop.run_in_executor(None, blocking_io, 42)

    # Simpler in 3.9+:
    r = await asyncio.to_thread(blocking_io, 42)

    # ProcessPoolExecutor -- for CPU-bound
    with concurrent.futures.ProcessPoolExecutor() as pool:
        r = await loop.run_in_executor(pool, cpu_heavy, 10_000_000)

    # Schedule from another thread:
    loop.call_soon_threadsafe(lambda: print('from thread'))
```

### 3.6 Common Pitfalls

1. **Forgotten await:** fetch() returns a coroutine object, doesn't execute.
2. **Task exception swallowing:** If you never await a task, Python 3.8+
   logs a warning but the exception is lost.
3. **Blocking the loop:** Never call time.sleep() or requests.get() in async
   code. Use await asyncio.sleep() or httpx.AsyncClient.
4. **Event loop lifecycle:** Don't create tasks after the loop stops. Don't
   call get_event_loop() when no loop exists in the current thread.

### 3.7 Trio / Anyio

Structured concurrency with nurseries (TaskGroup equivalent) and cancel scopes.
anyio works on both asyncio and trio backends.

```python
import anyio
async with anyio.create_task_group() as tg:
    tg.start_soon(fetch, 'a')
    tg.start_soon(fetch, 'b')
# All done or cancelled

with anyio.CancelScope() as scope:
    scope.cancel()
    await anyio.sleep(10)  # raises immediately
```

---

## 4. Import System

### 4.1 sys.meta_path and sys.path_hooks

Meta path finders searched in order for EVERY import. Default: BuiltinImporter,
FrozenImporter, PathFinder.

```python
import sys, importlib.abc, importlib.machinery, types

# Custom finder for in-memory modules
class MemoryFinder(importlib.abc.MetaPathFinder):
    _modules = {}
    def find_spec(self, fullname, path, target=None):
        if fullname in self._modules:
            loader = MemoryLoader(fullname)
            return importlib.machinery.ModuleSpec(fullname, loader)

sys.meta_path.insert(0, MemoryFinder())
# Now imports check MemoryFinder first, then default finders
```

### 4.2 importlib Machinery

```python
import importlib, importlib.util

spec = importlib.util.find_spec('pathlib')  # ModuleSpec or None
imp = importlib.import_module('json')       # programmatic import
importlib.reload(imp)                       # reload module
importlib.invalidate_caches()               # clear finder cache

# Load from file path
spec = importlib.util.spec_from_file_location('mymod', '/path/to/file.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

### 4.3 Namespace Packages (PEP 420)

Directories without __init__.py are namespace packages. Allows splitting a
package across multiple directories on sys.path. __path__ is a _NamespacePath
that auto-updates when sys.path changes.

### 4.4 Circular Import Resolution

1. **Deferred import:** put import inside the function that needs it.
2. **TYPE_CHECKING:** `if TYPE_CHECKING: from x import Y` -- mypy only.
3. **__getattr__ (3.7+):** lazy imports at module level.
4. **Import module, not from:** `import mod` then `mod.Class()` -- access deferred.

```python
# Pattern 3: module-level __getattr__
_heavy = None
def __getattr__(name):
    global _heavy
    if _heavy is None:
        import heavy_package
        _heavy = heavy_package
    return getattr(_heavy, name)
```

---
## 5. Common Python Patterns

### 5.1 Context Managers

```python
# Class-based
class ManagedFile:
    def __init__(self, fn, mode='r'): self.fn = fn
    def __enter__(self):
        self.f = open(self.fn, mode)
        return self.f
    def __exit__(self, *exc):
        self.f.close()
        return False  # propagate exceptions

# Generator-based
from contextlib import contextmanager
@contextmanager
def managed_file(fn, mode='r'):
    f = open(fn, mode)
    try:
        yield f
    finally:
        f.close()

# ExitStack -- dynamic set of context managers
from contextlib import ExitStack
with ExitStack() as stack:
    files = [stack.enter_context(open(f)) for f in filenames]

# nullcontext, suppress, redirect_stdout
from contextlib import nullcontext, suppress, redirect_stdout
with suppress(FileNotFoundError):
    os.remove('/tmp/nope')

f = io.StringIO()
with redirect_stdout(f):
    print('captured')
print(f.getvalue())  # 'captured\n'

# Async context manager
from contextlib import asynccontextmanager
@asynccontextmanager
async def async_res():
    await asyncio.sleep(0.1)  # setup
    try: yield 'res'
    finally: await asyncio.sleep(0.1)  # teardown
```

### 5.2 Decorators

```python
import functools, time

def timer(func):
    @functools.wraps(func)  # preserves __name__, __doc__
    def wrapper(*a, **kw):
        start = time.perf_counter()
        result = func(*a, **kw)
        print(f'{func.__name__} took {time.perf_counter()-start:.4f}s')
        return result
    return wrapper

# Decorator with arguments -- factory function
def retry(max_attempts=3, delay=0.1):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*a, **kw):
            for attempt in range(max_attempts):
                try: return func(*a, **kw)
                except Exception as e:
                    if attempt == max_attempts - 1: raise
                    time.sleep(delay)
        return wrapper
    return decorator

# Class-based decorator
class CountCalls:
    def __init__(self, func):
        functools.update_wrapper(self, func)
        self.func = func; self.count = 0
    def __call__(self, *a, **kw):
        self.count += 1
        return self.func(*a, **kw)

# Stacking -- applied bottom to top
@timer
@CountCalls
def compute(): return sum(range(1000))
# Equivalent: timer(CountCalls(compute))
```

### 5.3 Iterators and Generators

```python
# yield from -- delegate to sub-generator
def flatten(nested):
    for item in nested:
        if isinstance(item, (list, tuple)):
            yield from flatten(item)
        else:
            yield item

# send() -- inject values
def accumulator():
    total = 0
    while True:
        value = yield total
        if value is None: break
        total += value

acc = accumulator()
next(acc)  # prime
print(acc.send(10))  # 10
print(acc.send(20))  # 30

# throw() and close()
def controlled():
    try:
        while True: yield 'working'
    except GeneratorExit: print('Closed')
    except ValueError as e: print(f'Caught: {e}'); yield 'recovered'
```

### 5.4 Data Structures

| Type | When | Key features |
|------|------|-------------|
| dataclass | Mutable records | auto __init__/__repr__/__eq__; frozen=True for immutable |
| Pydantic | Runtime validation | Schema validation, JSON serialization, JSON schema |
| namedtuple | Lightweight immutable | Minimal overhead, no methods |
| TypedDict | Dict shape for mypy | Zero runtime cost |
| attrs | Validators + slots | attrs.ib(validator=...), slots=True |
```python
from dataclasses import dataclass, field
@dataclass(frozen=True)
class Person:
    name: str
    age: int = 0
    tags: list[str] = field(default_factory=list)
```

### 5.5 Singleton and Registry

```python
# Module-level -- simplest, most Pythonic
_config = None
def get_config():
    global _config
    if _config is None: _config = {'debug': False}
    return _config

# __new__ singleton
class Singleton:
    _instance = None
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

# Registry with decorator
class Registry:
    _reg = {}
    @classmethod
    def register(cls, name=None):
        def decorator(plugin):
            cls._reg[name or plugin.__name__] = plugin
            return plugin
        return decorator
```

---

## 6. Testing

### 6.1 pytest Fixtures

```python
# conftest.py
import pytest

@pytest.fixture(scope='session')
def session_res():
    yield 'data'  # teardown after yield

@pytest.fixture(scope='module')
def db():
    conn = 'fake_conn'; yield conn

@pytest.fixture  # scope='function' default
def tmp(tmp_path):
    return tmp_path / 'subdir'

@pytest.fixture(autouse=True)
def auto():  # runs before every test
    print('setup')
    yield
    print('teardown')

# request.addfinalizer alternative
@pytest.fixture
def res(request):
    obj = 'r'
    request.addfinalizer(lambda: print('cleanup'))
    return obj
```

### 6.2 Parametrization

```python
import pytest

@pytest.mark.parametrize('a,b,expected', [
    (1, 2, 3), (0, 0, 0), (-1, 1, 0),
])
def test_add(a, b, expected):
    assert a + b == expected

# Stacked = cartesian product
@pytest.mark.parametrize('x', [1, 2, 3])
@pytest.mark.parametrize('y', [10, 20])
def test_cartesian(x, y):  # 6 combos
    assert x * y > 0

# With marks and IDs
@pytest.mark.parametrize('n,expected', [
    pytest.param(100, 5050, id='small'),
    pytest.param(10000, 50005000, id='large',
                 marks=pytest.mark.slow),
])
def test_sum(n, expected):
    assert sum(range(n + 1)) == expected

# indirect -- route parameter through fixture
@pytest.mark.parametrize('op,a,b,expected', [
    ('add', 1, 2, 3), ('mul', 2, 3, 6),
], indirect=['op'])
def test_op(op, a, b, expected):
    assert op(a, b) == expected
```

### 6.3 Mocking

```python
from unittest.mock import Mock, MagicMock, patch, create_autospec, AsyncMock

# MagicMock auto-implements __len__, __getitem__, etc.
# patch target: 'package.module.name' -- where LOOKED UP, not defined

with patch('mymod.requests') as m:
    m.get.return_value.text = 'data'

# create_autospec enforces real interface
class DB:
    def query(self, sql: str) -> list[dict]: ...
mock_db = create_autospec(DB, instance=True)
# mock_db.quary(...)  # AttributeError (typo caught)

# AsyncMock for async code
am = AsyncMock(return_value='result')
r = await am()  # 'result'
am.assert_awaited_once()
```

### 6.4 Hypothesis (Property-Based)

```python
from hypothesis import given, strategies as st

@given(st.integers(), st.integers())
def test_commutative(a, b):
    assert a + b == b + a

@given(st.lists(st.integers()))
def test_sort_idempotent(lst):
    assert sorted(sorted(lst)) == sorted(lst)

# Custom strategy
json_val = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda c: st.lists(c) | st.dictionaries(st.text(), c),
    max_leaves=20)
```

### 6.5 Async Testing

```python
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_async():
    result = await asyncio.sleep(0.1, result=42)
    assert result == 42

@pytest.mark.asyncio
async def test_http(my_asgi_app):
    async with AsyncClient(
        transport=ASGITransport(app=my_asgi_app),
        base_url='http://test'
    ) as client:
        resp = await client.get('/api/health')
        assert resp.status_code == 200
```

---

## 7. Profiling and Debugging

### 7.1 cProfile
```python
import cProfile, pstats, io
pr = cProfile.Profile()
pr.enable()
# ... code under test ...
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
ps.print_stats(10)
print(s.getvalue())
# Save: pr.dump_stats('output.prof')
# View: python -m pstats output.prof
# Visualize: snakeviz output.prof
```

### 7.2 py-spy
Sampling profiler -- no code changes. Attach to running processes.
# py-spy top --pid PID        # live stack
# py-spy record -o flame.svg --pid PID  # flame graph
# py-spy dump --pid PID       # dump all stacks

### 7.3 tracemalloc
```python
import tracemalloc
tracemalloc.start()
snap1 = tracemalloc.take_snapshot()
data = [b'x' * 1024 for _ in range(1000)]  # allocate ~1MB
snap2 = tracemalloc.take_snapshot()
for stat in snap2.compare_to(snap1, 'lineno')[:10]:
    print(stat)  # who allocated what
```

### 7.4 pdb
```python
def buggy(x):
    breakpoint()  # 3.7+ (was: import pdb; pdb.set_trace())
    return x / (x - 5)
# Commands: n(ext), s(tep), c(ontinue), l(ist), p(rint), w(here), q(uit)

# Post-mortem debugging
import pdb, sys, traceback
try:
    1 / 0
except Exception:
    traceback.print_exc()
    pdb.post_mortem(sys.exc_info()[2])
```

### 7.5 Warnings
```python
import warnings
warnings.warn('deprecated', DeprecationWarning, stacklevel=2)
warnings.filterwarnings('error', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*deprecated.*')
# CLI: python -W error::DeprecationWarning -Wd script.py
# Test: pytest.warns(DeprecationWarning, match='deprecated')
```

---

## 8. Packaging

### 8.1 pyproject.toml

```toml
[build-system]
requires = ['hatchling']
build-backend = 'hatchling.build'

[project]
name = 'my-package'
version = '0.1.0'
requires-python = '>=3.11'
dependencies = ['httpx>=0.24', 'pydantic>=2.0']

[project.optional-dependencies]
dev = ['pytest>=7', 'ruff>=0.1', 'mypy>=1.0']

[project.scripts]
mycli = 'my_pkg.cli:main'

[project.entry-points.'my_pkg.plugins']
email = 'my_pkg.plugins.email:EmailPlugin'
```

### 8.2 Build Backends
| Backend | Use case |
|---------|----------|
| setuptools | Everything supported, most mature, complex |
| hatchling | Modern, fast, excellent defaults |
| flit | Pure Python only, simplest |
| poetry | All-in-one (lock file, publish, env) but non-standard |

### 8.3 Wheel Tags
Filename: {name}-{ver}-{python_tag}-{abi_tag}-{platform_tag}.whl
- python_tag: py3, cp311
- abi_tag: none (pure Python), cp311, abi3 (stable ABI)
- platform_tag: any, macosx_14_0_arm64, manylinux2014_x86_64, win_amd64

### 8.4 Version Specifiers (PEP 440)
```python
'requests==2.31.0'        # exactly
'requests>=2.25,<3'       # range
'requests~=2.31.0'        # compatible: >=2.31.0, ==2.31.*
'requests==2.31.*'        # wildcard
'pkg[extra1,extra2]'      # extras
'uvloop; platform_system!="Windows"'  # env marker
```

### 8.5 Editable Installs (PEP 660)
pip install -e .  # change source -> reflected immediately
Build backends implement editable_wheel hook: hatchling symlinks, setuptools
generates _editable_impl.pth file.

---

## 9. Python Implementations

### 9.1 PyPy
JIT-compiled Python in RPython. 3-7x faster than CPython for pure Python.

**How it works:** RPython toolchain translates interpreter to C, auto-adds
meta-tracing JIT. Generational moving GC (incminimark) instead of reference
counting. JIT traces hot loops -> machine code.

**Works well:** Pure Python (Flask, Django, SQLAlchemy), integer-heavy, asyncio.
**Doesn't:** C extensions via cpyext (often 2-4x SLOWER than CPython), NumPy,
pandas, __del__ timing guarantees, embedding in C apps.
**Avoid when:** C extension-heavy, memory-constrained, predictable lifetime needed.

### 9.2 Jython
Python on JVM. Compiles to JVM bytecode. Seamless Java interop.
```python
from java.util import ArrayList
# Subclass Java interfaces, use Java threading/GC/JIT
```
Python 2.7 only (3.x port incomplete). No C extensions. Use for:
Java app scripting, JDBC access, deploying Python in JVM infrastructure.

### 9.3 GraalPy
Python on GraalVM (Truffle framework). Polyglot: Java/JS/Ruby/R/Wasm interop.
Native image -> AOT-compiled native executables (fast startup, low memory).
Python 3.10+ compatible. No C extensions. Best for polyglot apps or native image
deployment.

### 9.4 Cinder (Meta's CPython Fork)
- **Immortal Objects (PEP 683):** INCREF/DECREF no-ops on None/True/False/small
  ints. Upstreamed to CPython 3.12.
- **Shadow Bytecode:** Optimized internal bytecode runs while original is profiled.
- **StrictModules:** Module-level static type checking at import time.
- Quickening bytecode -> upstreamed as PEP 659 (CPython 3.11).

### 9.5 MicroPython / CircuitPython
Lean Python 3 for microcontrollers (ARM, ESP32, RP2040).

**Missing:** multiprocessing, subprocess, ssl, full asyncio (uses uasyncio),
ctypes. Single-threaded. 256KB RAM typical -- gc.collect() essential.

```python
import gc, uasyncio as asyncio

async def blink(led, ms):
    while True:
        led.on()
        await asyncio.sleep_ms(ms)
        led.off()
        await asyncio.sleep_ms(ms)

async def main():
    gc.collect()
    t = asyncio.create_task(blink(led1, 500))
    await asyncio.gather(t)

asyncio.run(main())
```

### 9.6 Others
- **RustPython:** Python 3 in Rust. WebAssembly + Rust embedding. Incomplete.
- **IronPython:** Python on .NET CLR. Python 2.7 only (3.x in progress).

---

## 10. Key PEPs Index

### Type System
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 484 | Type Hints | 3.5 | typing module and annotation syntax |
| 526 | Variable Annotations | 3.6 | `x: int = 0` syntax |
| 544 | Protocols | 3.8 | Structural subtyping via Protocol |
| 560 | Core typing support | 3.7 | Optimized generic type creation |
| 563 | Postponed Annotations | 3.7 | `from __future__ import annotations` |
| 585 | Generics in Std Collections | 3.9 | `list[int]` vs `List[int]` |
| 586 | Literal Types | 3.8 | `Literal['a', 'b']` |
| 589 | TypedDict | 3.8 | Typed dicts with fixed key sets |
| 591 | Final | 3.8 | `Final` and `@final` |
| 604 | X | Y Union | 3.10 | `int | str` syntax |
| 612 | ParamSpec | 3.10 | ParamSpec/Concatenate for decorators |
| 613 | TypeAlias | 3.10 | Explicit type alias |
| 646 | Variadic Generics | 3.11 | TypeVarTuple for tensor types |
| 647 | TypeGuard | 3.10 | User-defined type narrowing |
| 655 | Required/NotRequired | 3.11 | Optional TypedDict keys |
| 673 | Self Type | 3.11 | `Self` return type |
| 675 | LiteralString | 3.11 | Arbitrary literal string type |
| 681 | dataclass_transform | 3.11 | Alternative dataclass decorators |
| 692 | Unpack TypedDict | 3.12 | `**kwargs: Unpack[TypedDict]` |
| 695 | Type Param Syntax | 3.12 | `def f[T](x: T)` syntax |
| 698 | Override Decorator | 3.12 | `@override` verification |
| 702 | @deprecated | 3.13 | Deprecation decorator |

### Async / Concurrency
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 492 | async/await | 3.5 | Native coroutine syntax |
| 525 | Async Generators | 3.6 | async for, async yield |
| 530 | Async Comprehensions | 3.6 | [x async for x in g()] |
| 554 | Multiple Interpreters | draft | Subinterpreters with channels |
| 567 | Context Variables | 3.7 | Async-safe thread-local storage |
| 615 | IANA Time Zone | 3.9 | zoneinfo in stdlib |
| 703 | Free-Threading | 3.13 | --disable-gil builds |

### Packaging
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 440 | Version Specifiers | - | ==, ~=, >= version syntax |
| 517 | Build-system format | - | pyproject.toml [build-system] |
| 518 | Build deps in pyproject | - | Build requires in pyproject |
| 621 | Project metadata | - | [project] table |
| 660 | Editable installs | - | pip install -e . with backends |
| 668 | Externally managed | 3.11 | Prevent pip on system Python |

### Performance
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 659 | Specializing Interpreter | 3.11 | Opcode specialization (quickening) |
| 703 | GIL Optional | 3.13 | Free-threaded CPython |
| 744 | JIT Compilation | draft | JIT for CPython |
| 683 | Immortal Objects | 3.12 | Skip refcount on common singletons |

### Language Features
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 8 | Style Guide | - | PEP 8 naming, whitespace |
| 20 | Zen of Python | - | Design principles (import this) |
| 257 | Docstrings | - | Triple-quoted docstring format |
| 318 | Decorators | 2.4 | @decorator syntax |
| 343 | with Statement | 2.5 | Context manager protocol |
| 380 | yield from | 3.3 | Generator delegation |
| 435 | Enum | 3.4 | enum.Enum type |
| 498 | f-strings | 3.6 | f'Hello {name}' |
| 515 | Numeric Underscores | 3.6 | 1_000_000 |
| 557 | Data Classes | 3.7 | @dataclass |
| 570 | Positional-Only | 3.8 | def f(a, /): |
| 572 | Walrus Operator | 3.8 | if (n := len(x)) > 10: |
| 584 | dict Union | 3.9 | d1 | d2 |
| 614 | Relaxed Decorators | 3.9 | Any expression after @ |
| 616 | removeprefix/suffix | 3.9 | str.removeprefix() |
| 634 | Pattern Matching | 3.10 | match / case spec |
| 636 | Pattern Match Tutorial | 3.10 | match / case guide |
| 654 | Exception Groups | 3.11 | ExceptionGroup, except* |
| 701 | f-string Formalized | 3.12 | No restrictions on f-string nesting |
| 3107 | Function Annotations | 3.0 | def f(x: int) -> str: |
| 3129 | Class Decorators | 2.6/3.0 | @decorator on classes |

### Import System
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 302 | New Import Hooks | 2.3 | sys.meta_path, finder/loader |
| 328 | Abs/Rel Imports | 2.4 | from . import foo |
| 366 | Main module rel import | 2.6 | Relative imports in __main__ |
| 420 | Namespace Packages | 3.3 | No __init__.py needed |
| 451 | ModuleSpec | 3.4 | Unified finder/loader interface |
| 690 | Lazy Imports | draft | Deferred module loading |

### Other Notable
| PEP | Title | Ver | Summary |
|-----|-------|-----|---------|
| 252 | Types/Like Classes | 2.2 | Unified type/class system |
| 289 | Generator Expressions | 2.4 | (x for x in iter) |
| 3118 | Buffer Protocol | 3.0 | memoryview interface |
| 3156 | asyncio Module | 3.4 | asyncio design and rationale |
| 3333 | WSGI 1.0.1 | - | Web server gateway interface |
| 384 | Stable ABI | 3.2 | Py_LIMITED_API |
| 487 | Simpler Class Creation | 3.6 | __init_subclass__, __set_name__ |
| 0 | PEP Index | - | Canonical list of all PEPs |
| 8016 | Steering Council | - | Post-BDFL governance |

---

## 11. Security Pitfalls

### 11.1 Pickle (RCE)
```python
# NEVER unpickle untrusted data -- arbitrary code execution
# data = pickle.loads(untrusted_bytes)  # DANGEROUS

import json, ast
# Safe alternatives:
data = json.loads('{"key": "value"}')
data = ast.literal_eval('["safe", 1, 2]')
# Restrict pickle if you must:
class RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == 'builtins' and name in {'int','str','list','dict','set','tuple'}:
            return getattr(builtins, name)
        raise pickle.UnpicklingError(f'forbidden: {module}.{name}')
```

### 11.2 eval / exec
```python
# NEVER pass user input to eval() or exec()
# Use ast.literal_eval for simple literals only
# Even restricted-globals eval has known escapes
```

### 11.3 YAML, Shell, Tempfiles
```python
# Use yaml.safe_load(), NEVER yaml.load() on untrusted input
# Use subprocess.run(['cmd', arg], shell=False), NEVER shell=True with user input
# Use tempfile.mkstemp() or NamedTemporaryFile, NEVER tempfile.mktemp()
```

### 11.4 ReDoS, XXE, Assert
```python
# Regex: avoid nested quantifiers like (a+)+b -- O(2^n)
# Use defusedxml for XML (billion laughs / XXE prevention)
# NEVER use assert for security checks (--O removes assertions)
#   if not user.is_admin: raise PermissionError()  # Correct
```

### 11.5 Crypto-Sensitive Operations
```python
import secrets, hmac
# Use secrets module, NOT random, for security:
token = secrets.token_hex(32)
# Constant-time comparison:
if hmac.compare_digest(received, expected): ...
# Always use parameterized queries (never string-format SQL)
# Don't log credentials; use env vars or secrets manager
```

---

## 12. Performance Patterns

### 12.1 Memory
```python
import sys, functools

# __slots__: save ~50% memory, C-level attr access
class Point:
    __slots__ = ('x', 'y')
    def __init__(self, x, y): self.x = x; self.y = y

# tuple vs list:
print(sys.getsizeof((1,2,3)))   # ~72B -- immutable, smaller
print(sys.getsizeof([1,2,3]))   # ~88B -- mutable, larger

# String interning:
a = sys.intern('some long string')  # force interning
# Python auto-interns: identifiers, strings matching identifier pattern,
#   small ints (-5 to 256)
```

### 12.2 Caching
```python
@functools.cache  # 3.9+: unbounded, for small result sets
def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)

@functools.lru_cache(maxsize=128)  # bounded, for large sets
def expensive_query(id): ...

print(expensive_query.cache_info())  # hits, misses, maxsize
expensive_query.cache_clear()
```

### 12.3 Walrus (:=)
```python
# Avoid double computation:
if (m := pattern.search(text)):  print(m.group(0))
# List comp: [y for x in data if (y := f(x)) > 0]  # f(x) once
```

### 12.4 Comprehensions vs map
```python
# map + builtin: often faster (C-level loop)
list(map(int, ['1','2','3']))
# list comp + Python function: faster than map+lambda
[x*2 for x in range(1000)]  # vs map(lambda x: x*2, ...)
# Generator expression: saves memory for large data
sum(x*x for x in range(10_000_000))  # no intermediate list
```

### 12.5 Other Tips
```python
# -O: remove assert; -OO: remove assert + docstrings (smaller .pyc)
# PYTHONOPTIMIZE=2 python script.py
# python -m compileall src/  # pre-compile .py to .pyc

# Frozen modules load faster (no FS access, already in memory)
# import sys; print(sys.modules['importlib._bootstrap'])  # frozen

# Import speed: group at top; avoid dynamic __import__();
#   use importlib.invalidate_caches() sparingly
```

### 1.10 Detailed Memory Layout of Common Objects

Every CPython type is a PyTypeObject struct containing:
- tp_name (str): type name
- tp_basicsize + tp_itemsize: instance size (basic + per-item for var-length)
- tp_dealloc, tp_new, tp_init: lifecycle methods
- tp_getattro, tp_setattro: attribute access
- tp_as_number, tp_as_sequence, tp_as_mapping: operator overload slots
- tp_methods: PyMethodDef array for methods
- tp_members: PyMemberDef array for C-level attribute descriptors
- tp_getset: PyGetSetDef array for getter/setter (property-like) descriptors
- tp_dict: class __dict__ (methods, class attributes)

```python
# Inspect type object internals
import ctypes, struct

class PyObject(ctypes.Structure):
    _fields_ = [
        ('ob_refcnt', ctypes.c_ssize_t),
        ('ob_type', ctypes.c_void_p),
    ]

class PyFloatObject(ctypes.Structure):
    _fields_ = [
        ('ob_refcnt', ctypes.c_ssize_t),
        ('ob_type', ctypes.c_void_p),
        ('ob_fval', ctypes.c_double),
    ]

# Get raw memory of a Python object
import sys
f = 3.14
float_struct = PyFloatObject.from_address(id(f))
print(f'refcnt: {float_struct.ob_refcnt}')
print(f'value:  {float_struct.ob_fval}')
print(f'size:   {sys.getsizeof(f)} bytes')
```

### 1.11 Working with .pyc Bytecode Internals

```python
import dis, types, marshal, struct, time, sys

# Manually compile a function and examine
def add(a, b):
    return a + b

code = add.__code__

# The code object is immutable -- all fields accessible
print(f'argcount:    {code.co_argcount}')
print(f'nlocals:     {code.co_nlocals}')
print(f'stacksize:   {code.co_stacksize}')
print(f'flags:       {bin(code.co_flags)}')
print(f'bytecode:    {code.co_code.hex()}')
print(f'consts:      {code.co_consts}')
print(f'names:       {code.co_names}')
print(f'varnames:    {code.co_varnames}')
print(f'freevars:    {code.co_freevars}')
print(f'cellvars:    {code.co_cellvars}')
print(f'filename:    {code.co_filename}')
print(f'firstlineno: {code.co_firstlineno}')
print(f'lnotab:      {code.co_lnotab}')  # line number table (deprecated in 3.10+)

# co_flags bit meanings (from Include/cpython/code.h):
CO_OPTIMIZED   = 0x0001  # fast locals
CO_NEWLOCALS   = 0x0002  # create new locals dict
CO_VARARGS     = 0x0004  # *args
CO_VARKEYWORDS = 0x0008  # **kwargs
CO_NESTED      = 0x0010  # nested function/scopes
CO_GENERATOR   = 0x0020  # generator function
CO_NOFREE      = 0x0040  # no free or cell vars
CO_COROUTINE   = 0x0080  # async def function
CO_ITERABLE_COROUTINE = 0x0100  # generator-based coroutine (legacy)
CO_ASYNC_GENERATOR    = 0x0200  # async generator

print(f'Is generator: {bool(code.co_flags & CO_GENERATOR)}')
print(f'Is coroutine: {bool(code.co_flags & CO_COROUTINE)}')

# Create a code object from scratch (advanced)
def make_add_one():
    # Build bytecode: LOAD_FAST 0, LOAD_CONST 1, BINARY_OP 0, RETURN_VALUE
    bytecode = bytes([
        dis.opmap['RESUME'], 0,       # RESUME 0
        dis.opmap['LOAD_FAST'], 0,    # load first arg
        dis.opmap['LOAD_CONST'], 1,   # load constant 1 (index 1 = 1)
        dis.opmap['BINARY_OP'], 0,    # ADD (BINARY_OP arg 0 = +)
        dis.opmap['RETURN_VALUE'], 0, # return
    ])
    consts = (None, 1)
    varnames = ('x',)
    names = ()
    return types.CodeType(
        1, 0, 1, 1, 2, 67, bytecode,
        consts, names, varnames,
        '<custom>', 'add_one', 1,
        b'', (), ()
    )

add_one = types.FunctionType(make_add_one(), {})
print(add_one(41))  # 42
```

### 4.5 Advanced Import Hooks: Custom Loaders

```python
# A complete custom loader that loads from encrypted .py files
import importlib.abc, importlib.machinery, types, sys, base64

class EncryptedLoader(importlib.abc.SourceLoader):
    """Loads from .pye (encrypted Python) files."""
    def __init__(self, fullname, path):
        self.fullname = fullname
        self.path = path

    def get_filename(self, fullname):
        return self.path

    def get_data(self, path):
        with open(path, 'rb') as f:
            encrypted = f.read()
        # Simple XOR decryption (for illustration -- NOT cryptographically secure)
        key = b'mysecretkey'
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))

class EncryptedFinder(importlib.abc.MetaPathFinder):
    """Finds .pye files (encrypted Python sources)."""
    def find_spec(self, fullname, path, target=None):
        # Search sys.path for <module>.pye
        import os
        for entry in sys.path:
            candidate = os.path.join(entry, fullname.replace('.', '/') + '.pye')
            if os.path.exists(candidate):
                loader = EncryptedLoader(fullname, candidate)
                return importlib.machinery.ModuleSpec(fullname, loader, origin=candidate)
        return None

# Install the finder
# sys.meta_path.insert(0, EncryptedFinder())
```

# Expand section 4 further
### 4.6 The sys.modules Cache

```python
import sys

# sys.modules is a dict mapping module names to module objects
# It is checked BEFORE finders in meta_path are called
print('os' in sys.modules)     # True -- already imported
print(sys.modules['os'])       # <module 'os' ...>

# Blocking a module from being imported
sys.modules['dangerous_pkg'] = None  # prevents import
try:
    import dangerous_pkg  # ImportError
except ImportError:
    pass

# Deleting from sys.modules forces reimport
import json
del sys.modules['json']
import json  # freshly loaded from disk

# __main__ is special -- the entry-point module
print(sys.modules['__main__'])  # <module '__main__'>
```

### 6.6 Coverage Deep Dive

```python
# Configuration in .coveragerc or pyproject.toml
# [tool.coverage.run]
# branch = true
# source = ['src']
# omit = ['*/tests/*', '*/migrations/*', '*/__init__.py']
#
# [tool.coverage.report]
# fail_under = 85
# exclude_lines = [
#     'pragma: no cover',
#     'def __repr__',
#     'raise NotImplementedError',
#     'if __name__ == .__main__.:',
#     'if TYPE_CHECKING:',
# ]

# Running:
# pytest --cov=src --cov-report=term-missing --cov-report=html
#
# Branch vs line coverage:
# Line: was this line executed?
# Branch: was EACH path through a conditional taken?
#   if x > 0:  # Branch 1: True, Branch 2: False
#       ...    # Both must be tested for 100% branch coverage

# Combining coverage from parallel runs:
# coverage combine
# coverage report
# coverage html

# Context-aware coverage (who called this line?):
# pytest --cov=src --cov-context=test
```

### 6.7 pytest Plugins and Advanced Features

```python
# Pytest discovers tests using name patterns:
# - Files: test_*.py or *_test.py
# - Classes: Test* (no __init__)
# - Functions: test_*

# conftest.py hierarchy -- fixtures cascade:
# tests/conftest.py          -- shared by all tests
# tests/unit/conftest.py     -- unit tests only, overrides parent
# tests/integration/conftest.py -- integration only

# Built-in fixtures:
# tmp_path -- Path object for temp directory
# tmp_path_factory -- session-scoped temp dir factory
# capsys -- capture stdout/stderr
# capsysbinary -- capture stdout/stderr as bytes
# capfd -- capture file descriptors 1 and 2
# monkeypatch -- modify attributes, dicts, env vars at runtime
# recwarn -- record warnings
# caplog -- capture log output

# monkeypatch example:
def test_env(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgres://test/db')
    monkeypatch.setattr('mymod.requests.get', lambda url: MockResponse())
    monkeypatch.delattr(sys, 'platform')

# capsys example:
def test_output(capsys):
    print('hello')
    captured = capsys.readouterr()
    assert captured.out == 'hello\n'

# recwarn example:
def test_warnings(recwarn):
    import warnings
    warnings.warn('old', DeprecationWarning)
    assert len(recwarn) == 1
    assert recwarn[0].category == DeprecationWarning

# caplog example:
def test_logging(caplog):
    import logging
    logging.getLogger('myapp').warning('test')
    assert 'test' in caplog.text
    assert caplog.records[0].levelname == 'WARNING'

# tmp_path_factory -- create temp dirs across tests:
@pytest.fixture(scope='session')
def session_tmp(tmp_path_factory):
    return tmp_path_factory.mktemp('data')

# Marks registration:
# pytest.ini or pyproject.toml [tool.pytest.ini_options]
# markers = [
#     'slow: marks tests as slow',
#     'integration: marks tests as integration tests',
#     'e2e: end-to-end tests',
# ]
# Run only: pytest -m 'not slow'
# Run multiple: pytest -m 'slow or integration'
```

### 7.6 line_profiler (Detail)

```python
# pip install line_profiler
# Add @profile decorator -- no import needed, kernprof injects it

# script.py:
@profile
def hot_loop():
    total = 0
    for i in range(100000):
        total += i * i        # line 4 -- expensive
    result = total * 2         # line 5
    return result              # line 6

# Run: kernprof -l -v script.py
# Output:
# Line #  Hits   Time     Per Hit  % Time  Line Contents
# 3       1      2.0      2.0      0.0     total = 0
# 4       100001 500000.0 5.0      99.8    total += i * i
# 5       1      100.0    100.0    0.0     result = total * 2
# 6       1      0.0      0.0      0.0     return result

# kernprof options:
# -l/--line-by-line:  line profiling
# -v/--view:          show output immediately
# -o/--outfile:        save to file
```

### 7.7 memory_profiler

```python
# pip install memory_profiler
#
# @profile on functions, shows per-line memory usage:
@profile
def memory_hungry():
    a = [0] * 10_000_000     # ~80 MB
    b = [1] * 10_000_000     # +80 MB (total ~160 MB)
    del a                    # free ~80 MB
    return sum(b[:100])

# Run: python -m memory_profiler script.py
# Or: mprof run script.py; mprof plot  (time-series graph)

# mprof commands:
# mprof run script.py       -- record memory over time
# mprof plot                -- generate plot from last run
# mprof list                -- list all recordings
# mprof peak                -- show peak memory
```

### 7.8 objgraph (Finding Memory Leaks)

```python
# pip install objgraph
import objgraph

# Show most common types in memory
objgraph.show_most_common_types(limit=10)

# Find what's holding a reference to an object:
obj = SomeClass()
objgraph.show_backrefs([obj], max_depth=3, filename='backrefs.png')

# Track growth over time:
objgraph.show_growth(limit=10)  # types that grew since last call

# Practical leak hunt workflow:
# 1. objgraph.show_growth() before operation
# 2. Run operation
# 3. objgraph.show_growth() again -- see what grew
# 4. objgraph.by_type('MyClass') -- get all instances
# 5. objgraph.show_backrefs(instances, ...) -- find what holds them
```

### 8.6 setuptools Configuration Deep Dive

```python
# setup.cfg (legacy but still common for C extensions):
# [metadata]
# name = my_c_extension
# version = 1.0.0
#
# [options]
# packages = find:
# python_requires = >=3.8
#
# [options.extras_require]
# dev = pytest>=7; mypy>=1.0
#
# Only needed for C extensions now:
# setup.py:
# from setuptools import setup, Extension
# ext = Extension('mypkg._fast', sources=['src/_fast.c'])
# setup(ext_modules=[ext])

# Data files (non-code resources):
# [tool.setuptools.package-data]
# my_pkg = ['*.json', '*.yaml', 'templates/*.html']

# Entry points in detail:
# console_scripts: executable CLI commands
# 'mycli = my_pkg.cli:main' becomes an executable in PATH
#
# gui_scripts: same but launches without console window on Windows
#
# Custom entry point groups -- plugin discovery:
# [project.entry-points.'myapp.exporters']
# pdf = 'myapp.exporters.pdf:PDFExporter'
#
# Discovering plugins at runtime:
from importlib.metadata import entry_points
eps = entry_points(group='myapp.exporters')
for ep in eps:
    exporter_cls = ep.load()
    instance = exporter_cls()

# importlib.metadata also gives:
# importlib.metadata.version('package-name')
# importlib.metadata.metadata('package-name')
# importlib.metadata.requires('package-name')
```

### 8.7 uv / PDM (Modern Package Managers)

```bash
# uv (by Astral, the ruff team):
# uv pip install pkg       # 10-100x faster than pip
# uv pip compile reqs.in   # lock file generation
# uv pip sync reqs.txt      # sync env to lock file
# uv venv                   # create virtualenv
# uv run script.py           # run in project environment

# PDM (PEP 582 -- local __pypackages__):
# pdm init                  # create pyproject.toml
# pdm add requests          # add dependency
# pdm install               # install all dependencies
# pdm run python script.py  # run in project environment
# pdm.lock                  # lock file
```
### 12.6 Advanced Performance Techniques

```python
# Local variable caching -- avoid global/attribute lookups in hot loops
def slow_loop():
    total = 0
    for i in range(10_000_000):
        total += math.sin(i)  # math.sin lookup each iteration

def fast_loop():
    total = 0
    sin = math.sin  # local variable -- single lookup
    for i in range(10_000_000):
        total += sin(i)
# ~15-20% faster because LOAD_FAST (local) < LOAD_GLOBAL (global)

# Built-in function lookup is expensive in hot loops:
# Prefer local aliases:
append = my_list.append
for x in data:
    append(x)  # faster than my_list.append(x)

# Join strings instead of +:
# Slow: result = ''; for s in strings: result += s
# Fast: result = ''.join(strings)
# Each + creates a new string object; join allocates once

# Pre-allocate lists when size is known:
# Slow: items = []; for x in source: items.append(x)
# Faster: items = [0] * len(source); for i, x in enumerate(source): items[i] = x
# Fastest: items = list(source)  # C-level copy

# Use array module for homogeneous numeric data:
import array
nums = array.array('i', range(10_000_000))  # tighter packing than list

# Use deque for FIFO (appendleft/popleft is O(1) vs list's O(n)):
from collections import deque
dq = deque(maxlen=1000)  # fixed-size ring buffer
dq.append(1); dq.append(2); dq.append(3)  # oldest drops when full

# Use heapq for priority queues (O(log n) push/pop vs O(n) for list.sort):
import heapq
heap = []
heapq.heappush(heap, (priority, item))
priority, item = heapq.heappop(heap)

# Use bisect for sorted list insertion (O(log n) search, O(n) insert):
import bisect
sorted_list = [1, 2, 4, 5]
bisect.insort(sorted_list, 3)  # [1, 2, 3, 4, 5]

# Use __slots__ for dataclass-like records (memory):
@dataclass(slots=True)  # Python 3.10+
class Point:
    x: float
    y: float

# Use NamedTuple for immutable records (tuple-based, compact):
from typing import NamedTuple
class Point(NamedTuple):
    x: float
    y: float

# dict merge -- 3.9+ | operator is faster than {**d1, **d2}:
# merged = d1 | d2  # optimized C-level implementation
# d1 |= d2          # in-place merge

# Avoid creating unnecessary objects:
# Slow: x = str(i) if i > 0 else 'default'
# Better: x = str(i) if i > 0 else 'default'  # str() call avoided when False

# Use itertools for memory-efficient data processing:
from itertools import chain, islice, tee, product, combinations, permutations
# chain: flatten iterables without creating intermediate list
all_items = list(chain(list1, list2, list3))
# islice: slice any iterator (not just sequences)
first_10 = list(islice(iter, 10))
# product: cartesian product
grid = list(product(range(10), range(10)))  # 100 items

# Use operator module for faster itemgetter/attrgetter:
from operator import itemgetter, attrgetter, methodcaller
get_second = itemgetter(1)
sorted(data, key=get_second)  # faster than lambda x: x[1]

# Use __missing__ on dict subclasses for auto-population:
class DefaultDict(dict):
    def __missing__(self, key):
        value = self[key] = expensive_default(key)
        return value
# This is how collections.defaultdict works internally

# Use functools.partial for pre-binding arguments:
from functools import partial
print_int = partial(print, end=' ')
print_int(1); print_int(2)  # prints '1 2 ' horizontally

# Use sys.setrecursionlimit() for deep recursion (cautiously):
import sys
print(sys.getrecursionlimit())  # typically 1000
# sys.setrecursionlimit(5000)   # increase (risks stack overflow)

# PGO (Profile-Guided Optimization) for building CPython:
# ./configure --with-lto --enable-optimizations
# make -j$(nproc)
# Makes CPython itself 10-30% faster by optimizing hot C paths
```

