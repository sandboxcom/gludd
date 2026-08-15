# Go Knowledge Reference for gludd

Comprehensive Go reference for gludd agents working on Go code, answering
Go questions, or debugging Go issues.

**Maintained by:** gludd agentic system
**Last updated:** 2026-07-25
**Status of:** Go 1.24 (stable), Go 1.25 (experimental)

---

## 1. Go Language

### 1.1 Zero Values

Every type has a zero value — the default when a variable is declared but not
initialized. Go never leaves memory uninitialized.

```go
var i int        // 0
var f float64    // 0.0
var b bool       // false
var s string     // "" (empty string, not nil)
var p *int       // nil
var sl []int     // nil (len=0, cap=0; can append, range, len())
var m map[string]int  // nil (read returns zero; write PANICS)
var ch chan int  // nil (blocks forever on send/receive)
var fn func()    // nil
var iface io.Reader  // nil interface (type=nil, value=nil)
```

A nil map read is safe but a nil map write panics. A nil slice can be appended
and iterated. A nil channel blocks forever in select — useful for disabling
cases dynamically.

```go
var ch chan int
select {
case v := <-ch:  // blocks forever — never selected
default:
    fmt.Println("unreachable when ch is nil and no default")
}
```

### 1.2 Short Variable Declarations (`:=`)

`:=` declares and initializes in one statement. Re-declaration is allowed in
multi-variable `:=` if at least one variable is new (in the same scope).

```go
// Declaration
x := 42
name, ok := m[key]      // comma-ok idiom

// Re-declaration (at least one new variable)
f, err := os.Open("a.txt")
f2, err := os.Open("b.txt")  // err re-declared; f2 is new — OK

// Shadowing pitfall — := creates NEW variables in inner scope
var result string
if val, err := fetch(); err == nil {
    result = val   // assigns outer result
}
// val and err no longer in scope here
```

### 1.3 Named Returns

Named return values are initialized to zero and can be modified by a bare
`return`. Use sparingly — they improve readability in short functions with
deferred closures but obscure control flow in longer functions.

```go
func split(sum int) (x, y int) {
    x = sum * 4 / 9
    y = sum - x
    return  // bare return — returns x, y
}

// Useful with defer for error wrapping:
func readFile(name string) (content []byte, err error) {
    f, err := os.Open(name)
    if err != nil {
        return nil, err
    }
    defer func() {
        if e := f.Close(); e != nil && err == nil {
            err = e  // modify named return so caller sees the close error
        }
    }()
    return io.ReadAll(f)
}
```

### 1.4 Blank Identifier (`_`)

The blank identifier discards values and silences unused-import/variable errors.

```go
// Discarding return values
for _, v := range slice { }       // ignore index
val, _ := m[key]                   // ignore presence bool
_ = someFunc()                     // explicitly discard

// Import for side effects only
import _ "net/http/pprof"         // registers handlers via init()

// Compile-time interface satisfaction check
var _ io.Reader = (*MyType)(nil)  // MyType must implement io.Reader
var _ json.Marshaler = MyType{}   // value receiver check
```

### 1.5 Iota Enumerations

`iota` is a predeclared identifier representing successive untyped integer
constants in a `const` block. It resets to 0 for each `const` keyword.

```go
// Basic enumeration
const (
    Sunday = iota  // 0
    Monday         // 1
    Tuesday        // 2
)

// Skip values
const (
    _ = iota       // 0, discard
    KB = 1 << (10 * iota)  // 1 << 10 = 1024
    MB                        // 1 << 20
    GB                        // 1 << 30
)

// Bitmask flags
type Flags uint8
const (
    FlagRead  Flags = 1 << iota  // 0b001
    FlagWrite                    // 0b010
    FlagExec                     // 0b100
)

// Multiple iota expressions per line
const (
    bit0, mask0 = 1 << iota, 1<<iota - 1  // 1, 0
    bit1, mask1                            // 2, 1
    bit2, mask2                            // 4, 3
)
```

### 1.6 Defer

Deferred functions execute in LIFO order when the surrounding function returns.
Arguments are evaluated IMMEDIATELY when the defer statement is executed, not
when the deferred function runs.

```go
func deferDemo() {
    for i := 0; i < 3; i++ {
        defer fmt.Println(i)  // prints 2, 1, 0 (LIFO)
    }
}

// Argument evaluation at defer time, not execution time:
func argumentTiming() {
    x := 1
    defer fmt.Println(x)  // x evaluated NOW → prints 1
    x = 2
    // return → "1" (not "2")
}

// Common patterns:
// 1. Close resource
f, _ := os.Open("file")
defer f.Close()

// 2. Unlock mutex
mu.Lock()
defer mu.Unlock()

// 3. Recover from panic
defer func() {
    if r := recover(); r != nil {
        log.Printf("recovered: %v", r)
    }
}()

// 4. Wrap return error (requires named return)
defer func() {
    if err != nil {
        err = fmt.Errorf("readFile: %w", err)
    }
}()
```

**Pitfall:** Deferred functions in a loop accumulate until the function
returns — not the loop iteration ends. For per-iteration cleanup, use an
anonymous function wrapper.

```go
for _, f := range files {
    func() {
        fd, _ := os.Open(f)
        defer fd.Close()  // closes at end of THIS iteration
        use(fd)
    }()
}
```

### 1.7 Panic and Recover

`panic` unwinds the stack, running deferred functions. `recover` stops the
unwinding and returns the value passed to `panic`. `recover` only works inside
a deferred function.

```go
func safeCall() (err error) {
    defer func() {
        if r := recover(); r != nil {
            err = fmt.Errorf("panicked: %v", r)
        }
    }()
    mightPanic()
    return nil
}

// Re-panic: re-raise a panic you can't handle
defer func() {
    if r := recover(); r != nil {
        if _, ok := r.(net.Error); ok {
            // handle network errors
        } else {
            panic(r)  // re-panic for caller's defers to run
        }
    }
}()
```

**Never use panic for normal error handling.** Reserve it for unrecoverable
programmer errors: nil pointer dereference on a required invariant, out-of-bounds
on an internal data structure, or early init() failure that must abort startup.

### 1.8 Init() Ordering

