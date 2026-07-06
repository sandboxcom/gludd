---
name: type-safety
description: Use when writing or reviewing code that carries type annotations (Python, Terraform, TypeScript). Defines what a "tight" variable shape is, enumerates the approved type constructs per language, lists the Any-based anti-patterns to avoid, and gives a tracing workflow for identifying the correct type. Pairs with `make check-types` which mechanically flags `Any` usage in new Python code.
---

# Type Safety

A type annotation is a *contract*. A contract that says "anything" (`Any`,
`object`, `any`, `interface {}`) is not a contract — it is the absence of one.
This skill defines what a **tight** variable shape is and how to produce one
when the obvious answer is `Any`.

## What "tight" means

A variable shape is **tight** when it carries the maximum information the
caller/consumer actually relies on, and no more. Concretely:

- Every field of a record has a named, specific type — not `dict[str, Any]`.
- Every element of a collection has a named element type — not `list[Any]`.
- Every function either returns a concrete type or signals failure via a
  precise union (`T | None`, `Result[T, E]`), never `-> Any`.
- `Optional[T]` / `T | None` is used **only** when `None` is a legitimate
  in-domain value (absent, not-yet-set, skipped). It must not be a euphemism
  for "I don't know what this returns."

A shape is **loose** when a reader cannot, from the annotation alone, name the
fields, element types, or failure modes. Loose shapes migrate bugs from
compile-time to run-time.

## Approved constructs, by language

### Python

| Need | Use | Not |
|---|---|---|
| A record/dict with known keys | `TypedDict` (or `pydantic.BaseModel`) | `dict[str, Any]` |
| A bag of validated config | `pydantic.BaseModel` with explicit fields | `dict[str, Any]` |
| A callback / callable shape | `typing.Protocol` | `Callable[..., Any]` |
| A function signature | `typing.Protocol` or `Callable[[A, B], R]` | `Callable[..., Any]` |
| A distinguished primitive (user-id vs int) | `typing.NewType` | bare `int` |
| A fixed set of values | `enum.Enum` / `Literal[...]` | `str` + free-text |
| A value-or-absent | `T \| None` (only if `None` is in-domain) | `Optional[Any]` |
| Heterogeneous tuple | `tuple[int, str, bool]` | `tuple[Any, ...]` |
| A container you genuinely cannot narrow | `object` + an `isinstance` narrowing comment | `Any` |

Prefer `pydantic.BaseModel` when you need runtime validation; prefer
`TypedDict` for static-only structural shapes (e.g., the body of a JSON
payload you only read). Prefer `Protocol` for duck-typed parameters — it
documents the surface area you actually depend on instead of demanding a
concrete class.

### Terraform

| Need | Use | Not |
|---|---|---|
| Input variable | `variable "x" { type = string }` (or `list(...)`, `map(...)`, `object(...)`) | untyped `variable "x" {}` |
| Structured input | `object({ name = string, ports = list(number) })` | `map(any)` |
| Output | `output "x" { value = ... }` with a documented type | `type = any` |
| A module's surface | explicit `type` on every variable + output | reliance on inference |

`any` in Terraform is the same regression as `Any` in Python — it disables
`terraform validate` as a meaningful check. Always declare `type`.

### TypeScript

| Need | Use | Not |
|---|---|---|
| An object shape | `interface` or `type` alias | `object` / `Record<string, any>` |
| A keyed map | `Record<K, V>` with concrete `K` and `V` | `{ [k: string]: any }` |
| A callback | `type Handler = (event: E) => void` | `Function` / `(...args: any[]) => any` |
| A constrained generic | `<T extends Foo>` | `<T>` then cast to `any` |
| A union of known shapes | a discriminated union | a loose `any`-bag with a `kind` field |
| An external you can't model | `unknown` + a type guard | `any` |

Prefer `unknown` over `any` for "I haven't parsed this yet" — `unknown`
forces a narrowing; `any` silently infects every consumer.

## Anti-patterns (mechanically flagged in Python)

