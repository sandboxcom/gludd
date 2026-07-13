# AG.8: Named Pipeline Passes

A **named pass** is a discrete, ordered pipeline stage with a registry that
guarantees execution ordering and observability.

## Concepts

| Term | Definition |
|---|---|
| Pass | A named validation/transformation step (e.g. `lint`, `typecheck`). |
| Registry | The ordered collection of all known passes. |
| Phase | A logical group of passes (`validation`, `test`, `build`). |
| Priority | Integer that determines execution order (lower = earlier). |

## Pass Structure

```python
@dataclass(frozen=True)
class NamedPass:
    name: str          # "lint", "typecheck", "test-unit", "collect"
    phase: str         # "validation", "test", "build"
    priority: int      # execution order within the phase
    required: bool     # failure blocks downstream passes
```

## Built-in Passes (in priority order)

### Validation Phase
| Priority | Name | Required |
|---|---|---|
| 10 | `lint` | yes |
| 20 | `typecheck` | yes |
| 30 | `collect` | yes |

### Test Phase
| Priority | Name | Required |
|---|---|---|
| 40 | `test-unit` | yes |
| 50 | `test-integration` | no |

### Build Phase
| Priority | Name | Required |
|---|---|---|
| 60 | `bundle-binaries` | no |
| 70 | `verify-release-artifact` | no |

## PassRegistry

The `PassRegistry` owns all registered passes and enforces:

1. **Ordered execution** — passes run in priority order grouped by phase.
2. **Fail-fast (required passes)** — if a required pass fails, remaining
   passes in its phase are skipped; downstream phases begin after the
   failed phase's non-required passes complete (already skipped).
3. **Result tracking** — each pass records `status` (`pending`, `running`,
   `passed`, `failed`, `skipped`), `duration_s`, and `error` string.
4. **Idempotent registration** — registering a duplicate pass name is a
   no-op (same name + same phase + same priority) or raises `ValueError`
   (conflicting definition).

## Execution Model

```python
registry = PassRegistry()
registry.register_many(BUILTIN_PASSES)  # pre-defined list
results = await registry.execute_all()  # runs in priority order
assert results["lint"].status == "passed"
```

Each pass is an async callable `Callable[[], Awaitable[bool]]`. The
registry invokes them in order, records timing, and handles exceptions by
marking the pass failed without crashing the run.