`init()` functions run automatically at program startup, after variable
initialization in dependency order (package A imports B → B's inits run first).

```go
// Ordering within a package: source file lexical order then top-to-bottom
// Multiple init() in the same file run in order of declaration.
// Within a file, variable initializers run before init().

// init() in package a
var config = loadConfig()  // runs first

func init() {
    // runs after config is set
    validateConfig(config)
}

func init() {
    // runs after previous init()
    setupLogging(config)
}
```

**Cross-package ordering:** If A imports B indirectly through C, B's inits
run before C's, C's before A's. Circular imports are compile errors. Never
rely on init ordering across packages — if you need a specific order, make
it explicit in main().

### 1.9 Build Tags

Build tags control which files are included in compilation. The modern
directive is `//go:build`; the legacy `// +build` is still recognized.

```go
//go:build linux && amd64
// +build linux,amd64

package mypkg

// File only compiled on linux/amd64
```

```go
//go:build !windows
//go:build linux || darwin

//go:build go1.21    // minimum Go version
//go:build ignore     // file is never compiled (test helpers, code generators)

// Constraints: &&, ||, !, parentheses, go1.N versions
// Common: linux, darwin, windows, amd64, arm64, cgo, !cgo, ignore
```

### 1.10 Type System — Structs

Structs are value types. Embedding provides composition, not inheritance.
There is no subclassing in Go.

```go
type Person struct {
    Name string
    Age  int
}

// Struct tags for serialization
type Config struct {
    Host     string `json:"host" yaml:"host" validate:"required,hostname"`
    Port     int    `json:"port" yaml:"port" validate:"min=1,max=65535"`
    internal string // unexported — accessible only within this package
}

// Embedding (composition)
type Employee struct {
    Person          // embedded — Employee IS-A Person for method promotion
    ID     int
    Dept   string   `json:"department"`
}

e := Employee{Person: Person{"Alice", 30}, ID: 1}
fmt.Println(e.Name)  // promoted field — works
fmt.Println(e.Person.Name)  // explicit — also works
```

**Embedding vs. inheritance:**
- Embedded methods are PROMOTED to the outer struct — the outer struct satisfies
  interfaces that the embedded type does.
- There is no `super`, no virtual dispatch by default, no runtime type lookup.
- If the outer struct defines a method with the same name, it SHADOWS the
  embedded method (no automatic delegation).
- Tags are used by `reflect` at runtime. Common tag keys: `json`, `yaml`,
  `xml`, `db`, `validate`, `mapstructure`.

**Unexported fields** (lowercase first letter) are invisible outside the
defining package. Embedding an unexported type in an exported struct exposes
the embedded type's exported methods but not its fields.

### 1.11 Interfaces

Interfaces are satisfied implicitly — no `implements` keyword. A type satisfies
an interface if it has all the methods the interface requires.

```go
type Reader interface {
    Read(p []byte) (n int, err error)
}

// os.File satisfies io.Reader — no declaration needed
var r io.Reader = os.Stdin

// Nil interface vs. nil concrete type — THE classic Go pitfall:
var buf *bytes.Buffer          // nil concrete pointer
var r io.Reader = buf          // r is NON-nil interface!
fmt.Println(r == nil)          // false!
// r holds (type=*bytes.Buffer, value=nil) — the interface is NOT nil

// Correct nil check:
func isNilReader(r io.Reader) bool {
    if r == nil {
        return true
    }
    v := reflect.ValueOf(r)
    return v.Kind() == reflect.Ptr && v.IsNil()
}
```

**Type assertion** — extract the concrete value from an interface. Panics on
mismatch without the comma-ok form.

```go
var w io.Writer = os.Stdout

// Comma-ok (safe)
f, ok := w.(*os.File)
if ok {
    fmt.Println(f.Name())
}

// Single-value (panics on mismatch)
f := w.(*os.File)

// Type switch
switch v := x.(type) {
case nil:
    fmt.Println("nil")
case int:
    fmt.Printf("int: %d\n", v)
case string:
    fmt.Printf("string: %s\n", v)
case io.Reader:
    data, _ := io.ReadAll(v)
default:
    fmt.Printf("unknown type: %T\n", v)
}
```

**Interface pollution** — define interfaces at the CALL site, not the
implementation site. Small interfaces (1-3 methods) are preferred. Accept
interfaces, return concrete types.

```go
// BAD — interface at implementation site
type UserStore interface { /* 15 methods */ }

// GOOD — small interface at call site
type UserFinder interface {
    FindByID(id string) (*User, error)
}

func fetchUser(finder UserFinder, id string) (*User, error) { }
```

### 1.12 Generics (Go 1.18+)

Type parameters enable generic functions and types. Constraints specify what
operations are allowed on type parameters.

```go
// Generic function
func Min[T constraints.Ordered](a, b T) T {
    if a < b {
        return a
    }
    return b
}

// Generic type
type Set[T comparable] map[T]struct{}

func (s Set[T]) Add(v T) {
    s[v] = struct{}{}
}

func (s Set[T]) Contains(v T) bool {
    _, ok := s[v]
    return ok
}
```

**Constraints:**
```go
// any — equivalent to interface{} (all types allowed)
func Print[T any](v T) { fmt.Println(v) }

// comparable — types that support == and != (map keys)
func Keys[M ~map[K]V, K comparable, V any](m M) []K { /* */ }

// Custom constraint (interface with type list — Go 1.18) — DEPRECATED
// Use interface method sets instead:

// The ~ operator (approximation constraint) — allows defined types
// whose underlying type matches:
type Unsigned interface {
    ~uint | ~uint8 | ~uint16 | ~uint32 | ~uint64
}

type MyUint uint
func SumUnsigned[T Unsigned](vals []T) T { /* */ }
_ = SumUnsigned([]MyUint{1, 2})  // OK — ~uint matches MyUint

// Union constraint:
type Number interface {
    ~int | ~int32 | ~int64 | ~float32 | ~float64
}
```

**Type inference** reduces explicit type arguments:

```go
_ = Min(1, 2)         // infer int
_ = Min(1.5, 2.3)     // infer float64

// When inference is insufficient:
var s Set[string]      // explicit type argument required
```

### 1.13 Error Handling

Go has no exceptions. Errors are values returned by functions.

```go
// Sentinel errors — predefined error values for comparison
var ErrNotFound = errors.New("not found")
var ErrPermission = errors.New("permission denied")

if errors.Is(err, ErrNotFound) {
    // ErrNotFound or ANY error wrapping it
}

// Custom error types — for carrying structured data
type ValidationError struct {
    Field string
    Value interface{}
    Tag   string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation: field %s failed %s", e.Field, e.Tag)
}

// errors.As — extract a concrete error type from the chain
var valErr *ValidationError
if errors.As(err, &valErr) {
    fmt.Println(valErr.Field)  // access typed fields
}

// Wrapping — fmt.Errorf with %w preserves the error chain
func getUser(id string) (*User, error) {
    u, err := db.QueryUser(id)
    if err != nil {
        return nil, fmt.Errorf("getUser(%s): %w", id, err)
    }
    return u, nil
}

// errors.Is unwraps through the chain; errors.As finds the target type.
// Both work with any error that implements Unwrap() error or
// Unwrap() []error (multiple errors, Go 1.20+).

// Opaque errors — expose behavior, not type
type Temporary interface{ Temporary() bool }
type Timeout interface{ Timeout() bool }

if errors.As(err, &temporary); temporary.Temporary() { /* retry */ }
```

**Error handling patterns:**

```go
// Inline when return is simple
if err != nil {
    return err
}

// Defer error annotation with named returns
defer func() {
    if err != nil {
        err = fmt.Errorf("operation: %w", err)
    }
}()
```

### 1.14 Context

Context carries deadlines, cancellation signals, and request-scoped values
across API boundaries and goroutines.

```go
// Never store context in a struct — pass it as the first argument
func Process(ctx context.Context, data []byte) error {
    select {
    case <-ctx.Done():
        return ctx.Err()  // context.Canceled or context.DeadlineExceeded
    default:
    }
    // do work
}

// Cancellation
ctx, cancel := context.WithCancel(context.Background())
defer cancel()  // cancel when Process returns — propagates to children

// Deadline
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
// or: context.WithDeadline(ctx, time.Now().Add(5*time.Second))

// Values — use only for request-scoped data (trace IDs, auth tokens)
// Never for optional function parameters
type contextKey string
const traceIDKey contextKey = "traceID"
ctx = context.WithValue(ctx, traceIDKey, "abc123")
if tid, ok := ctx.Value(traceIDKey).(string); ok { /* */ }
```

**Context rules:**
1. `context.Background()` at the top of a call chain (main, init, tests).
2. `context.TODO()` when the right context is unclear — it's a placeholder.
3. Context is the FIRST parameter, named `ctx`.
4. Never store a context in a struct — pass it explicitly.
5. Never pass a nil context — use `context.TODO()` if unsure.
6. Any function that blocks should take a context.

---

## 2. Concurrency

### 2.1 Goroutines

Goroutines are lightweight threads managed by the Go runtime. They use M:N
scheduling — M goroutines multiplexed onto N OS threads.

```go
go func() {
    // runs concurrently
}()

// Goroutine lifecycle:
// - Started with `go`
// - Exits when the function returns
// - NO way to kill a goroutine from outside — use context cancellation
// - Stack starts at ~2KB and grows/shrinks as needed
// - Default GOMAXPROCS = number of CPU cores
```

**Goroutine leaks** — a goroutine blocked forever on a channel that will never
receive/send is a leak:

```go
// LEAK: goroutine blocks forever
ch := make(chan int)
go func() {
    ch <- 42  // blocks — nobody receives
}()
// Goroutine never exits

// FIX: buffered channel or context
ctx, cancel := context.WithTimeout(context.Background(), time.Second)
defer cancel()
go func() {
    select {
    case ch <- 42:
    case <-ctx.Done():
    }
}()
```

### 2.2 Channels

Channels are typed conduits for communication between goroutines.

```go
// Unbuffered — sender blocks until receiver is ready (synchronous)
ch := make(chan int)

// Buffered — sender blocks only when buffer is full
ch := make(chan int, 10)  // capacity 10

// Send
ch <- 42

// Receive
v := <-ch
v, ok := <-ch  // ok=false if channel closed and drained

// Close — signals "no more values"
close(ch)

// Range over channel — exits when closed and drained
for v := range ch {
    fmt.Println(v)
}

// Nil channel — blocks forever, useful in select for disabling cases
var nilCh chan int
```

**Channel semantics:**
| Operation | nil | closed | open (with room) | open (full) |
|-----------|-----|--------|------------------|-------------|
| Send | blocks forever | PANICS | sends | blocks |
| Receive | blocks forever | zero value (ok=false) | receives | blocks |
| Close | PANICS | PANICS | closes | closes |

**Select** — the multi-channel multiplexer:

```go
select {
case v := <-ch1:
    // ch1 was ready
case ch2 <- x:
    // sent to ch2
case <-ctx.Done():
    // context cancelled
    return ctx.Err()
default:
    // non-blocking — runs if no case is ready
}

// Timeout pattern
select {
case v := <-ch:
    fmt.Println(v)
case <-time.After(time.Second):
    fmt.Println("timeout")
}
// NOTE: time.After leaks until the timer fires — use time.NewTimer for loops
```

**Close semantics** — only the SENDER should close a channel. Closing from the
receiver panics if the sender tries to send later. Sending on a closed channel
panics.

```go
// Safe sender-closes pattern
go func() {
    defer close(ch)  // sender closes
    for _, item := range items {
        ch <- item
    }
}()
for v := range ch {
    fmt.Println(v)
}

// Multiple senders — use sync.Once or a separate done channel
var once sync.Once
closeCh := func() { once.Do(func() { close(ch) }) }
```

### 2.3 Fan-In / Fan-Out Patterns

```go
// Fan-out: distribute work to multiple goroutines
func fanOut(ctx context.Context, inputs <-chan Work, workers int) []<-chan Result {
    channels := make([]<-chan Result, workers)
    for i := 0; i < workers; i++ {
        channels[i] = worker(ctx, inputs)
    }
    return channels
}

// Fan-in: merge multiple channels into one
func fanIn(ctx context.Context, channels ...<-chan Result) <-chan Result {
    out := make(chan Result)
    var wg sync.WaitGroup
    wg.Add(len(channels))
    for _, ch := range channels {
        go func(c <-chan Result) {
            defer wg.Done()
            for v := range c {
                select {
                case out <- v:
                case <-ctx.Done():
                    return
                }
            }
        }(ch)
    }
    go func() {
        wg.Wait()
        close(out)
    }()
    return out
}
```

### 2.4 Sync Package

```go
// Mutex — mutual exclusion
var mu sync.Mutex
mu.Lock()
defer mu.Unlock()
// critical section

// RWMutex — multiple readers, exclusive writer
var rw sync.RWMutex
rw.RLock()   // shared lock
defer rw.RUnlock()
// reading

rw.Lock()    // exclusive lock
defer rw.Unlock()
// writing

// WaitGroup — wait for a collection of goroutines
var wg sync.WaitGroup
for i := 0; i < 10; i++ {
    wg.Add(1)
    go func(n int) {
        defer wg.Done()
        process(n)
    }(i)
}
wg.Wait()

// Once — execute exactly once (thread-safe singleton)
var once sync.Once
once.Do(func() {
    // initialization — runs exactly once
})

// Cond — broadcast / signal waiting goroutines
// Rarely needed; prefer channels or errgroup.

// Atomic — low-level atomic memory operations
var counter atomic.Int64
counter.Add(1)
val := counter.Load()
counter.Store(0)
if counter.CompareAndSwap(old, new) { /* */ }

// sync.Map — concurrent map (specialized; use regular map+mutex by default)
// sync.Map wins when: (1) entry is written once and read many times,
// (2) goroutines operate on disjoint key sets.
// For general concurrent maps, prefer a regular map + sync.RWMutex.
```

### 2.5 Errgroup

`golang.org/x/sync/errgroup` runs goroutines and collects the first error.

```go
g, ctx := errgroup.WithContext(ctx)

g.Go(func() error {
    return fetchUser(ctx, userID)
})
g.Go(func() error {
    return fetchPosts(ctx, userID)
})

if err := g.Wait(); err != nil {
    log.Printf("one of the operations failed: %v", err)
}

// With limit (Go 1.22+ via SetLimit or use semaphore)
g.SetLimit(10)  // max concurrent goroutines
```

### 2.6 Worker Pool vs Semaphore

```go
// Semaphore pattern — limit concurrent operations (no worker state)
sem := make(chan struct{}, 10)  // max 10 concurrent
for _, url := range urls {
    sem <- struct{}{}  // acquire
    go func(u string) {
        defer func() { <-sem }()  // release
        fetch(u)
    }(url)
}

// Worker pool — goroutines share a channel of work
func workerPool(ctx context.Context, numWorkers int, jobs <-chan Job) {
    for i := 0; i < numWorkers; i++ {
        go func() {
            for job := range jobs {
                process(ctx, job)
            }
        }()
    }
}
```

### 2.7 Common Concurrency Bugs

```go
// 1. Goroutine leak — goroutine blocked on unbuffered channel
ch := make(chan int)
go func() { ch <- 1 }()  // blocks forever — no reader

// Fix: buffered channel or ensure reader exists

// 2. Data race — concurrent read/write without synchronization
var counter int
go func() { counter++ }()  // RACE
fmt.Println(counter)        // RACE
// Detect: go test -race

// 3. Closing from receiver side
ch := make(chan int)
go func() {
    close(ch)  // receiver closes
}()
ch <- 1  // PANICS: send on closed channel

// 4. WaitGroup Add called inside goroutine
var wg sync.WaitGroup
for i := 0; i < 5; i++ {
    go func() {
        wg.Add(1)    // WRONG — may run after wg.Wait()
        defer wg.Done()
        work()
    }()
}
wg.Wait()  // may return before all goroutines start

// Fix: Add before go
for i := 0; i < 5; i++ {
    wg.Add(1)
    go func() {
        defer wg.Done()
        work()
    }()
}

// 5. Loop variable capture (pre-Go 1.22)
for _, v := range items {
    go func(val Item) {  // pass as argument
        process(val)
    }(v)
}
// Go 1.22+ fixes this — loop variable is per-iteration
```

---

## 3. Project Structure

### 3.1 Standard Go Project Layout

```text
myproject/
├── cmd/                    # Main applications (one dir per binary)
│   ├── server/
│   │   └── main.go
│   └── cli/
│       └── main.go
├── internal/               # Private application code (compiler-enforced)
│   ├── auth/
│   ├── db/
│   └── service/
├── pkg/                    # Public library code (importable by external projects)
│   └── api/
├── api/                    # API definitions (protobuf, OpenAPI, graphql)
│   └── proto/
├── web/                    # Web assets (templates, static files)
├── scripts/                # Build, install, analysis scripts
├── build/                  # Packaging and CI configs
│   ├── Dockerfile
│   └── ci/
├── deployments/            # IaaS, PaaS, orchestration configs
│   └── k8s/
├── test/                   # External test apps and test data
│   └── testdata/
├── internal/               # (enforced by go tool — not importable from outside)
│
├── go.mod
├── go.sum
└── Makefile
```

**Key principle:** `internal/` is compiler-enforced — packages in `internal/`
can only be imported by code rooted at the parent of `internal/`. External
projects cannot import them.

```go
// myproject/internal/auth/token.go
package auth
// Can be imported by: myproject/cmd/server, myproject/internal/db
// CANNOT be imported by: github.com/other/project

// Flat layout (simpler for small projects):
// myproject/
//   main.go
//   server.go
//   handler.go
```

### 3.2 Package Naming Conventions

- Short, lowercase, single-word names: `http`, `json`, `sql`, `auth`
- No underscores or mixedCaps: `rate_limiter` → `ratelimit`
- Descriptive: `util`, `common`, `misc`, `helpers` are BAD names
- Package name is the last segment of the import path; may differ from directory:
  ```go
  // directory: myproject/user
  // file: myproject/user/user.go
  package user  // matches directory name
  ```

### 3.3 Module System

```go
// go.mod
module github.com/example/myproject

go 1.24

require (
    github.com/gin-gonic/gin v1.10.0
    golang.org/x/sync v0.10.0
)

require (
    // indirect dependencies — automatically maintained
    github.com/bytedance/sonic v1.11.6 // indirect
)

// replace — redirect an import path (local development, forks)
replace github.com/old/lib => github.com/myfork/lib v1.2.3
replace github.com/example/api => ../api  // local path

// exclude — prevent a specific version from being used
exclude github.com/broken/mod v3.0.0

// retract — signal that a published version should not be used
retract [v1.0.0, v1.0.5]  // version range
retract v1.1.0             // single version
```

**`go.mod` file:**
- `go.mod` declares the module path and dependency requirements.
- `go.sum` contains cryptographic checksums of all dependencies — commit both.
- `require` block lists direct and indirect dependencies.
- `go mod tidy` removes unused dependencies and adds missing ones.
- `go mod vendor` creates a `vendor/` directory with dependency source code.
- `go mod verify` checks that downloaded dependencies match go.sum hashes.

### 3.4 Workspace (Go 1.18+)

`go.work` enables multi-module repositories where you can work on multiple
modules simultaneously without `replace` directives.

```go
// go.work
go 1.24

use (
    ./server
    ./shared
    ./cli
)
```

Workspace files are local-only (never committed). They let you develop across
modules without pushing changes.

### 3.5 Build Constraints and Platform-Specific Files

Go uses filename suffixes for platform-specific compilation:

```go
// File naming convention:
// *_GOOS.go        — e.g., file_linux.go, file_darwin.go, file_windows.go
// *_GOARCH.go      — e.g., file_amd64.go, file_arm64.go
// *_GOOS_GOARCH.go — e.g., file_linux_amd64.go

// mypkg_default.go (no suffix — compiled on all platforms)
// mypkg_linux.go        (linux only)
// mypkg_linux_amd64.go  (linux/amd64 only)
// mypkg_windows.go      (windows only)
```

```go
//go:build linux && amd64 && cgo

// Common GOOS values: linux, darwin, windows, freebsd, openbsd, netbsd,
//                      dragonfly, solaris, plan9, js, wasip1, android, ios
// Common GOARCH values: amd64, arm64, 386, arm, mips, mips64, ppc64le,
//                       riscv64, s390x, wasm
```

### 3.6 Cgo

Cgo enables Go packages to call C code.

```go
/*
#include <stdlib.h>
#include <stdio.h>

void hello(const char *name) {
    printf("Hello, %s!\n", name);
}
*/
import "C"
import "unsafe"

func Greet(name string) {
    cname := C.CString(name)
    defer C.free(unsafe.Pointer(cname))
    C.hello(cname)
}
```

**Key cgo considerations:**
- `C.CString()` allocates C memory — must call `C.free()`.
- Cgo disables cross-compilation by default (requires C cross-compiler).
- Use `CGO_ENABLED=0` for pure Go builds (no C dependencies, full
  cross-compilation).
- `#cgo` directives control compiler/linker flags:
  ```go
  // #cgo LDFLAGS: -lsqlite3
  // #cgo linux pkg-config: foo
  ```
- Static linking: `#cgo LDFLAGS: -static`
- `//export` exposes Go functions to C callers — the comment must be directly
  above the `func`, no blank line.
- cgo has a performance cost at the Go/C boundary — batch calls where possible.

---

## 4. Build & Tooling

### 4.1 go build

```bash
# Basic build
go build ./...                              # all packages
go build -o bin/server ./cmd/server         # named output

# Flags
go build -ldflags="-s -w" ./cmd/server      # strip debug info
go build -ldflags="-X main.version=v1.0.0"  # inject linker variable
go build -tags="integration,debug"          # build with tags
go build -race                              # enable race detector
go build -gcflags="-m"                      # escape analysis output
go build -buildvcs=false                    # omit VCS info from binary

# Cross-compilation
GOOS=linux GOARCH=amd64 go build
GOOS=darwin GOARCH=arm64 go build
GOOS=windows GOARCH=amd64 go build -o app.exe
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build

# All supported combinations:
# go tool dist list
```

### 4.2 go install vs go build

```bash
# go install compiles AND installs the binary to $GOPATH/bin (or $GOBIN)
go install ./cmd/server           # installs to GOBIN
go install golang.org/x/tools/cmd/goimports@latest  # install tool globally

# go build ONLY compiles — binary stays in current directory
go build ./cmd/server
```

### 4.3 go test

```bash
# Basic
go test ./...                      # all packages
go test -v ./...                   # verbose
go test -count=1 ./...             # disable caching
go test -run TestFoo ./...         # run specific test (regex)
go test -run TestFoo/SubTest ./...
go test -bench=. ./...             # benchmarks
go test -bench=. -benchmem ./...   # with memory stats
go test -race ./...                # race detector
go test -cover ./...               # coverage percentage
go test -coverprofile=coverage.out ./...
go tool cover -html=coverage.out   # coverage in browser
go test -fuzz=FuzzFoo -fuzztime=30s ./...  # fuzzing
go test -shuffle=on ./...          # randomize test order
go test -timeout=30s ./...         # per-test-binary timeout
go test -short ./...               # skip long tests
go test -parallel=4 ./...          # parallel tests
```

**Table-driven tests** (canonical Go testing pattern):

```go
func TestAdd(t *testing.T) {
    tests := []struct {
        name string
        a, b int
        want int
    }{
        {"positive", 1, 2, 3},
        {"negative", -1, -2, -3},
        {"zero", 0, 0, 0},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := Add(tt.a, tt.b)
            if got != tt.want {
                t.Errorf("Add(%d, %d) = %d, want %d",
                    tt.a, tt.b, got, tt.want)
            }
        })
    }
}
```

### 4.4 go mod

```bash
go mod init github.com/user/project    # initialize module
go mod tidy                            # clean up go.mod + go.sum
go mod vendor                          # create vendor/ directory
go mod verify                          # verify dependencies match go.sum
go mod download                        # download dependencies to module cache
go mod graph                           # print module dependency graph
go mod why -m golang.org/x/sync        # why is this module needed?
go mod edit -go 1.24                   # change Go version
go mod edit -replace old@v=new@v       # add replace directive
```

### 4.5 go vet

```bash
go vet ./...  # runs built-in analyzers:
# - copylocks: copying sync.Mutex, sync.WaitGroup, etc.
# - loopclosure: loop variable captured by function literal (pre-Go 1.22)
# - unreachable: dead code after return/panic
# - printf: format string mismatches
# - shadow: unintentional variable shadowing
# - lostcancel: context.WithCancel result not used
```

### 4.6 Static Analysis Tools

```bash
# staticcheck — the definitive Go linter (runs 150+ checks)
go install honnef.co/go/tools/cmd/staticcheck@latest
staticcheck ./...

# golangci-lint — linter aggregator (50+ linters in one binary)
golangci-lint run ./...
```

**`.golangci.yml` — canonical config:**

```yaml
run:
  timeout: 5m
  tests: true

linters:
  enable:
    - errcheck
    - gosimple
    - govet
    - ineffassign
    - staticcheck
    - unused
    - revive
    - gosec
    - goimports
    - misspell
    - unconvert
    - unparam
    - prealloc
    - bodyclose
    - noctx
    - forcetypeassert
    - gocritic
    - nilerr
    - exportloopref

linters-settings:
  gocritic:
    enabled-tags:
      - diagnostic
      - style
      - performance
  revive:
    rules:
      - name: blank-imports
      - name: context-as-argument
      - name: error-return
      - name: error-strings
      - name: exported

issues:
  exclude-use-default: false
  max-issues-per-linter: 0
  max-same-issues: 0
```

### 4.7 Profiling

```bash
# CPU profiling
go test -cpuprofile=cpu.prof -bench=. ./...
go tool pprof cpu.prof
go tool pprof -http=:8080 cpu.prof  # web UI

# Memory/heap profiling
go test -memprofile=mem.prof -bench=. ./...
go tool pprof mem.prof

# Goroutine profiling
# SIGQUIT (kill -QUIT <pid>) dumps all goroutine stacks to stderr
# Or programmatically:
import _ "net/http/pprof"
go func() { log.Fatal(http.ListenAndServe(":6060", nil)) }()
# Then: go tool pprof http://localhost:6060/debug/pprof/heap

# Execution trace
go test -trace=trace.out ./...
go tool trace trace.out
```

### 4.8 Debugging (Delve)

```bash
# Attach to running process
dlv attach <pid>

# Debug a running binary
dlv exec ./binary

# Headless mode (remote debugging)
dlv debug --headless --listen=:2345 --api-version=2 ./cmd/server

# Common commands within dlv:
# break main.go:42    — set breakpoint
# continue            — run until breakpoint
# next / step         — line-by-line
# print var           — inspect variable
# goroutines          — list all goroutines
# goroutine <id>      — switch to goroutine
# stack               — print stack trace
```

### 4.9 Additional Tooling

```bash
# Air — live reload for development
go install github.com/air-verse/air@latest
air  # watches .go files, rebuilds and restarts on change

# ko — build and publish Go container images
# Minimal images: ko builds FROM scratch with just the Go binary
ko build ./cmd/server
ko apply -f deployment.yaml  # build + push + deploy to k8s

# goreleaser — cross-compile and publish releases
goreleaser release --clean
# Configuration in .goreleaser.yml — handles GOOS/GOARCH matrix,
# ldflags, archives, checksums, signing, Docker images, Homebrew, Scoop
```

---

## 5. Security Patterns

### 5.1 SQL Injection Prevention

Always use parameterized queries — never concatenate user input into SQL.

```go
// CORRECT — parameterized query
rows, err := db.Query(
    "SELECT name FROM users WHERE email = $1",
    userInput,
)

// CORRECT — named parameters (sqlx, pgx)
rows, err := db.NamedQuery(
    "SELECT name FROM users WHERE email = :email",
    map[string]interface{}{"email": userInput},
)

// WRONG — string concatenation (SQL INJECTION)
query := "SELECT name FROM users WHERE email = '" + userInput + "'"
rows, err := db.Query(query)  // NEVER DO THIS
```

### 5.2 Path Traversal

```go
// filepath.Clean removes .. and redundant separators
// BUT it does NOT prevent traversal to parent directories
path := filepath.Clean(userPath)     // NOT sufficient alone

// Secure join — prevents traversal outside a root directory
import "github.com/cyphar/filepath-securejoin"
fd, err := securejoin.OpenInRoot("/safe/root", userPath)

// Manual check
func safePath(baseDir, userPath string) (string, error) {
    resolved := filepath.Join(baseDir, filepath.Clean(userPath))
    abs, _ := filepath.Abs(resolved)
    baseAbs, _ := filepath.Abs(baseDir)
    if !strings.HasPrefix(abs, baseAbs+string(os.PathSeparator)) {
        return "", fmt.Errorf("path traversal detected: %s", userPath)
    }
    return abs, nil
}
```

### 5.3 Template Injection

```go
import "html/template"

// CORRECT — html/template auto-escapes based on context
tmpl := template.Must(template.New("page").Parse("<h1>{{.Title}}</h1>"))
tmpl.Execute(w, data)  // Title is HTML-escaped automatically

// DANGER — text/template does NOT escape
import "text/template"
tmpl := template.Must(template.New("page").Parse("<h1>{{.Title}}</h1>"))
tmpl.Execute(w, data)  // Title rendered raw — XSS if user-controlled
```

### 5.4 Cryptography

```go
// Use crypto/rand for cryptographic randomness
import "crypto/rand"
buf := make([]byte, 32)
if _, err := rand.Read(buf); err != nil { /* */ }

// NEVER use math/rand for security — it's a PRNG
// import "math/rand"   // WRONG for tokens, keys, passwords

// Constant-time comparison (prevents timing attacks on secrets)
import "crypto/subtle"
if subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 {
    // equal
}

// Key generation
import "crypto/ed25519"
pub, priv, err := ed25519.GenerateKey(rand.Reader)

// AES-GCM (authenticated encryption — preferred)
import "crypto/aes"
import "crypto/cipher"
block, _ := aes.NewCipher(key)
gcm, _ := cipher.NewGCM(block)
ciphertext := gcm.Seal(nil, nonce, plaintext, additionalData)
plaintext, err := gcm.Open(nil, nonce, ciphertext, additionalData)

// Password hashing — bcrypt or argon2id
import "golang.org/x/crypto/bcrypt"
hash, _ := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
err := bcrypt.CompareHashAndPassword(hash, []byte(password))
```

### 5.5 TLS Configuration

```go
tlsConfig := &tls.Config{
    MinVersion: tls.VersionTLS13,     // Minimum TLS 1.3
    CurvePreferences: []tls.CurveID{  // Only safe curves
        tls.X25519,
        tls.CurveP256,
    },
    CipherSuites: nil,                // nil = safe defaults in Go 1.22+
    // Server certificates
    Certificates: []tls.Certificate{cert},
}
```

### 5.6 Input Validation

```go
import "github.com/go-playground/validator/v10"

type CreateUserRequest struct {
    Email    string `validate:"required,email,max=254"`
    Age      int    `validate:"gte=0,lte=150"`
    Password string `validate:"required,min=12"`
}

validate := validator.New()
if err := validate.Struct(req); err != nil {
    // return 400 with validation errors
}
```

### 5.7 Memory Safety

```go
// Slice bounds checking — Go does this automatically, but be careful with reslicing:
s := []int{1, 2, 3}
t := s[:5]  // PANICS at runtime: slice bounds out of range

// Cap vs len confusion:
s := make([]int, 0, 10)  // len=0, cap=10
s[0] = 1                 // PANICS: index out of range (len is 0)
s = append(s, 1)         // OK — len becomes 1

// Nil pointer dereference — always check before calling methods:
var p *MyType
p.Method()  // PANICS: nil pointer dereference in method call

// Avoid unsafe unless absolutely necessary.
// NEVER use encoding/gob with untrusted input — it can execute arbitrary code.
// NEVER use reflect to bypass unexported field checks for security-sensitive code.
```

---

## 6. Debugging & Troubleshooting

### 6.1 Race Detector

```bash
# Run tests with race detection
go test -race ./...

# Run binary with race detection
go build -race -o myapp ./cmd/server
./myapp  # will panic on first detected race

# Race detector catches:
# - Concurrent read/write to the same variable
# - Unsynchronized access to maps
# - Atomic violations
```

### 6.2 Goroutine Dump

```bash
# Send SIGQUIT to a running Go process (not SIGKILL)
kill -QUIT <pid>  # dumps all goroutine stacks to stderr

# Programmatic dump
import "runtime"
buf := make([]byte, 1<<20)  // 1MB
n := runtime.Stack(buf, true)  // all=true for all goroutines
fmt.Println(string(buf[:n]))

# Via pprof
import _ "net/http/pprof"
# curl http://localhost:6060/debug/pprof/goroutine?debug=2
```

### 6.3 Profiling Workflow

```bash
# 1. Benchmark to identify slow code
go test -bench=. -benchmem -cpuprofile=cpu.prof ./...

# 2. Analyze profile
go tool pprof cpu.prof
# (pprof) top10         — top CPU consumers
# (pprof) list funcName — annotated source
# (pprof) web           — call graph (requires graphviz)

# 3. Flamegraph (requires FlameGraph or pprof -http)
go tool pprof -http=:8080 cpu.prof
# Open http://localhost:8080 in browser → View → Flame Graph
```

### 6.4 Memory Leak Diagnosis

```go
// Goroutine leak — most common memory leak in Go
// Cause: goroutine blocks forever on channel/condition
// Detection: compare goroutine count over time
import "runtime"
fmt.Println(runtime.NumGoroutine())

// Or via pprof:
// http://localhost:6060/debug/pprof/goroutine?debug=1

// Slice leak — keeping reference to large backing array
func leak() []byte {
    data := make([]byte, 1<<30)  // 1GB
    return data[:4]              // returns 4 bytes but keeps 1GB alive!
}

// Fix: copy the needed portion
func noLeak() []byte {
    data := make([]byte, 1<<30)
    result := make([]byte, 4)
    copy(result, data[:4])
    return result  // only 4 bytes kept alive
}

// Finalizer leak — SetFinalizer prevents GC of the object
// Use sparingly and always provide a way to release explicitly.
```

### 6.5 Performance Patterns

```go
// sync.Pool — reuse allocated objects (reduces GC pressure)
var bufPool = sync.Pool{
    New: func() interface{} {
        return new(bytes.Buffer)
    },
}
buf := bufPool.Get().(*bytes.Buffer)
buf.Reset()
// ... use buf ...
bufPool.Put(buf)

// strings.Builder — efficient string concatenation
var sb strings.Builder
for _, s := range parts {
    sb.WriteString(s)
}
result := sb.String()  // single allocation

// Pre-allocate slices and maps when size is known
users := make([]User, 0, expectedCount)  // avoids reallocation
scores := make(map[string]int, expectedCount)

// Avoid boxing — interfaces cause heap allocation
var r io.Reader = bytes.NewReader(data)  // heap allocation
// Prefer concrete types in hot paths

// Escape analysis — see what allocates to heap
// go build -gcflags="-m" 2>&1 | grep "escapes to heap"
```

---

## 7. Popular Frameworks & Libraries

### 7.1 Web Frameworks

```go
// net/http — standard library (preferred for simple services)
mux := http.NewServeMux()
mux.HandleFunc("GET /users/{id}", handleGetUser)
http.ListenAndServe(":8080", mux)  // Go 1.22+ routing patterns

// chi — lightweight, idiomatic router (stdlib-compatible)
r := chi.NewRouter()
r.Use(middleware.Logger)
r.Use(middleware.Recoverer)
r.Get("/users/{id}", handleGetUser)
http.ListenAndServe(":8080", r)

// gorilla/mux — mature, widely used (now archived; migrate to chi or stdlib)
// gin — high-performance, validation, binding
// echo — minimalistic, fast
// fiber — Express-inspired, built on fasthttp (not net/http compatible)
```

### 7.2 Database

```go
// database/sql — standard library interface
db, _ := sql.Open("pgx", dsn)
defer db.Close()
db.SetMaxOpenConns(25)
db.SetMaxIdleConns(5)
db.SetConnMaxLifetime(5 * time.Minute)

// sqlx — extensions to database/sql (struct scanning, named params)
type User struct {
    ID    int    `db:"id"`
    Name  string `db:"name"`
}
var users []User
db.Select(&users, "SELECT id, name FROM users WHERE active = $1", true)

// pgx — PostgreSQL driver and toolkit (preferred for Postgres)
// sqlc — compile-time SQL → type-safe Go codegen
// queries.sql:
//   -- name: GetUser :one
//   SELECT * FROM users WHERE id = $1;
// Generated: func (q *Queries) GetUser(ctx context.Context, id int32) (User, error)

// ent — entity framework (codegen from schema)
// gorm — ORM (use with caution — can obscure query cost)
// bun — SQL-first ORM for PostgreSQL, MySQL, SQLite
```

### 7.3 gRPC

```go
// protobuf definition (service.proto):
// service UserService {
//   rpc GetUser(GetUserRequest) returns (GetUserResponse);
// }

// Generated code:
// protoc --go_out=. --go-grpc_out=. service.proto

// Server
lis, _ := net.Listen("tcp", ":50051")
s := grpc.NewServer()
pb.RegisterUserServiceServer(s, &server{})
s.Serve(lis)

// Client
conn, _ := grpc.Dial("localhost:50051", grpc.WithTransportCredentials(insecure.NewCredentials()))
client := pb.NewUserServiceClient(conn)
resp, _ := client.GetUser(ctx, &pb.GetUserRequest{Id: "123"})

// Connect (buf) — gRPC-compatible with HTTP/1.1 fallback, simpler toolchain
```

### 7.4 CLI

```go
// cobra — the standard for Go CLIs
var rootCmd = &cobra.Command{
    Use:   "myapp",
    Short: "A brief description",
    Run: func(cmd *cobra.Command, args []string) {
        // root command logic
    },
}

// urfave/cli — declarative, fluent API
// charmbracelet/bubbletea — TUI framework (Elm architecture in Go)
```

### 7.5 Testing

```go
// testify — assertions and mocking
import "github.com/stretchr/testify/assert"
assert.Equal(t, expected, actual)
assert.NoError(t, err)
assert.Contains(t, slice, element)

// gomock — mock generation from interfaces
// mockgen -source=service.go -destination=mock_service.go

// sqlmock — SQL driver for testing database interactions
db, mock, _ := sqlmock.New()
mock.ExpectQuery("SELECT name FROM users WHERE id = ?").
    WithArgs(1).
    WillReturnRows(sqlmock.NewRows([]string{"name"}).AddRow("Alice"))

// httptest — HTTP testing (stdlib)
ts := httptest.NewServer(handler)
defer ts.Close()
resp, _ := http.Get(ts.URL + "/users/1")
```

### 7.6 Observability

```go
// slog — structured logging (stdlib, Go 1.21+)
logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))
logger.Info("request", "method", "GET", "path", "/users", "duration", d)

// zap — high-performance structured logging
// zerolog — zero-allocation JSON logger

// OpenTelemetry
import (
    "go.opentelemetry.io/otel"
    "go.opentelemetry.io/otel/trace"
)
ctx, span := otel.Tracer("myapp").Start(ctx, "operation")
defer span.End()

// Prometheus client
import "github.com/prometheus/client_golang/prometheus"
counter := prometheus.NewCounter(prometheus.CounterOpts{
    Name: "requests_total",
    Help: "Total number of requests",
})
```

### 7.7 Task Runners

```bash
# Task (Taskfile.yml) — YAML-based, shell-agnostic
# task build    → runs build target
# task test     → runs test target

# mage — Go-based build tool (write tasks in Go)
# just — command runner (Makefile-like syntax)
```

---

## 8. Platform-Specific Variations

### 8.1 TinyGo

TinyGo is a Go compiler for small places: WebAssembly, microcontrollers
(Arduino, ESP32, Raspberry Pi Pico), and embedded systems.

```bash
# WebAssembly
tinygo build -o main.wasm -target wasm main.go

# Microcontroller
tinygo flash -target arduino-nano33 ./blinky
```

**Limitations:**
- Limited `reflect` support — many libraries that use reflection won't compile.
- No cgo.
- Smaller standard library (no `net/http` client, no `database/sql`).
- Garbage collector is simpler (no concurrent GC on some targets).

### 8.2 Go in Containers

```dockerfile
# Multi-stage build — produce minimal image
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /server ./cmd/server

# Distroless — no shell, no package manager, minimal attack surface
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /server /server
USER nonroot:nonroot
ENTRYPOINT ["/server"]

# FROM scratch — only the binary (no CA certs, no tzdata, no users)
FROM scratch
COPY --from=builder /server /server
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
ENTRYPOINT ["/server"]
```

**Static build for `FROM scratch`:**
```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w -extldflags '-static'" \
    -o server ./cmd/server
```

### 8.3 Go Mobile

```bash
# gomobile — build Go libraries for Android and iOS
gomobile bind -target=android ./mypkg    # .aar for Android
gomobile bind -target=ios ./mypkg        # framework for iOS

# gomobile build — standalone APK/IPA
gomobile build -target=android ./mypkg
```

### 8.4 Go Shared Libraries

```bash
# Build as a C shared library
go build -buildmode=c-shared -o libmylib.so ./mypkg

# Generates: libmylib.so + libmylib.h
# Callable from C, Python (ctypes), Node.js (ffi-napi), etc.
```

### 8.5 Platform Notes

| Platform | Notes |
|----------|-------|
| Linux | Primary target. Full syscall support. seccomp, cgroups, namespaces available. |
| macOS | Full support. `CGO_ENABLED=1` by default. Cross-compiling FROM macOS TO Linux: set `CGO_ENABLED=0` or use `zig cc` as C compiler. |
| Windows | Full support. Path handling uses backslashes (`filepath` handles this). No `SIGQUIT`. Service support via `golang.org/x/sys/windows/svc`. |
| FreeBSD/OpenBSD/NetBSD | Tier 1 ports. File monitoring differences (`kqueue` vs `inotify`). |
| Plan 9 | Historical port (limited utility). |
| Illumos/Solaris | Tier 2/3 ports. Most stdlib works; some networking edge cases. |
| Wasm/WASI | `GOOS=js GOARCH=wasm` (browser) or `GOOS=wasip1 GOARCH=wasm` (WASI preview 1). No threads, no `net` (except via WASI sockets). |