These are the regressions `make check-types` catches. Each one replaces a
checkable contract with a hole.

| Anti-pattern | Why it's bad | Fix |
|---|---|---|
| `-> Any` | Caller must read the body to know what comes back | Return the concrete type; if multiple, a `Union`/`Result` |
| `dict[str, Any]` | Keys and values untyped; typos invisible | `TypedDict` or `BaseModel` |
| `list[Any]` / `tuple[Any, ...]` | Element type unknown | `list[T]` / `tuple[A, B, C]` |
| `Optional[Any]` | Equivalent to `Any` — `None` info adds nothing | The concrete optional `T \| None`, or just `T` |
| `Callable[..., Any]` | Args and return both holes | `Protocol` or full `Callable[[...], R]` |
| `Any` as a parameter | Every caller is untype-checked | Name the parameter's type or `Protocol` |
| `typing.Any` in a `cast(...)` | `cast(Any, x)` is a no-op cast | Cast to the real target type, or remove the cast |

`Any` is acceptable **only** in these narrow cases:

- Interop with a C-extension / third-party library whose types are not
  available and you cannot add a stub. Add a `# type: ignore[...]` with a
  reference to the upstream issue.
- A genuinely dynamic dispatcher where the dispatch table keys are unknown
  at type-check time. Document why in a comment.
- A `TypeVar` bound by `Any` is never legitimate — use `object` or a real
  bound.

Any other `Any` is a bug.

## How to identify the correct type

When you reach for `Any`, you have stopped doing the work. The type is
discoverable; trace it.

1. **Read the producer.** What function/object/value produces this? Its
   declared return type / field type IS the type. Copy it verbatim.
2. **Read the consumer.** What does the caller do with it? It will access
   fields, index, iterate, or call methods. Each access narrows the
   possible type — union those accesses into the shape the consumer
   actually requires.
3. **Read the schema.** If the value crosses a boundary (JSON, DB row,
   API response), there is a schema: a Pydantic model, a SQLAlchemy column
   type, an OpenAPI spec, a TypedDict. That schema is the type. Do not
   re-derive it ad hoc at each call site.
4. **Read the test.** Tests construct the value — they show the literal
   shape. A test that builds `{"name": "x", "ports": [80, 443]}` has just
   told you the type is `{"name": str, "ports": list[int]}`.
5. **Union the variants.** If multiple producers feed this site, take the
   union — a `TypedDict` with optional keys, a `Union[A | B]`, or a
   discriminated union. Do not collapse the union into `Any` to save typing.
6. **If, and only if, the type is truly unknowable** (e.g., a
   user-supplied plugin returns an opaque blob), use `object` and require
   the consumer to narrow. `object` is honest; `Any` is not.

## Mechanical enforcement

- **`make check-types`** — scans `src/` for `Any` in any annotation context
  (returns, params, AnnAssign, stringified annotations, nested inside
  `dict[...]`/`Optional[...]`/`Union[...]`). Exits non-zero on any hit.
- **`make check-types BASELINE=config/type_any_baseline.txt`** — same scan,
  but tolerates pre-existing violations listed in the baseline so the gate
  enforces on **new** code only. Add a line `path/to/file.py:LINE` for each
  legacy violation you are not fixing today; the gate fails the moment a new
  one appears.
- **`tests/unit/test_type_strictness.py`** — pins the scanner's behavior
  (detection of every anti-pattern, baseline filtering, exit codes).

Workflow when the gate fails:
1. Run `make check-types` and read the reported file:line.
2. Apply the tracing steps above to find the real type.
3. Replace `Any` with that type. If you cannot, add a typed `# type: ignore`
   with a reason — never silence by switching to `Any` elsewhere.
4. Re-run `make check-types`. Commit only when green.

## See also

- `scripts/check_type_strictness.py` — the scanner implementation.
- `tests/unit/test_type_strictness.py` — the behavioral spec.
- `docs/architecture.md` — data-flow diagrams that help trace producer →
  consumer types across the daemon/worker boundary.
